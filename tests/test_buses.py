import httpx
import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio

SEARCH = "bus.mrbilit.ir/api/GetBusServices"


async def test_mb_search_buses(client, api):
    api[SEARCH] = fixture("bus_search.json")
    data = (
        await client.call_tool("mb_search_buses", {"origin": 11320000, "destination": 31310000, "date": "2026-10-20"})
    ).structured_content
    assert [b["bus_id"] for b in data["buses"]] == [55983988, 55983989, 56145483, 56246979]  # earliest first
    assert data["buses"][0] == {
        "bus_id": 55983988,
        "company": "گیتی نورد",
        "company_id": 16,
        "cooperative": "شرکت گیتی نورد تعاونى شماره 12 - پایانه جنوب",
        "from": "تهران (جنوب)",
        "to": "مشهد",
        "departure": "2026-10-20T06:00:00",
        "arrival": "2026-10-20T18:00:00",
        "price_toman": 1315500,
        "seats_left": 23,
        "vip": True,
        "bus": "وی آی پی29نفره صندلی تخت شو همراه باشارژراختصاصی",
        "features": ["تخت شو", "شارژر دار"],
        "stops": ["سبزوار", "نیشابور"],
        "free_cancel_minutes": None,
    }
    assert data["refund_penalties"] == [  # the most common table, listed once
        {"until_hours_before": 1.0, "penalty_pct": 10},
        {"until_hours_before": None, "penalty_pct": 50},
    ]
    assert [b["bus_id"] for b in data["buses"] if "refund_penalties" in b] == [56246979]  # a bus with its own
    assert data["buses"][3]["refund_penalties"][1]["condition"].startswith("درصورت عدم چاپ")
    assert {"id": 16, "name": "گیتی نورد"} in data["companies"]
    sent = body(api.calls[0])
    assert (sent["from"], sent["to"], sent["date"], sent["includeClosed"]) == (11320000, 31310000, "2026-10-20", True)


async def test_mb_search_buses_filters_and_sold_out(client, api):
    page = fixture("bus_search.json")
    page["buses"][0]["capacity"] = 0  # sold out
    api[SEARCH] = page
    args = {
        "origin": 11320000,
        "destination": 31310000,
        "date": "2026-10-20",
        "sort": "cheapest",
        "companies": [15, 12, 16],
        "depart_after": "07:30",
    }
    data = (await client.call_tool("mb_search_buses", args)).structured_content
    assert [b["bus_id"] for b in data["buses"]] == [56145483, 56246979]  # same price: earlier first
    data = (
        await client.call_tool(
            "mb_search_buses", {**args, "depart_after": None, "include_sold_out": True, "companies": [16]}
        )
    ).structured_content
    assert [(b["bus_id"], b["seats_left"]) for b in data["buses"]] == [(55983988, 0), (55983989, 19)]


async def test_mb_search_buses_odd_body_and_past_date(client, api):
    api[SEARCH] = []  # bus search answers a bare [] to some bad inputs
    args = {"origin": 11320000, "destination": 31310000, "date": "2026-10-20"}
    data = (await client.call_tool("mb_search_buses", args)).structured_content
    assert data["buses"] == [] and "mb_find_place" in data["note"]
    api["bus.mrbilit.ir/api/v2/GetSeats"] = []
    data = (await client.call_tool("mb_bus_seats", {"bus_id": 1})).structured_content
    assert data["layout"] == [] and data["free_seats"] == []
    for tool in ("mb_search_buses", "mb_search_taxis"):
        result = await client.call_tool(tool, {**args, "date": "2026-10-03"})
        assert result.is_error and "in the past" in result.content[0].text
    assert len(api.calls) == 2


