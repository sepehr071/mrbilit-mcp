import json

import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio


async def test_mb_notices_matches_route_path(client, api):
    api["content.mrbilit.ir/messages"] = fixture("messages.json")
    data = (
        await client.call_tool("mb_notices", {"service": "hotel", "destination": "mashhad/enghelab"})
    ).structured_content
    assert data["path"] == "/hotel/mashhad/enghelab"
    assert data["notices"] == [
        {
            "text": "امکان پرداخت قسطی برای این هتل فراهم است.",
            "applies_to": "^/hotel/[^/]+/[^/?]+.*",
            "search_mode": None,
            "link": None,
        }
    ]
    assert api.calls[0].url.params["web"] == "true"  # web=false rows are switched off
    data = (
        await client.call_tool("mb_notices", {"service": "flight", "origin": "thr", "destination": "mhd"})
    ).structured_content
    assert data["path"] == "/flights/THR-MHD" and data["notices"] == []
    assert data["site_wide_active"] == len(fixture("messages.json"))  # every active notice, not only the route's


async def test_mb_notices_never_leaks_admin_fields(client, api):
    api["content.mrbilit.ir/messages"] = fixture("messages.json")
    result = await client.call_tool("mb_notices", {})
    text = json.dumps(result.structured_content)
    assert "redacted" not in text and "created_by" not in text and len(result.structured_content["notices"]) == 1


async def test_mb_help(client, api):
    api["content.mrbilit.ir/faqs"] = fixture("faqs.json")
    api["content.mrbilit.ir/terms-and-conditions"] = fixture("terms.json")
    api["directus.mrbilit.ir/graphql"] = fixture("support_help.json")
    data = (await client.call_tool("mb_help", {"query": "رزرو خودکار", "limit": 3})).structured_content
    assert data["results"][0]["source"] == "support"
    assert data["results"][0]["section"].startswith("رزرو خودکار: ")
    data = (
        await client.call_tool("mb_help", {"query": "کنسلی بلیط قطار", "source": "terms", "limit": 1})
    ).structured_content
    top = data["results"][0]
    assert top["source"] == "terms" and top["section"] == "شرایط و مقررات بلیط قطار" and "جریمه" in top["text"]
    assert "<" not in top["text"]
    assert [r.url.path for r in api.calls].count("/graphql") == 1  # source=terms skips the help center
    assert "query" in body(api.calls[2])


async def test_mb_help_faq_only(client, api):
    api["content.mrbilit.ir/faqs"] = fixture("faqs.json")
    data = (await client.call_tool("mb_help", {"query": "خرید بلیط قطار", "source": "faq"})).structured_content
    assert data["results"][0] == {"source": "faq", "section": "خرید بلیط قطار", "text": data["results"][0]["text"]}
    assert api.calls[0].url.params["whitelabel"] == "mrbilit"


async def test_mb_travel_guide(client, api):
    api["content.mrbilit.ir/search-result-contents/full-content"] = fixture("route_content.json")
    api["mrbilit.com/mag/wp-json/wp/v2/posts"] = fixture("mag_posts.json")
    args = {"service": "flight", "origin": "thr", "destination": "mhd", "query": "مشهد", "limit": 2}
    data = (await client.call_tool("mb_travel_guide", args)).structured_content
    guide = data["guide"]
    assert guide["summary"].startswith("خرید بلیط رفت و برگشت هواپیما تهران مشهد")
    assert guide["faq"][0]["q"] == "قیمت بلیط هواپیما تهران مشهد چقدر است؟" and len(guide["faq"]) == 3
    assert guide["article"] and "<" not in guide["article"]
    assert data["magazine"][1] == {
        "title": "راهنمای حمل خودرو با قطار ۱۴۰۵",
        "date": "2026-07-19",
        "url": "https://mrbilit.com/mag/guide-to-transporting-car-by-train/",
        "summary": data["magazine"][1]["summary"],
    }
    assert "updated_by" not in json.dumps(data)
    sent = api.calls[0].url.params
    assert (sent["service"], sent["origin"], sent["destination"]) == ("flight", "THR", "MHD")


async def test_mb_travel_guide_rejects_mismatched_row(client, api):
    # a missing or unknown key can match another route's row; only an echo of the request counts
    api["content.mrbilit.ir/search-result-contents/full-content"] = fixture("route_content.json")
    data = (
        await client.call_tool("mb_travel_guide", {"service": "flight", "origin": "KIH", "destination": "MHD"})
    ).structured_content
    assert data == {"guide": None}


async def test_mb_travel_guide_needs_something(client, api):
    result = await client.call_tool("mb_travel_guide", {"service": "train"})
    assert result.is_error and not api.calls
    result = await client.call_tool("mb_travel_guide", {"destination": "mashhad"})  # no service: nothing to look up
    assert result.is_error and "service" in result.content[0].text and not api.calls
    result = await client.call_tool(
        "mb_travel_guide", {"service": "taxi", "origin": "tehran", "destination": "rasht"}
    )  # route guides have no taxi rows
    assert result.is_error and not api.calls
