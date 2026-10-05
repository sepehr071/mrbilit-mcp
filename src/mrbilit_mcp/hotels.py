"""Hotel tools: city search with stay prices, hotel detail and reviews, rooms, calendar, static city list."""

from __future__ import annotations

import datetime as dt
import math
from typing import Annotated, Any, Literal
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .content import html_text
from .http import HOTEL, ApiError, fetch, not_past, obj, toman
from .registry import tool

CitySlug = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z][\w-]*$",
        max_length=60,
        description="City slug from mb_find_place(mode='hotel'), e.g. 'mashhad' or 'kish'.",
    ),
]
REF = r"[A-Za-z0-9][\w-]*/[A-Za-z0-9][\w-]*"  # slug characters only: no '..' path segments
HotelRef = Annotated[
    str,
    Field(
        pattern=rf"^{REF}$",
        max_length=120,
        description="Hotel as 'city_slug/hotel_slug' from mb_search_hotels, mb_city_hotels or mb_find_place, e.g. 'mashhad/enghelab'.",
    ),
]
CheckIn = Annotated[dt.date, Field(description="Check-in date YYYY-MM-DD, e.g. '2026-10-20'.")]
CheckOut = Annotated[dt.date, Field(description="Check-out date YYYY-MM-DD, e.g. '2026-10-23' (1 to 30 nights).")]
HotelType = Literal["any", "hotel", "hotel_apartment", "traditional", "eco_lodge", "guest_house", "boutique", "other"]
TYPE_IDS = {
    "hotel": {1, 2, 3, 4, 5},
    "hotel_apartment": {9, 15, 18},
    "traditional": {12, 24},
    "eco_lodge": {10},
    "guest_house": {11, 25, 27},
    "boutique": {23},
    "other": {13, 14},
}
STARRED = TYPE_IDS["hotel"] | {15, 18}
STATIC_PAGE = 90  # the static list is cached per city and page, so always ask for the default page size


@tool("Search hotels")
async def mb_search_hotels(
    city: CitySlug,
    check_in: CheckIn,
    check_out: CheckOut,
    sort: Annotated[
        Literal["rating", "cheapest", "most_expensive"], Field(description="Order; rating is the site's default.")
    ] = "rating",
    min_stars: Annotated[
        int | None, Field(ge=1, le=5, description="Only hotels with at least this many stars, e.g. 4.")
    ] = None,
    hotel_type: Annotated[HotelType, Field(description="Kind of stay.")] = "any",
    max_price_per_night_toman: Annotated[
        int | None, Field(ge=0, description="Highest price per night for one room in Toman, e.g. 3000000.")
    ] = None,
    refundable_only: Annotated[bool, Field(description="Only refundable stays.")] = False,
    limit: Annotated[int, Field(ge=1, le=50, description="Max hotels.")] = 20,
) -> dict[str, Any]:
    """Hotels and stays with a free room in a city for the dates, with the cheapest room price for the whole stay.

    price_toman is the cheapest room for all nights (one room, after discount); per_night_toman
    divides it by the nights; cheapest_room_sleeps is how many that room sleeps (often 1).
    Guest count does not change prices: pick a room that fits with mb_hotel_rooms(guests=...). Sold-out hotels are not listed (mb_city_hotels lists every hotel). Next:
    mb_hotel (reviews, location), mb_hotel_rooms (every room and exact price),
    mb_hotel_calendar (cheapest nights).
    """
    nights = _nights(check_in, check_out)
    body = await fetch(
        f"{HOTEL}/api/hotels/v2/hotels/search",
        {
            "cityName": city,
            "from": check_in.isoformat(),
            "to": check_out.isoformat(),
            "adultCount": 1,
            "childrenCount": 0,
            "roomCount": 1,  # does not change prices: they are always for one room
            "hasHotelInName": "true",
        },
    )
    hotels = obj(body).get("hotels") or []
    rows = []
    for h in hotels:
        price = toman(h.get("payable")) or 0
        if (
            (min_stars and (_stars(h) or 0) < min_stars)
            or (hotel_type != "any" and h.get("hotelTypeId") not in TYPE_IDS[hotel_type])
            or (max_price_per_night_toman is not None and price / nights > max_price_per_night_toman)
            or (refundable_only and not h.get("refundable"))
        ):
            continue
        rows.append(
            {
                **_card(h),
                "price_toman": price,
                "per_night_toman": round(price / nights),
                "before_discount_toman": toman(h.get("totalPrice")),
                "meals": (h.get("serviceLevel") or {}).get("title"),
                "cheapest_room_sleeps": h.get("minRoomPriceCapacity"),
                "refundable": h.get("refundable"),
                "mrbilit_offer": h.get("isMrBilitOffer"),
            }
        )
    if sort == "rating":
        rows.sort(key=lambda r: (r["rating"] or 0, r["reviews"] or 0), reverse=True)
    else:
        rows.sort(key=lambda r: r["price_toman"], reverse=sort == "most_expensive")
    out: dict[str, Any] = {
        "nights": nights,
        "available_hotels": len(hotels),
        "matching": len(rows),
        "hotels": rows[:limit],
    }
    if not hotels:
        out["note"] = "Nothing available. Check the city slug with mb_find_place(mode='hotel') or try other dates."
    return out


