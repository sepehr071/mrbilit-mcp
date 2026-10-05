import httpx
import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio

STAY = {"check_in": "2026-10-20", "check_out": "2026-10-23"}


async def test_mb_search_hotels(client, api):
    api["hotel.mrbilit.ir/api/hotels/v2/hotels/search"] = fixture("hotel_search.json")
    data = (await client.call_tool("mb_search_hotels", {"city": "mashhad", **STAY})).structured_content
    assert (data["nights"], data["available_hotels"], data["matching"]) == (3, 4, 4)
    assert [h["id"] for h in data["hotels"]] == [3462, 5326, 8778, 8194]  # highest rating first
    assert data["hotels"][2] == {
        "id": 8778,
        "name": "هتل انقلاب",
        "hotel": "mashhad/enghelab",
        "type": "هتل 2 ستاره",
        "stars": 2,
        "rating": 3.5,
        "reviews": 20,
        "address": "خیابان امام رضا 8",
        "price_toman": 3927000,
        "per_night_toman": 1309000,
        "before_discount_toman": 4002000,
        "meals": "بدون وعده غذایی",
        "cheapest_room_sleeps": 2,
        "refundable": False,
        "mrbilit_offer": False,
    }
    assert data["hotels"][3]["stars"] is None  # a hotel apartment has no star rating
    sent = api.calls[0].url.params
    assert (sent["cityName"], sent["from"], sent["to"], sent["roomCount"]) == (
        "mashhad",
        "2026-10-20",
        "2026-10-23",
        "1",
    )


async def test_mb_search_hotels_filters(client, api):
    api["hotel.mrbilit.ir/api/hotels/v2/hotels/search"] = fixture("hotel_search.json")
    args = {"city": "mashhad", **STAY, "min_stars": 5, "sort": "cheapest", "max_price_per_night_toman": 8000000}
    data = (await client.call_tool("mb_search_hotels", args)).structured_content
    assert [(h["id"], h["per_night_toman"]) for h in data["hotels"]] == [(3462, 7433333)]
    args = {"city": "mashhad", **STAY, "hotel_type": "hotel_apartment"}
    data = (await client.call_tool("mb_search_hotels", args)).structured_content
    assert [h["id"] for h in data["hotels"]] == [8194]


async def test_mb_search_hotels_rejects_bad_stay(client, api):
    result = await client.call_tool(
        "mb_search_hotels", {"city": "mashhad", "check_in": "2026-10-20", "check_out": "2026-10-20"}
    )
    assert result.is_error and "nights" in result.content[0].text and not api.calls


async def test_hotel_tools_reject_past_check_in(client, api):
    stay = {"check_in": "2026-10-03", "check_out": "2026-10-05"}
    for name, args in (("mb_search_hotels", {"city": "mashhad"}), ("mb_hotel_rooms", {"hotel": "mashhad/enghelab"})):
        result = await client.call_tool(name, {**args, **stay})
        assert result.is_error and "check_in 2026-10-03 is in the past" in result.content[0].text
    assert not api.calls


async def test_hotel_ref_rejects_dot_segments(client, api):
    for name, ref in (("mb_hotel_calendar", "../hotels"), ("mb_hotel", "mashhad/..")):
        result = await client.call_tool(name, {"hotel": ref})
        assert result.is_error
    assert not api.calls


async def test_mb_hotel(client, api):
    page = fixture("hotel_detail.json")
    page["hotel"]["hotelFaqs"].append({"question": "هتل انقلاب مشهد کجاست؟", "answer": "خیابان امام رضا"})
    api["hotel.mrbilit.ir/api/hotel/mashhad/enghelab/static"] = page
    data = (await client.call_tool("mb_hotel", {"hotel": "mashhad/enghelab", "reviews": 2})).structured_content
    assert (data["id"], data["name"], data["stars"], data["rating"]) == (8778, "هتل انقلاب", 2, 3.6)
    assert data["hotel"] == "mashhad/enghelab"  # ready for mb_hotel_rooms
    assert (data["check_in_from"], data["check_out_by"], data["lat"]) == ("14:00", "12:00", 36.280205)
    assert data["landmarks"][0] == {"name": "بازار رضا", "km": 0.9, "minutes": 2}
    assert data["reviews_available"] == 3 and len(data["reviews"]) == 2
    assert data["reviews"][1] == {
        "date": "2026-07-10",
        "author": "محمد جواد هادیزاده",
        "rating": 4,
        "text": data["reviews"][1]["text"],
        "booked_here": False,
        "scores": {"نظافت هتل": 4, "امکانات هتل": 4, "برخورد پرسنل": 4, "مکان و دسترسی پذیری": 4, "کیفیت کافی شاپ": 4},
    }
    assert data["description"].startswith("هتل انقلاب مشهد") and "<" not in data["description"]
    assert "لابی" in data["amenities"]
    assert data["faq"] == [{"q": "هتل انقلاب مشهد کجاست؟", "a": "خیابان امام رضا"}]  # site-wide questions dropped


