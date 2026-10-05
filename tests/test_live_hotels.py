import pytest
from conftest import days
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]

STAY = {"check_in": days(16), "check_out": days(19)}


async def test_mb_search_hotels(client):
    data = await call(client, "mb_search_hotels", {"city": "mashhad", **STAY, "sort": "cheapest", "limit": 10})
    prices = [h["price_toman"] for h in data["hotels"]]
    assert prices == sorted(prices) and prices[0] > 100_000
    assert all(h["hotel"].startswith("mashhad/") for h in data["hotels"])


async def test_mb_hotel(client):
    data = await call(client, "mb_hotel", {"hotel": "mashhad/enghelab", "reviews": 3})
    assert data["id"] == 8778 and 0 < data["rating"] <= 5 and data["landmarks"]


async def test_mb_hotel_rooms(client):
    data = await call(client, "mb_hotel_rooms", {"hotel": "mashhad/enghelab", **STAY})
    assert data["nights"] == 3
    assert all(r["price_toman"] <= r["before_discount_toman"] for r in data["rooms"])


async def test_mb_hotel_calendar(client):
    data = await call(client, "mb_hotel_calendar", {"hotel": "mashhad/enghelab"})
    assert len(data["nights"]) > 20 and data["cheapest"]["price_toman"] > 100_000


async def test_mb_city_hotels(client):
    data = await call(client, "mb_city_hotels", {"city": "mashhad", "stars": 5})
    assert data["total"] > 100 and all(h["stars"] == 5 for h in data["hotels"])
