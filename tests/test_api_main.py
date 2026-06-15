from pathlib import Path
from starlette.responses import Response
from typing import Any
from typing import List
from typing import Literal
import asyncio
import builtins
import importlib.util
import logging
import operaton.tasks.api as api_module
import operaton.tasks.main as main_module
import pytest


def _load_module_with_blocked_imports(
    module_name: str,
    file_path: str,
    blocked_imports: List[str],
) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: Any = (),
        level: int = 0,
    ) -> Any:
        if name in blocked_imports:
            raise ImportError(name)
        return original_import(name, globals, locals, fromlist, level)

    builtins.__import__ = fake_import
    try:
        spec.loader.exec_module(module)
    finally:
        builtins.__import__ = original_import

    return module


def test_set_log_level_updates_settings_and_loggers() -> None:
    original_levels = (
        api_module.logger.level,
        api_module.logger_main.level,
        api_module.logger_worker.level,
        api_module.stream_handler.level,
    )

    try:
        api_module.set_log_level("WARNING")

        assert api_module.settings.LOG_LEVEL == "WARNING"
        assert api_module.logger.level == logging.WARNING
        assert api_module.logger_main.level == logging.WARNING
        assert api_module.logger_worker.level == logging.WARNING
        assert api_module.stream_handler.level == logging.WARNING
    finally:
        (
            api_module.logger.level,
            api_module.logger_main.level,
            api_module.logger_worker.level,
            api_module.stream_handler.level,
        ) = original_levels


def test_api_serve_runs_cli_when_available(monkeypatch: Any) -> None:
    calls: List[str] = []

    monkeypatch.setattr(api_module, "HAS_CLI", True)
    monkeypatch.setattr(api_module, "cli", lambda: calls.append("cli"))

    api_module.serve()

    assert calls == ["cli"]


def test_api_module_sets_has_cli_false_when_imports_missing() -> None:
    module = _load_module_with_blocked_imports(
        "operaton.tasks.api_no_cli",
        api_module.__file__ or "",
        ["typer", "uvicorn"],
    )

    assert module.HAS_CLI is False
    assert module.typer is None
    assert module.uvicorn is None


def test_api_serve_exits_without_cli(monkeypatch: Any) -> None:
    calls: List[int] = []

    def fake_exit(code: int) -> None:
        calls.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(api_module, "HAS_CLI", False)
    monkeypatch.setattr(api_module, "exit", fake_exit, raising=False)

    with pytest.raises(SystemExit) as error:
        api_module.serve()

    assert error.value.code == 1
    assert calls == [1]


def test_api_cli_serve_configures_proxy_headers(monkeypatch: Any) -> None:
    callback = api_module.cli.registered_commands[0].callback
    calls: List[str] = []

    monkeypatch.setattr(api_module.uvicorn, "main", lambda: calls.append("uvicorn"))
    monkeypatch.setattr(api_module.sys, "argv", ["operaton-tasks"])

    callback(
        base_url="https://engine.test/rest",
        authorization="Basic token",
        oauth2_client_id="client-id",
        oauth2_client_secret="client-secret",
        oauth2_token_url="https://id.example/token",
        oauth2_scopes="scope-a",
        timeout=12,
        poll_ttl=13,
        lock_ttl=14,
        worker_id="worker-1",
        log_level="INFO",
        args=["--reload"],
    )

    assert calls == ["uvicorn"]
    assert api_module.settings.ENGINE_REST_BASE_URL == "https://engine.test/rest"
    assert api_module.settings.ENGINE_REST_AUTHORIZATION == "Basic token"
    assert api_module.settings.OAUTH2_CLIENT_ID == "client-id"
    assert api_module.settings.OAUTH2_CLIENT_SECRET == "client-secret"
    assert api_module.settings.OAUTH2_TOKEN_URL == "https://id.example/token"
    assert api_module.settings.OAUTH2_SCOPES == "scope-a"
    assert api_module.settings.ENGINE_REST_TIMEOUT_SECONDS == 12
    assert api_module.settings.ENGINE_REST_POLL_TTL_SECONDS == 13
    assert api_module.settings.ENGINE_REST_LOCK_TTL_SECONDS == 14
    assert api_module.settings.TASKS_WORKER_ID == "worker-1"
    assert api_module.settings.TASKS_MODULE is None
    assert api_module.sys.argv == [
        "operaton-tasks",
        "operaton.tasks.main:app",
        "--proxy-headers",
        "--reload",
    ]


def test_api_cli_serve_skips_proxy_header_injection(monkeypatch: Any) -> None:
    callback = api_module.cli.registered_commands[0].callback

    monkeypatch.setattr(api_module.uvicorn, "main", lambda: None)
    monkeypatch.setattr(api_module.sys, "argv", ["operaton-tasks"])

    callback(args=["--no-proxy-headers"])

    assert api_module.sys.argv == [
        "operaton-tasks",
        "operaton.tasks.main:app",
        "--no-proxy-headers",
    ]


