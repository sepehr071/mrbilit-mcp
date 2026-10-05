import asyncio
import datetime
import json
from pathlib import Path

import httpx
import pytest
from mcp import Client

from mrbilit_mcp import http, places
from mrbilit_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    """A recorded response: parsed JSON for .json files, the raw text otherwise (HTML, JS chunks, plain text)."""
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(raw) if name.endswith(".json") else raw


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def days(n: int) -> str:
    """A date n days from today, YYYY-MM-DD (live tests need future dates inside the sales window)."""
    return (datetime.date.today() + datetime.timedelta(days=n)).isoformat()


async def live_call(client, name: str, args: dict) -> dict:
    """Call a tool against the real site, about one request per second, and fail on tool errors."""
    await asyncio.sleep(1)
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_state(request, monkeypatch):
    """The shared httpx client is bound to one event loop; each test gets a new loop and empty list caches."""
    if "live" not in request.keywords:  # unit tests use the fixtures' dates: pin "today" to their recording day
        monkeypatch.setattr(http, "today", lambda: datetime.date(2026, 10, 4))
    http.set_transport(None)
    places._cache.clear()
    yield
    http.set_transport(None)
    places._cache.clear()


@pytest.fixture
def api():
    """Route table for a fake upstream: api["host/path"] = JSON body, text, or a callable(request) -> httpx.Response.

    Keys are host + path, e.g. "bus.mrbilit.ir/api/GetMinPrices" (train and bus share some paths).
    Unknown keys return 404. Every request is appended to api.calls so tests can assert on what was sent.
    """

    class Routes(dict):
        calls: list[httpx.Request]

    routes = Routes()
    routes.calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        target = routes.get(f"{request.url.host}{request.url.path}")
        if target is None:
            return httpx.Response(404, json={"error": "no fake route"})
        if callable(target):
            return target(request)
        if isinstance(target, str):
            return httpx.Response(200, text=target)
        return httpx.Response(200, json=target)

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
def bundled(api):
    """The site's home page, entry chunk and the two bundled location chunks (airports, train stations)."""
    api["mrbilit.com/"] = fixture("site_home.html")
    api["mrbilit.com/_nuxt/BcB8L5fh.js"] = fixture("site_entry.js")
    api["mrbilit.com/_nuxt/CJolaB_0.js"] = fixture("chunk_airports.js")
    api["mrbilit.com/_nuxt/Dzr3BWXP.js"] = fixture("chunk_stations.js")
    return api


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c
