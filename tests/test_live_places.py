import pytest
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_mb_find_place(client):
    data = await call(client, "mb_find_place", {"query": "مشهد", "mode": "flight"})
    assert {"code": "MHD", "city": "مشهد", "airport": "فرودگاه شهید هاشمی نژاد"} in data["airports"]
    data = await call(client, "mb_find_place", {"query": "مشهد", "mode": "train"})
    assert data["stations"][0]["id"] == 191
    data = await call(client, "mb_find_place", {"query": "تهران", "mode": "bus"})
    assert data["cities"][0]["id"] == 11320000


async def test_mb_companies(client):
    data = await call(client, "mb_companies", {"mode": "flight", "query": "W5"})
    assert data["companies"][0]["code"] == "W5" and data["companies"][0]["english"] == "Mahan Air"