@tool("Hotel details")
async def mb_hotel(
    hotel: Annotated[
        str,
        Field(
            pattern=rf"^(\d{{1,9}}|{REF})$",
            max_length=120,
            description="Hotel as 'city_slug/hotel_slug' (e.g. 'mashhad/enghelab') or its numeric id (e.g. '8778').",
        ),
    ],
    reviews: Annotated[int, Field(ge=0, le=15, description="How many of the latest guest reviews to include.")] = 5,
) -> dict[str, Any]:
    """One hotel: stars, guest rating (0-5) and latest reviews, location, landmarks, check-in rules, amenities, FAQ.

    The site shows ratings x2 out of 10. landmarks: distance in km (drive minutes). For room
    prices on dates call mb_hotel_rooms with `hotel` (null after a numeric id: get the
    'city/hotel' ref from mb_find_place(mode='hotel') with the name); for the cheapest nights
    mb_hotel_calendar.
    """
    try:
        body = await fetch(f"{HOTEL}/api/hotel/{_path(hotel)}/static")
    except ApiError as e:
        if e.status == 404:
            raise ApiError(
                f"Unknown hotel '{hotel}'. Get the slugs from mb_find_place(mode='hotel') or mb_search_hotels.", 404
            ) from e
        raise
    h = obj(body).get("hotel")
    if not h:
        raise ToolError(f"Unknown hotel '{hotel}'. Get the slugs from mb_find_place(mode='hotel') or mb_search_hotels.")
    comments = h.get("comments") or []
    title = h.get("title") or ""
    return {
        "id": h.get("id"),
        "name": f"{h.get('hotelTypeName') or ''} {title}".strip(),
        # The detail has no English city slug (cityEnglishName is Persian), so only a slug input gives the ref.
        "hotel": hotel if "/" in hotel else None,
        "slug": h.get("englishTitle"),
        "stars": h.get("stars"),
        "rating": h.get("rating") or None,
        "city": h.get("cityName"),
        "address": h.get("address"),
        "lat": h.get("latitude"),
        "lon": h.get("longitude"),
        "check_in_from": h.get("checkinHour"),
        "check_out_by": h.get("checkoutHour"),
        "free_airport_transfer": h.get("hasFreeTransfer"),
        "amenities": [a.get("title") for a in h.get("amenities") or []],
        "landmarks": [
            {
                "name": p.get("title"),
                "km": round(p["distance"] / 1000, 1) if p.get("distance") is not None else None,
                "minutes": round(p["durationInMinutes"]) if p.get("durationInMinutes") is not None else None,
            }
            for p in (h.get("popularPlaces") or [])[:10]
        ],
        "description": html_text(h.get("description") or h.get("shortDescription"))[:1500],
        "rules": (h.get("termsAndConditions") or "").strip()[:1500],
        # The other FAQ rows are the same site-wide questions (voucher, pets, invoice) on every hotel.
        "faq": [
            {"q": f.get("question"), "a": f.get("answer")}
            for f in h.get("hotelFaqs") or []
            if title and title in (f.get("question") or "")
        ][:6],
        "reviews_available": len(comments),
        "reviews": [
            {
                "date": (c.get("postedOn") or "")[:10],
                "author": " ".join((c.get("customer") or "").split()),
                "rating": c.get("rating"),
                "text": (c.get("comment") or "").strip(),
                "booked_here": c.get("reserved"),
                "scores": {
                    (r.get("ratingFactor") or {}).get("name"): r.get("rating") for r in c.get("commentRatings") or []
                },
            }
            for c in comments[:reviews]
        ],
    }


