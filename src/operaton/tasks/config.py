from fastapi import APIRouter
from operaton.tasks.types import ExternalTaskTopic
from pydantic_settings import BaseSettings
from typing import Dict
from typing import Optional
import logging


# https://pydantic-docs.helpmanual.io/usage/settings/
class Settings(BaseSettings):
    ENGINE_REST_BASE_URL: str = "http://localhost:8080/engine-rest"
    ENGINE_REST_AUTHORIZATION: Optional[str] = None

    ENGINE_REST_TIMEOUT_SECONDS: int = 20
    ENGINE_REST_POLL_TTL_SECONDS: int = 10
    ENGINE_REST_LOCK_TTL_SECONDS: int = 30

    TASKS_HEARTBEAT_TOPIC: str = "operaton.tasks.heartbeat"
    TASKS_WORKER_ID: str = "operaton-tasks-client"
    TASKS_MODULE: Optional[str] = None

    LOG_LEVEL: str = "DEBUG"


settings = Settings()

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s:%(lineno)d | %(message)s",
    "%d-%m-%Y %H:%M:%S",
)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
stream_handler.setLevel(settings.LOG_LEVEL)

logger = logging.getLogger("operaton.tasks")
logger.addHandler(stream_handler)
logger.setLevel(settings.LOG_LEVEL)
logger.propagate = False


def update() -> None:
    stream_handler.setLevel(settings.LOG_LEVEL)
    logger.setLevel(settings.LOG_LEVEL)


# Built-in FastAPI router
router = APIRouter()

# All topics registered using the task decorator
handlers: Dict[str, ExternalTaskTopic] = {}


__all__ = ["logger", "settings", "router", "handlers", "update"]
