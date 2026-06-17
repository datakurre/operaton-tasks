from operaton.tasks import healthz as healthz_module
from operaton.tasks.config import settings
from operaton.tasks.healthz import handler
from operaton.tasks.healthz import Heartbeat
from operaton.tasks.types import LockedExternalTaskDto
from starlette.exceptions import HTTPException
from typing import Any
from typing import Dict
import asyncio
import datetime
import pytest


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakeSessionContext:
    async def __aenter__(self) -> "FakeSessionContext":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


def _task() -> LockedExternalTaskDto:
    return LockedExternalTaskDto.model_construct(
        id="task-id",
        topicName="operaton.tasks.heartbeat",
        workerId="worker-id",
    )


def test_handler_updates_timestamp_and_returns_variable() -> None:
    result = asyncio.run(handler(_task()))

    assert healthz_module.state.timestamp is not None
    assert result.response.variables is not None
    assert (
        result.response.variables["timestamp"].value == healthz_module.state.timestamp
    )


def test_healthz_checks_engine_when_no_heartbeat(monkeypatch: Any) -> None:
    calls: Dict[str, str] = {}

    async def fake_request_with_auth_retry(
        session: FakeSessionContext,
        method: str,
        url: str,
    ) -> FakeResponse:
        calls["method"] = method
        calls["url"] = url
        assert isinstance(session, FakeSessionContext)
        return FakeResponse(200)

    async def fake_verify_response_status(
        response: FakeResponse,
        status: tuple[int, ...],
    ) -> FakeResponse:
        assert response.status == 200
        assert status == (200,)
        return response

    monkeypatch.setattr(healthz_module, "operaton_session", FakeSessionContext)
    monkeypatch.setattr(
        healthz_module,
        "request_with_auth_retry",
        fake_request_with_auth_retry,
    )
    monkeypatch.setattr(
        healthz_module,
        "verify_response_status",
        fake_verify_response_status,
    )

    result = asyncio.run(healthz_module.healthz())

    assert isinstance(result, Heartbeat)
    assert result.timestamp is not None
    assert calls == {
        "method": "GET",
        "url": settings.ENGINE_REST_BASE_URL + "/engine",
    }


def test_healthz_returns_timestamp_for_recent_heartbeat() -> None:
    recent_timestamp = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=5)
    ).isoformat()
    healthz_module.state.timestamp = recent_timestamp

    result = asyncio.run(healthz_module.healthz())

    assert result == Heartbeat(timestamp=recent_timestamp)


def test_healthz_raises_for_stale_heartbeat() -> None:
    healthz_module.state.timestamp = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=90)
    ).isoformat()

    with pytest.raises(HTTPException) as error:
        asyncio.run(healthz_module.healthz())

    assert error.value.status_code == 500
    assert "No heartbeat for" in str(error.value.detail)
