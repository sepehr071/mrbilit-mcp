"""Shared async HTTP client for the MrBilit APIs.

Every tool goes through `fetch` (REST, JSON or text) or `gql` (Directus GraphQL), which cap
concurrency and turn HTTP failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import random
import re
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

FLIGHT = "https://flight.atighgasht.com"  # the flight backend still runs on the historic domain
CHARTER = "https://charter.mrbilit.com"
TRAIN = "https://train.mrbilit.com"
MASIR = "https://masir.mrbilit.com"
BUS = "https://bus.mrbilit.ir"
HOTEL = "https://hotel.mrbilit.ir"
CONTENT = "https://content.mrbilit.ir"
DIRECTUS = "https://directus.mrbilit.ir/graphql"
SITE = "https://mrbilit.com"

# A library User-Agent gets 403 from the WAF, and a few 403s were followed by TLS resets of every
# direct connection to the API origin. curl_cffi sends a real Chrome TLS/HTTP2 profile with its User-Agent and
# sec-ch-ua (see _get_client), so none is set here; Origin/Referer/sec-fetch-site depend on the host (_site_headers).
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "priority": "u=1, i",
}

# Public, static "deployment token" hardcoded in the site's entry chunk; not tied to a user.
# Required only by the combined-journey search and the bus seat map.
TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJidXMiOiI0ZiIsInRybiI6IjE3Iiwic3JjIjoiMiJ9"
    ".vvpr9fgASvk7B7I4KQKCz-SaCmoErab_p3csIvULG1w"
)
TOKEN_HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-PlayerID": str(uuid.uuid4()),
    "SessionId": f"session_{uuid.uuid4()}",
}

# flight, train, bus, hotel and masir all sit on one origin server: keep the total load low.
MAX_CONCURRENCY = 2
JITTER = (0.15, 0.6)  # seconds before each call
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10  # cap for Retry-After and backoff, seconds
RETRY_DELAY = 0.5

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_browser_lock: asyncio.Lock | None = None
_cleared: tuple[httpx.AsyncClient, float] | None = None


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them (API calls are XHR)."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        p = super()._create_request_params(req)
        p["headers"] = [*p["headers"], (b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        return p


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, None for network errors."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _proxy_index, _browser_lock, _cleared
    _transport, _client, _limit, _proxy_index, _browser_lock, _cleared = transport, None, None, 0, None, None


def _proxies() -> list[str]:
    """MRBILIT_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("MRBILIT_MCP_PROXY", "").split(",") if p.strip()]


_CHALLENGE = re.compile(r"just a moment|captcha|challenge|arvan|attention required|enable javascript", re.I)


def _is_challenge(r: httpx.Response) -> bool:
    """A bot-check page: 403/429/503 with an HTML body that carries a known challenge marker."""
    return (
        r.status_code in (403, 429, 503)
        and "html" in r.headers.get("content-type", "") + r.text[:20].lower()
        and bool(_CHALLENGE.search(r.text[:5000]))
    )


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate: a slower request that failed on an old client must not skip a good proxy.
    The old client is closed late because other requests may still be using it.
    """
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(90)  # longer than the 60 s request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Ignore system proxy settings; route through a proxy only when the user sets MRBILIT_MCP_PROXY.
        # curl_cffi presents Chrome's TLS/HTTP2 fingerprint and default headers; cookies and redirects stay with
        # httpx. The proxy goes to the transport (a client-level proxy is ignored once a transport is given).
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=60,  # hotel city search is 1.6 MB, an international round trip takes ~17 s
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=True,
            trust_env=False,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


def _site_headers(host: str | None) -> dict[str, str]:
    """What the web app's own XHRs send: Origin/Referer of mrbilit.com, sec-fetch-site by how the host relates to it."""
    if host == "mrbilit.com":
        return {"Referer": f"{SITE}/", "sec-fetch-site": "same-origin"}
    same_site = bool(host) and host.endswith(".mrbilit.com")  # atighgasht.com and mrbilit.ir are other sites
    return {"Origin": SITE, "Referer": f"{SITE}/", "sec-fetch-site": "same-site" if same_site else "cross-site"}


