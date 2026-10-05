import pytest
from conftest import days
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]

ROUTE = {"origin": 1, "destination": 191}  # Tehran -> Mashhad


async def _class_id(client) -> int:
    data = await call(client, "mb_search_trains", {**ROUTE, "date": days(10), "sort": "cheapest"})
    return data["trains"][0]["classes"][0]["class_id"]


async def test_mb_search_trains(client):
    data = await call(client, "mb_search_trains", {**ROUTE, "date": days(10)})
    assert data["trains"] and all(c["bookable"] and c["seats_left"] > 0 for t in data["trains"] for c in t["classes"])
    assert 100_000 < data["trains"][0]["classes"][0]["price_toman"] < 20_000_000


async def test_mb_train_price_calendar(client):
    data = await call(client, "mb_train_price_calendar", {**ROUTE, "days": 20})
    assert data["days"] and data["cheapest"]["price_toman"] > 100_000


async def test_mb_train_price(client):
    data = await call(client, "mb_train_price", {"class_id": await _class_id(client), "adults": 2, "children": 1})
    units = data["unit_price_toman"]
    assert units["child"] < units["adult"] and data["total_toman"] == 2 * units["adult"] + units["child"]


async def test_mb_train_stops(client):
    data = await call(client, "mb_train_stops", {"class_id": await _class_id(client)})
    ids = [s["station_id"] for s in data["stops"]]
    assert 1 in ids and ids[-1] == 191


async def test_mb_alternative_routes(client):
    data = await call(client, "mb_alternative_routes", {**ROUTE, "date": days(10)})
    assert data["nearby_train_routes"] and data["nearby_flights"]
    assert "with_one_change" in data or "with_one_change" in data["errors"]  # masir can still hang after retries