def test_api_cli_serve_accepts_no_extra_args(monkeypatch: Any) -> None:
    callback = api_module.cli.registered_commands[0].callback

    monkeypatch.setattr(api_module.uvicorn, "main", lambda: None)
    monkeypatch.setattr(api_module.sys, "argv", ["operaton-tasks"])

    callback(
        base_url=None,
        authorization=None,
        oauth2_client_id=None,
        oauth2_client_secret=None,
        oauth2_token_url=None,
        oauth2_scopes=None,
        timeout=None,
        poll_ttl=None,
        lock_ttl=None,
        worker_id=None,
        log_level=None,
        args=None,
    )

    assert api_module.sys.argv == ["operaton-tasks", "operaton.tasks.main:app"]


class FakeLoader:
    def __init__(self) -> None:
        self.executed_module: Any = None

    def exec_module(self, module: Any) -> None:
        self.executed_module = module


class FakeSpec:
    def __init__(self, loader: Any) -> None:
        self.loader = loader


def test_lifespan_schedules_worker_without_tasks_module(monkeypatch: Any) -> None:
    calls: List[Any] = []

    async def fake_worker(_: Any) -> None:
        return None

    def fake_ensure_future(coro: Any) -> None:
        calls.append(coro)
        coro.close()

    monkeypatch.setattr(main_module.settings, "TASKS_MODULE", None)
    monkeypatch.setattr(main_module, "external_task_worker", fake_worker)
    monkeypatch.setattr(main_module.asyncio, "ensure_future", fake_ensure_future)

    async def run() -> None:
        async with main_module.lifespan(main_module.app):
            return None

    asyncio.run(run())

    assert len(calls) == 1


def test_lifespan_loads_configured_tasks_module(monkeypatch: Any) -> None:
    calls: List[Any] = []
    loader = FakeLoader()
    module = object()

    async def fake_worker(_: Any) -> None:
        return None

    def fake_ensure_future(coro: Any) -> None:
        calls.append(coro)
        coro.close()

    monkeypatch.setattr(main_module.settings, "TASKS_MODULE", "/tmp/tasks.py")
    monkeypatch.setattr(
        main_module.importlib.util,
        "spec_from_file_location",
        lambda module_name, path: FakeSpec(loader),
    )
    monkeypatch.setattr(
        main_module.importlib.util, "module_from_spec", lambda spec: module
    )
    monkeypatch.setattr(main_module, "external_task_worker", fake_worker)
    monkeypatch.setattr(main_module.asyncio, "ensure_future", fake_ensure_future)

    async def run() -> None:
        async with main_module.lifespan(main_module.app):
            return None

    asyncio.run(run())

    assert loader.executed_module is module
    assert len(calls) == 1


def test_lifespan_handles_missing_spec(monkeypatch: Any) -> None:
    calls: List[Any] = []

    async def fake_worker(_: Any) -> None:
        return None

    def fake_ensure_future(coro: Any) -> None:
        calls.append(coro)
        coro.close()

    monkeypatch.setattr(main_module.settings, "TASKS_MODULE", "/tmp/tasks.py")
    monkeypatch.setattr(
        main_module.importlib.util,
        "spec_from_file_location",
        lambda module_name, path: None,
    )
    monkeypatch.setattr(main_module, "external_task_worker", fake_worker)
    monkeypatch.setattr(main_module.asyncio, "ensure_future", fake_ensure_future)

    async def run() -> None:
        async with main_module.lifespan(main_module.app):
            return None

    asyncio.run(run())

    assert len(calls) == 1


def test_lifespan_handles_missing_loader(monkeypatch: Any) -> None:
    calls: List[Any] = []

    async def fake_worker(_: Any) -> None:
        return None

    def fake_ensure_future(coro: Any) -> None:
        calls.append(coro)
        coro.close()

    monkeypatch.setattr(main_module.settings, "TASKS_MODULE", "/tmp/tasks.py")
    monkeypatch.setattr(
        main_module.importlib.util,
        "spec_from_file_location",
        lambda module_name, path: FakeSpec(loader=None),
    )
    monkeypatch.setattr(
        main_module.importlib.util, "module_from_spec", lambda spec: object()
    )
    monkeypatch.setattr(main_module, "external_task_worker", fake_worker)
    monkeypatch.setattr(main_module.asyncio, "ensure_future", fake_ensure_future)

    async def run() -> None:
        async with main_module.lifespan(main_module.app):
            return None

    asyncio.run(run())

    assert len(calls) == 1


def test_cache_headers_sets_no_store_header() -> None:
    async def call_next(_: Any) -> Response:
        return Response()

    async def run() -> Response:
        return await main_module.cache_headers(object(), call_next)

    response = asyncio.run(run())

    assert response.headers["Cache-Control"] == "no-store, max-age=0"