async def fetch(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    method: str = "GET",
    json: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    text: bool = False,
    redirects: bool = True,
) -> Any:
    """Call an endpoint and return the parsed JSON body (the text with `text=True`, None for 204).

    A timeout, network error, 5xx or 429 is retried up to MAX_RETRIES times with jittered exponential backoff,
    or after Retry-After when sent. Other 4xx (403 too) are never retried.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await _once(url, params, method, json, headers, timeout, text, redirects)
        except ApiError as e:
            if attempt == MAX_RETRIES or (e.status is not None and e.status < 500 and e.status != 429):
                raise
            wait = e.retry_after if e.retry_after is not None else RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))


async def _send(client, limit, method, url, kwargs) -> httpx.Response:
    host = urlsplit(url).hostname
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            return await client.request(method, url, **kwargs)
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). MrBilit's API resets connections from many networks "
            "outside Iran: set MRBILIT_MCP_PROXY to an HTTP proxy, or check the internet connection."
        ) from e


async def _browser_cookies(proxy: str | None) -> list[dict[str, Any]]:
    """Open the home page in headless patchright, wait for the challenge to clear, return the cookies."""
    try:
        from patchright.async_api import async_playwright
    except ImportError as e:
        raise ApiError(
            "MrBilit served a bot-check page. Install the browser extra (`pip install mrbilit-mcp[browser]`, or "
            "`uvx --with patchright==1.63.0 mrbilit-mcp`) and run `patchright install chromium`.",
            403,
        ) from e
    u = httpx.URL(proxy) if proxy else None
    opts = u and {"server": f"{u.scheme}://{u.host}:{u.port}", "username": u.username, "password": u.password}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=opts)
        try:
            page = await browser.new_page()
            await page.goto(SITE, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_function(
                "!/just a moment|access denied|attention required|arvancloud/i.test(document.title)", timeout=30_000
            )
            return await browser.contexts[0].cookies()
        finally:
            await browser.close()


async def _clear_challenge(client: httpx.AsyncClient) -> None:
    """Rung 2: let a real browser pass the bot check once and share its cookies with the httpx jar."""
    global _browser_lock, _cleared
    _browser_lock = _browser_lock or asyncio.Lock()
    async with _browser_lock:  # one browser at a time; callers that waited reuse the cookies just fetched
        if _cleared and _cleared[0] is client and time.monotonic() - _cleared[1] < 30:
            return
        proxies = _proxies()
        try:
            cookies = await _browser_cookies(proxies[_proxy_index % len(proxies)] if proxies else None)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(f"MrBilit bot check was not cleared by the browser ({type(e).__name__}).", 403) from e
        for c in cookies:
            client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        _cleared = (client, time.monotonic())


async def _once(url, params, method, json, headers, timeout, text, redirects) -> Any:
    client, limit = _get_client()
    host = urlsplit(url).hostname
    kwargs: dict[str, Any] = {
        "params": params,
        "json": json,
        "headers": {**_site_headers(host), **(headers or {})},
        "follow_redirects": redirects,
    }
    if timeout:
        kwargs["timeout"] = timeout
    r = await _send(client, limit, method, url, kwargs)
    if _is_challenge(r):
        _next_proxy(client)
        client, limit = _get_client()
        await _clear_challenge(client)
        r = await _send(client, limit, method, url, kwargs)  # one retry; a second block is final
        if _is_challenge(r):
            _next_proxy(client)

    if r.status_code >= 400:
        raise ApiError(_status_message(r, host), r.status_code, _retry_after(r))
    if r.status_code == 204:
        return None
    if text:
        return r.text
    try:
        return r.json()
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).") from e


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


async def gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """POST a read-only Directus GraphQL query and return its `data` object."""
    body = await fetch(DIRECTUS, method="POST", json={"query": query, "variables": variables or {}})
    if not isinstance(body, dict) or body.get("errors"):
        raise ApiError(f"MrBilit's content service rejected the request: {_error_text(body)}")
    return body.get("data") or {}


def toman(rial: Any) -> int | None:
    """Every MrBilit money field is Rial (calendars send strings); 1 Toman = 10 Rial."""
    try:
        value = int(rial)
    except (TypeError, ValueError):
        return None
    return value // 10


def obj(body: Any) -> dict[str, Any]:
    """The body when it is a JSON object, else {} (some endpoints answer `[]` or a string to bad input)."""
    return body if isinstance(body, dict) else {}


def today() -> dt.date:
    """Today in Tehran (UTC+3:30, no DST since 2022)."""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=3, minutes=30))).date()


def not_past(day: dt.date, name: str = "date") -> None:
    """Reject a date before today in Tehran: the API still answers for past dates, with stale prices."""
    if day < today():
        raise ToolError(f"{name} {day} is in the past (today in Tehran is {today()}).")


def _status_message(r: httpx.Response, host: str | None) -> str:
    code = r.status_code
    if code == 403:
        return (
            f"{host} blocked the request (HTTP 403). Wait a few minutes; if it persists, "
            "set MRBILIT_MCP_PROXY to another proxy."
        )
    if code == 429:
        return f"{host} is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if code in (502, 503, 504):
        return f"{host} is temporarily unavailable (HTTP {code}). Try again in a few minutes."
    try:
        detail = _error_text(r.json())
    except ValueError:
        detail = "" if r.text.lstrip().startswith("<") else r.text[:200]  # an HTML error page says nothing useful
    detail = f": {detail}" if detail else ""
    if code >= 500:
        return f"{host} had a server error (HTTP {code}){detail}. Check the ids and dates, or try again later."
    return f"{host} rejected the request (HTTP {code}){detail}"


def _error_text(body: Any) -> str:
    """ASP.NET ProblemDetails, the flight/bus `Message`, masir `UserMessage` or GraphQL `errors`."""
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("message"))[:300]
        if isinstance(errors, dict) and errors:
            return "; ".join(f"{k}: {' '.join(map(str, v))}" for k, v in errors.items())[:300]
        for key in ("UserMessage", "Message", "message", "Error", "title"):
            if body.get(key):
                return str(body[key])[:300]
    return str(body)[:300]
