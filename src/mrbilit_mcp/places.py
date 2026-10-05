"""Place and company lookups: names -> the codes every search takes, airline/rail/bus companies.

Domestic airports and train stations exist only as JS data chunks of the site build. They are
fetched once per process with the build-meta recipe (home page -> entry chunk -> chunk name) and
fall back to `snapshot.json` (build 60f4c50c, 2026-09-28) when the recipe breaks. Bus and taxi
cities come from the live API, which is fresher than the bundled copies.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import BUS, FLIGHT, HOTEL, SITE, TRAIN, ApiError, fetch, obj
from .registry import tool

# Patterns from the build-meta recipe; minified names change per build, hence \w+.
CHUNK_RE = {
    "airports": r'loadLocalDomesticAirports\(\)\{return\(await \w+\(async\(\)=>\{const\{default:\w+\}=await import\("\./([\w-]+)\.js"\)',
    "stations": r'this\.trainStations=\(await \w+\(async\(\)=>\{const\{default:\w+\}=await import\("\./([\w-]+)\.js"\)',
}

_cache: dict[str, Any] = {}  # process-wide: bundled lists, city lists, company lists

Query = Annotated[
    str,
    Field(
        min_length=2,
        max_length=60,
        description="Place name in Persian or English, or a code, e.g. 'مشهد', 'tehran' or 'IST'.",
    ),
]


@tool("Find place codes")
async def mb_find_place(
    query: Query,
    mode: Annotated[
        Literal["flight", "train", "bus", "taxi", "hotel"],
        Field(description="Which search the code is for; each mode uses its own ids."),
    ],
    limit: Annotated[int, Field(ge=1, le=30, description="Max places per list.")] = 10,
) -> dict[str, Any]:
    """Turn a city, airport, station or hotel name into the code a search takes.

    flight: `airports` (Iranian airports, IATA code for domestic flights; Tehran domestic is THR,
    not IKA; prefer these to XXXALL codes for domestic routes) and `cities` (any country,
    `XXXALL` city code = all its airports, plus each airport).
    train: station ids (Tehran 1, Mashhad 191). bus / taxi: 8-digit city ids (Tehran 11320000 =
    all terminals; terminal ids such as 11321006 Tehran South). hotel: city slugs for
    mb_search_hotels / mb_city_hotels and hotel slugs for mb_hotel / mb_hotel_rooms.
    Next: mb_search_flights, mb_search_trains, mb_search_buses, mb_search_taxis or mb_search_hotels.
    """
    q = norm(query)
    if mode == "flight":
        cities = await fetch(f"{FLIGHT}/api/Airports", {"term": query.strip()})
        # The domestic list is Persian only: also take the airports of the cities an English query found.
        found = {a.get("Code") for c in cities or [] for a in c.get("Airports") or []}
        airports = [
            a
            for a in await domestic_airports()
            if a["code"] in found or q in norm(f"{a['code']} {a['title']} {a['subtitle']}")
        ]
        return {
            "airports": [{"code": a["code"], "city": a["title"], "airport": a["subtitle"]} for a in airports[:limit]],
            "cities": [
                {
                    "code": c.get("Code"),
                    "city": c.get("Title"),
                    "city_fa": c.get("PersianTitle"),
                    "country": c.get("CountryTitle"),
                    "airports": [{"code": a.get("Code"), "name": a.get("Title")} for a in c.get("Airports") or []],
                }
                for c in (cities or [])[:limit]
            ],
        }
    if mode == "train":
        hits = [s for s in await train_stations() if q in norm(f"{s['title']} {s.get('enTitle', '')}")]
        hits.sort(key=lambda s: norm(s["title"]) != q)  # exact name first
        return {
            "stations": [
                {
                    "id": s["code"],
                    "name": s["title"],
                    "slug": s.get("enTitle"),
                    "lat": s.get("latitude"),
                    "lon": s.get("longitude"),
                }
                for s in hits[:limit]
            ]
        }
    if mode in ("bus", "taxi"):
        hits = [
            c
            for c in await road_cities(mode)
            if q in norm(f"{c['title']} {c['persianTitle']} {c['englishTitle']} {c['code']}")
        ]
        hits.sort(key=lambda c: c["id"] % 10000 != 0)  # whole cities before single terminals
        return {
            "cities": [
                {
                    "id": c["id"],
                    "name": (c.get("title") or "").strip(),
                    "english": c.get("englishTitle"),
                    "slug": c.get("code"),
                    "province": c["province"],
                    "abroad": bool(c.get("isForeign") and not c.get("isDomestic")),
                }
                for c in hits[:limit]
            ]
        }
    d = obj(await fetch(f"{HOTEL}/api/hotels/list", {"searchTerm": query.strip()}))
    return {
        "cities": [
            {"id": c.get("id"), "name": c.get("title"), "slug": c.get("englishTitle"), "tags": c.get("tags") or []}
            for c in (d.get("cities") or [])[:limit]
        ],
        "hotels": [
            {
                "id": h.get("id"),
                "name": h.get("name"),
                "slug": h.get("englishName"),
                "city": h.get("cityName"),
                "city_slug": h.get("cityEnglishName"),
            }
            for h in (d.get("hotels") or [])[:limit]
        ],
    }


@tool("Transport companies")
async def mb_companies(
    mode: Annotated[
        Literal["flight", "train", "bus"],
        Field(description="Airlines, rail operators, or bus companies and taxi classes."),
    ],
    query: Annotated[
        str | None,
        Field(max_length=60, description="Optional name or code filter, e.g. 'ماهان', 'IR' or 'Iran Peyma'."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max companies.")] = 30,
) -> dict[str, Any]:
    """List airlines (IATA code), rail operators or bus companies (id) with Persian and English names.

    Use to name a code seen in results (airline 'W5', bus company 4) or to find an airline code
    for the `airlines` filter of mb_search_flights. Rail operators repeat under several ids; each
    row lists all of them. Bus ids 276/278/280 are the taxi classes VIP/Economy/Formal.
    """
    q = norm(query or "")
    if mode == "flight":
        rows = [
            {"code": a.get("IataCode"), "name": a.get("PersianTitle"), "english": a.get("EnglishTitle")}
            for a in await _cached("airlines", lambda: fetch(f"{FLIGHT}/api/Airlines/GetAirlines"))
            if a.get("IataCode")  # the first row is an empty placeholder
        ]
        if q:  # an exact code wins over substring matches ('IR' is also inside 'AIR')
            rows.sort(key=lambda r: norm(r["code"]) != q)
    elif mode == "train":
        names: dict[str, list[int]] = {}
        for c in await _cached("rail", lambda: fetch(f"{TRAIN}/api/Corporations")):
            names.setdefault((c.get("name") or "").strip(), []).append(c.get("id"))
        rows = [{"ids": ids, "name": name} for name, ids in names.items()]
    else:
        rows = [
            {"id": c.get("id"), "name": (c.get("title") or "").strip(), "english": c.get("englishTitle")}
            for c in await _cached("bus_companies", lambda: fetch(f"{BUS}/api/SuperCorporations"))
        ]
    if q:
        rows = [r for r in rows if q in norm(" ".join(str(v) for v in r.values() if v))]
    return {"count": len(rows), "companies": rows[:limit]}


async def domestic_airports() -> list[dict[str, Any]]:
    """Iranian airports of the site's domestic search: [{code, title (city), subtitle (airport)}]."""
    return await _bundled("airports")


