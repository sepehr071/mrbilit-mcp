"""Flight tools: search (one-way, round trip, domestic and international), price calendar, fare details."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import Field

from .content import html_text
from .http import CHARTER, FLIGHT, ApiError, fetch, not_past, obj, toman
from .places import domestic_airports
from .registry import tool

Airport = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z]{3}([Aa][Ll][Ll])?$",
        description="IATA airport or city code from mb_find_place(mode='flight'), e.g. 'THR', 'MHD', 'IKA' or 'ISTALL' (all Istanbul airports).",
    ),
]
Date = Annotated[dt.date, Field(description="Gregorian date YYYY-MM-DD, e.g. '2026-10-20'.")]
ReturnDate = Annotated[
    dt.date | None, Field(description="Return date YYYY-MM-DD for a round trip, e.g. '2026-10-23'; omit for one-way.")
]
Adults = Annotated[int, Field(ge=1, le=9, description="Adults (12+).")]
Children = Annotated[int, Field(ge=0, le=8, description="Children (2-11).")]
Infants = Annotated[int, Field(ge=0, le=8, description="Infants (under 2).")]
HHMM = Annotated[str | None, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="Local time HH:MM, e.g. '06:00'.")]

CABINS = {
    "economy": "Economy",
    "economy_plus": "EconomyPlus",
    "premium_economy": "PremiumEconomy",
    "business": "Business",
    "first": "FirstClass",
}


@tool("Search flights")
async def mb_search_flights(
    origin: Airport,
    destination: Airport,
    date: Date,
    return_date: ReturnDate = None,
    adults: Adults = 1,
    children: Children = 0,
    infants: Infants = 0,
    cabin: Annotated[
        Literal["any", "economy", "economy_plus", "premium_economy", "business", "first"],
        Field(description="Cabin class filter (applied here; the API itself ignores it)."),
    ] = "any",
    sort: Annotated[Literal["cheapest", "earliest"], Field(description="Order of the flights.")] = "cheapest",
    airlines: Annotated[
        list[Annotated[str, Field(pattern=r"^[A-Za-z0-9]{2,3}$")]] | None,
        Field(
            max_length=10,
            description="Only these airline codes, e.g. ['IR', 'W5'] (codes from results or mb_companies).",
        ),
    ] = None,
    direct_only: Annotated[bool, Field(description="Only flights without a connection.")] = False,
    depart_after: HHMM = None,
    depart_before: HHMM = None,
    limit: Annotated[int, Field(ge=1, le=50, description="Max flights per list.")] = 15,
) -> dict[str, Any]:
    """Search flights for a date: every bookable flight with price, seats left, baggage and times.

    price_toman is one adult's fare (taxes included); total_toman is the whole party (adult,
    child and infant fares). Fares without enough seats for the party are dropped by the site.
    Domestic round trips come back as two lists (`outbound`, `return`, each its own ticket);
    international round trips as packages whose price covers both ways (flight_id names both
    legs). Times are local to each airport, even though they all carry the API's +03:30 suffix;
    international flights from Tehran can leave from IKA when THR is asked (segments show the
    real airports). Flights not on sale (sold out or auto-reserve only) are counted in
    not_on_sale. For refund penalties, exact baggage and fare rules call mb_flight_fare_details
    with the flight_id; for the cheapest day use mb_flight_price_calendar first.
    """
    not_past(date)
    o, d = origin.upper(), destination.upper()
    pax = (adults, children, infants)
    filters = {
        "cabin": cabin,
        "sort": sort,
        "airlines": {a.upper() for a in airlines or []},
        "direct_only": direct_only,
        "after": depart_after,
        "before": depart_before,
        "limit": limit,
    }
    if return_date and return_date < date:
        raise ApiError("return_date is before date.")
    if return_date and await is_domestic(o) and await is_domestic(d):
        # The site searches a domestic round trip one leg at a time; two routes in one call drop the one-way fares.
        out, back = await asyncio.gather(_search([(o, d, date)], pax), _search([(d, o, return_date)], pax))
        return {"outbound": _result(out, pax, filters), "return": _result(back, pax, filters)}
    routes = [(o, d, date)] + ([(d, o, return_date)] if return_date else [])
    result = _result(await _search(routes, pax), pax, filters)
    if return_date:
        result["round_trip_package"] = True  # price_toman covers both ways
    return result


@tool("Flight price calendar")
async def mb_flight_price_calendar(
    origin: Airport,
    destination: Airport,
    start_date: Annotated[
        dt.date | None, Field(description="First day YYYY-MM-DD, e.g. '2026-10-15'; default today.")
    ] = None,
    days: Annotated[int, Field(ge=1, le=180, description="Number of days to cover.")] = 30,
) -> dict[str, Any]:
    """Cheapest one-adult fare per day for a route over a date range, in one call.

    Use to find the cheapest day to fly, then call mb_search_flights for that day. The values
    are the site's cached minimums and can differ from a live search; days with no known fare
    are left out (no flights, sold out or not cached).
    """
    start = start_date or dt.date.today()
    rows = await fetch(
        f"{FLIGHT}/api/Flights/MinPrices",
        method="POST",
        json={
            "AdultCount": 1,
            "ChildCount": 0,
            "InfantCount": 0,
            "Origin": origin.upper(),
            "Destination": destination.upper(),
            "StartDate": f"{start.isoformat()}T00:00:00",
            "EndDate": f"{(start + dt.timedelta(days=days - 1)).isoformat()}T00:00:00",
        },
    )
    priced = [
        {"date": (r.get("Date") or "")[:10], "price_toman": toman(r.get("TotalFare")), "airline": r.get("AirlineCode")}
        for r in rows or []
        if r.get("TotalFare")
    ]
    out: dict[str, Any] = {
        "days": priced,
        "cheapest": min(priced, key=lambda r: r["price_toman"]) if priced else None,
        "days_without_price": len(rows or []) - len(priced),
    }
    if not priced:
        out["note"] = "No known fares. Check the codes with mb_find_place(mode='flight'), or try other dates."
    return out


@tool("Flight fare details")
async def mb_flight_fare_details(
    origin: Airport,
    destination: Airport,
    date: Date,
    flight_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=400,
            description="flight_id from mb_search_flights, e.g. '21919377' (international round trip: '21894997+21924108').",
        ),
    ],
    return_date: Annotated[
        dt.date | None,
        Field(
            description="Only for an international round-trip package: its return date YYYY-MM-DD, e.g. '2026-10-23'."
        ),
    ] = None,
    adults: Adults = 1,
    children: Children = 0,
    infants: Infants = 0,
) -> dict[str, Any]:
    """Every fare of one flight with refund penalties, confirmed baggage, fare rules, notes and per-passenger prices.

    Re-runs the search of mb_search_flights (same route, date and party) and returns the chosen
    flight. baggage_checked_with_airline is true when the site re-checked the baggage with the
    airline (false = the search value, usually right). refund_penalties: penalty percent of the
    ticket price per time window before departure, refund = paid x (100 - penalty_pct) / 100;
    penalty_text when there is no percent (e.g. the supplier's terms).
    """
    not_past(date)
    o, d = origin.upper(), destination.upper()
    routes = [(o, d, date)] + ([(d, o, return_date)] if return_date else [])
    body = await _search(routes, (adults, children, infants))
    flight = next((f for f in body.get("Flights") or [] if _flight_id(f) == flight_id), None)
    if flight is None:
        raise ApiError(
            f"Flight {flight_id} is not in the current results for {o}-{d} on {date}. Ids change between searches: "
            "run mb_search_flights again with the same route, date and party, and pass its flight_id."
        )
    fares = []
    for p in flight.get("Prices") or []:
        fares.append(await _fare(p))
    return {
        "flight_id": flight_id,
        "segments": [_segment(s, full=True) for s in flight.get("Segments") or []],
        "on_sale": bool(fares),
        "fares": fares,
    }


async def is_domestic(code: str) -> bool:
    """IATA code of an Iranian airport on the site's domestic list (XXXALL city codes are international)."""
    return any(a["code"] == code for a in await domestic_airports())


async def _search(routes: list[tuple[str, str, dt.date]], pax: tuple[int, int, int]) -> dict[str, Any]:
    body = {
        "AdultCount": pax[0],
        "ChildCount": pax[1],
        "InfantCount": pax[2],
        "CabinClass": "All",  # the server ignores the cabin filter; _result applies it
        "Baggage": True,
        "IncludeFlightsWithHigherCapacity": False,
        "Routes": [{"OriginCode": o, "DestinationCode": d, "DepartureDate": day.isoformat()} for o, d, day in routes],
    }
    return obj(await fetch(f"{FLIGHT}/api/Flights", method="POST", json=body))


def _flight_id(fl: dict[str, Any]) -> str:
    """The flight Id; for a round-trip package also its return leg ids (many packages share the outbound Id)."""
    back = [str(leg.get("Id")) for s in (fl.get("Segments") or [])[1:] for leg in s.get("Legs") or []]
    return "+".join([str(fl.get("Id")), *back])


def _result(body: dict[str, Any], pax: tuple[int, int, int], f: dict[str, Any]) -> dict[str, Any]:
    meta = body.get("Meta") or {}
    route = (meta.get("RoutesInfo") or [{}])[0]
    flights = body.get("Flights") or []
    rows = []
    for fl in flights:
        fares = [p for p in fl.get("Prices") or [] if f["cabin"] == "any" or p.get("CabinClass") == CABINS[f["cabin"]]]
        if not fares:
            continue
        segments = fl.get("Segments") or []
        legs = [leg for s in segments for leg in s.get("Legs") or []]
        if f["airlines"] and not any(leg.get("AirlineCode") in f["airlines"] for leg in legs):
            continue
        if f["direct_only"] and any(len(s.get("Legs") or []) > 1 for s in segments):
            continue
        hhmm = (legs[0].get("DepartureTime") or "")[11:16] if legs else ""
        if (f["after"] and hhmm < f["after"]) or (f["before"] and hhmm > f["before"]):
            continue
        fare = min(fares, key=lambda p: _pax_fare(p, "ADL") or 0)
        rows.append(
            {
                "flight_id": _flight_id(fl),
                "price_toman": toman(_pax_fare(fare, "ADL")),
                "total_toman": party_total(fare, pax),
                "seats_left": fare.get("Capacity"),
                "cabin": fare.get("CabinClass"),
                "charter": fare.get("IsCharter"),
                "baggage": _baggage(fare.get("Baggage"), fare.get("BaggageType")),
                "baggage_to_confirm": bool(fare.get("NeedGetPrice")),
                "segments": [_segment(s) for s in segments],
            }
        )
    if f["sort"] == "earliest":
        rows.sort(key=lambda r: r["segments"][0]["departure"] or "")
    else:
        rows.sort(key=lambda r: r["price_toman"] or 0)
    out: dict[str, Any] = {
        "from": _place(route.get("Origin")),
        "to": _place(route.get("Destination")),
        "matching": len(rows),
        "not_on_sale": sum(1 for fl in flights if not fl.get("Prices")),
        "airlines": [{"code": a.get("IataCode"), "name": a.get("PersianTitle")} for a in meta.get("Airlines") or []],
        "flights": rows[: f["limit"]],
    }
    if not flights:
        unknown = [
            p.get("IataCode")
            for p in (route.get("Origin"), route.get("Destination"))
            if p and not p.get("PersianTitle")
        ]
        out["note"] = (
            f"Unknown airport code {', '.join(map(str, unknown))}: get codes from mb_find_place(mode='flight')."
            if unknown
            else "No flights for this route and date. Check the codes with mb_find_place, or other days with mb_flight_price_calendar."
        )
    return out


def party_total(fare: dict[str, Any], pax: tuple[int, int, int]) -> int | None:
    """Sum of the per-passenger fares of the party (adult, child, infant)."""
    total = 0
    for kind, count in zip(("ADL", "CHD", "INF"), pax, strict=True):
        if count:
            price = _pax_fare(fare, kind)
            if price is None:
                return None
            total += price * count
    return toman(total)


def _pax_fare(fare: dict[str, Any], kind: str) -> int | None:
    return next((p.get("TotalFare") for p in fare.get("PassengerFares") or [] if p.get("PaxType") == kind), None)


def _baggage(amount: Any, kind: Any) -> str | None:
    if kind in (None, "None") or not amount:
        return "none" if kind == "None" else None
    return f"{amount} {kind}"


def _place(p: dict[str, Any] | None) -> str | None:
    return f"{p.get('PersianTitle') or 'unknown code'} ({p.get('IataCode')})" if p else None


def _segment(s: dict[str, Any], full: bool = False) -> dict[str, Any]:
    legs = s.get("Legs") or []
    out: dict[str, Any] = {
        "from": legs[0].get("OriginCode") if legs else None,
        "to": legs[-1].get("DestinationCode") if legs else None,
        "departure": legs[0].get("DepartureTime") if legs else None,
        "arrival": legs[-1].get("ArrivalTime") if legs else None,
        "duration": s.get("TotalTime"),
        "stops": max(len(legs) - 1, 0),
        "flights": [f"{leg.get('AirlineCode')} {leg.get('FlightNumber')}" for leg in legs],
        "airline": ((legs[0].get("Airline") or {}).get("PersianTitle")) if legs else None,
        "aircraft": ((legs[0].get("AirCraft") or {}).get("ShortTitle")) if legs else None,
    }
    if full:
        out["legs"] = [
            {
                "flight": f"{leg.get('AirlineCode')} {leg.get('FlightNumber')}",
                "airline": (leg.get("Airline") or {}).get("EnglishTitle"),
                "operated_by": leg.get("OperatingAirlineCode"),
                "from": f"{leg.get('Origin')} - {leg.get('OriginAirport')} ({leg.get('OriginCode')})",
                "to": f"{leg.get('Destination')} - {leg.get('DestinationAirport')} ({leg.get('DestinationCode')})",
                "departure": leg.get("DepartureTime"),
                "arrival": leg.get("ArrivalTime"),
                "aircraft": (leg.get("AirCraft") or {}).get("ShortTitle"),
                "status": leg.get("FlightStatus"),
            }
            for leg in legs
        ]
        out["connection_time"] = s.get("ConnectionTime") if len(legs) > 1 else None
    return out


async def _fare(p: dict[str, Any]) -> dict[str, Any]:
    baggage, confirmed, booking_class = _baggage(p.get("Baggage"), p.get("BaggageType")), False, p.get("BookingClass")
    if p.get("NeedGetPrice") and p.get("ProposalId"):
        # The search value can be wrong for these fares (40 KG in search, 30 KG confirmed); 204 = keep it.
        info = await fetch(f"{FLIGHT}/api/Flights/GetBaggageInfo", {"proposalId": p["ProposalId"]}, method="POST")
        if info:
            baggage = _baggage(info.get("Baggage"), info.get("BaggageType")) if info.get("HasBaggage") else "none"
            booking_class, confirmed = info.get("BookingClass") or booking_class, True
    rules = p.get("FareRules")
    url = p.get("FareRulesUrl") or ""
    if urlsplit(url).hostname == urlsplit(CHARTER).hostname:  # international charter: rules text behind a URL
        try:
            rules = (await fetch(url, text=True)).strip()[:2000] or rules
        except ApiError:
            pass
    return {
        "cabin": p.get("CabinClass"),
        "booking_class": booking_class,
        "seats_left": p.get("Capacity"),
        "charter": p.get("IsCharter"),
        "official_airline_rate": p.get("HasOfficialRatePrice"),
        "price_toman": {
            {"ADL": "adult", "CHD": "child", "INF": "infant"}.get(x.get("PaxType"), x.get("PaxType")): toman(
                x.get("TotalFare")
            )
            for x in p.get("PassengerFares") or []
        },
        "baggage": baggage,
        "baggage_checked_with_airline": confirmed,
        "refund_penalties": [
            {
                "window": c.get("Title"),
                "penalty_pct": c.get("PercentAmount"),
                **({"penalty_text": c.get("PenaltyAmountTextEn")} if c.get("PercentAmount") is None else {}),
            }
            for c in p.get("CancellationTernEntities") or []
        ],
        "rules": rules,
        "notes": html_text(p.get("ExtraTerms"))[:1500] or None,
        "badges": [f"{o.get('Title')}: {o.get('Description')}" for o in p.get("FlightSpecialOffers") or []],
    }
