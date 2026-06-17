from operaton.tasks.config import handlers
from operaton.tasks.config import router
from operaton.tasks.config import settings
from operaton.tasks.config import stream_handler
from operaton.tasks.deco import task
from operaton.tasks.main import logger as logger_main
from operaton.tasks.utils import operaton_session
from operaton.tasks.worker import external_task_worker
from operaton.tasks.worker import logger as logger_worker
from typing import Any
from typing import Optional
import logging
import sys


try:
    import typer
    import uvicorn

    HAS_CLI = True
except ImportError:
    typer: Any = None  # type: ignore
    uvicorn: Any = None  # type: ignore

    HAS_CLI = False


logger = logging.getLogger(__name__)
logger.addHandler(stream_handler)
logger.setLevel(settings.LOG_LEVEL)


def set_log_level(log_level: str) -> None:
    settings.LOG_LEVEL = log_level
    stream_handler.setLevel(log_level)
    logger.setLevel(log_level)
    logger_main.setLevel(log_level)
    logger_worker.setLevel(log_level)


if HAS_CLI:
    cli = typer.Typer()

    @cli.command(name="serve")
    def cli_serve(
        base_url: Optional[str] = None,
        authorization: Optional[str] = None,
        oauth2_client_id: Optional[str] = None,
        oauth2_client_secret: Optional[str] = None,
        oauth2_token_url: Optional[str] = None,
        oauth2_scopes: Optional[str] = None,
        timeout: Optional[int] = None,
        poll_ttl: Optional[int] = None,
        lock_ttl: Optional[int] = None,
        worker_id: Optional[str] = None,
        log_level: Optional[str] = None,
        args: Optional[list[str]] = typer.Argument(
            default=None, help="arguments passed to uvicorn"
        ),
    ) -> None:
        """CLI."""
        if base_url is not None:
            settings.ENGINE_REST_BASE_URL = base_url
        if authorization is not None:
            settings.ENGINE_REST_AUTHORIZATION = authorization
        if oauth2_client_id is not None:
            settings.OAUTH2_CLIENT_ID = oauth2_client_id
        if oauth2_client_secret is not None:
            settings.OAUTH2_CLIENT_SECRET = oauth2_client_secret
        if oauth2_token_url is not None:
            settings.OAUTH2_TOKEN_URL = oauth2_token_url
        if oauth2_scopes is not None:
            settings.OAUTH2_SCOPES = oauth2_scopes
        if timeout is not None:
            settings.ENGINE_REST_TIMEOUT_SECONDS = timeout
        if poll_ttl is not None:
            settings.ENGINE_REST_POLL_TTL_SECONDS = poll_ttl
        if lock_ttl is not None:
            settings.ENGINE_REST_LOCK_TTL_SECONDS = lock_ttl
        if worker_id is not None:
            settings.TASKS_WORKER_ID = worker_id
        if log_level is not None:
            set_log_level(log_level)
        settings.TASKS_MODULE = None

        sys.argv = [sys.argv[0], "operaton.tasks.main:app"]
        if args and "--no-proxy-headers" not in args:
            sys.argv.append("--proxy-headers")
        if args:
            sys.argv.extend(args)
        uvicorn.main()


def serve() -> None:
    """Run Operaton External Service Task Worker."""
    if HAS_CLI:
        cli()
    else:
        logger.error("operaton-tasks[cli] required")
        exit(1)


__all__ = [
    "external_task_worker",
    "handlers",
    "operaton_session",
    "router",
    "serve",
    "settings",
    "stream_handler",
    "set_log_level",
    "task",
]