async def test_mb_bus_price_calendar(client, api):
    api["bus.mrbilit.ir/api/GetMinPrices"] = fixture("bus_min_prices.json")
    args = {"origin": 11320000, "destination": 31310000, "start_date": "2026-10-04", "days": 31, "seats": 3}
    data = (await client.call_tool("mb_bus_price_calendar", args)).structured_content
    assert data["days"][0] == {"date": "2026-10-04", "price_toman": 820000}
    assert [d["date"] for d in data["days"]] == sorted(d["date"] for d in data["days"])
    assert data["cheapest"] == {"date": "2026-10-06", "price_toman": 785000}
    sent = api.calls[0].url.params
    assert (sent["fromDate"], sent["toDate"], sent["capacity"]) == ("2026-10-04", "2026-11-03", "3")


async def test_mb_bus_seats(client, api):
    page = fixture("bus_seats.json")
    page["seats"][4][1]["status"] = 1  # seat 11 sold to a woman
    page["seats"][5][0]["number"] = -1  # some providers mark gaps -1
    api["bus.mrbilit.ir/api/v2/GetSeats"] = page
    data = (await client.call_tool("mb_bus_seats", {"bus_id": 55983988})).structured_content
    assert data["sold_to_men"] == [1, 2, 3, 4, 5, 6] and data["sold_to_women"] == [11]
    assert data["free_seats"] == [7, 8, 9, 10, 12, *range(13, 30)]
    assert data["layout"][:6] == [  # '|' = aisle after spacePlace (2) seats
        "-- -- | DR",
        "01m 02m | 03m",
        "04m 05m | 06m",
        "07 08 | 09",
        "10 11w | 12",
        "-- -- | 13",
    ]
    assert data["seat_rules"].startswith("انتخاب صندلی ردیف اول") and data["price_toman"] is None
    sent = api.calls[0]
    assert sent.url.params["busId"] == "55983988" and sent.headers["Authorization"].startswith("Bearer ")


async def test_mb_bus_seats_expired_id(client, api):
    api["bus.mrbilit.ir/api/v2/GetSeats"] = lambda r: httpx.Response(
        500, json={"Message": "Value cannot be null. (Parameter 'element')"}
    )
    result = await client.call_tool("mb_bus_seats", {"bus_id": 1})
    assert result.is_error and "Run mb_search_buses again" in result.content[0].text


async def test_mb_search_taxis(client, api):
    api["bus.mrbilit.ir/api/GetTaxiServices"] = fixture("taxi_search.json")
    data = (
        await client.call_tool("mb_search_taxis", {"origin": 11320000, "destination": 54310000, "date": "2026-10-20"})
    ).structured_content
    assert data["classes"] == [
        {"class": "اکونومی", "cars": "سمند | پژو", "from_price_toman": 4490000, "slots": 2},
        {"class": "تشریفاتی", "cars": "کمری | سفران", "from_price_toman": 7950000, "slots": 1},
        {"class": "وی\u200cآی\u200cپی", "cars": "اکسنت | آریو", "from_price_toman": 5690000, "slots": 1},
    ]
    assert data["offers"][0] == {
        "offer_id": 56443357,
        "class": "اکونومی",
        "cars": "سمند | پژو",
        "pickup": "2026-10-20T00:00:00",
        "price_toman": 4490000,
        "max_passengers": 3,
    }
    assert data["refund_penalties"] == [
        {"until_hours_before": 12.0, "penalty_pct": 20},
        {"until_hours_before": 3.0, "penalty_pct": 50},
    ]
    assert data["free_cancel_minutes"] == 60
    data = (
        await client.call_tool(
            "mb_search_taxis", {"origin": 11320000, "destination": 54310000, "date": "2026-10-20", "car_class": "vip"}
        )
    ).structured_content
    assert [o["offer_id"] for o in data["offers"]] == [56443359]


async def test_mb_search_taxis_none(client, api):
    api["bus.mrbilit.ir/api/GetTaxiServices"] = {"buses": [], "filterData": {}}
    data = (
        await client.call_tool("mb_search_taxis", {"origin": 11320000, "destination": 31310000, "date": "2026-10-20"})
    ).structured_content
    assert data["offers"] == [] and "no taxis" in data["note"].lower()
