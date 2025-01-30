from aiohttp import ClientResponse
from contextlib import asynccontextmanager
from fastapi.exceptions import HTTPException
from operaton.tasks.config import settings
from typing import AsyncGenerator
from typing import Dict
from typing import Optional
from typing import Tuple
from urllib.parse import urlparse
from urllib.parse import urlunparse
import aiohttp
import math
import re


@asynccontextmanager
async def operaton_session(
    authorization: Optional[str] = settings.ENGINE_REST_AUTHORIZATION,
    headers: Optional[Dict[str, Optional[str]]] = None,
) -> AsyncGenerator[aiohttp.ClientSession, None]:
    """Get aiohttp session with Operaton headers."""
    headers_: Dict[str, str] = {
        key: value
        for key, value in (
            (
                {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": authorization,
                }
                if authorization
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
