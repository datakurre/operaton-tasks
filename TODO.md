# TODO: Implement OAuth2 Client Credentials Support

## Goal

Add OAuth2 client credentials flow support so the worker can authenticate to Operaton (via Keycloak or any OAuth2 provider) using `OAUTH2_CLIENT_ID` and `OAUTH2_CLIENT_SECRET`, automatically obtaining and refreshing Bearer tokens.

## Current State

- Authentication is a static `ENGINE_REST_AUTHORIZATION` string (e.g. `Basic ...`) set once in `config.py:Settings`.
- `utils.py:operaton_session()` passes it as a header on every request.
- There is no token lifecycle management — the header value never changes at runtime.

## Design

### New Settings (in `config.py`)

Add these optional fields to the `Settings` class:

```python
OAUTH2_CLIENT_ID: Optional[str] = None
OAUTH2_CLIENT_SECRET: Optional[str] = None
OAUTH2_TOKEN_URL: Optional[str] = None  # e.g. "http://localhost:8081/realms/operaton/protocol/openid-connect/token"
OAUTH2_SCOPES: Optional[str] = None     # space-separated scopes, optional
```

**Behaviour rule**: When `OAUTH2_CLIENT_ID` is set, OAuth2 takes precedence over `ENGINE_REST_AUTHORIZATION`.

### New Module: `src/operaton/tasks/oauth2.py`

Create a self-contained OAuth2 token manager:

```python
"""OAuth2 client credentials token manager."""

from operaton.tasks.config import settings
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
        return bool(settings.OAUTH2_CLIENT_ID and settings.OAUTH2_CLIENT_SECRET and settings.OAUTH2_TOKEN_URL)

    async def get_token(self) -> str:
        """Return a valid access token, refreshing if expired or about to expire."""
        async with self._lock:
            if self._access_token and time.time() < self._expires_at - 30:
                return self._access_token
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        """Perform the client_credentials grant to obtain a new token."""
        data = {
            "grant_type": "client_credentials",
            "client_id": settings.OAUTH2_CLIENT_ID,
            "client_secret": settings.OAUTH2_CLIENT_SECRET,
        }
        if settings.OAUTH2_SCOPES:
            data["scope"] = settings.OAUTH2_SCOPES

        async with aiohttp.ClientSession() as session:
            async with session.post(settings.OAUTH2_TOKEN_URL, data=data) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"OAuth2 token request failed ({resp.status}): {body}")
                token_data = await resp.json()

        self._access_token = token_data["access_token"]
        self._expires_at = time.time() + token_data.get("expires_in", 300)
        return self._access_token


token_manager = OAuth2TokenManager()
```

Key design decisions:
- Use an `asyncio.Lock` so concurrent requests don't race to refresh.
- Refresh 30 seconds before expiry to avoid edge-case 401s.
- Module-level singleton `token_manager` — same pattern as `settings`.
- Token URL uses `application/x-www-form-urlencoded` (standard OAuth2 token endpoint format via `data=` not `json=`).

### Modify `utils.py:operaton_session()`

Update the session factory to use OAuth2 when configured:

```python
from operaton.tasks.oauth2 import token_manager

@asynccontextmanager
async def operaton_session(
    authorization: Optional[str] = None,
    headers: Optional[Dict[str, Optional[str]]] = None,
) -> AsyncGenerator[aiohttp.ClientSession, None]:
    """Get aiohttp session with Operaton headers."""

    # Resolve authorization header
    if authorization:
        auth_header = authorization
    elif token_manager.is_configured:
        token = await token_manager.get_token()
        auth_header = f"Bearer {token}"
    else:
        auth_header = settings.ENGINE_REST_AUTHORIZATION

    # Build headers dict (same logic as before, using auth_header)
    ...
```

**Important**: The default parameter `authorization` must change from `settings.ENGINE_REST_AUTHORIZATION` to `None` so the OAuth2 path can activate. Callers that explicitly pass `authorization=` still override.

### Modify CLI (`api.py` and `main.py`)

Add CLI options for the new OAuth2 settings:

```python
@cli.command(name="serve")
def cli_serve(
    ...
    oauth2_client_id: Optional[str] = None,
    oauth2_client_secret: Optional[str] = None,
    oauth2_token_url: Optional[str] = None,
    oauth2_scopes: Optional[str] = None,
    ...
) -> None:
    ...
    settings.OAUTH2_CLIENT_ID = oauth2_client_id
    settings.OAUTH2_CLIENT_SECRET = oauth2_client_secret
    settings.OAUTH2_TOKEN_URL = oauth2_token_url
    settings.OAUTH2_SCOPES = oauth2_scopes
```

Similarly update the `serve` command in `main.py` if it still has its own CLI definition.

### Handle 401 Retry (Optional Enhancement)

In `worker.py`, if a request returns HTTP 401:
1. Invalidate the cached token (`token_manager._expires_at = 0`).
2. Retry the request once with a fresh token.

This can be done in `verify_response_status` or as a wrapper around session requests.

## Implementation Steps

1. **Add settings** — Add `OAUTH2_CLIENT_ID`, `OAUTH2_CLIENT_SECRET`, `OAUTH2_TOKEN_URL`, `OAUTH2_SCOPES` to `Settings` in `config.py`.
2. **Create `oauth2.py`** — Implement `OAuth2TokenManager` class and module-level `token_manager` singleton.
3. **Update `utils.py`** — Change `operaton_session()` to resolve auth dynamically (OAuth2 > static header > none).
4. **Update CLI** — Add OAuth2 options to `api.py` CLI command and wire them to settings.
5. **Add type stubs if needed** — Ensure `mypy --strict` passes.
6. **Test** — Add a unit test mocking the token endpoint; verify the Authorization header is `Bearer <token>`.
7. **Integration test** — With `make devenv-up` (Keycloak on port 8081), configure a client in the `operaton` realm and test the full flow.

## Verification Checklist

- [ ] `make check` passes (black, isort, flake8, mypy --strict)
- [ ] `make test-pytest` passes
- [ ] Worker starts with `OAUTH2_*` env vars and authenticates successfully
- [ ] Worker starts without `OAUTH2_*` env vars and falls back to `ENGINE_REST_AUTHORIZATION`
- [ ] Token auto-refreshes before expiry (verify via logs at DEBUG level)
- [ ] No new dependencies required (`aiohttp` already handles form-encoded POST)

## File Change Summary

| File | Change |
|------|--------|
| `src/operaton/tasks/config.py` | Add 4 new Optional settings fields |
| `src/operaton/tasks/oauth2.py` | **New file** — token manager |
| `src/operaton/tasks/utils.py` | Update `operaton_session()` to use token manager |
| `src/operaton/tasks/api.py` | Add OAuth2 CLI options |
| `src/operaton/tasks/main.py` | Add OAuth2 CLI options (if duplicated) |
| `src/operaton/tasks/__init__.py` | No change needed (oauth2 is internal) |
| `tests/test_oauth2.py` | **New file** — unit tests for token manager |

## Notes

- The Keycloak instance in `devenv.nix` is on port 8081 with realm `operaton` — use this for local testing.
- Token endpoint for local Keycloak: `http://localhost:8081/realms/operaton/protocol/openid-connect/token`
- Follow existing code conventions: one import per line, alphabetical, `from` first, `async def`, strict typing.
- Do NOT add `requests` or `httpx` as dependencies — use `aiohttp` which is already a dependency.
