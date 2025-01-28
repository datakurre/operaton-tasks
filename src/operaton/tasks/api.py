from operaton.tasks.config import handlers
from operaton.tasks.config import logger
from operaton.tasks.config import router
from operaton.tasks.config import settings
from operaton.tasks.types import ExternalTaskHandler
from operaton.tasks.types import ExternalTaskTopic
from operaton.tasks.utils import operaton_session
from operaton.tasks.worker import external_task_worker
from typing import Any
from typing import Callable
from typing import Optional
import sys


try:
    import typer
    import uvicorn

    HAS_CLI = True
except ImportError:
    typer: Any = None  # type: ignore
    uvicorn: Any = None  # type: ignore

    HAS_CLI = False


def task(
    topic: str,
    localVariables: bool = True,
) -> Callable[[ExternalTaskHandler], ExternalTaskHandler]:
    """Register function as a service task."""

    def decorator(func: ExternalTaskHandler) -> ExternalTaskHandler:
        handlers[topic] = ExternalTaskTopic(handler=func, localVariables=localVariables)
        return func

    return decorator


if HAS_CLI:
    cli = typer.Typer()

    @cli.command(name="serve")
    def cli_serve(
        base_url: str = "http://localhost:8080/engine-rest",
        authorization: Optional[str] = None,
        timeout: int = 20,
        poll_ttl: int = 10,
        lock_ttl: int = 30,
        worker_id: str = "operaton-tasks-client",
        log_level: str = "INFO",
        args: Optional[list[str]] = typer.Argument(
            default=None, help="arguments passed to uvicorn"
        ),
    ) -> None:
        """CLI."""
        settings.ENGINE_REST_BASE_URL = base_url
        settings.ENGINE_REST_AUTHORIZATION = authorization
        settings.ENGINE_REST_TIMEOUT_SECONDS = timeout
        settings.ENGINE_REST_POLL_TTL_SECONDS = poll_ttl
        settings.ENGINE_REST_LOCK_TTL_SECONDS = lock_ttl
        settings.LOG_LEVEL = log_level
        settings.TASKS_WORKER_ID = worker_id
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
    "task",
]
