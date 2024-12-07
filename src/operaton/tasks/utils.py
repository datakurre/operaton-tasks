from aiohttp import ClientResponse
from fastapi.exceptions import HTTPException
from typing import Optional
from typing import Tuple
from urllib.parse import urlparse
from urllib.parse import urlunparse
import math
import re


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
