"""OAuth2 client credentials token manager."""

from operaton.tasks.config import settings
from typing import Any
from typing import Dict
from typing import Optional
import aiohttp
import asyncio
import time


class OAuth2TokenManager:
    """Manages OAuth2 client credentials token lifecycle."""

    def __init__(self) -> None:
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def is_configured(self) -> bool:
        return bool(
            settings.OAUTH2_CLIENT_ID
            and settings.OAUTH2_CLIENT_SECRET
            and settings.OAUTH2_TOKEN_URL
        )

    async def get_token(self) -> str:
        """Return a valid access token, refreshing if expired or about to expire."""
        async with self._lock:
            if self._access_token and time.time() < self._expires_at - 30:
                return self._access_token
            return await self._fetch_token()

    def invalidate(self) -> None:
        """Invalidate cached token and force refresh on next request."""
        self._expires_at = 0.0

    async def _fetch_token(self) -> str:
        """Perform the client_credentials grant to obtain a new token."""
        token_url = settings.OAUTH2_TOKEN_URL
        client_id = settings.OAUTH2_CLIENT_ID
        client_secret = settings.OAUTH2_CLIENT_SECRET

        if not (token_url and client_id and client_secret):
            raise RuntimeError("OAuth2 client credentials settings are incomplete")

        data: Dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if settings.OAUTH2_SCOPES:
            data["scope"] = settings.OAUTH2_SCOPES

        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=data) as response:
                if response.status != 200:
                    body = await response.text()
                    raise RuntimeError(
                        f"OAuth2 token request failed ({response.status}): {body}"
                    )
                token_data = await response.json()

        access_token = token_data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("OAuth2 token response did not contain access_token")

        expires_in_raw: Any = token_data.get("expires_in", 300)
        try:
            expires_in = float(expires_in_raw)
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                "OAuth2 token response had invalid expires_in"
            ) from error

        self._access_token = access_token
        self._expires_at = time.time() + expires_in
        return access_token


token_manager = OAuth2TokenManager()


__all__ = [
    "aiohttp",
    "time",
    "token_manager",
    "OAuth2TokenManager",
]
