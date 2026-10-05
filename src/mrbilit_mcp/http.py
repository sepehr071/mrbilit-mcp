"""Shared async HTTP client for the MrBilit APIs.

Every tool goes through `fetch` (REST, JSON or text) or `gql` (Directus GraphQL), which cap
concurrency and turn HTTP failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
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
# direct connection to the API origin: always send a browser UA.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
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

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, None for network errors."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit
    _transport, _client, _limit = transport, None, None


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Ignore system proxy settings; route through a proxy only when the user sets MRBILIT_MCP_PROXY.
        _client = httpx.AsyncClient(
            transport=_transport,
            headers=HEADERS,
            timeout=60,  # hotel city search is 1.6 MB, an international round trip takes ~17 s
            follow_redirects=True,
            trust_env=False,
            proxy=os.environ.get("MRBILIT_MCP_PROXY") or None,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


async def fetch(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    method: str = "GET",
    json: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    text: bool = False,
) -> Any:
    """Call an endpoint and return the parsed JSON body (the text with `text=True`, None for 204)."""
    client, limit = _get_client()
    host = urlsplit(url).hostname
    kwargs: dict[str, Any] = {"params": params, "json": json, "headers": headers}
    if timeout:
        kwargs["timeout"] = timeout
    r = None
    # The API origin sometimes resets the TLS handshake; one retry after a short pause fixes it.
    for attempt in range(2):
        try:
            async with limit:
                r = await client.request(method, url, **kwargs)
            break
        except httpx.TimeoutException as e:
            raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
        except httpx.RequestError as e:
            if attempt == 0 and isinstance(e, httpx.ConnectError):
                await asyncio.sleep(1)
                continue
            raise ApiError(
                f"Could not reach {host} ({type(e).__name__}). MrBilit's API resets connections from many networks "
                "outside Iran: set MRBILIT_MCP_PROXY to an HTTP proxy, or check the internet connection."
            ) from e
    assert r is not None

    if r.status_code >= 400:
        raise ApiError(_status_message(r, host), r.status_code)
    if r.status_code == 204:
        return None
    if text:
        return r.text
    try:
        return r.json()
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).") from e


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
