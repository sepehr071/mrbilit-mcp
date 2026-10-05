import httpx
import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio

FLIGHTS = "flight.atighgasht.com/api/Flights"


async def test_mb_search_flights_one_way_party_total(client, api):
    api[FLIGHTS] = fixture("flight_search.json")
    args = {"origin": "thr", "destination": "MHD", "date": "2026-10-20", "adults": 2, "children": 1, "infants": 1}
    data = (await client.call_tool("mb_search_flights", args)).structured_content
    assert [f["flight_id"] for f in data["flights"]] == [
        "21919377",
        "21911427",
        "21921948",
    ]  # cheapest adult fare first
    assert data["flights"][0] == {
        "flight_id": "21919377",
        "price_toman": 11856000,
        "total_toman": 36816000,  # 2 x 11,856,000 + 11,856,000 child + 1,248,000 infant
        "seats_left": 2,
        "cabin": "Economy",
        "charter": True,
        "baggage": "20 KG",
        "baggage_to_confirm": False,
        "segments": [
            {
                "from": "THR",
                "to": "MHD",
                "departure": "2026-10-20T08:30:00+03:30",
                "arrival": "2026-10-20T09:30:00+03:30",
                "duration": "01:00:00",
                "stops": 0,
                "flights": ["MEH 4200"],
                "airline": "مهر",
                "aircraft": "Boeing 737",
            }
        ],
    }
    assert data["flights"][1]["total_toman"] == 2 * 12320000 + 10480000 + 2490000  # system fare: cheaper child
    assert data["flights"][2]["baggage_to_confirm"] is True
    assert (data["from"], data["to"], data["matching"], data["not_on_sale"]) == ("تهران (THR)", "مشهد (MHD)", 3, 2)
    sent = body(api.calls[0])
    assert sent["Routes"] == [{"OriginCode": "THR", "DestinationCode": "MHD", "DepartureDate": "2026-10-20"}]
    assert (sent["AdultCount"], sent["ChildCount"], sent["InfantCount"], sent["CabinClass"]) == (2, 1, 1, "All")


async def test_mb_search_flights_filters(client, api):
    api[FLIGHTS] = fixture("flight_search.json")
    data = (
        await client.call_tool(
            "mb_search_flights", {"origin": "THR", "destination": "MHD", "date": "2026-10-20", "airlines": ["v1"]}
        )
    ).structured_content
    assert [f["flight_id"] for f in data["flights"]] == ["21921948"]
    args = {"origin": "THR", "destination": "MHD", "date": "2026-10-20", "sort": "earliest", "depart_after": "10:00"}
    data = (await client.call_tool("mb_search_flights", args)).structured_content
    assert [f["segments"][0]["departure"][11:16] for f in data["flights"]] == ["14:00", "18:40"]


async def test_mb_search_flights_domestic_round_trip_is_two_searches(client, bundled):
    def by_leg(request):
        name = "flight_search.json" if body(request)["Routes"][0]["OriginCode"] == "THR" else "flight_search_back.json"
        return httpx.Response(200, json=fixture(name))

    bundled[FLIGHTS] = by_leg
    args = {
        "origin": "THR",
        "destination": "MHD",
        "date": "2026-10-20",
        "return_date": "2026-10-23",
        "cabin": "business",
    }
    data = (await client.call_tool("mb_search_flights", args)).structured_content
    assert data["outbound"]["flights"] == []  # no business fares on the way out
    back = data["return"]["flights"]
    assert [(f["flight_id"], f["cabin"], f["price_toman"], f["baggage"]) for f in back] == [
        ("21874543", "Business", 21565500, "30 KG")
    ]
    legs = [body(r)["Routes"] for r in bundled.calls if r.url.path == "/api/Flights"]
    assert sorted(len(r) for r in legs) == [1, 1]
    assert {r[0]["DepartureDate"] for r in legs} == {"2026-10-20", "2026-10-23"}