async def train_stations() -> list[dict[str, Any]]:
    """[{code (station id), title, enTitle, latitude, longitude}]; 6 stations lack coordinates."""
    return await _bundled("stations")


async def road_cities(mode: str) -> list[dict[str, Any]]:
    """Live bus or taxi city list, flattened, with the province name on each city."""

    async def load() -> list[dict[str, Any]]:
        groups = await fetch(f"{BUS}/api/CityList/{'GetBusCityList' if mode == 'bus' else 'GetTaxiCityList'}")
        return [{**c, "province": g.get("provinceName")} for g in groups or [] for c in g.get("cities") or []]

    return await _cached(f"{mode}_cities", load)


async def _cached(key: str, load: Any) -> Any:
    if key not in _cache:
        _cache[key] = await load()
    return _cache[key]


async def _bundled(name: str) -> list[dict[str, Any]]:
    async def load() -> list[dict[str, Any]]:
        try:
            if "chunk_names" not in _cache:
                html = await fetch(f"{SITE}/", text=True)
                entry = re.search(r'<script[^>]+type="module"[^>]+src="/_nuxt/([\w-]+\.js)"', html)
                js = await fetch(f"{SITE}/_nuxt/{entry[1]}", text=True)  # type: ignore[index]
                _cache["chunk_names"] = {k: m[1] for k, p in CHUNK_RE.items() if (m := re.search(p, js))}
            chunk = _cache["chunk_names"][name]
            return parse_chunk(await fetch(f"{SITE}/_nuxt/{chunk}.js", text=True))
        except (ApiError, TypeError, KeyError, AttributeError, ValueError):
            # ponytail: snapshot of build 60f4c50c; refresh it when the recipe breaks on a redesign
            return json.loads(files("mrbilit_mcp").joinpath("snapshot.json").read_text(encoding="utf-8"))[name]

    return await _cached(f"chunk:{name}", load)


def parse_chunk(js: str) -> list[dict[str, Any]]:
    """Bundled location chunk (`const t=[{code:"ABD",...}]`) -> list of dicts."""
    lit = re.search(r"const \w+=(\[.*?\])(?:;export|,\w+=)", js, re.S)[1]  # type: ignore[index]
    lit = re.sub(r"([{,])([A-Za-z_$][\w$]*):", r'\1"\2":', lit).replace("!0", "true").replace("!1", "false")
    return json.loads(lit, parse_float=lambda s: int(float(s)) if re.fullmatch(r"\d+e\d+", s) else float(s))


def norm(text: str) -> str:
    """Fold Arabic/Persian letter variants, half-spaces and case so names match loosely."""
    text = text.replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک").replace("آ", "ا").lower()
    # ponytail: one known English spelling split (bus 'Esfahan', train/hotel 'isfahan'); add others when seen
    return " ".join(text.replace("isfahan", "esfahan").split())