def test_main_cli_serve_sets_proxy_headers(monkeypatch: Any) -> None:
    callback = main_module.cli.registered_commands[0].callback
    calls: List[str] = []

    monkeypatch.setattr(main_module.uvicorn, "main", lambda: calls.append("uvicorn"))
    monkeypatch.setattr(main_module.sys, "argv", ["operaton-tasks"])

    callback(
        module=Path("heartbeat.py"),
        base_url="https://engine.test/rest",
        authorization="Basic token",
        oauth2_client_id="client-id",
        oauth2_client_secret="client-secret",
        oauth2_token_url="https://id.example/token",
        oauth2_scopes="scope-a",
        timeout=21,
        poll_ttl=22,
        lock_ttl=23,
        worker_id="worker-2",
        log_level="DEBUG",
        args=["--workers", "1"],
    )

    assert calls == ["uvicorn"]
    assert main_module.settings.ENGINE_REST_BASE_URL == "https://engine.test/rest"
    assert main_module.settings.ENGINE_REST_AUTHORIZATION == "Basic token"
    assert main_module.settings.OAUTH2_CLIENT_ID == "client-id"
    assert main_module.settings.OAUTH2_CLIENT_SECRET == "client-secret"
    assert main_module.settings.OAUTH2_TOKEN_URL == "https://id.example/token"
    assert main_module.settings.OAUTH2_SCOPES == "scope-a"
    assert main_module.settings.ENGINE_REST_TIMEOUT_SECONDS == 21
    assert main_module.settings.ENGINE_REST_POLL_TTL_SECONDS == 22
    assert main_module.settings.ENGINE_REST_LOCK_TTL_SECONDS == 23
    assert main_module.settings.TASKS_WORKER_ID == "worker-2"
    assert main_module.settings.LOG_LEVEL == "DEBUG"
    assert main_module.settings.TASKS_MODULE == str(Path("heartbeat.py").absolute())
    assert main_module.sys.argv == [
        "operaton-tasks",
        "operaton.tasks.main:app",
        "--proxy-headers",
        "--workers",
        "1",
    ]


def test_main_cli_serve_writes_reload_env_file(monkeypatch: Any) -> None:
    callback = main_module.cli.registered_commands[0].callback
    calls: List[str] = []
    writes: List[str] = []

    class FakeTempFile:
        name = "/tmp/reload.env"

        def __enter__(self) -> "FakeTempFile":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
            return False

        def writelines(self, lines: List[str]) -> None:
            writes.extend(lines)

        def flush(self) -> None:
            return None

    monkeypatch.setattr(main_module.uvicorn, "main", lambda: calls.append("uvicorn"))
    monkeypatch.setattr(main_module.sys, "argv", ["operaton-tasks"])
    monkeypatch.setattr(
        main_module.tempfile,
        "NamedTemporaryFile",
        lambda mode, delete: FakeTempFile(),
    )

    callback(
        module=Path("heartbeat.py"),
        base_url="https://engine.test/rest",
        authorization=None,
        oauth2_client_id=None,
        oauth2_client_secret=None,
        oauth2_token_url=None,
        oauth2_scopes=None,
        timeout=20,
        poll_ttl=10,
        lock_ttl=30,
        worker_id="worker-3",
        log_level="INFO",
        args=["--reload"],
    )

    assert calls == ["uvicorn"]
    assert "ENGINE_REST_BASE_URL=https://engine.test/rest\n" in writes
    assert f"TASKS_MODULE={Path('heartbeat.py').absolute()}\n" in writes
    assert main_module.sys.argv == [
        "operaton-tasks",
        "operaton.tasks.main:app",
        "--proxy-headers",
        "--reload",
        "--env-file",
        "/tmp/reload.env",
    ]


def test_main_cli_serve_accepts_no_extra_args(monkeypatch: Any) -> None:
    callback = main_module.cli.registered_commands[0].callback

    monkeypatch.setattr(main_module.uvicorn, "main", lambda: None)
    monkeypatch.setattr(main_module.sys, "argv", ["operaton-tasks"])

    callback(
        module=Path("heartbeat.py"),
        base_url=None,
        authorization=None,
        oauth2_client_id=None,
        oauth2_client_secret=None,
        oauth2_token_url=None,
        oauth2_scopes=None,
        timeout=None,
        poll_ttl=None,
        lock_ttl=None,
        worker_id=None,
        log_level=None,
        args=None,
    )

    assert main_module.sys.argv == ["operaton-tasks", "operaton.tasks.main:app"]


def test_main_runs_cli_when_available(monkeypatch: Any) -> None:
    calls: List[str] = []

    monkeypatch.setattr(main_module, "HAS_CLI", True)
    monkeypatch.setattr(main_module, "cli", lambda: calls.append("cli"))

    main_module.main()

    assert calls == ["cli"]


def test_main_module_sets_has_cli_false_when_imports_missing() -> None:
    module = _load_module_with_blocked_imports(
        "operaton.tasks.main_no_cli",
        main_module.__file__ or "",
        ["typer", "uvicorn"],
    )

    assert module.HAS_CLI is False
    assert module.typer is None
    assert module.uvicorn is None


def test_main_exits_without_cli(monkeypatch: Any) -> None:
    calls: List[int] = []

    def fake_exit(code: int) -> None:
        calls.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(main_module, "HAS_CLI", False)
    monkeypatch.setattr(main_module, "exit", fake_exit, raising=False)

    with pytest.raises(SystemExit) as error:
        main_module.main()

    assert error.value.code == 1
    assert calls == [1]