async def test_mb_search_flights_international_round_trip_is_one_package(client, bundled):
    bundled[FLIGHTS] = fixture("flight_search_intl_rt.json")
    args = {"origin": "IKA", "destination": "IST", "date": "2026-10-20", "return_date": "2026-10-23"}
    data = (await client.call_tool("mb_search_flights", args)).structured_content
    assert data["round_trip_package"] is True and data["not_on_sale"] == 1
    first = data["flights"][0]
    assert (first["flight_id"], first["price_toman"], first["baggage"]) == (
        "21894997+21924108",  # outbound Id + return leg id: packages share the outbound Id
        50547000,
        "30 KG",
    )  # ties keep API order
    assert [(s["from"], s["to"]) for s in first["segments"]] == [("IKA", "IST"), ("IST", "IKA")]
    routes = [body(r)["Routes"] for r in bundled.calls if r.url.path == "/api/Flights"]
    assert routes == [
        [
            {"OriginCode": "IKA", "DestinationCode": "IST", "DepartureDate": "2026-10-20"},
            {"OriginCode": "IST", "DestinationCode": "IKA", "DepartureDate": "2026-10-23"},
        ]
    ]


async def test_mb_search_flights_rejects_return_before_departure(client, api):
    args = {"origin": "THR", "destination": "MHD", "date": "2026-10-20", "return_date": "2026-10-19"}
    result = await client.call_tool("mb_search_flights", args)
    assert result.is_error and not api.calls


async def test_mb_search_flights_rejects_past_date(client, api):
    result = await client.call_tool("mb_search_flights", {"origin": "THR", "destination": "MHD", "date": "2026-10-03"})
    assert result.is_error and "in the past" in result.content[0].text and not api.calls


async def test_mb_search_flights_unknown_code(client, api):
    page = fixture("flight_search.json")
    page["Flights"] = []
    page["Meta"]["RoutesInfo"][0]["Origin"] = {"PersianTitle": None, "IataCode": "XQZ"}
    api[FLIGHTS] = page
    data = (
        await client.call_tool("mb_search_flights", {"origin": "XQZ", "destination": "MHD", "date": "2026-10-20"})
    ).structured_content
    assert data["from"] == "unknown code (XQZ)" and data["note"].startswith("Unknown airport code XQZ")


async def test_mb_search_flights_odd_body(client, api):
    api[FLIGHTS] = lambda r: httpx.Response(200, json="x")  # a JSON body that is not an object: an empty result
    data = (
        await client.call_tool("mb_search_flights", {"origin": "THR", "destination": "MHD", "date": "2026-10-20"})
    ).structured_content
    assert data["flights"] == [] and data["note"]


async def test_mb_flight_price_calendar(client, api):
    rows = fixture("flight_min_prices.json")
    rows[2]["TotalFare"] = None  # a day with no known fare
    api["flight.atighgasht.com/api/Flights/MinPrices"] = rows
    data = (
        await client.call_tool(
            "mb_flight_price_calendar", {"origin": "THR", "destination": "MHD", "start_date": "2026-10-04", "days": 10}
        )
    ).structured_content
    assert data["days"][0] == {"date": "2026-10-04", "price_toman": 12000000, "airline": "AXV"}
    assert len(data["days"]) == 9 and data["days_without_price"] == 1
    assert data["cheapest"] == {"date": "2026-10-09", "price_toman": 7700000, "airline": "AXV"}
    sent = body(api.calls[0])
    assert (sent["StartDate"], sent["EndDate"]) == ("2026-10-04T00:00:00", "2026-10-13T00:00:00")


async def test_mb_flight_price_calendar_empty_says_why(client, api):
    api["flight.atighgasht.com/api/Flights/MinPrices"] = [{"Date": "2026-10-04T00:00:00", "TotalFare": None}]
    data = (
        await client.call_tool("mb_flight_price_calendar", {"origin": "XQZ", "destination": "MHD", "days": 1})
    ).structured_content
    assert data["days"] == [] and "mb_find_place" in data["note"]


