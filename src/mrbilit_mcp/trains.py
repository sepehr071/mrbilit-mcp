"""Train tools: search, price calendar, family price, stops, and alternatives when direct trains are full."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import BUS, FLIGHT, MASIR, TOKEN_HEADERS, TRAIN, ApiError, fetch, not_past, obj, toman
from .places import train_stations
from .registry import tool

Station = Annotated[
    int,
    Field(
        ge=1,
        le=9_999_999,
        description="Train station id from mb_find_place(mode='train'), e.g. 1 (Tehran) or 191 (Mashhad).",
    ),
]
ClassId = Annotated[
    int, Field(ge=1, description="class_id of a train class from a fresh mb_search_trains result, e.g. 9847134.")
]
Date = Annotated[
    dt.date, Field(description="Travel date YYYY-MM-DD, e.g. '2026-10-14'. Sales open about 18 days ahead.")
]
# Sell type (quota) codes of the API.
QUOTAS = {"general": 3, "men": 1, "women": 2, "car": 4}
Quota = Annotated[
    Literal["general", "men", "women", "car"],
    Field(description="Seat quota: general (families, mixed), men only, women only, or car transport."),
]


@tool("Search trains")
async def mb_search_trains(
    origin: Station,
    destination: Station,
    date: Date,
    quota: Quota = "general",
    sort: Annotated[Literal["cheapest", "earliest"], Field(description="Order of the trains.")] = "earliest",
    available_only: Annotated[bool, Field(description="Hide sold-out classes and trains with no free seat.")] = True,
    outbound_class_id: Annotated[
        int | None,
        Field(
            ge=1,
            description="Round trip, return leg only: the class_id chosen on the outbound leg, e.g. 9847134 (limits the return to the same rail company, as the site does).",
        ),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=50, description="Max trains.")] = 20,
) -> dict[str, Any]:
    """Trains between two stations on a date with every class (wagon), its price per adult and free seats.

    Seat counts are fresh (not cached). price_toman is per adult; for children, infants or a
    whole compartment call mb_train_price with the class_id. refund_penalties apply to every
    class unless a class lists its own. Stops and times: mb_train_stops. No seats left: try
    mb_train_price_calendar for other days or mb_alternative_routes. A round trip is two calls:
    the return leg with outbound_class_id.
    """
    not_past(date)
    params: dict[str, Any] = {
        "from": origin,
        "to": destination,
        "date": date.isoformat(),
        "genderCode": QUOTAS[quota],
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 0,
        "exclusive": "false",
        "availableStatus": "Both",
        "disableCache": "true",  # cached capacity can be stale
    }
    if outbound_class_id:
        params["selectedCapacityId"] = outbound_class_id
    body = obj(await fetch(f"{TRAIN}/api/GetAvailable/v2", params))
    trains, common = [], None
    for t in body.get("trains") or []:
        classes = []
        for p in t.get("prices") or []:
            for c in p.get("classes") or []:
                # reservationAvailable is true on sold-out classes too; only isAvailable and price mark a buyable one.
                bookable = bool(c.get("isAvailable") and c.get("price"))
                if available_only and not bookable:
                    continue
                tiers = [
                    {"window": e.get("title"), "penalty_pct": e.get("percentAmount")}
                    for e in c.get("cancellationTermEntries") or []
                ]
                common = common if common is not None else tiers
                row = {
                    "class_id": c.get("id"),
                    "wagon": (c.get("wagonName") or "").strip(),
                    "stars": c.get("class"),
                    "seating": c.get("compartmentType"),
                    "price_toman": toman(c.get("price")),
                    "seats_left": c.get("capacity"),
                    "bookable": bookable,
                    "features": c.get("features") or [],
                }
                if tiers != common:
                    row["refund_penalties"] = tiers
                classes.append(row)
        if not classes:
            continue
        trains.append(
            {
                "train_number": t.get("trainNumber"),
                "company": t.get("corporationName"),
                "departure": t.get("departureTime"),
                "arrival": t.get("arrivalTime"),
                "refundable_online": t.get("cancellable"),
                "classes": sorted(classes, key=lambda c: (not c["bookable"], c["price_toman"] or 0)),
            }
        )
    if sort == "cheapest":  # sold-out classes have price 0: rank on bookable ones only
        trains.sort(key=lambda t: min((c["price_toman"] for c in t["classes"] if c["bookable"]), default=float("inf")))
    else:
        trains.sort(key=lambda t: t["departure"] or "")
    out: dict[str, Any] = {
        "from": ((body.get("fromLocation") or {}).get("title")),
        "to": ((body.get("toLocation") or {}).get("title")),
        "trains_listed": len(body.get("trains") or []),
        "matching": len(trains),
        "refund_penalties": common or [],
        "trains": trains[:limit],
    }
    if not trains:
        out["note"] = (
            "No train with free seats. Sales open about 18 days ahead; try mb_train_price_calendar, another quota, "
            "or mb_alternative_routes."
        )
    return out


@tool("Train price calendar")
async def mb_train_price_calendar(
    origin: Station,
    destination: Station,
    start_date: Annotated[
        dt.date | None, Field(description="First day YYYY-MM-DD, e.g. '2026-10-10'; default today.")
    ] = None,
    days: Annotated[int, Field(ge=1, le=60, description="Number of days to cover.")] = 30,
    seats: Annotated[
        int, Field(ge=1, le=10, description="Seats needed; days without that many free seats are left out.")
    ] = 1,
    quota: Quota = "general",
) -> dict[str, Any]:
    """Cheapest train ticket per day (one adult) for a route, only days with free seats.

    Use to find the cheapest or the next bookable day, then mb_search_trains for that day.
    Train sales open only about 18 days ahead, so later days are missing.
    """
    start = start_date or dt.date.today()
    prices = await fetch(
        f"{TRAIN}/api/GetMinPrices",
        {
            "From": origin,
            "To": destination,
            "FromDate": start.isoformat(),
            "ToDate": (start + dt.timedelta(days=days - 1)).isoformat(),
            # Without Capacity the map also holds today and sold-out days, at prices no seat is sold for.
            "Capacity": seats,
            "GenderCode": QUOTAS[quota],
        },
    )
    rows = sorted(
        ({"date": day[:10], "price_toman": toman(v)} for day, v in (prices or {}).items() if toman(v)),
        key=lambda r: r["date"],
    )
    return {"days": rows, "cheapest": min(rows, key=lambda r: r["price_toman"]) if rows else None}


@tool("Train ticket price")
async def mb_train_price(
    class_id: ClassId,
    adults: Annotated[int, Field(ge=1, le=9, description="Adults (12+).")] = 1,
    children: Annotated[int, Field(ge=0, le=8, description="Children (2-11).")] = 0,
    infants: Annotated[int, Field(ge=0, le=8, description="Infants (under 2).")] = 0,
    foreigners: Annotated[int, Field(ge=0, le=9, description="Foreign passengers (priced separately).")] = 0,
    empty_seats: Annotated[
        int,
        Field(
            ge=0,
            le=5,
            description="Empty berths to buy for a whole (exclusive) compartment, e.g. 1 for 3 people in a 4-berth.",
        ),
    ] = 0,
) -> dict[str, Any]:
    """Exact price of one train class for a party: per adult, child, infant, foreigner and empty berth, and the total.

    class_id comes from mb_search_trains (it expires with the search). A sold-out class still
    returns a price here, so check seats_left in the search first. breakdown is the adult
    fare's parts (they do not add up exactly to the price; the price is what is paid).
    """
    try:
        body = obj(
            await fetch(
                f"{TRAIN}/api/GetPricing",
                {
                    "CapacityId": class_id,
                    "AdultCount": adults,
                    "ChildCount": children,
                    "InfantCount": infants,
                    "EmptySeatCount": empty_seats,
                    "ForeignerCount": foreigners,
                },
            )
        )
    except ApiError as e:
        if e.status == 500:  # an unknown or expired class id answers 500
            raise ApiError(
                f"Unknown or expired class_id {class_id}. Run mb_search_trains again and use a fresh class_id.", 500
            ) from e
        raise
    counts = {
        "adultPrice": ("adult", adults),
        "childPrice": ("child", children),
        "infantPrice": ("infant", infants),
        "foreignerPrice": ("foreigner", foreigners),
        "emptySeatPrice": ("empty_seat", empty_seats),
    }
    units = {k: toman((body.get(field) or {}).get("price")) for field, (k, _) in counts.items()}
    total = sum((units[k] or 0) * n for k, n in counts.values())
    adult = body.get("adultPrice") or {}
    return {
        "class_id": class_id,
        "unit_price_toman": {k: units[k] for k, n in counts.values() if n},
        "total_toman": total,
        "adult_breakdown_toman": {
            k: toman(adult.get(f))
            for k, f in (
                ("base_fare", "baseFare"),
                ("station_fee", "stationService"),
                ("hall_fee", "hallPrice"),
                ("food", "foodPrice"),
                ("commission", "commission"),
                ("discount", "discount"),
            )
        },
    }


@tool("Train stops")
async def mb_train_stops(class_id: ClassId) -> dict[str, Any]:
    """Every station a train stops at, in order, with the date and local time at each.

    Pass a class_id from mb_search_trains (sold-out classes work too; never a train number).
    The first stop can be before your station (a Tehran-Mashhad train may start in Qom).
    """
    stops = await fetch(f"{TRAIN}/api/MidStation/{class_id}")
    return {
        "stops": [
            {
                "station_id": s.get("stationId"),
                "station": s.get("station"),
                "date": (s.get("date") or "")[:10],
                "time": (s.get("time") or "")[:5],
            }
            for s in stops or []
        ]
    }


@tool("Alternative routes")
async def mb_alternative_routes(origin: Station, destination: Station, date: Date) -> dict[str, Any]:
    """Alternatives when direct trains are full or dear: trips with one change, and nearby train, bus and flight routes.

    with_one_change: up to 7 journeys with one change, any mix of train and bus (bus+bus too;
    price per adult, total duration, wait at the change); a direct bus in nearby_bus_routes can
    be cheaper and faster. nearby_*: routes from or to stations, bus cities and airports near the
    two stations, with the cheapest price where known (classes_listed is the API's raw count,
    not the bookable classes); pass their ids to mb_search_trains, mb_search_buses or
    mb_search_flights. Seat counts here can be cached: confirm with a search.
    """
    not_past(date)
    stations = {s["code"]: s for s in await train_stations()}
    a, b = stations.get(origin) or {}, stations.get(destination) or {}
    have_coords = all(s.get("latitude") for s in (a, b))
    points = {
        "from": {"latitude": a.get("latitude"), "longitude": a.get("longitude")},
        "to": {"latitude": b.get("latitude"), "longitude": b.get("longitude")},
        "departureDate": date.isoformat(),
    }
    calls = {"with_one_change": _journeys(origin, destination, date)}
    if have_coords:
        calls["nearby_train_routes"] = fetch(f"{TRAIN}/api/GetNearbyRoutes", method="POST", json=points)
        calls["nearby_bus_routes"] = fetch(
            f"{BUS}/api/GetNearbyRoutes", method="POST", json={**points, "providerId": None}
        )
        calls["nearby_flights"] = fetch(
            f"{FLIGHT}/api/Airports/Nearby",
            {
                "OriginLatitude": a["latitude"],
                "OriginLongitude": a["longitude"],
                "DestinationLatitude": b["latitude"],
                "DestinationLongitude": b["longitude"],
            },
        )
    results = dict(zip(calls, await asyncio.gather(*calls.values(), return_exceptions=True), strict=True))
    out: dict[str, Any] = {}
    errors = {k: str(v) for k, v in results.items() if isinstance(v, Exception)}
    if not isinstance(results["with_one_change"], Exception):
        out["with_one_change"] = results["with_one_change"]
    if have_coords:
        if "nearby_train_routes" not in errors:
            out["nearby_train_routes"] = [
                {
                    "from_id": r.get("fromID"),
                    "to_id": r.get("toID"),
                    "route": f"{r.get('fromName')} - {r.get('toName')}",
                    "classes_listed": r.get("count"),
                    "min_price_toman": toman(r.get("minPrice")) or None,
                }
                for r in (results["nearby_train_routes"] or {}).get("routes") or []
            ]
        if "nearby_bus_routes" not in errors:
            out["nearby_bus_routes"] = [
                {
                    "from_id": r.get("fromID"),
                    "to_id": r.get("toID"),
                    "route": f"{r.get('fromName')} - {r.get('toName')}",
                    "min_price_toman": toman(r.get("minPrice")) or None,  # 0 = no price known, not free
                }
                for r in (results["nearby_bus_routes"] or {}).get("routes") or []
            ]
        if "nearby_flights" not in errors:
            pairs = dict.fromkeys(
                (f.get("SourceId"), f.get("DestinationId"), f.get("SourceTitle"), f.get("DestinationTitle"))
                for f in results["nearby_flights"] or []
            )  # the API repeats pairs
            out["nearby_flights"] = [{"from": s, "to": d, "airports": f"{sa} - {da}"} for s, d, sa, da in pairs]
    else:
        out["note"] = "One of the stations has no coordinates, so nearby routes were skipped."
    if errors:
        out["errors"] = errors
    return out


async def _journeys(origin: int, destination: int, date: dt.date) -> list[dict[str, Any]]:
    params = {
        "idType": 2,
        "originId": origin,
        "destinationId": destination,
        "date": date.isoformat(),
        "adultCount": 1,
        "childCount": 0,
        "infantCount": 0,
    }
    # masir hangs at random (60-90 s) and answers in under 2 s on retry: short timeout, retry twice.
    for attempt in range(3):
        try:
            body = await fetch(f"{MASIR}/api/GetAvailable", params, headers=TOKEN_HEADERS, timeout=25)
            break
        except ApiError as e:
            if e.status is not None or attempt == 2:
                raise
    journeys = [
        {
            "price_toman": toman(j.get("price")),
            "duration": _hm(j.get("durationSeconds")),
            "wait_at_change": _hm(j.get("stopDurationSeconds")),
            "legs": [
                {
                    "mode": {1: "train", 2: "bus"}.get(s.get("serviceType"), s.get("serviceType")),
                    "from": s.get("originTitle"),
                    "to": s.get("destinationTitle"),
                    "from_id": s.get("originCode"),
                    "to_id": s.get("destinationCode"),
                    "departure": s.get("departureTime"),
                    "arrival": s.get("arrivalTime"),
                    "price_toman": toman(s.get("adultPrice")),
                    "seats_left": s.get("capacity"),
                    "operator": (s.get("corporationTitle") or s.get("carrierTitle") or "").strip(),
                    "train_number": s.get("serviceNumber") or None,
                    "class_id": int(s["domainOptionId"]) if s.get("domainOptionId") else None,
                    "vehicle": (s.get("wagonName") or s.get("vehicleType") or "").strip() or None,
                }
                for s in j.get("segments") or []
            ],
        }
        for j in (body or {}).get("journeys") or []
    ]
    return sorted(journeys, key=lambda j: j["price_toman"] or 0)


def _hm(seconds: Any) -> str | None:
    if not isinstance(seconds, int):
        return None
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
