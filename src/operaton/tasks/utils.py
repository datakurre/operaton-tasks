from aiohttp import ClientResponse
from contextlib import asynccontextmanager
from fastapi.exceptions import HTTPException
from operaton.tasks.config import settings
from operaton.tasks.oauth2 import token_manager
from typing import Any
from typing import AsyncGenerator
from typing import Dict
from typing import Optional
from typing import Tuple
from urllib.parse import urlparse
from urllib.parse import urlunparse
import aiohttp
import math
import re


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


@asynccontextmanager
async def operaton_session(
    authorization: Optional[str] = None,
    headers: Optional[Dict[str, Optional[str]]] = None,
) -> AsyncGenerator[aiohttp.ClientSession, None]:
    """Get aiohttp session with Operaton headers."""
    auth_header = await resolve_authorization_header(authorization)
    headers_: Dict[str, str] = {
        key: value
        for key, value in (
            (
                {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": auth_header,
                }
                if auth_header
                else {"Content-Type": "application/json", "Accept": "application/json"}
            )
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
    response: Optional[ClientResponse] = None

    for attempt in range(2):  # pragma: no branch
        auth_header = await resolve_authorization_header(authorization)
        headers = dict(request_headers)
        if auth_header and "Authorization" not in headers:
            headers["Authorization"] = auth_header

        response = await http.request(method, url, headers=headers, **request_kwargs)

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
    "request_with_auth_retry",
    "token_manager",
    "canonical_url",
]
