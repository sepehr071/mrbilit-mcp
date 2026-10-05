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
    assert len(tries) == 2  # one retry only


async def test_fetch_timeout_is_not_retried():
    tries = []

    def hang(request):
        tries.append(request)
        raise httpx.ReadTimeout("hung", request=request)

    http.set_transport(httpx.MockTransport(hang))
    with pytest.raises(http.ApiError, match="did not answer in time"):
        await http.fetch("https://masir.mrbilit.com/api/x")
    assert len(tries) == 1