async def test_mb_flight_fare_details_confirms_baggage(client, api):
    api[FLIGHTS] = fixture("flight_search.json")
    api["flight.atighgasht.com/api/Flights/GetBaggageInfo"] = fixture("flight_baggage.json")
    args = {"origin": "THR", "destination": "MHD", "date": "2026-10-20", "flight_id": "21921948"}
    data = (await client.call_tool("mb_flight_fare_details", args)).structured_content
    fare = data["fares"][0]
    assert fare["baggage"] == "20 KG" and fare["baggage_checked_with_airline"] is True and fare["booking_class"] == "TA"
    assert fare["price_toman"] == {"adult": 12323000, "child": 9244900, "infant": 1238400}
    assert fare["refund_penalties"][0] == {"window": "از لحظه صدور تا 7 روز قبل از پرواز", "penalty_pct": 30}
    assert fare["official_airline_rate"] is True and data["on_sale"] is True
    assert data["segments"][0]["legs"][0]["from"] == "تهران - مهرآباد (THR)"
    baggage_call = api.calls[1]
    assert baggage_call.method == "POST" and baggage_call.url.params["proposalId"].startswith("W3si")
    assert baggage_call.headers["content-length"] == "0"  # httpx sends it for an empty POST


async def test_mb_flight_fare_details_penalty_text(client, api):
    page = fixture("flight_search.json")
    tiers = page["Flights"][2]["Prices"][0]["CancellationTernEntities"]
    tiers[0].update(PercentAmount=None, PenaltyAmountTextEn="According to the supplier's terms and conditions")
    api[FLIGHTS] = page
    api["flight.atighgasht.com/api/Flights/GetBaggageInfo"] = fixture("flight_baggage.json")
    args = {"origin": "THR", "destination": "MHD", "date": "2026-10-20", "flight_id": str(page["Flights"][2]["Id"])}
    fare = (await client.call_tool("mb_flight_fare_details", args)).structured_content["fares"][0]
    assert fare["refund_penalties"][0]["penalty_pct"] is None
    assert fare["refund_penalties"][0]["penalty_text"] == "According to the supplier's terms and conditions"
    assert "penalty_text" not in fare["refund_penalties"][1]


INTL = {"origin": "IKA", "destination": "IST", "date": "2026-10-20", "return_date": "2026-10-23"}


async def test_mb_flight_fare_details_reads_rules_url(client, api):
    page = fixture("flight_search_intl_rt.json")
    page["Flights"][0]["Prices"][0]["FareRulesUrl"] = "https://charter.mrbilit.com/api/Flights/FareRules/68307192"
    api[FLIGHTS] = page
    api["charter.mrbilit.com/api/Flights/FareRules/68307192"] = fixture("flight_fare_rules.txt")
    data = (
        await client.call_tool("mb_flight_fare_details", {**INTL, "flight_id": "21894997+21924108"})
    ).structured_content
    fare = data["fares"][0]
    assert fare["rules"] == fixture("flight_fare_rules.txt").strip()[:2000]
    assert fare["baggage_checked_with_airline"] is False and fare["notes"]  # visa and nationality notes
    assert len(data["segments"]) == 2


async def test_mb_flight_fare_details_ignores_foreign_rules_url(client, api):
    page = fixture("flight_search_intl_rt.json")
    page["Flights"][0]["Prices"][0]["FareRulesUrl"] = "https://example.com/rules"
    api[FLIGHTS] = page
    data = (
        await client.call_tool("mb_flight_fare_details", {**INTL, "flight_id": "21894997+21924108"})
    ).structured_content
    assert [r.url.host for r in api.calls] == ["flight.atighgasht.com"]  # only the search
    assert data["fares"][0]["rules"] == page["Flights"][0]["Prices"][0].get("FareRules")


async def test_mb_flight_fare_details_picks_the_package(client, api):
    # Packages share the outbound Id; the return leg tells them apart.
    page = fixture("flight_search_intl_rt.json")
    page["Flights"][1]["Id"] = page["Flights"][0]["Id"]
    page["Flights"][1]["Segments"][1]["Legs"][0]["Id"] = 1
    page["Flights"][1]["Prices"][0]["PassengerFares"][0]["TotalFare"] = 123450
    api[FLIGHTS] = page
    data = (await client.call_tool("mb_flight_fare_details", {**INTL, "flight_id": "21894997+1"})).structured_content
    assert data["fares"][0]["price_toman"]["adult"] == 12345


async def test_mb_flight_fare_details_unknown_id(client, api):
    api[FLIGHTS] = fixture("flight_search.json")
    result = await client.call_tool(
        "mb_flight_fare_details", {"origin": "THR", "destination": "MHD", "date": "2026-10-20", "flight_id": "1"}
    )
    assert result.is_error and "run mb_search_flights again" in result.content[0].text
