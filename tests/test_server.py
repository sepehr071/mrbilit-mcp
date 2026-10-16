import json
import time

import httpx
import pytest
from conftest import fixture
from mcp import Client

from mrbilit_mcp import places
from mrbilit_mcp.server import mcp

pytestmark = pytest.mark.anyio

FLIGHTS = "flight.atighgasht.com/api/Flights"
INTL = {"origin": "IKA", "destination": "IST", "date": "2026-10-20", "return_date": "2026-10-23"}


async def test_wire_format_is_one_compact_text_block(api):
    api["hotel.mrbilit.ir/api/hotel/mashhad/enghelab/calendar"] = fixture("hotel_calendar.json")
    async with Client(mcp, raise_exceptions=True) as c:  # the real client, not the conftest wrapper
        tools = (await c.list_tools()).tools
        assert tools and all(t.output_schema is None for t in tools)
        assert all("title" not in p for t in tools for p in t.input_schema.get("properties", {}).values())
        result = await c.call_tool("mb_hotel_calendar", {"hotel": "mashhad/enghelab"})
    assert len(result.content) == 1 and result.content[0].type == "text"
    assert result.structured_content is None
    text = result.content[0].text
    assert text == json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))


async def test_cache_expires_after_ttl(api, monkeypatch):
    api["flight.atighgasht.com/api/Airlines/GetAirlines"] = fixture("airlines.json")
    for _ in range(2):
        await places._cached("airlines", lambda: places.fetch("https://flight.atighgasht.com/api/Airlines/GetAirlines"))
    assert len(api.calls) == 1
    t, v = places._cache["airlines"]
    places._cache["airlines"] = (t - places.CACHE_TTL - 1, v)
    await places._cached("airlines", lambda: places.fetch("https://flight.atighgasht.com/api/Airlines/GetAirlines"))
    assert len(api.calls) == 2 and time.monotonic() - places._cache["airlines"][0] < 5


async def test_hotel_review_text_is_capped(client, api):
    page = fixture("hotel_detail.json")
    page["hotel"]["comments"][0]["comment"] = "x" * 1000
    api["hotel.mrbilit.ir/api/hotel/mashhad/enghelab/static"] = page
    data = (await client.call_tool("mb_hotel", {"hotel": "mashhad/enghelab", "reviews": 1})).structured_content
    assert data["reviews"][0]["text"] == "x" * 400 + "…[cut]"
    data = (
        await client.call_tool("mb_hotel", {"hotel": "mashhad/enghelab", "reviews": 1, "full_text": True})
    ).structured_content
    assert data["reviews"][0]["text"] == "x" * 1000


async def test_full_text_restores_clipped_texts(client, api):
    places._cache["help:faq"] = (time.monotonic(), [{"source": "faq", "section": "قطار", "text": "y" * 3000}])
    args = {"query": "قطار", "source": "faq"}
    assert len((await client.call_tool("mb_help", args)).structured_content["results"][0]["text"]) == 1200
    full = (await client.call_tool("mb_help", {**args, "full_text": True})).structured_content
    assert len(full["results"][0]["text"]) == 3000

    page = fixture("route_content.json")
    page["content"]["content"] = "<p>" + "z" * 5000 + "</p>"
    api["content.mrbilit.ir/search-result-contents/full-content"] = page
    args = {"service": "flight", "origin": "THR", "destination": "MHD"}
    assert len((await client.call_tool("mb_travel_guide", args)).structured_content["guide"]["article"]) == 2500
    full = (await client.call_tool("mb_travel_guide", {**args, "full_text": True})).structured_content
    assert len(full["guide"]["article"]) == 5000

    flight = fixture("flight_search_intl_rt.json")
    flight["Flights"][0]["Prices"][0]["FareRulesUrl"] = "https://charter.mrbilit.com/api/Flights/FareRules/1"
    api[FLIGHTS] = flight
    api["charter.mrbilit.com/api/Flights/FareRules/1"] = "r" * 3000
    args = {**INTL, "flight_id": "21894997+21924108"}
    assert len((await client.call_tool("mb_flight_fare_details", args)).structured_content["fares"][0]["rules"]) == 2000
    full = (await client.call_tool("mb_flight_fare_details", {**args, "full_text": True})).structured_content
    assert len(full["fares"][0]["rules"]) == 3000


async def test_rules_url_must_be_https_and_is_not_redirected(client, api):
    page = fixture("flight_search_intl_rt.json")
    page["Flights"][0]["Prices"][0]["FareRulesUrl"] = "http://charter.mrbilit.com/api/Flights/FareRules/1"
    api[FLIGHTS] = page
    await client.call_tool("mb_flight_fare_details", {**INTL, "flight_id": "21894997+21924108"})
    assert [r.url.host for r in api.calls] == ["flight.atighgasht.com"]  # plain http: not fetched
    page["Flights"][0]["Prices"][0]["FareRulesUrl"] = "https://charter.mrbilit.com/api/Flights/FareRules/1"
    api["charter.mrbilit.com/api/Flights/FareRules/1"] = lambda r: httpx.Response(
        302, headers={"location": "https://example.com/x"}
    )
    data = (
        await client.call_tool("mb_flight_fare_details", {**INTL, "flight_id": "21894997+21924108"})
    ).structured_content
    assert not any(r.url.host == "example.com" for r in api.calls)
    assert data["fares"]


async def test_notice_pattern_longer_than_200_is_not_run_as_regex(client, api):
    rows = fixture("messages.json")
    rows[0]["urlPattern"] = "a" * 201
    api["content.mrbilit.ir/messages"] = rows
    result = await client.call_tool("mb_notices", {"service": "flight", "origin": "thr", "destination": "mhd"})
    assert not result.is_error
