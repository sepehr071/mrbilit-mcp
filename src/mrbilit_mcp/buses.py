"""Bus and taxi tools: search, price calendar, seat map, private intercity taxis."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import BUS, TOKEN_HEADERS, ApiError, fetch, not_past, obj, toman
from .registry import tool

City = Annotated[
    int,
    Field(
        ge=100_000,
        le=99_999_999,
        description="Bus city or terminal id from mb_find_place(mode='bus'), e.g. 11320000 (Tehran, all terminals) or 31310000 (Mashhad).",
    ),
]
TaxiCity = Annotated[
    int,
    Field(
        ge=100_000,
        le=99_999_999,
        description="City id from mb_find_place(mode='taxi'), e.g. 11320000 (Tehran) or 54310000 (Rasht).",
    ),
]
Date = Annotated[dt.date, Field(description="Departure date YYYY-MM-DD, e.g. '2026-10-20'.")]
HHMM = Annotated[str | None, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="Local time HH:MM, e.g. '18:00'.")]
SEARCH_FLAGS = {
    "includeClosed": True,
    "includePromotions": True,
    "loadFromDbOnUnavailability": True,
    "includeUnderDevelopment": True,
}
CAR_CLASSES = {"economy": 278, "vip": 276, "formal": 280}  # taxi classes are superCorporationID values
SEAT_MARKS = {0: "", 1: "w", 2: "m", 4: "x"}  # free, sold to a woman, sold to a man, not sold online


@tool("Search buses")
async def mb_search_buses(
    origin: City,
    destination: City,
    date: Date,
    sort: Annotated[Literal["cheapest", "earliest"], Field(description="Order of the buses.")] = "earliest",
    vip_only: Annotated[bool, Field(description="Only VIP buses.")] = False,
    companies: Annotated[
        list[Annotated[int, Field(ge=1)]] | None,
        Field(
            max_length=10,
            description="Only these company ids, from `companies` of a result or mb_companies(mode='bus'), e.g. [4, 16].",
        ),
    ] = None,
    depart_after: HHMM = None,
    depart_before: HHMM = None,
    include_sold_out: Annotated[bool, Field(description="Also list buses with no free seat.")] = False,
    limit: Annotated[int, Field(ge=1, le=60, description="Max buses.")] = 20,
) -> dict[str, Any]:
    """Intercity buses on a date: company, terminal, times, price per seat, free seats, bus type, stops, refund penalties.

    A city id covers all its terminals; a terminal id only that terminal. price_toman is per
    seat (a party pays it once per seat). refund_penalties apply to every bus unless a bus lists
    its own: until_hours_before = the step applies until that many hours before departure
    (null = up to departure); free_cancel_minutes = free cancellation within that many minutes
    after purchase. Next: mb_bus_seats with a bus_id for seat numbers and women/men seats; other
    days: mb_bus_price_calendar.
    """
    not_past(date)
    body = obj(
        await fetch(
            f"{BUS}/api/GetBusServices",
            method="POST",
            json={"from": origin, "to": destination, "date": date.isoformat(), **SEARCH_FLAGS},
        )
    )
    buses = body.get("buses") or []
    rows = []
    for b in buses:
        hhmm = (b.get("departureTime") or "")[11:16]
        if (
            (not include_sold_out and not b.get("capacity"))
            or (vip_only and not b.get("isVIP"))
            or (companies and b.get("superCorporationID") not in companies)
            or (depart_after and hhmm < depart_after)
            or (depart_before and hhmm > depart_before)
        ):
            continue
        rows.append(
            {
                "bus_id": b.get("id"),
                "company": (b.get("superCorporation") or "").strip(),
                "company_id": b.get("superCorporationID"),
                "cooperative": " ".join((b.get("corporation") or "").split()),
                "from": b.get("fromName"),
                "to": b.get("toName"),
                "departure": b.get("departureTime"),
                "arrival": b.get("arrivalTime"),
                "price_toman": toman((b.get("price") or 0) - (b.get("discount") or 0)),
                "seats_left": b.get("capacity"),
                "vip": b.get("isVIP"),
                "bus": (b.get("busType") or "").strip(),
                "features": b.get("features") or [],
                "stops": b.get("intermediateDestinations") or [],
                "refund_penalties": _penalties(b),
                "free_cancel_minutes": b.get("freeCancellationMins"),
            }
        )
    rows.sort(
        key=(lambda r: (r["price_toman"] or 0, r["departure"] or ""))
        if sort == "cheapest"
        else (lambda r: r["departure"] or "")
    )
    shown = rows[:limit]
    # Most buses share one of two penalty tables: list the one that saves most text once, as mb_search_trains does.
    tiers = [r["refund_penalties"] for r in shown]
    common = max(tiers, key=lambda t: tiers.count(t) * len(str(t))) if tiers else []
    for r in shown:
        if r["refund_penalties"] == common:
            del r["refund_penalties"]
    fd = body.get("filterData") or {}
    out: dict[str, Any] = {
        "buses_listed": len(buses),
        "matching": len(rows),
        "companies": [{"id": c.get("id"), "name": (c.get("name") or "").strip()} for c in fd.get("corporations") or []],
        "refund_penalties": common,
        "buses": shown,
    }
    if not buses:
        out["note"] = (
            "No buses for this route and date (or not on sale yet). Try mb_bus_price_calendar or check the ids with mb_find_place."
        )
    return out


@tool("Bus price calendar")
async def mb_bus_price_calendar(
    origin: City,
    destination: City,
    start_date: Annotated[
        dt.date | None, Field(description="First day YYYY-MM-DD, e.g. '2026-10-10'; default today.")
    ] = None,
    days: Annotated[int, Field(ge=1, le=90, description="Number of days to cover.")] = 30,
    seats: Annotated[int, Field(ge=1, le=10, description="Seats needed.")] = 1,
) -> dict[str, Any]:
    """Cheapest bus seat per day for a route; days without buses (or not on sale yet) are left out.

    Use to find the cheapest day, then mb_search_buses for that day. Bus sales usually open
    about a month ahead.
    """
    start = start_date or dt.date.today()
    prices = await fetch(
        f"{BUS}/api/GetMinPrices",
        {
            "from": origin,
            "to": destination,
            "fromDate": start.isoformat(),
            "toDate": (start + dt.timedelta(days=days - 1)).isoformat(),
            "capacity": seats,  # without it (or 0) the answer is always {}
        },
    )
    rows = sorted(
        ({"date": day[:10], "price_toman": toman(v)} for day, v in (prices or {}).items() if toman(v)),
        key=lambda r: r["date"],
    )
    return {"days": rows, "cheapest": min(rows, key=lambda r: r["price_toman"]) if rows else None}


@tool("Bus seat map")
async def mb_bus_seats(
    bus_id: Annotated[int, Field(ge=1, description="bus_id from a fresh mb_search_buses result, e.g. 55983988.")],
) -> dict[str, Any]:
    """Seat map of one bus: free seat numbers, seats sold to women and to men, and a row-by-row layout.

    layout: one line per row from the front; each seat is its number plus a mark: none = free,
    w = sold to a woman, m = sold to a man, x = not sold online; '|' = the aisle (seats on the
    same side of it are neighbours), '--' = no seat, 'DR' = driver. Rule text (who may sit
    where) is in seat_rules. price_toman is null for most buses: use the search price. bus_id
    expires: take it from a fresh search.
    """
    try:
        d = await fetch(
            f"{BUS}/api/v2/GetSeats", {"busId": bus_id, "convertFemaleToMale": "false"}, headers=TOKEN_HEADERS
        )
    except ApiError as e:
        if e.status in (400, 500):
            raise ApiError(
                f"Unknown or expired bus_id {bus_id}. Run mb_search_buses again and use a fresh bus_id.", e.status
            ) from e
        raise
    d = obj(d)
    aisle = d.get("spacePlace") or 0  # seats before the aisle: 2 for both 2+1 and 2+2 buses
    by_status: dict[int, list[int]] = {}
    layout = []
    for row in d.get("seats") or []:
        cells = []
        for c in row:
            status, number = c.get("status"), c.get("number") or 0
            if status == 5:
                cells.append("DR")
            elif status == 3 or number <= 0:  # aisle, door or gap (number 0 or -1)
                cells.append("--")
            else:
                by_status.setdefault(status, []).append(number)
                cells.append(f"{number:02d}{SEAT_MARKS.get(status, 'x')}")
        if 0 < aisle < len(cells):
            cells.insert(aisle, "|")
        layout.append(" ".join(cells))
    return {
        "free_seats": sorted(by_status.get(0, [])),
        "sold_to_women": sorted(by_status.get(1, [])),
        "sold_to_men": sorted(by_status.get(2, [])),
        "not_sold_online": sorted(by_status.get(4, [])),
        "layout": layout,
        "seat_rules": d.get("customSeatsMessage"),
        "price_toman": toman(d.get("price")),  # only some providers send it
    }


@tool("Search intercity taxis")
async def mb_search_taxis(
    origin: TaxiCity,
    destination: TaxiCity,
    date: Date,
    car_class: Annotated[
        Literal["any", "economy", "vip", "formal"],
        Field(description="Car class: economy (Samand/Peugeot), vip, formal (top cars)."),
    ] = "any",
    depart_after: HHMM = None,
    depart_before: HHMM = None,
    limit: Annotated[int, Field(ge=1, le=80, description="Max offers.")] = 24,
) -> dict[str, Any]:
    """Private door-to-door intercity taxis (whole car, up to 3 passengers) for a date, one offer per pick-up slot.

    price_toman is for the whole car, not per person. `classes` summarises each car class
    (cars, cheapest price, slots). Many city pairs have no taxis (Tehran-Mashhad, Tehran-Qom):
    the empty result then says so. Taxi cities: mb_find_place(mode='taxi').
    """
    not_past(date)
    body = obj(
        await fetch(
            f"{BUS}/api/GetTaxiServices",
            method="POST",
            json={"from": origin, "to": destination, "date": date.isoformat(), **SEARCH_FLAGS},
        )
    )
    offers = body.get("buses") or []
    rows = []
    for b in offers:
        hhmm = (b.get("departureTime") or "")[11:16]
        if (
            (car_class != "any" and b.get("superCorporationID") != CAR_CLASSES[car_class])
            or (depart_after and hhmm < depart_after)
            or (depart_before and hhmm > depart_before)
        ):
            continue
        rows.append(
            {
                "offer_id": b.get("id"),
                "class": (b.get("superCorporation") or "").strip(),
                "cars": (b.get("busType") or "").strip(),
                "pickup": b.get("departureTime"),
                "price_toman": toman(b.get("price")),
                "max_passengers": b.get("capacity"),
            }
        )
    rows.sort(key=lambda r: r["pickup"] or "")
    classes: dict[str, dict[str, Any]] = {}
    for r in rows:
        c = classes.setdefault(
            r["class"], {"class": r["class"], "cars": r["cars"], "from_price_toman": r["price_toman"], "slots": 0}
        )
        c["from_price_toman"] = min(c["from_price_toman"] or 0, r["price_toman"] or 0)
        c["slots"] += 1
    out: dict[str, Any] = {
        "classes": list(classes.values()),
        "refund_penalties": _penalties(offers[0]) if offers else [],
        "free_cancel_minutes": offers[0].get("freeCancellationMins") if offers else None,
        "offers": rows[:limit],
    }
    if not offers:
        out["note"] = "No taxis for this pair and date. Many pairs have none; try a bus with mb_search_buses."
    return out


def _penalties(b: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for p in b.get("penaltyRates") or []:
        row = {"until_hours_before": p.get("hoursBefore"), "penalty_pct": p.get("percent")}
        if p.get("customText"):
            row["condition"] = p["customText"]
        out.append(row)
    return out