@tool("Hotel rooms and prices")
async def mb_hotel_rooms(
    hotel: HotelRef,
    check_in: CheckIn,
    check_out: CheckOut,
    guests: Annotated[
        int | None,
        Field(ge=1, le=12, description="Only rooms that sleep at least this many (with extra beds), e.g. 3."),
    ] = None,
) -> dict[str, Any]:
    """Every room of a hotel with its exact price for the dates: whole stay, per night, rooms left, meals.

    price_toman is the whole stay for one room after discount (prices do not depend on the
    guest count; choose rooms whose sleeps fits, and add up several rooms for a group).
    rooms_left 0 = full. cancellation is the hotel's refund rule. Empty rooms list = nothing
    free for these dates (try mb_hotel_calendar).
    """
    nights = _nights(check_in, check_out)
    try:
        body = await fetch(
            f"{HOTEL}/api/hotel/{_path(hotel)}/rooms",
            {
                "from": check_in.isoformat(),
                "to": check_out.isoformat(),
                "adultCount": 1,
                "childrenCount": 0,
                "roomCount": 1,
            },
        )
    except ApiError as e:
        if e.status == 500:  # an unknown slug answers 500, not 404
            raise ApiError(
                f"Could not load rooms of '{hotel}'. Check the slugs with mb_find_place(mode='hotel').", 500
            ) from e
        raise
    rows = []
    for r in obj(body).get("rooms") or []:
        sleeps = (r.get("capacity") or 0) + (r.get("extraBedCapacity") or 0)
        if guests and sleeps < guests:
            continue
        for p in r.get("prices") or []:
            nightly = [toman(n.get("salePrice")) for n in p.get("breakDownRoomPrices") or []]
            rows.append(
                {
                    "room": r.get("name"),
                    "sleeps": r.get("capacity"),
                    "extra_beds": r.get("extraBedCapacity") or 0,
                    "price_toman": toman(p.get("payable")),
                    "before_discount_toman": toman(p.get("totalPrice")),
                    "per_night_toman": nightly[0] if len(set(nightly)) == 1 else nightly,
                    "extra_bed_toman": toman(p.get("extraBedPrice")) or None,
                    "rooms_left": p.get("availableRoomCount"),
                    "meals": " ".join(p.get("roomServiceTitle") or []) or None,
                    "min_nights": p.get("minNights"),
                    "description": (r.get("description") or "").strip() or None,
                }
            )
    rows.sort(key=lambda x: x["price_toman"] or 0)
    return {"nights": nights, "cancellation": obj(body).get("cancellationRules"), "rooms": rows}