async def test_mb_hotel_by_id_and_unknown(client, api):
    api["hotel.mrbilit.ir/api/hotel/8778/static"] = fixture("hotel_detail.json")
    api["hotel.mrbilit.ir/api/hotel/999999/static"] = {"hotel": None}
    data = (await client.call_tool("mb_hotel", {"hotel": "8778", "reviews": 0})).structured_content
    assert data["id"] == 8778 and data["reviews"] == []
    assert data["hotel"] is None and data["slug"] == "enghelab"  # the detail has no English city slug
    result = await client.call_tool("mb_hotel", {"hotel": "999999"})
    assert result.is_error and "Unknown hotel" in result.content[0].text
    api["hotel.mrbilit.ir/api/hotel/777/static"] = lambda r: httpx.Response(200, json="x")  # not an object
    result = await client.call_tool("mb_hotel", {"hotel": "777"})
    assert result.is_error and "Unknown hotel" in result.content[0].text
    result = await client.call_tool("mb_hotel", {"hotel": "mashhad/nope"})  # the slug path answers 404
    assert result.is_error and "Unknown hotel" in result.content[0].text


async def test_mb_hotel_rooms(client, api):
    api["hotel.mrbilit.ir/api/hotel/mashhad/enghelab/rooms"] = fixture("hotel_rooms.json")
    data = (await client.call_tool("mb_hotel_rooms", {"hotel": "mashhad/enghelab", **STAY})).structured_content
    assert data["nights"] == 3 and data["cancellation"] == "رزروهای این هتل غیر قابل استرداد می باشد."
    assert [r["price_toman"] for r in data["rooms"]] == [3927000, 3927000, 5892000, 6110000]
    assert data["rooms"][0] == {
        "room": "دو تخته دابل (بدون صبحانه)",
        "sleeps": 2,
        "extra_beds": 0,
        "price_toman": 3927000,
        "before_discount_toman": 4002000,
        "per_night_toman": 1309000,
        "extra_bed_toman": None,
        "rooms_left": 4,
        "meals": None,
        "min_nights": 1,
        "description": None,
    }
    assert data["rooms"][3]["per_night_toman"] == [1746000, 2182000, 2182000] and data["rooms"][3]["meals"] == "صبحانه"
    data = (
        await client.call_tool("mb_hotel_rooms", {"hotel": "mashhad/enghelab", **STAY, "guests": 3})
    ).structured_content
    assert [r["room"] for r in data["rooms"]] == ["سه تخته (بدون صبحانه)"]


async def test_mb_hotel_rooms_unknown_slug(client, api):
    api["hotel.mrbilit.ir/api/hotel/mashhad/nope/rooms"] = lambda r: httpx.Response(
        500, json={"message": "متاسفانه درخواست شما با خطا مواجه شد"}
    )
    result = await client.call_tool("mb_hotel_rooms", {"hotel": "mashhad/nope", **STAY})
    assert result.is_error and "mb_find_place" in result.content[0].text


async def test_mb_hotel_calendar(client, api):
    api["hotel.mrbilit.ir/api/hotel/mashhad/enghelab/calendar"] = fixture("hotel_calendar.json")
    data = (await client.call_tool("mb_hotel_calendar", {"hotel": "mashhad/enghelab"})).structured_content
    assert data["nights"][0] == {"date": "2026-11-12", "price_toman": 2182000}  # sorted oldest first
    assert data["cheapest"] == {"date": "2026-11-13", "price_toman": 1746000}
    assert len(data["nights"]) == 10 and "note" not in data


async def test_mb_hotel_calendar_unknown_hotel(client, api):
    api["hotel.mrbilit.ir/api/hotel/mashhad/nope/calendar"] = []  # an unknown slug answers an empty list
    data = (await client.call_tool("mb_hotel_calendar", {"hotel": "mashhad/nope"})).structured_content
    assert data["nights"] == [] and "mb_find_place" in data["note"]


async def test_mb_city_hotels(client, api):
    api["hotel.mrbilit.ir/api/hotels/searchstatics"] = fixture("hotel_static.json")
    data = (await client.call_tool("mb_city_hotels", {"city": "mashhad", "stars": 5})).structured_content
    assert (data["total"], data["page"], data["pages"], data["matching"]) == (360, 1, 4, 2)
    assert data["hotels"][0] == {
        "id": 5326,
        "name": "مجلل درویشی",
        "hotel": "mashhad/darvishi",
        "type": "هتل 5 ستاره",
        "stars": 5,
        "rating": 4.1,
        "reviews": 0,
        "address": "خیابان امام رضا",
    }
    assert "note" in data  # 6 hotels on a page that should hold 90: a short page from the server cache
    sent = api.calls[0].url.params
    assert dict(sent) == {"cityName": "mashhad", "pageNumber": "1"}  # filters stay client-side (server cache)
    data = (
        await client.call_tool("mb_city_hotels", {"city": "mashhad", "hotel_type": "hotel_apartment", "name": "تابش"})
    ).structured_content
    assert [h["id"] for h in data["hotels"]] == [8162] and data["hotels"][0]["stars"] is None
