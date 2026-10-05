import httpx
import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio

SEARCH = "train.mrbilit.com/api/GetAvailable/v2"


async def test_mb_search_trains_bookable_only(client, api):
    api[SEARCH] = fixture("train_search.json")
    data = (
        await client.call_tool("mb_search_trains", {"origin": 1, "destination": 191, "date": "2026-10-14"})
    ).structured_content
    assert (data["from"], data["to"], data["trains_listed"], data["matching"]) == ("تهران", "مشهد", 4, 3)
    assert [t["train_number"] for t in data["trains"]] == [318, 368, 350]  # earliest first; sold-out train 172 hidden
    assert data["trains"][0] == {
        "train_number": 318,
        "company": "رجا",
        "departure": "2026-10-14T09:20:00",
        "arrival": "2026-10-14T21:45:00",
        "refundable_online": True,
        "classes": [
            {
                "class_id": 9847134,
                "wagon": "4 ستاره اتوبوسی صبا",
                "stars": "چهار ستاره",
                "seating": "اتوبوسی",
                "price_toman": 760000,
                "seats_left": 18,
                "bookable": True,
                "features": [],
            }
        ],
    }
    assert len(data["refund_penalties"]) == 7 and data["refund_penalties"][1] == {
        "window": "تا 3 روز مانده به حرکت (تا 12 ظهر)",
        "penalty_pct": 10,
    }
    sent = api.calls[0].url.params
    assert (sent["from"], sent["to"], sent["date"], sent["genderCode"], sent["disableCache"]) == (
        "1",
        "191",
        "2026-10-14",
        "3",
        "true",
    )
    assert "selectedCapacityId" not in sent


async def test_mb_search_trains_cheapest_with_sold_out(client, api):
    api[SEARCH] = fixture("train_search.json")
    args = {
        "origin": 191,
        "destination": 1,
        "date": "2026-10-14",
        "quota": "women",
        "sort": "cheapest",
        "available_only": False,
        "outbound_class_id": 9847134,
    }
    data = (await client.call_tool("mb_search_trains", args)).structured_content
    assert [t["train_number"] for t in data["trains"]] == [318, 350, 368, 172]  # sold-out train last
    assert [(c["class_id"], c["bookable"]) for c in data["trains"][0]["classes"]] == [(9847134, True), (9866050, False)]
    sent = api.calls[0].url.params
    assert sent["genderCode"] == "2" and sent["selectedCapacityId"] == "9847134"


async def test_mb_search_trains_empty_note(client, api):
    api[SEARCH] = {"trains": [], "filters": {}, "meta": {}}
    data = (
        await client.call_tool("mb_search_trains", {"origin": 1, "destination": 191, "date": "2026-12-30"})
    ).structured_content
    assert data["trains"] == [] and "18 days" in data["note"]


async def test_mb_train_price_calendar(client, api):
    api["train.mrbilit.com/api/GetMinPrices"] = fixture("train_min_prices.json")
    args = {"origin": 1, "destination": 191, "start_date": "2026-10-05", "days": 30, "seats": 2, "quota": "men"}
    data = (await client.call_tool("mb_train_price_calendar", args)).structured_content
    assert data["days"][:3] == [
        {"date": "2026-10-05", "price_toman": 760000},
        {"date": "2026-10-06", "price_toman": 760000},
        {"date": "2026-10-07", "price_toman": 760000},
    ]
    assert {"date": "2026-10-08", "price_toman": 1520000} in data["days"] and len(data["days"]) == 18
    assert data["cheapest"] == {"date": "2026-10-05", "price_toman": 760000}
    sent = api.calls[0].url.params
    assert (sent["FromDate"], sent["ToDate"], sent["Capacity"], sent["GenderCode"]) == (
        "2026-10-05",
        "2026-11-03",
        "2",
        "1",
    )


async def test_mb_train_price_family(client, api):
    api["train.mrbilit.com/api/GetPricing"] = fixture("train_pricing.json")
    data = (
        await client.call_tool("mb_train_price", {"class_id": 9847134, "adults": 2, "children": 1, "infants": 1})
    ).structured_content
    assert data["unit_price_toman"] == {"adult": 760000, "child": 410150, "infant": 70900}
    assert data["total_toman"] == 2 * 760000 + 410150 + 70900
    assert data["adult_breakdown_toman"]["base_fare"] == 603261
    sent = api.calls[0].url.params
    assert (
        sent["CapacityId"],
        sent["AdultCount"],
        sent["ChildCount"],
        sent["InfantCount"],
        sent["EmptySeatCount"],
    ) == ("9847134", "2", "1", "1", "0")


