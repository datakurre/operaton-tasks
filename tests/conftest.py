from operaton.tasks.config import handlers
from operaton.tasks.config import settings
from operaton.tasks.healthz import state
from typing import Any
from typing import Dict
import pytest


SETTINGS_FIELDS = (
    "ENGINE_REST_BASE_URL",
    "ENGINE_REST_AUTHORIZATION",
    "OAUTH2_CLIENT_ID",
    "OAUTH2_CLIENT_SECRET",
    "OAUTH2_TOKEN_URL",
    "OAUTH2_SCOPES",
    "ENGINE_REST_TIMEOUT_SECONDS",
    "ENGINE_REST_POLL_TTL_SECONDS",
    "ENGINE_REST_LOCK_TTL_SECONDS",
    "TASKS_HEARTBEAT_TOPIC",
    "TASKS_WORKER_ID",
    "TASKS_MODULE",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def restore_global_state() -> None:
    original_handlers: Dict[str, Any] = dict(handlers)
    original_settings = {field: getattr(settings, field) for field in SETTINGS_FIELDS}
    original_timestamp = state.timestamp

    yield

    handlers.clear()
    handlers.update(original_handlers)
    for field, value in original_settings.items():
        setattr(settings, field, value)
    state.timestamp = original_timestamp
