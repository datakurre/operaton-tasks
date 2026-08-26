from operaton.tasks.config import settings
from operaton.tasks.utils import canonical_url
from operaton.tasks.utils import next_retry_timeout
from operaton.tasks.utils import operaton_session
from operaton.tasks.utils import request_with_auth_retry
from operaton.tasks.utils import resolve_authorization_header
from operaton.tasks.utils import verify_response_status
from starlette.exceptions import HTTPException
from tests.fake_server import FakeRequestResponse
from tests.fake_server import FakeServer
from tests.fake_server import with_read_tracking
from types import SimpleNamespace
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import cast
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


@pytest.mark.asyncio
async def test_request_with_auth_retry_does_not_retry_with_explicit_authorization(
    monkeypatch: Any,
    aiohttp_client: Any,
) -> None:
    monkeypatch.setattr(
        utils_module,
        "token_manager",
        FakeTokenManager(configured=True, tokens=["token-1"]),
    )
    server = FakeServer(statuses=[401])
    session = await aiohttp_client(server.app)

    response = await request_with_auth_retry(
        session,  # type: ignore[arg-type]
        "GET",
        "/endpoint",
        authorization="Bearer explicit",
    )

    assert response.status == 401
    assert len(server.calls) == 1
    assert server.calls[0].headers["Authorization"] == "Bearer explicit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("middlewares", "expected"),
    [
        ([], []),
        ((), ()),
    ],
)
async def test_request_with_auth_retry_does_not_mutate_middlewares(
    monkeypatch: Any,
    middlewares: Any,
    expected: Any,
) -> None:
    monkeypatch.setattr(
        utils_module,
        "token_manager",
        FakeTokenManager(configured=False),
    )
    session = FakeRequestSession([200, 200])

    response = await request_with_auth_retry(
        session,  # type: ignore[arg-type]
        "GET",
        "/endpoint",
        middlewares=middlewares,
    )
    await request_with_auth_retry(
        session,  # type: ignore[arg-type]
        "GET",
        "/endpoint",
        middlewares=middlewares,
    )

    assert response.status == 200
    assert middlewares == expected
    assert len(session.calls[0]["kwargs"]["middlewares"]) == 1
    assert len(session.calls[1]["kwargs"]["middlewares"]) == 1


@pytest.mark.asyncio
async def test_request_with_auth_retry_reuses_buffered_generator_body(
    monkeypatch: Any,
    aiohttp_client: Any,
) -> None:
    fake_token_manager = FakeTokenManager(
        configured=True,
        tokens=["token-1", "token-2"],
    )
    monkeypatch.setattr(utils_module, "token_manager", fake_token_manager)
    server = FakeServer(statuses=[401, 200])
    session = await aiohttp_client(server.app)

    async def body() -> Any:
        yield b"request body"

    response = await request_with_auth_retry(
        session,  # type: ignore[arg-type]
        "POST",
        "/endpoint",
        data=body(),
    )

    assert response.status == 200
    assert server.bodies == [
        b"request body",
        b"request body",
    ]


@pytest.mark.asyncio
async def test_buffer_request_body_reuses_bytes_body() -> None:
    request = SimpleNamespace(body=b"request body")

    await utils_module._buffer_request_body(cast(Any, request))

    assert request.body._value == b"request body"


@pytest.mark.asyncio
async def test_buffer_request_body_ignores_missing_body() -> None:
    request = SimpleNamespace(body=None)

    await utils_module._buffer_request_body(cast(Any, request))

    assert request.body is None


@pytest.mark.asyncio
async def test_request_with_auth_retry_does_not_retry_with_request_header(
    monkeypatch: Any,
    aiohttp_client: Any,
) -> None:
    monkeypatch.setattr(
        utils_module,
        "token_manager",
        FakeTokenManager(configured=True, tokens=["token-1"]),
    )
    server = FakeServer(statuses=[401])
    session = await aiohttp_client(server.app)

    response = await request_with_auth_retry(
        session,  # type: ignore[arg-type]
        "GET",
        "/endpoint",
        headers={"Authorization": "Bearer supplied"},
    )

    assert response.status == 401
    assert len(server.calls) == 1
    assert server.calls[0].headers["Authorization"] == "Bearer supplied"


@pytest.mark.asyncio
async def test_request_with_auth_retry_does_not_retry_when_oauth2_disabled(
    monkeypatch: Any,
    aiohttp_client: Any,
) -> None:
    monkeypatch.setattr(
        utils_module,
        "token_manager",
        FakeTokenManager(configured=False),
    )
    server = FakeServer(statuses=[401])
    session = await aiohttp_client(server.app)

    response = await request_with_auth_retry(
        session,  # type: ignore[arg-type]
        "GET",
        "/endpoint",
    )

    assert response.status == 401
    assert len(server.calls) == 1


@pytest.mark.asyncio
async def test_request_with_auth_retry_returns_second_401_after_retry(
    monkeypatch: Any,
    aiohttp_client: Any,
) -> None:
    fake_token_manager = FakeTokenManager(
        configured=True,
        tokens=["token-1", "token-2"],
    )
    monkeypatch.setattr(utils_module, "token_manager", fake_token_manager)
    server = FakeServer(statuses=[401, 401])
    session = with_read_tracking(await aiohttp_client(server.app))

    response = await request_with_auth_retry(
        session,  # type: ignore[arg-type]
        "GET",
        "/endpoint",
    )

    assert response.status == 401
    assert len(server.calls) == 2
    assert session.responses[0].read_calls == 1
    assert fake_token_manager.invalidations == 1


@pytest.mark.asyncio
async def test_request_with_auth_retry_omits_authorization_when_none_resolved(
    monkeypatch: Any,
    aiohttp_client: Any,
) -> None:
    server = FakeServer(statuses=[200])
    session = await aiohttp_client(server.app)

    async def fake_resolve_authorization_header(
        authorization: Optional[str] = None,
    ) -> Optional[str]:
        return None

    monkeypatch.setattr(
        utils_module,
        "resolve_authorization_header",
        fake_resolve_authorization_header,
    )

    response = await request_with_auth_retry(
        session,  # type: ignore[arg-type]
        "GET",
        "/endpoint",
    )

    assert response.status == 200
    assert server.calls[0].headers.get("Authorization") is None


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