@tool("Hotel price calendar")
async def mb_hotel_calendar(hotel: HotelRef) -> dict[str, Any]:
    """Cheapest one-night room price per night from today, up to about 48 days (often fewer).

    Use to find the cheapest or the next free nights, then mb_hotel_rooms for exact prices.
    Nights missing inside the range have no free room; nights after the last one listed are
    not loaded yet (not the same as sold out).
    """
    rows = await fetch(f"{HOTEL}/api/hotel/{_path(hotel)}/calendar")
    days = sorted(
        (
            {"date": (r.get("date") or "")[:10], "price_toman": toman(r.get("price"))}
            for r in rows or []
            if r.get("price")
        ),
        key=lambda r: r["date"],
    )
    out: dict[str, Any] = {"nights": days, "cheapest": min(days, key=lambda r: r["price_toman"]) if days else None}
    if not days:
        out["note"] = "No priced nights. Check the hotel ref with mb_find_place(mode='hotel'), or the hotel is full."
    return out


@tool("All hotels of a city")
async def mb_city_hotels(
    city: CitySlug,
    stars: Annotated[int | None, Field(ge=1, le=5, description="Only this star count, e.g. 4.")] = None,
    hotel_type: Annotated[HotelType, Field(description="Kind of stay.")] = "any",
    name: Annotated[
        str | None, Field(max_length=60, description="Only names containing this text, e.g. 'درویشی'.")
    ] = None,
    page: Annotated[int, Field(ge=1, le=50, description="Page of 90 hotels (the site's ranking order).")] = 1,
    limit: Annotated[int, Field(ge=1, le=90, description="Max hotels returned from the page.")] = 40,
) -> dict[str, Any]:
    """Every listed hotel of a city, sold out ones included, with stars, rating and address; no prices.

    Use to browse or find a hotel by stars or type regardless of dates; filters apply to one
    page of 90 hotels at a time, so check `pages`. Prices: mb_search_hotels (dates) or
    mb_hotel_calendar (one hotel).
    """
    # The server caches this list on cityName + pageNumber only and then ignores stars/tag/pageSize:
    # send neither, filter here.
    body = obj(await fetch(f"{HOTEL}/api/hotels/searchstatics", {"cityName": city, "pageNumber": page}))
    hotels = body.get("hotels") or []
    total = body.get("totalCountHotels") or 0
    q = (name or "").strip()
    rows = [
        _card(h)
        for h in hotels
        if (not stars or _stars(h) == stars)
        and (hotel_type == "any" or h.get("hotelTypeId") in TYPE_IDS[hotel_type])
        and (not q or q in (h.get("name") or ""))
    ]
    out: dict[str, Any] = {
        "total": total,
        "page": page,
        "pages": math.ceil(total / STATIC_PAGE),
        "matching": len(rows),
        "hotels": rows[:limit],
    }
    if len(hotels) < STATIC_PAGE and page * STATIC_PAGE < total:
        out["note"] = "The server returned a short page from its cache; call again in a few minutes for the full page."
    if not total:
        out["note"] = "Unknown city slug. Get it from mb_find_place(mode='hotel')."
    return out


def _card(h: dict[str, Any]) -> dict[str, Any]:
    """Compact hotel row from the search and static lists."""
    return {
        "id": h.get("id"),
        "name": h.get("name"),
        "hotel": f"{h.get('englishCityName')}/{h.get('englishName')}",  # pass to mb_hotel / mb_hotel_rooms
        "type": h.get("hotelTypeTitle"),
        "stars": _stars(h),
        "rating": h.get("rating") or None,
        "reviews": h.get("numberOfComments"),
        "address": h.get("shortAddress") or h.get("hotelAddress"),
    }


def _stars(h: dict[str, Any]) -> int | None:
    """Stars only mean something for star-rated types (hotels, 2-3 star hotel apartments)."""
    return h.get("hotelTypeStars") if h.get("hotelTypeId") in STARRED else None


def _nights(check_in: dt.date, check_out: dt.date) -> int:
    not_past(check_in, "check_in")
    nights = (check_out - check_in).days
    if not 1 <= nights <= 30:
        raise ToolError("check_out must be 1 to 30 nights after check_in.")
    return nights


def _path(hotel: str) -> str:
    return "/".join(quote(part, safe="") for part in hotel.split("/"))
