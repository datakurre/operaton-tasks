"""Operaton External Service Task Client"""

from contextlib import asynccontextmanager
from fastapi.applications import FastAPI
from operaton.tasks.config import handlers
from operaton.tasks.config import router
from operaton.tasks.config import settings
from operaton.tasks.config import stream_handler
from operaton.tasks.healthz import healthz  # noqa  # keep import for registration
from operaton.tasks.utils import operaton_session
from operaton.tasks.worker import external_task_worker
from operaton.tasks.worker import fetch_and_lock_and_complete
from operaton.tasks.worker import format_error_message
from operaton.tasks.worker import LimitReached
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


logger = logging.getLogger(__name__)
logger.addHandler(stream_handler)
logger.setLevel(settings.LOG_LEVEL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[Any, Any]:
    """Start external task worker on FastAPI startup."""
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


async def run_worker_once(limit: int = 0, timeout_seconds: int = 0) -> int:
    """Run worker until limit reached or timeout. Returns exit code (0=success, 1=failure)."""
    try:
        async with operaton_session() as http:
            coro = fetch_and_lock_and_complete(http, handlers, limit=limit)
            if timeout_seconds > 0:
                await asyncio.wait_for(coro, timeout=float(timeout_seconds))
            else:
                await coro
    except LimitReached:
        return 0
    except asyncio.TimeoutError:
        return 0 if limit == 0 else 1
    except Exception as e:  # pylint: disable=W0703
        logger.exception("Worker exited with error: %s", format_error_message(e))
        return 1
    return 0


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

    @cli.callback()
    def cli_callback() -> None:
        """Operaton External Service Task Worker."""

    @cli.command()
    def serve(
        module: Path = typer.Argument(..., help="Path to Python module with task handlers"),
        args: Optional[list[str]] = typer.Argument(default=None, help="Arguments passed to uvicorn"),
        base_url: Optional[str] = typer.Option(
            None, help="Engine REST base URL"
        ),
        authorization: Optional[str] = typer.Option(
            None, help="Authorization header value"
        ),
        oauth2_client_id: Optional[str] = typer.Option(
            None, help="OAuth2 client ID"
        ),
        oauth2_client_secret: Optional[str] = typer.Option(
            None, help="OAuth2 client secret"
        ),
        oauth2_token_url: Optional[str] = typer.Option(
            None, help="OAuth2 token URL"
        ),
        oauth2_scopes: Optional[str] = typer.Option(
            None, help="OAuth2 scopes (space-separated)"
        ),
        timeout: Optional[int] = typer.Option(None, help="HTTP request timeout in seconds"),
        poll_ttl: Optional[int] = typer.Option(None, help="Long-poll timeout in seconds"),
        lock_ttl: Optional[int] = typer.Option(None, help="External task lock duration in seconds"),
        worker_id: Optional[str] = typer.Option(
            None, help="Worker ID sent to Operaton"
        ),
        log_level: Optional[str] = typer.Option(None, help="Logging level"),
        limit: Optional[int] = typer.Option(None, help="Max tasks to process before exit (0=unlimited)"),
        run_timeout: Optional[int] = typer.Option(None, help="Exit after N seconds (0=no timeout)"),
    ) -> None:
        """Run External Service Task Worker."""
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
            settings.LOG_LEVEL = log_level
        settings.TASKS_MODULE = f"{module.absolute()}"
        if run_timeout is not None:
            settings.TASKS_RUN_TIMEOUT_SECONDS = run_timeout
        if limit is not None:
            settings.TASKS_LIMIT = limit

        if settings.TASKS_LIMIT > 0 or settings.TASKS_RUN_TIMEOUT_SECONDS > 0:
            _hash = hashlib.sha256(settings.TASKS_MODULE.encode("utf-8")).hexdigest()
            _spec = importlib.util.spec_from_file_location(_hash, settings.TASKS_MODULE)
            if _spec:
                _mod = importlib.util.module_from_spec(_spec)
                if _spec.loader:
                    _spec.loader.exec_module(_mod)
            exit(asyncio.run(run_worker_once(
                limit=settings.TASKS_LIMIT,
                timeout_seconds=settings.TASKS_RUN_TIMEOUT_SECONDS,
            )))

        sys.argv = [sys.argv[0], "operaton.tasks.main:app"]
        if args and "--no-proxy-headers" not in args:
            sys.argv.append("--proxy-headers")
        if args:
            sys.argv.extend(args)
        if args and "--reload" in args:
            with tempfile.NamedTemporaryFile(mode="w+", delete=True) as temp_file:
                temp_file.writelines(
                    [
                        f"ENGINE_REST_BASE_URL={settings.ENGINE_REST_BASE_URL}\n",
                        f"ENGINE_REST_AUTHORIZATION={settings.ENGINE_REST_AUTHORIZATION or ''}\n",
                        f"OAUTH2_CLIENT_ID={settings.OAUTH2_CLIENT_ID or ''}\n",
                        f"OAUTH2_CLIENT_SECRET={settings.OAUTH2_CLIENT_SECRET or ''}\n",
                        f"OAUTH2_TOKEN_URL={settings.OAUTH2_TOKEN_URL or ''}\n",
                        f"OAUTH2_SCOPES={settings.OAUTH2_SCOPES or ''}\n",
                        f"ENGINE_REST_TIMEOUT_SECONDS={settings.ENGINE_REST_TIMEOUT_SECONDS}\n",
                        f"ENGINE_REST_POLL_TTL_SECONDS={settings.ENGINE_REST_POLL_TTL_SECONDS}\n",
                        f"ENGINE_REST_LOCK_TTL_SECONDS={settings.ENGINE_REST_LOCK_TTL_SECONDS}\n",
                        f"TASKS_WORKER_ID={settings.TASKS_WORKER_ID}\n",
                        f"TASKS_MODULE={settings.TASKS_MODULE}\n",
                        f"LOG_LEVEL={settings.LOG_LEVEL}",
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
