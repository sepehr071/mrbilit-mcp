import json

import pytest
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_mb_notices(client):
    data = await call(client, "mb_notices", {})
    assert data["site_wide_active"] == len(data["notices"])
    assert "created_by" not in json.dumps(data) and "updated_by" not in json.dumps(data)


async def test_mb_help(client):
    data = await call(client, "mb_help", {"query": "استرداد بلیط قطار"})
    assert data["results"] and {r["source"] for r in data["results"]} <= {"faq", "terms", "support"}


async def test_mb_travel_guide(client):
    data = await call(
        client, "mb_travel_guide", {"service": "train", "origin": "tehran", "destination": "mashhad", "query": "کیش"}
    )
    assert (
        data["guide"]["faq"] and data["magazine"] and data["magazine"][0]["url"].startswith("https://mrbilit.com/mag/")
    )
