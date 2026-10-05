"""Content tools: site notices for a route, help-center search, route guides and magazine.

Strapi rows carry admin users in created_by/updated_by (emails, password hashes): every tool
here copies only the fields it needs, so those two never leave this module.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import CONTENT, SITE, fetch, gql
from .places import norm
from .registry import tool

Service = Literal["flight", "train", "bus", "hotel"]  # route guides exist for these only
RouteCode = Annotated[
    str | None,
    Field(
        pattern=r"^[\w()-]{2,40}(/[\w()-]{2,60})?$",
        description="Flight: IATA code ('THR'); train, bus, taxi: English city slug ('tehran'); hotel destination: city slug or 'city/hotel' ('mashhad/enghelab').",
    ),
]
PAGE = {"flight": "/flights", "train": "/trains", "bus": "/buses", "taxi": "/taxi", "hotel": "/hotel"}
SUPPORT = 'query { TicketingSection(limit: -1, sort: ["Sort"]) { Title Key Faqs Tutorials { TicketingTutorials_id { Title Tutorial } } } }'


@tool("Site notices")
async def mb_notices(
    service: Annotated[
        Literal["any", "flight", "train", "bus", "taxi", "hotel"],
        Field(description="Which pages; any = every active notice."),
    ] = "any",
    origin: RouteCode = None,
    destination: RouteCode = None,
) -> dict[str, Any]:
    """Notices MrBilit currently shows on its pages (disruptions, rule changes, payment options), for a service or route.

    Route codes build the site path the notice rules match against: /flights/THR-MHD,
    /trains/tehran-mashhad, /hotel/mashhad. search_mode limits a flight notice to domestic or
    international searches; site_wide_active counts every active notice, not only these. Use
    before recommending a trip, alongside the search tools.
    """
    rows = await fetch(f"{CONTENT}/messages", {"web": "true"})  # web=false rows are old, switched-off texts
    path = None
    if service != "any":
        path = PAGE[service]
        if service == "hotel" and destination:
            path += f"/{destination}"
        elif origin and destination:
            o, d = (origin.upper(), destination.upper()) if service == "flight" else (origin, destination)
            path += f"/{o}-{d}"
    notices = []
    for m in rows or []:
        pattern = m.get("urlPattern") or ""
        if path is not None and pattern:
            try:
                if not re.search(pattern, path):
                    continue
            except re.error:
                pass  # a JS-only regex: keep the notice and let the model read applies_to
        notices.append(
            {
                "text": (m.get("text") or "").strip(),
                "applies_to": pattern or None,
                "search_mode": m.get("searchMode"),
                "link": m.get("url"),
            }
        )
    return {"path": path, "site_wide_active": len(rows or []), "notices": notices}


@tool("Help center search")
async def mb_help(
    query: Annotated[
        str,
        Field(
            min_length=2,
            max_length=100,
            description="Question or keywords in Persian, e.g. 'استرداد بلیط قطار' or 'رزرو خودکار'.",
        ),
    ],
    source: Annotated[
        Literal["all", "terms", "faq", "support"],
        Field(
            description="terms = official rules (refunds, baggage, ID), faq = general FAQ, support = help-center guides and request forms."
        ),
    ] = "all",
    limit: Annotated[int, Field(ge=1, le=10, description="Max passages.")] = 5,
) -> dict[str, Any]:
    """Search MrBilit's official rules, FAQ and help-center guides; returns the best matching passages.

    Use for refund and cancellation rules, ID and baggage rules, how auto-reserve works, how to
    request a change, etc. Passages are plain text with the section they come from. Rules of
    one fare are in mb_flight_fare_details / mb_search_trains / mb_search_buses instead.
    """
    passages: list[dict[str, str]] = []
    if source in ("all", "faq"):
        for row in await fetch(f"{CONTENT}/faqs", {"whitelabel": "mrbilit"}) or []:
            for item in row.get("FaqItem") or []:
                passages.append(
                    {"source": "faq", "section": item.get("question") or "", "text": html_text(item.get("answer"))}
                )
    if source in ("all", "terms"):
        for row in await fetch(f"{CONTENT}/terms-and-conditions", {"whitelabel": ""}) or []:
            section = ""
            for line in html_text(
                re.sub(r"<h2[^>]*>(.*?)</h2>", r"<h2>## \1</h2>", row.get("body") or "")
            ).splitlines():
                if line.startswith("## "):
                    section = line[3:]
                else:
                    passages.append({"source": "terms", "section": section, "text": line})
    if source in ("all", "support"):
        data = await gql(SUPPORT)
        for s in data.get("TicketingSection") or []:
            for t in s.get("Tutorials") or []:
                tut = t.get("TicketingTutorials_id") or {}
                passages.append(
                    {
                        "source": "support",
                        "section": f"{s.get('Title')}: {(tut.get('Title') or '').strip()}",
                        "text": html_text(tut.get("Tutorial")),
                    }
                )
            for f in s.get("Faqs") or []:
                passages.append(
                    {
                        "source": "support",
                        "section": f"{s.get('Title')}: {(f.get('Title') or '').strip()}",
                        "text": html_text(f.get("Description")),
                    }
                )
    words = [w for w in norm(query).split() if len(w) > 1]
    scored = []
    for p in passages:
        hay = norm(f"{p['section']} {p['text']}")
        score = sum(w in hay for w in words) + sum(w in norm(p["section"]) for w in words)  # title hits count double
        if score:
            scored.append((score, p))
    scored.sort(key=lambda sp: -sp[0])
    return {
        "matches": len(scored),
        "results": [{**p, "text": p["text"][:1200]} for _, p in scored[:limit]],
    }


@tool("Travel guide")
async def mb_travel_guide(
    service: Annotated[Service | None, Field(description="Route guide for this service; needs destination.")] = None,
    origin: RouteCode = None,
    destination: RouteCode = None,
    query: Annotated[
        str | None,
        Field(min_length=2, max_length=100, description="Magazine search, e.g. 'کیش' or 'جاهای دیدنی مشهد'."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=10, description="Max magazine posts.")] = 5,
) -> dict[str, Any]:
    """MrBilit's guide for a route or city (summary, FAQ, article) and matching travel-magazine posts.

    Route guide: service + origin + destination (flight IATA codes, train/bus English slugs such
    as 'tehran'; hotel: destination city slug only). Magazine: query. Editorial text: prices or
    rules quoted in it can be out of date; use the search tools for live prices.
    """
    if not (service and destination) and not query:
        raise ToolError("Give service + destination for a route guide, or query for magazine posts.")
    out: dict[str, Any] = {}
    if service and destination:
        o = "" if service == "hotel" else (origin or "")
        d = destination
        if service == "flight":
            o, d = o.upper(), d.upper()
        body = await fetch(
            f"{CONTENT}/search-result-contents/full-content", {"service": service, "origin": o, "destination": d}
        )
        c = (body or {}).get("content") or {}
        # A missing key matches any row, so only trust a row that echoes what was asked.
        if c and (c.get("service"), c.get("origin") or "", c.get("destination")) == (service, o, d):
            out["guide"] = {
                "summary": c.get("description"),
                "faq": [
                    {"q": f.get("question"), "a": html_text(f.get("answer"))[:500]} for f in (c.get("faqs") or [])[:10]
                ],
                "article": html_text(c.get("content"))[:2500],
            }
        else:
            out["guide"] = None
    if query:
        posts = await fetch(
            f"{SITE}/mag/wp-json/wp/v2/posts",
            {"search": query, "per_page": limit, "_fields": "id,date,title,link,excerpt"},
        )
        out["magazine"] = [
            {
                "title": unescape((p.get("title") or {}).get("rendered") or ""),
                "date": (p.get("date") or "")[:10],
                "url": p.get("link"),
                "summary": html_text((p.get("excerpt") or {}).get("rendered"))[:400],
            }
            for p in posts or []
        ]
    return out


class _Text(HTMLParser):
    BLOCKS = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_text(html: str | None) -> str:
    """HTML to plain text, one line per block, without soft hyphens or empty lines."""
    parser = _Text()
    parser.feed(html or "")
    lines = (" ".join(line.replace("\xad", "").split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)
