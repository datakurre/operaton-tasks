"""Operaton External Service Task Client"""

from contextlib import asynccontextmanager
from fastapi.applications import FastAPI
from operaton.tasks import healthz
from operaton.tasks.config import handlers
from operaton.tasks.config import router
from operaton.tasks.config import settings
from operaton.tasks.config import update
from operaton.tasks.worker import external_task_worker
from pathlib import Path
from starlette.requests import Request
from starlette.responses import Response
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import Optional
import asyncio
import hashlib
import importlib.util
import logging
import sys
import tempfile


try:
    import typer
    import uvicorn

    HAS_CLI = True
except ImportError:
    typer: Any = None  # type: ignore
    uvicorn: Any = None  # type: ignore

    HAS_CLI = False


assert healthz  # import registers healthz task handler


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[Any, Any]:
    """Start external task worker on FastAPI startup."""
    update()
    if settings.TASKS_MODULE:
        module_name = hashlib.sha256(settings.TASKS_MODULE.encode("utf-8")).hexdigest()
        spec = importlib.util.spec_from_file_location(
            module_name, settings.TASKS_MODULE
        )
        if spec:
            module = importlib.util.module_from_spec(spec)
            if spec.loader:
                spec.loader.exec_module(module)
    asyncio.ensure_future(external_task_worker(handlers))
    logger.info("Event loop: %s", asyncio.get_event_loop())
    yield


app = FastAPI(
    title="Operaton Tasks Client",
    description="Operaton External Service Task Client",
    version="0.1.0",
    lifespan=lifespan,
)


app.include_router(router)


@app.middleware("http")
async def cache_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Set cache headers."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


if HAS_CLI:
    cli = typer.Typer()

    @cli.command()
    def serve(
        module: Path,
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
        settings.TASKS_WORKER_ID = worker_id
        settings.LOG_LEVEL = log_level
        settings.TASKS_MODULE = f"{module.absolute()}"

        sys.argv = [sys.argv[0], "operaton.tasks.main:app"]
        if args and "--no-proxy-headers" not in args:
            sys.argv.append("--proxy-headers")
        if args:
            sys.argv.extend(args)
        if args and "--reload" in args:
            with tempfile.NamedTemporaryFile(mode="w+", delete=True) as temp_file:
                temp_file.writelines(
                    [
                        f"ENGINE_REST_BASE_URL={base_url}\n",
                        f"ENGINE_REST_AUTHORIZATION={authorization}\n",
                        f"ENGINE_REST_TIMEOUT_SECONDS={timeout}\n",
                        f"ENGINE_REST_POLL_TTL_SECONDS={poll_ttl}\n",
                        f"ENGINE_REST_LOCK_TTL_SECONDS={lock_ttl}\n",
                        f"TASKS_WORKER_ID={worker_id}\n",
                        f"TASKS_MODULE={module}\n",
                        f"LOG_LEVEL={log_level}",
                    ]
                )
                temp_file.flush()
                sys.argv.extend(["--env-file", temp_file.name])
                uvicorn.main()
        else:
            uvicorn.main()


def main() -> None:
    """Main."""
    if HAS_CLI:
        cli()
    else:
        logger.error("operaton-tasks[cli] required")
        exit(1)
