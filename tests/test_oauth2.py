from operaton.tasks.config import settings
from operaton.tasks.config import Settings
from operaton.tasks.oauth2 import OAuth2TokenManager
from operaton.tasks.utils import operaton_session
from operaton.tasks.utils import request_with_auth_retry
from operaton.tasks.utils import resolve_authorization_header
from pydantic import ValidationError
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
import asyncio
import operaton.tasks.oauth2 as oauth2_module
import operaton.tasks.utils as utils_module
import pytest


class FakeTokenResponse:
    def __init__(self, status: int, payload: Dict[str, Any]) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> "FakeTokenResponse":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    async def json(self) -> Dict[str, Any]:
        return self._payload

    async def text(self) -> str:
        return str(self._payload)


class FakeOAuth2ClientSession:
    def __init__(
        self, responses: List[FakeTokenResponse], calls: List[Dict[str, Any]]
    ) -> None:
        self._responses = responses
        self._calls = calls

    async def __aenter__(self) -> "FakeOAuth2ClientSession":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    def post(self, url: str, data: Dict[str, str]) -> FakeTokenResponse:
        self._calls.append({"url": url, "data": data})
        return self._responses.pop(0)


class FakeRequestResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.read_count = 0

    async def read(self) -> bytes:
        self.read_count += 1
        return b""


class FakeRequestSession:
    def __init__(self, statuses: List[int]) -> None:
        self._statuses = statuses
        self.calls: List[Dict[str, Any]] = []

    async def request(
        self, method: str, url: str, headers: Dict[str, str], **kwargs: Any
    ) -> FakeRequestResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "kwargs": kwargs,
            }
        )
        return FakeRequestResponse(self._statuses.pop(0))


class FakeSessionFactory:
    def __init__(self) -> None:
        self.headers: Optional[Dict[str, str]] = None

    def __call__(self, **kwargs: Any) -> "FakeSessionFactory":
        self.headers = kwargs.get("headers")
        return self

    async def __aenter__(self) -> "FakeSessionFactory":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class FakeTokenManager:
    def __init__(self, tokens: List[str], configured: bool = True) -> None:
        self._tokens = tokens
        self.is_configured = configured
        self.invalidations = 0

    async def get_token(self) -> str:
        token = self._tokens.pop(0)
        return token

    def invalidate(self) -> None:
        self.invalidations += 1


def test_oauth2_token_manager_caches_token(monkeypatch: Any) -> None:
    original_client_id = settings.OAUTH2_CLIENT_ID
    original_client_secret = settings.OAUTH2_CLIENT_SECRET
    original_token_url = settings.OAUTH2_TOKEN_URL
    original_scopes = settings.OAUTH2_SCOPES
    try:
        settings.OAUTH2_CLIENT_ID = "client"
        settings.OAUTH2_CLIENT_SECRET = "secret"
        settings.OAUTH2_TOKEN_URL = "https://example.test/token"
        settings.OAUTH2_SCOPES = "scope-a scope-b"

        calls: List[Dict[str, Any]] = []
        responses = [
            FakeTokenResponse(
                200,
                {"access_token": "token-1", "expires_in": 3600},
            )
        ]
        monkeypatch.setattr(
            oauth2_module.aiohttp,
            "ClientSession",
            lambda: FakeOAuth2ClientSession(responses, calls),
        )

        now = [1000.0]
        monkeypatch.setattr(oauth2_module.time, "time", lambda: now[0])

        manager = OAuth2TokenManager()
        token1 = asyncio.run(manager.get_token())
        token2 = asyncio.run(manager.get_token())

        assert token1 == "token-1"
        assert token2 == "token-1"
        assert len(calls) == 1
        assert calls[0]["data"]["scope"] == "scope-a scope-b"
    finally:
        settings.OAUTH2_CLIENT_ID = original_client_id
        settings.OAUTH2_CLIENT_SECRET = original_client_secret
        settings.OAUTH2_TOKEN_URL = original_token_url
        settings.OAUTH2_SCOPES = original_scopes


def test_oauth2_token_manager_refreshes_when_near_expiry(monkeypatch: Any) -> None:
    original_client_id = settings.OAUTH2_CLIENT_ID
    original_client_secret = settings.OAUTH2_CLIENT_SECRET
    original_token_url = settings.OAUTH2_TOKEN_URL
    try:
        settings.OAUTH2_CLIENT_ID = "client"
        settings.OAUTH2_CLIENT_SECRET = "secret"
        settings.OAUTH2_TOKEN_URL = "https://example.test/token"

        calls: List[Dict[str, Any]] = []
        responses = [
            FakeTokenResponse(200, {"access_token": "token-1", "expires_in": 20}),
            FakeTokenResponse(200, {"access_token": "token-2", "expires_in": 20}),
        ]
        monkeypatch.setattr(
            oauth2_module.aiohttp,
            "ClientSession",
            lambda: FakeOAuth2ClientSession(responses, calls),
        )

        now = [1000.0]
        monkeypatch.setattr(oauth2_module.time, "time", lambda: now[0])

        manager = OAuth2TokenManager()
        token1 = asyncio.run(manager.get_token())
        token2 = asyncio.run(manager.get_token())

        assert token1 == "token-1"
        assert token2 == "token-2"
        assert len(calls) == 2
    finally:
        settings.OAUTH2_CLIENT_ID = original_client_id
        settings.OAUTH2_CLIENT_SECRET = original_client_secret
        settings.OAUTH2_TOKEN_URL = original_token_url


