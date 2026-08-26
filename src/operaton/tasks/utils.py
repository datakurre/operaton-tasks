from aiohttp import ClientHandlerType
from aiohttp import ClientRequest
from aiohttp import ClientResponse
from aiohttp.abc import AbstractStreamWriter
from aiohttp.payload import BytesPayload
from contextlib import asynccontextmanager
from fastapi.exceptions import HTTPException
from multidict import CIMultiDict
from operaton.tasks.config import settings
from operaton.tasks.oauth2 import token_manager
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Tuple
from typing import cast
from urllib.parse import urlparse
from urllib.parse import urlunparse
import aiohttp
import math
import re


class _RequestBodyBuffer:
    def __init__(self) -> None:
        self.body = bytearray()

    async def write(self, chunk: bytes) -> None:
        self.body.extend(chunk)


async def _buffer_request_body(req: ClientRequest) -> None:
    body = req.body
    if body is None:
        return
    if isinstance(body, bytes):
        req.body = BytesPayload(body)
        return

    # Streaming payloads are consumed by the first request, so retain their
    # bytes before a 401 retry needs to send the body again.
    buffer = _RequestBodyBuffer()
    await body.write(cast(AbstractStreamWriter, buffer))
    req.body = BytesPayload(bytes(buffer.body))


async def resolve_authorization_header(
    authorization: Optional[str] = None,
) -> Optional[str]:
    """Resolve Authorization header value using configured precedence."""
    if authorization:
        return authorization
    if token_manager.is_configured:
        token = await token_manager.get_token()
        return f"Bearer {token}"
    return settings.ENGINE_REST_AUTHORIZATION


async def token_refresh_middleware(
    req: ClientRequest, handler: ClientHandlerType, authorization: Optional[str] = None
) -> ClientResponse:

    request_headers = req.headers or CIMultiDict[str]()
    response: Optional[ClientResponse] = None
    can_retry = (
        authorization is None
        and "Authorization" not in request_headers
        and token_manager.is_configured
    )
    if can_retry:
        # Only OAuth-managed requests retry; explicit credentials retain the
        # original one-shot body and existing no-retry behavior.
        await _buffer_request_body(req)

    for attempt in range(2):  # pragma: no branch
        auth_header = await resolve_authorization_header(authorization)
        headers = request_headers.copy()
        if auth_header and "Authorization" not in headers:
            headers["Authorization"] = auth_header

        req.headers = headers

        response = await handler(req)
        if (
            response.status != 401
            or attempt == 1
            or authorization is not None
            or "Authorization" in request_headers
            or not token_manager.is_configured
        ):
            break
        await response.read()
        token_manager.invalidate()

    assert response is not None
    return response


def token_refresh_middleware_with_auth(
    authorization: Optional[str] = None,
) -> Callable[[ClientRequest, ClientHandlerType], Awaitable[ClientResponse]]:
    return lambda req, handler: token_refresh_middleware(req, handler, authorization)


@asynccontextmanager
async def operaton_session(
    authorization: Optional[str] = None,
    headers: Optional[Dict[str, Optional[str]]] = None,
) -> AsyncGenerator[aiohttp.ClientSession, None]:
    """Get aiohttp session with Operaton headers."""
    # Note: Authorization is intentionally NOT set at the session level.
    # request_with_auth_retry sets it per-request, which avoids duplicate
    # Authorization headers that aiohttp would produce via CIMultiDict.add()
    # when the same key exists in both session and per-request headers.
    headers_: Dict[str, str] = {
        key: value
        for key, value in (
            {"Content-Type": "application/json", "Accept": "application/json"}
            | (headers or {})
        ).items()
        if value
    }
    async with aiohttp.ClientSession(
        headers=headers_,
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=settings.ENGINE_REST_TIMEOUT_SECONDS),
    ) as session:
        yield session


async def request_with_auth_retry(
    http: aiohttp.ClientSession,
    method: str,
    url: str,
    authorization: Optional[str] = None,
    **kwargs: Any,
) -> ClientResponse:
    """Execute request and retry once on 401 when using OAuth2."""
    request_kwargs: Dict[str, Any] = dict(kwargs)
    request_headers = dict(request_kwargs.pop("headers", {}) or {})

    # Keep caller-owned middleware sequences unchanged across repeated calls.
    middlewares = list(request_kwargs.pop("middlewares", ()))
    middlewares.append(token_refresh_middleware_with_auth(authorization))
    response: ClientResponse = await http.request(
        method, url, headers=request_headers, middlewares=middlewares, **request_kwargs
    )

    assert response is not None
    return response


# https://www.desmos.com/calculator/n8c16ahnrx
def next_retry_timeout(
    retry_timeout: int, retry_timeout_max: int, retries: int, retries_max: int
) -> float:
    """Return timout before the next retry."""
    multiplier = (retries_max - retries) / retries_max
    return retry_timeout + (retry_timeout_max - retry_timeout) * (
        2 - math.sin(math.pi * 0.5 * multiplier)
    ) * math.sin(math.pi * 0.5 * multiplier)


async def verify_response_status(
    response: ClientResponse,
    status: Tuple[int, ...] = (200, 201, 204),
    error_status: Optional[int] = None,
) -> ClientResponse:
    """Raise HTTPException for unexpected status codes."""
    if response.status not in status:
        if response.content_type == "application/json":
            error = await response.json()
        else:
            error = await response.text()
        if response.status == 404:
            raise HTTPException(status_code=error_status or 404, detail=error)
        raise HTTPException(status_code=error_status or 500, detail=error)
    return response


def canonical_url(url: str) -> str:
    """Strip unnecessary slashes from url."""
    parts = [x for x in urlparse(url)]
    parts[2] = re.sub("/+", "/", parts[2])
    return f"{urlunparse(parts)}"


__all__ = [
    "aiohttp",
    "resolve_authorization_header",
    "operaton_session",
    "token_refresh_middleware",
    "token_refresh_middleware_with_auth",
    "request_with_auth_retry",
    "token_manager",
    "canonical_url",
]
