import pytest
from conftest import days
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_mb_search_flights(client):
    data = await call(
        client, "mb_search_flights", {"origin": "THR", "destination": "MHD", "date": days(16), "adults": 2}
    )
    prices = [f["price_toman"] for f in data["flights"]]
    assert prices and prices == sorted(prices) and 1_000_000 < prices[0] < 100_000_000  # Toman, not Rial
    assert all(f["total_toman"] == 2 * f["price_toman"] for f in data["flights"])


async def test_mb_flight_price_calendar(client):
    data = await call(client, "mb_flight_price_calendar", {"origin": "THR", "destination": "MHD", "days": 20})
    assert data["days"] and data["cheapest"]["price_toman"] == min(d["price_toman"] for d in data["days"])


async def test_mb_flight_fare_details(client):
    args = {"origin": "THR", "destination": "MHD", "date": days(16)}
    flights = (await call(client, "mb_search_flights", args))["flights"]
    pick = next((f for f in flights if f["baggage_to_confirm"]), flights[0])
    data = await call(client, "mb_flight_fare_details", {**args, "flight_id": pick["flight_id"]})
    fare = data["fares"][0]
    assert fare["price_toman"]["adult"] == pick["price_toman"] and fare["rules"]
    assert data["segments"][0]["legs"][0]["flight"] == pick["segments"][0]["flights"][0]
