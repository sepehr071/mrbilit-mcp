import asyncio
import sys

import httpx
import pytest

from mrbilit_mcp import http

pytestmark = pytest.mark.anyio

PAGE = "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(403, text="<html>Forbidden</html>"), "blocked the request (HTTP 403)"),
        (httpx.Response(429, json={}), "rate limiting requests (HTTP 429)"),
        (httpx.Response(502, text=PAGE), "temporarily unavailable (HTTP 502). Try again"),
        (httpx.Response(500, text=PAGE), "server error (HTTP 500). Check the ids"),
        (httpx.Response(500, json={"Message": "bad id"}), "server error (HTTP 500): bad id."),
        (httpx.Response(400, text="no such route"), "rejected the request (HTTP 400): no such route"),
        (httpx.Response(200, text=PAGE), "non-JSON response (HTTP 200)"),
    ],
)
async def test_fetch_error_messages(response, expected):
    http.set_transport(httpx.MockTransport(lambda r: response))
    with pytest.raises(http.ApiError) as e:
        await http.fetch("https://bus.mrbilit.ir/api/x")
    assert str(e.value).startswith("bus.mrbilit.ir") and expected in str(e.value) and "<" not in str(e.value)


async def test_fetch_retries_one_connect_reset():
    tries = []

    def flaky(request):
        tries.append(request)
        if len(tries) == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, json={"ok": True})

    http.set_transport(httpx.MockTransport(flaky))
    assert await http.fetch("https://train.mrbilit.com/api/x") == {"ok": True} and len(tries) == 2

    def down(request):
        tries.append(request)
        raise httpx.ConnectError("reset", request=request)

    tries.clear()
    http.set_transport(httpx.MockTransport(down))
    with pytest.raises(http.ApiError, match="MRBILIT_MCP_PROXY"):
        await http.fetch("https://train.mrbilit.com/api/x")
    assert len(tries) == 1 + http.MAX_RETRIES


async def test_fetch_timeout_is_retried():
    tries = []

    def hang(request):
        tries.append(request)
        raise httpx.ReadTimeout("hung", request=request)

    http.set_transport(httpx.MockTransport(hang))
    with pytest.raises(http.ApiError, match="did not answer in time"):
        await http.fetch("https://masir.mrbilit.com/api/x")
    assert len(tries) == 1 + http.MAX_RETRIES


async def test_403_is_not_retried():
    tries = []

    def blocked(request):
        tries.append(request)
        return httpx.Response(403, text="<html>Forbidden</html>")

    http.set_transport(httpx.MockTransport(blocked))
    with pytest.raises(http.ApiError):
        await http.fetch("https://bus.mrbilit.ir/api/x")
    assert len(tries) == 1


@pytest.mark.parametrize(
    ("url", "site", "origin"),
    [
        ("https://train.mrbilit.com/api/x", "same-site", True),
        ("https://flight.atighgasht.com/api/x", "cross-site", True),
        ("https://bus.mrbilit.ir/api/x", "cross-site", True),
        ("https://mrbilit.com/", "same-origin", False),
    ],
)
async def test_frontend_headers_per_host(url, site, origin):
    seen = []

    def echo(request):
        seen.append(request.headers)
        return httpx.Response(200, json={})

    http.set_transport(httpx.MockTransport(echo))
    await http.fetch(url)
    h = seen[0]
    assert h["sec-fetch-site"] == site and h["sec-fetch-mode"] == "cors" and h["accept-language"].startswith("fa-IR")
    assert h["referer"] == "https://mrbilit.com/" and (h.get("origin") == "https://mrbilit.com") is origin


def test_is_challenge():
    html = {"content-type": "text/html"}
    assert http._is_challenge(httpx.Response(403, text="<html>Just a moment...</html>", headers=html))
    assert http._is_challenge(httpx.Response(503, text="<!doctype html><title>ArvanCloud</title>"))
    assert not http._is_challenge(httpx.Response(403, text="<html>Forbidden</html>", headers=html))
    assert not http._is_challenge(httpx.Response(403, json={"x": 1}))
    assert not http._is_challenge(httpx.Response(200, text="<html></html>", headers=html))


async def test_proxy_list_rotates_and_stale_client_does_not(monkeypatch):
    monkeypatch.setenv("MRBILIT_MCP_PROXY", "http://p1:1, http://p2:2")
    assert http._proxies() == ["http://p1:1", "http://p2:2"]
    old, _ = http._get_client()
    http._next_proxy(old)
    new, _ = http._get_client()
    assert http._proxy_index == 1 and new is not old
    http._next_proxy(old)  # a late failure on the retired client must not skip the proxy now in use
    assert http._proxy_index == 1 and http._client is new


async def test_challenge_cleared_by_browser_once(monkeypatch, api):
    runs = []

    async def fake_browser(proxy):
        runs.append(proxy)
        await asyncio.sleep(0.01)
        return [{"name": "clearance", "value": "ok", "domain": ".mrbilit.ir", "path": "/"}]

    monkeypatch.setattr(http, "_browser_cookies", fake_browser)

    def guarded(request):
        if "clearance=ok" not in request.headers.get("cookie", ""):
            return httpx.Response(403, text="<html>Just a moment</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, json={})

    api["content.mrbilit.ir/a"] = guarded
    api["content.mrbilit.ir/b"] = guarded
    await asyncio.gather(http.fetch("https://content.mrbilit.ir/a"), http.fetch("https://content.mrbilit.ir/b"))
    assert len(runs) == 1  # the second caller reused the cookies the first one fetched


async def test_challenge_without_patchright(monkeypatch, api):
    monkeypatch.setitem(sys.modules, "patchright.async_api", None)  # makes the import fail
    api["content.mrbilit.ir/a"] = lambda r: httpx.Response(403, text="<html>Just a moment</html>")
    with pytest.raises(http.ApiError, match=r"mrbilit-mcp\[browser\].*patchright install chromium"):
        await http.fetch("https://content.mrbilit.ir/a")


async def test_retry_after_is_honoured_and_capped(monkeypatch, api):
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(http.asyncio, "sleep", fake_sleep)
    hits = []

    def limited(request):
        hits.append(1)
        if len(hits) == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "3"})
        if len(hits) == 2:
            return httpx.Response(503, json={}, headers={"Retry-After": "999"})
        return httpx.Response(200, json={"ok": True})

    api["train.mrbilit.com/a"] = limited
    assert await http.fetch("https://train.mrbilit.com/a") == {"ok": True}
    assert [w for w in waits if w] == [3, http.MAX_RETRY_WAIT]
