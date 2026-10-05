import pytest
from conftest import days
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]

ROUTE = {"origin": 11320000, "destination": 31310000}  # Tehran -> Mashhad


async def test_mb_search_buses(client):
    data = await call(client, "mb_search_buses", {**ROUTE, "date": days(16), "sort": "cheapest"})
    prices = [b["price_toman"] for b in data["buses"]]
    assert prices and prices == sorted(prices) and 100_000 < prices[0] < 20_000_000
    assert data["refund_penalties"]  # the common table, listed once


async def test_mb_bus_price_calendar(client):
    data = await call(client, "mb_bus_price_calendar", {**ROUTE, "days": 20})
    assert data["days"] and data["cheapest"]["price_toman"] > 100_000


async def test_mb_bus_seats(client):
    buses = (await call(client, "mb_search_buses", {**ROUTE, "date": days(16)}))["buses"]
    data = await call(client, "mb_bus_seats", {"bus_id": buses[0]["bus_id"]})
    assert data["free_seats"] and data["layout"] and "DR" in data["layout"][0]


async def test_mb_search_taxis(client):
    data = await call(
        client, "mb_search_taxis", {"origin": 11320000, "destination": 54310000, "date": days(16)}
    )  # Tehran -> Rasht
    assert data["offers"] and all(o["max_passengers"] <= 4 for o in data["offers"])
    assert 1_000_000 < min(c["from_price_toman"] for c in data["classes"]) < 50_000_000
