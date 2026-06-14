from fastapi import APIRouter
from operaton.tasks.runtime import ExternalTaskTopic
from pydantic import model_validator
from pydantic_settings import BaseSettings
from typing import Dict
from typing import Optional
from typing import Self
import logging


# https://pydantic-docs.helpmanual.io/usage/settings/
class Settings(BaseSettings):
    ENGINE_REST_BASE_URL: str = "http://localhost:8080/engine-rest"
    ENGINE_REST_AUTHORIZATION: Optional[str] = None
    OAUTH2_CLIENT_ID: Optional[str] = None
    OAUTH2_CLIENT_SECRET: Optional[str] = None
    OAUTH2_TOKEN_URL: Optional[str] = None
    OAUTH2_SCOPES: Optional[str] = None

    ENGINE_REST_TIMEOUT_SECONDS: int = 20
    ENGINE_REST_POLL_TTL_SECONDS: int = 10
    ENGINE_REST_LOCK_TTL_SECONDS: int = 30

    TASKS_HEARTBEAT_TOPIC: str = "operaton.tasks.heartbeat"
    TASKS_WORKER_ID: str = "operaton-tasks-client"
    TASKS_MODULE: Optional[str] = None

    LOG_LEVEL: str = "DEBUG"

    @model_validator(mode="after")
    def check_auth_conflict(self) -> Self:
        """Ensure ENGINE_REST_AUTHORIZATION and OAuth2 are not both configured."""
        oauth2_configured = bool(
            self.OAUTH2_CLIENT_ID
            and self.OAUTH2_CLIENT_SECRET
            and self.OAUTH2_TOKEN_URL
        )
        if self.ENGINE_REST_AUTHORIZATION and oauth2_configured:
            raise ValueError(
                "Cannot configure both ENGINE_REST_AUTHORIZATION and OAuth2 "
                "(OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET, OAUTH2_TOKEN_URL). "
                "Use only one authentication method."
            )
        return self


settings = Settings()

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s:%(lineno)d | %(message)s",
    "%d-%m-%Y %H:%M:%S",
)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
stream_handler.setLevel(settings.LOG_LEVEL)

# Built-in FastAPI router
router = APIRouter()

# All topics registered using the task decorator
handlers: Dict[str, ExternalTaskTopic] = {}


__all__ = ["settings", "stream_handler", "router", "handlers"]
