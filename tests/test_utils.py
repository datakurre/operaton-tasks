from operaton.tasks.config import settings
from operaton.tasks.utils import canonical_url
from operaton.tasks.utils import next_retry_timeout
from operaton.tasks.utils import operaton_session
from operaton.tasks.utils import request_with_auth_retry
from operaton.tasks.utils import resolve_authorization_header
from operaton.tasks.utils import verify_response_status
from starlette.exceptions import HTTPException
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
import asyncio
import operaton.tasks.utils as utils_module
import pytest


class FakeClientSessionFactory:
    def __init__(self) -> None:
        self.kwargs: Optional[Dict[str, Any]] = None

    def __call__(self, **kwargs: Any) -> "FakeClientSessionFactory":
        self.kwargs = kwargs
        return self

    async def __aenter__(self) -> "FakeClientSessionFactory":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class FakeRequestResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.read_calls = 0

    async def read(self) -> bytes:
        self.read_calls += 1
        return b""


class FakeRequestSession:
    def __init__(self, statuses: List[int]) -> None:
        self._statuses = statuses
        self.calls: List[Dict[str, Any]] = []
        self.responses: List[FakeRequestResponse] = []

    async def request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        **kwargs: Any,
    ) -> FakeRequestResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "kwargs": kwargs,
            }
        )
        response = FakeRequestResponse(self._statuses.pop(0))
        self.responses.append(response)
        return response


class FakeResponse:
    def __init__(
        self,
        status: int,
        content_type: str = "text/plain",
        payload: Any = "error",
    ) -> None:
        self.status = status
        self.content_type = content_type
        self._payload = payload

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return str(self._payload)


class FakeTokenManager:
    def __init__(self, configured: bool, tokens: Optional[List[str]] = None) -> None:
        self.is_configured = configured
        self._tokens = tokens or []
        self.invalidations = 0

    async def get_token(self) -> str:
        return self._tokens.pop(0)

    def invalidate(self) -> None:
        self.invalidations += 1


def test_resolve_authorization_header_prefers_explicit_value() -> None:
    async def run() -> str:
        header = await resolve_authorization_header("Bearer explicit")
        assert header is not None
        return header

    assert asyncio.run(run()) == "Bearer explicit"


def test_operaton_session_builds_default_headers_without_auth(monkeypatch: Any) -> None:
    fake_factory = FakeClientSessionFactory()
    monkeypatch.setattr(utils_module.aiohttp, "ClientSession", fake_factory)
    monkeypatch.setattr(
        utils_module,
        "resolve_authorization_header",
        lambda authorization=None: asyncio.sleep(0, result=None),
    )

    async def run() -> None:
        async with operaton_session(headers={"X-Test": "value"}):
            return None

    asyncio.run(run())

    assert fake_factory.kwargs is not None
    assert fake_factory.kwargs["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Test": "value",
    }
    assert fake_factory.kwargs["trust_env"] is True
    assert fake_factory.kwargs["timeout"].total == settings.ENGINE_REST_TIMEOUT_SECONDS


def test_request_with_auth_retry_does_not_retry_with_explicit_authorization(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        utils_module,
        "token_manager",
        FakeTokenManager(configured=True, tokens=["token-1"]),
    )
    session = FakeRequestSession(statuses=[401])

    async def run() -> int:
        response = await request_with_auth_retry(
            session,  # type: ignore[arg-type]
            "GET",
            "https://example.test/endpoint",
            authorization="Bearer explicit",
        )
        return response.status

    assert asyncio.run(run()) == 401
    assert len(session.calls) == 1
    assert session.calls[0]["headers"]["Authorization"] == "Bearer explicit"


def test_request_with_auth_retry_does_not_retry_with_request_header(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        utils_module,
        "token_manager",
        FakeTokenManager(configured=True, tokens=["token-1"]),
    )
    session = FakeRequestSession(statuses=[401])

    async def run() -> int:
        response = await request_with_auth_retry(
            session,  # type: ignore[arg-type]
            "GET",
            "https://example.test/endpoint",
            headers={"Authorization": "Bearer supplied"},
        )
        return response.status

    assert asyncio.run(run()) == 401
    assert len(session.calls) == 1
    assert session.calls[0]["headers"]["Authorization"] == "Bearer supplied"


def test_request_with_auth_retry_does_not_retry_when_oauth2_disabled(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        utils_module,
        "token_manager",
        FakeTokenManager(configured=False),
    )
    session = FakeRequestSession(statuses=[401])

    async def run() -> int:
        response = await request_with_auth_retry(
            session,  # type: ignore[arg-type]
            "GET",
            "https://example.test/endpoint",
        )
        return response.status

    assert asyncio.run(run()) == 401
    assert len(session.calls) == 1


def test_request_with_auth_retry_returns_second_401_after_retry(
    monkeypatch: Any,
) -> None:
    fake_token_manager = FakeTokenManager(
        configured=True,
        tokens=["token-1", "token-2"],
    )
    monkeypatch.setattr(utils_module, "token_manager", fake_token_manager)
    session = FakeRequestSession(statuses=[401, 401])

    async def run() -> FakeRequestResponse:
        return await request_with_auth_retry(
            session,  # type: ignore[arg-type]
            "GET",
            "https://example.test/endpoint",
        )

    response = asyncio.run(run())

    assert response.status == 401
    assert len(session.calls) == 2
    assert session.responses[0].read_calls == 1
    assert fake_token_manager.invalidations == 1


def test_request_with_auth_retry_omits_authorization_when_none_resolved(
    monkeypatch: Any,
) -> None:
    session = FakeRequestSession(statuses=[200])

    async def fake_resolve_authorization_header(
        authorization: Optional[str] = None,
    ) -> Optional[str]:
        return None

    monkeypatch.setattr(
        utils_module,
        "resolve_authorization_header",
        fake_resolve_authorization_header,
    )

    async def run() -> int:
        response = await request_with_auth_retry(
            session,  # type: ignore[arg-type]
            "GET",
            "https://example.test/endpoint",
        )
        return response.status

    assert asyncio.run(run()) == 200
    assert session.calls[0]["headers"] == {}


def test_next_retry_timeout_stays_within_bounds() -> None:
    initial = next_retry_timeout(1, 60, 5, 5)
    later = next_retry_timeout(1, 60, 0, 5)

    assert initial == pytest.approx(1.0)
    assert 1 < later <= 60


def test_verify_response_status_accepts_expected_status() -> None:
    response = FakeResponse(status=204)

    async def run() -> FakeResponse:
        return await verify_response_status(response, status=(204,))

    assert asyncio.run(run()) is response


def test_verify_response_status_raises_not_found_with_json_payload() -> None:
    response = FakeResponse(
        status=404, content_type="application/json", payload={"message": "missing"}
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(verify_response_status(response, error_status=418))

    assert error.value.status_code == 418
    assert error.value.detail == {"message": "missing"}


def test_verify_response_status_raises_server_error_with_text_payload() -> None:
    response = FakeResponse(status=500, payload="broken")

    with pytest.raises(HTTPException) as error:
        asyncio.run(verify_response_status(response))

    assert error.value.status_code == 500
    assert error.value.detail == "broken"


def test_canonical_url_collapses_duplicate_path_slashes() -> None:
    assert canonical_url("https://example.test//engine-rest///external-task") == (
        "https://example.test/engine-rest/external-task"
    )