def test_operaton_session_uses_oauth2_bearer_header(monkeypatch: Any) -> None:
    fake_token_manager = FakeTokenManager(tokens=["session-token"], configured=True)
    monkeypatch.setattr(utils_module, "token_manager", fake_token_manager)
    fake_factory = FakeSessionFactory()
    monkeypatch.setattr(utils_module.aiohttp, "ClientSession", fake_factory)

    async def run() -> None:
        async with operaton_session():
            return None

    asyncio.run(run())

    assert fake_factory.headers is not None
    assert fake_factory.headers["Authorization"] == "Bearer session-token"


def test_resolve_authorization_header_falls_back_to_static(monkeypatch: Any) -> None:
    original_auth = settings.ENGINE_REST_AUTHORIZATION
    try:
        settings.ENGINE_REST_AUTHORIZATION = "Basic abc123"
        monkeypatch.setattr(
            utils_module,
            "token_manager",
            FakeTokenManager(tokens=[], configured=False),
        )

        async def run() -> str:
            header = await resolve_authorization_header()
            assert header
            return header

        result = asyncio.run(run())
        assert result == "Basic abc123"
    finally:
        settings.ENGINE_REST_AUTHORIZATION = original_auth


def test_request_with_auth_retry_retries_once_on_401(monkeypatch: Any) -> None:
    fake_token_manager = FakeTokenManager(
        tokens=["token-1", "token-2"], configured=True
    )
    monkeypatch.setattr(utils_module, "token_manager", fake_token_manager)
    session = FakeRequestSession(statuses=[401, 200])

    async def run() -> int:
        response = await request_with_auth_retry(
            session,  # type: ignore[arg-type]
            "GET",
            "https://example.test/endpoint",
        )
        return response.status

    status = asyncio.run(run())

    assert status == 200
    assert len(session.calls) == 2
    assert session.calls[0]["headers"]["Authorization"] == "Bearer token-1"
    assert session.calls[1]["headers"]["Authorization"] == "Bearer token-2"
    assert fake_token_manager.invalidations == 1


def test_oauth2_is_not_configured_when_values_are_missing() -> None:
    original_client_id = settings.OAUTH2_CLIENT_ID
    original_client_secret = settings.OAUTH2_CLIENT_SECRET
    original_token_url = settings.OAUTH2_TOKEN_URL
    try:
        settings.OAUTH2_CLIENT_ID = None
        settings.OAUTH2_CLIENT_SECRET = "secret"
        settings.OAUTH2_TOKEN_URL = "https://example.test/token"

        assert OAuth2TokenManager().is_configured is False
    finally:
        settings.OAUTH2_CLIENT_ID = original_client_id
        settings.OAUTH2_CLIENT_SECRET = original_client_secret
        settings.OAUTH2_TOKEN_URL = original_token_url


def test_oauth2_invalidate_resets_expiry() -> None:
    manager = OAuth2TokenManager()
    manager._expires_at = 123.0

    manager.invalidate()

    assert manager._expires_at == 0.0


def test_oauth2_fetch_token_raises_for_incomplete_settings() -> None:
    original_client_id = settings.OAUTH2_CLIENT_ID
    original_client_secret = settings.OAUTH2_CLIENT_SECRET
    original_token_url = settings.OAUTH2_TOKEN_URL
    try:
        settings.OAUTH2_CLIENT_ID = None
        settings.OAUTH2_CLIENT_SECRET = None
        settings.OAUTH2_TOKEN_URL = None

        with pytest.raises(RuntimeError, match="incomplete"):
            asyncio.run(OAuth2TokenManager()._fetch_token())
    finally:
        settings.OAUTH2_CLIENT_ID = original_client_id
        settings.OAUTH2_CLIENT_SECRET = original_client_secret
        settings.OAUTH2_TOKEN_URL = original_token_url