async def test_mb_train_price_expired_class(client, api):
    api["train.mrbilit.com/api/GetPricing"] = lambda r: httpx.Response(
        500, json={"message": "Train must have path code (Parameter 'trainCapacity')"}
    )
    result = await client.call_tool("mb_train_price", {"class_id": 1})
    assert result.is_error and "Run mb_search_trains again" in result.content[0].text


async def test_mb_train_price_zero_fare_is_unknown_class(client, api):
    # Seen 2026-10-05 while the rail service was down: class 1 answered 200 with a price and every part 0.
    parts = {"discount": 0, "stationService": 0, "baseFare": 0, "hallPrice": 0, "foodPrice": 0, "commission": 0}
    api["train.mrbilit.com/api/GetPricing"] = {"adultPrice": {"price": 956000, **parts}, "childPrice": None}
    result = await client.call_tool("mb_train_price", {"class_id": 1})
    assert result.is_error and "Run mb_search_trains again" in result.content[0].text


async def test_train_tools_reject_past_date(client, api):
    for name in ("mb_search_trains", "mb_alternative_routes"):
        result = await client.call_tool(name, {"origin": 1, "destination": 191, "date": "2026-10-03"})
        assert result.is_error and "in the past" in result.content[0].text
    assert not api.calls


async def test_mb_train_stops(client, api):
    api["train.mrbilit.com/api/MidStation/9847134"] = fixture("train_stops.json")
    data = (await client.call_tool("mb_train_stops", {"class_id": 9847134})).structured_content
    assert data["stops"][0] == {"station_id": 1, "station": "تهران", "date": "2026-10-14", "time": "09:20"}
    assert [s["station_id"] for s in data["stops"]] == [1, 90, 209, 191]


async def test_mb_alternative_routes(client, bundled):
    tries = []

    def masir(request):
        tries.append(request)
        if len(tries) == 1:
            raise httpx.ReadTimeout("hung", request=request)  # masir hangs at random; the retry answers
        return httpx.Response(200, json=fixture("masir.json"))

    bundled["masir.mrbilit.com/api/GetAvailable"] = masir
    bundled["train.mrbilit.com/api/GetNearbyRoutes"] = fixture("train_nearby.json")
    bundled["bus.mrbilit.ir/api/GetNearbyRoutes"] = fixture("bus_nearby.json")
    bundled["flight.atighgasht.com/api/Airports/Nearby"] = fixture("nearby_airports.json")
    data = (
        await client.call_tool("mb_alternative_routes", {"origin": 1, "destination": 191, "date": "2026-10-14"})
    ).structured_content
    first = data["with_one_change"][0]
    assert (first["price_toman"], first["duration"], first["wait_at_change"]) == (1355000, "16h45m", "2h15m")
    assert [(leg["mode"], leg["from"], leg["to"], leg["price_toman"]) for leg in first["legs"]] == [
        ("train", "تهران", "قم", 55000),
        ("bus", "قم", "مشهد", 1300000),
    ]
    assert first["legs"][0]["class_id"] == 9857109 and first["legs"][1]["class_id"] is None
    assert data["nearby_train_routes"][1] == {
        "from_id": 209,
        "to_id": 191,
        "route": "ورامین - مشهد",
        "classes_listed": 4,
        "min_price_toman": 723200,
    }
    assert data["nearby_bus_routes"][1]["min_price_toman"] is None  # 0 = no known price, not free
    assert data["nearby_flights"][0] == {"from": "THR", "to": "MHD", "airports": "مهرآباد - شهید هاشمی نژاد"}
    assert "errors" not in data
    assert len(tries) == 2 and tries[1].headers["Authorization"].startswith("Bearer ey")
    train_body = body(next(r for r in bundled.calls if r.url.host == "train.mrbilit.com"))
    assert (
        train_body["from"] == {"latitude": 35.658142, "longitude": 51.398014}
        and train_body["departureDate"] == "2026-10-14"
    )


async def test_mb_alternative_routes_partial_failure(client, bundled):
    # 9 (Atashbag) has no coordinates; masir rejects the pair: the error is reported, not raised
    bundled["masir.mrbilit.com/api/GetAvailable"] = lambda r: httpx.Response(
        400, json={"UserMessage": "Invalid originId or destinationId"}
    )
    data = (
        await client.call_tool("mb_alternative_routes", {"origin": 9, "destination": 191, "date": "2026-10-14"})
    ).structured_content
    assert "Invalid originId" in data["errors"]["with_one_change"]
    assert "no coordinates" in data["note"] and "nearby_train_routes" not in data