def test_oauth2_fetch_token_raises_on_non_200(monkeypatch: Any) -> None:
    original_client_id = settings.OAUTH2_CLIENT_ID
    original_client_secret = settings.OAUTH2_CLIENT_SECRET
    original_token_url = settings.OAUTH2_TOKEN_URL
    try:
        settings.OAUTH2_CLIENT_ID = "client"
        settings.OAUTH2_CLIENT_SECRET = "secret"
        settings.OAUTH2_TOKEN_URL = "https://example.test/token"

        responses = [FakeTokenResponse(400, {"error": "bad_request"})]
        calls: List[Dict[str, Any]] = []
        monkeypatch.setattr(
            oauth2_module.aiohttp,
            "ClientSession",
            lambda: FakeOAuth2ClientSession(responses, calls),
        )

        with pytest.raises(RuntimeError, match=r"failed \(400\)"):
            asyncio.run(OAuth2TokenManager()._fetch_token())
    finally:
        settings.OAUTH2_CLIENT_ID = original_client_id
        settings.OAUTH2_CLIENT_SECRET = original_client_secret
        settings.OAUTH2_TOKEN_URL = original_token_url


def test_oauth2_fetch_token_raises_without_access_token(monkeypatch: Any) -> None:
    original_client_id = settings.OAUTH2_CLIENT_ID
    original_client_secret = settings.OAUTH2_CLIENT_SECRET
    original_token_url = settings.OAUTH2_TOKEN_URL
    try:
        settings.OAUTH2_CLIENT_ID = "client"
        settings.OAUTH2_CLIENT_SECRET = "secret"
        settings.OAUTH2_TOKEN_URL = "https://example.test/token"

        responses = [FakeTokenResponse(200, {"expires_in": 300})]
        calls: List[Dict[str, Any]] = []
        monkeypatch.setattr(
            oauth2_module.aiohttp,
            "ClientSession",
            lambda: FakeOAuth2ClientSession(responses, calls),
        )

        with pytest.raises(RuntimeError, match="did not contain access_token"):
            asyncio.run(OAuth2TokenManager()._fetch_token())
    finally:
        settings.OAUTH2_CLIENT_ID = original_client_id
        settings.OAUTH2_CLIENT_SECRET = original_client_secret
        settings.OAUTH2_TOKEN_URL = original_token_url


def test_oauth2_fetch_token_raises_on_invalid_expires_in(monkeypatch: Any) -> None:
    original_client_id = settings.OAUTH2_CLIENT_ID
    original_client_secret = settings.OAUTH2_CLIENT_SECRET
    original_token_url = settings.OAUTH2_TOKEN_URL
    try:
        settings.OAUTH2_CLIENT_ID = "client"
        settings.OAUTH2_CLIENT_SECRET = "secret"
        settings.OAUTH2_TOKEN_URL = "https://example.test/token"

        responses = [
            FakeTokenResponse(
                200,
                {"access_token": "token", "expires_in": "not-a-number"},
            )
        ]
        calls: List[Dict[str, Any]] = []
        monkeypatch.setattr(
            oauth2_module.aiohttp,
            "ClientSession",
            lambda: FakeOAuth2ClientSession(responses, calls),
        )

        with pytest.raises(RuntimeError, match="invalid expires_in"):
            asyncio.run(OAuth2TokenManager()._fetch_token())
    finally:
        settings.OAUTH2_CLIENT_ID = original_client_id
        settings.OAUTH2_CLIENT_SECRET = original_client_secret
        settings.OAUTH2_TOKEN_URL = original_token_url


def test_settings_validator_rejects_both_authorization_and_oauth2() -> None:
    """Verify that ENGINE_REST_AUTHORIZATION and OAuth2 cannot be configured together."""
    with pytest.raises(
        ValidationError,
        match="Cannot configure both ENGINE_REST_AUTHORIZATION and OAuth2",
    ):
        Settings.model_validate(
            {
                "ENGINE_REST_AUTHORIZATION": "Basic xyz",
                "OAUTH2_CLIENT_ID": "client",
                "OAUTH2_CLIENT_SECRET": "secret",
                "OAUTH2_TOKEN_URL": "http://example.com/token",
            }
        )


def test_settings_validator_accepts_authorization_alone(monkeypatch: Any) -> None:
    """Verify that ENGINE_REST_AUTHORIZATION alone is accepted."""
    # Clear OAuth2 env vars to simulate only ENGINE_REST_AUTHORIZATION being configured
    monkeypatch.delenv("OAUTH2_CLIENT_ID", raising=False)
    monkeypatch.delenv("OAUTH2_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OAUTH2_TOKEN_URL", raising=False)
    s = Settings(ENGINE_REST_AUTHORIZATION="Basic xyz")
    assert s.ENGINE_REST_AUTHORIZATION == "Basic xyz"


def test_settings_validator_accepts_oauth2_alone(monkeypatch: Any) -> None:
    """Verify that OAuth2 configuration alone is accepted."""
    # Clear ENGINE_REST_AUTHORIZATION but keep OAuth2
    monkeypatch.delenv("ENGINE_REST_AUTHORIZATION", raising=False)
    s = Settings(
        OAUTH2_CLIENT_ID="client",
        OAUTH2_CLIENT_SECRET="secret",
        OAUTH2_TOKEN_URL="http://example.com/token",
    )
    assert s.OAUTH2_CLIENT_ID == "client"
