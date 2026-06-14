# AGENTS.md — Development Guide for AI Agents

## Project Overview

**operaton-tasks** is a Python library and CLI for implementing [Operaton](https://operaton.org/) external service task workers. It provides:

- A decorator-based API (`@task`) for registering async handler functions for BPMN external task topics.
- A long-polling worker that fetches, locks, executes, and completes/fails external tasks against the Operaton REST API.
- A FastAPI application with health check endpoint (`/healthz`) and optional heartbeat BPMN process integration.
- A CLI (`operaton-tasks`) for running the worker with uvicorn.

Current version: `1.0.0a5` (alpha).

## Architecture

```
src/operaton/tasks/
├── __init__.py      # Public API re-exports
├── api.py           # Public API module (serve CLI, set_log_level, re-exports)
├── config.py        # Settings (pydantic-settings), shared state (router, handlers dict)
├── deco.py          # @task decorator — registers handlers into config.handlers
├── healthz.py       # /healthz endpoint + heartbeat task handler
├── main.py          # FastAPI app, lifespan (loads task module, starts worker), CLI
├── types.py         # Pydantic models (generated from OpenAPI + manual additions)
├── utils.py         # HTTP session helper, retry logic, response verification
├── worker.py        # Core async worker loop (fetch-and-lock, execute, complete/fail)
└── py.typed         # PEP 561 marker
```

### Key Data Flow

1. User registers handlers via `@operaton.tasks.task("topic-name")` or `@operaton.tasks.register("topic-name")`.
2. On startup, `main.py` lifespan loads the user's task module (if `TASKS_MODULE` is set) and starts `external_task_worker`.
3. `worker.py` long-polls `/external-task/fetchAndLock`, dispatches tasks to registered handlers as asyncio tasks.
4. Results are reported back via `/external-task/{id}/complete`, `/bpmnError`, or `/failure`.
5. Lock extension runs for pending tasks when new poll results arrive.
6. On disconnect, exponential backoff reconnection is applied.

## Technology Stack

- **Python 3.9+** (targets 3.12 in dev via Nix)
- **FastAPI** — web framework for the health endpoint and optional user routes
- **aiohttp** — async HTTP client for Operaton REST API communication
- **Pydantic v2 + pydantic-settings** — data models and configuration
- **Hatchling** — build backend
- **Optional CLI deps**: uvicorn, typer (in `[cli]` extra)

## Development Environment

This project uses [devenv](https://devenv.sh/) (Nix-based) for reproducible development:

- `devenv shell` or `make shell` — enter the dev shell
- `devenv processes up -d` / `make devenv-up` — start background services (Operaton engine on port 8080, Keycloak on 8081)
- `devenv test` / `make devenv-test` — run integration tests (waits for Operaton on port 8080)
- `devenv update` — update the lock file (NOT `devenv inputs update`)

### Important Nix Notes

- Treefmt config shape: `treefmt.config.programs.<formatter>.enable`
- External modules: `devenv-module-operaton` and `devenv-module-uv2nix` are imported via `devenv.yaml`
- Python interpreter: Python 3.12 via `languages.python.interpreter`
- `rg` (ripgrep) is NOT on PATH; use `grep` instead

## Build & Run Commands

| Command | Purpose |
|---------|---------|
| `make check` | Run black, isort, flake8, mypy (strict) |
| `make format` | Format code via treefmt |
| `make test` | Run all checks + pytest |
| `make test-pytest` | Run pytest only |
| `make watch` | Start app in watch/reload mode |
| `make watch-tests` | Continuously run mypy + pytest |
| `make build` | Build via devenv/Nix |
| `make env` | Create symlinked virtualenv |

### Environment Variables

Configured via pydantic-settings (env vars or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `ENGINE_REST_BASE_URL` | `http://localhost:8080/engine-rest` | Operaton REST API base URL |
| `ENGINE_REST_AUTHORIZATION` | `None` | Authorization header value (e.g. `Basic ...`) |
| `ENGINE_REST_TIMEOUT_SECONDS` | `20` | HTTP request timeout |
| `ENGINE_REST_POLL_TTL_SECONDS` | `10` | Long-poll timeout for fetchAndLock |
| `ENGINE_REST_LOCK_TTL_SECONDS` | `30` | External task lock duration |
| `TASKS_HEARTBEAT_TOPIC` | `operaton.tasks.heartbeat` | Topic for heartbeat process |
| `TASKS_WORKER_ID` | `operaton-tasks-client` | Worker ID sent to Operaton |
| `TASKS_MODULE` | `None` | Path to Python module with task handlers |
| `LOG_LEVEL` | `DEBUG` | Logging level |

## Code Conventions

- **Imports**: One import per line, alphabetical, `from` imports first (`isort` with `force_single_line`).
- **Formatting**: `black` (default settings), line length 200 for isort.
- **Type checking**: `mypy --strict` on all src and tests. The project ships `py.typed`.
- **Async everywhere**: All task handlers are `async def` returning typed result objects.
- **Namespace package**: Uses `src/operaton/tasks/` layout (namespace package `operaton`).

## Testing

- **Unit tests**: `tests/test_smoketest.py` — currently a basic import test.
- **Integration tests**: Run via `devenv test` with a live Operaton instance.
- **Coverage**: `pytest --cov=operaton.tasks tests`, HTML via `make test-coverage`.

## Writing Task Handlers

```python
from operaton.tasks.types import CompleteExternalTaskDto, ExternalTaskComplete, LockedExternalTaskDto, VariableValueDto
import operaton.tasks

@operaton.tasks.task("my-topic")
async def my_handler(task: LockedExternalTaskDto) -> ExternalTaskComplete:
    # Process the task...
    return ExternalTaskComplete(
        task=task,
        response=CompleteExternalTaskDto(
            workerId=task.workerId,
            variables={"result": VariableValueDto(value="done", type="String")},
        ),
    )
```

Handlers must return `ExternalTaskComplete` (with `CompleteExternalTaskDto` or `ExternalTaskBpmnError`) or raise an exception (auto-converted to `ExternalTaskFailure`).

## Types

`src/operaton/tasks/types.py` is **generated** from the Operaton OpenAPI spec via `datamodel-code-generator`. Manual additions (like `VariableValueType` enum and handler type aliases) are at the top, clearly marked. Do not reformat or restructure the generated portion.

## Fixture

`fixture/` contains a Spring Boot application that deploys a BPMN heartbeat process to the local Operaton instance for integration testing and development.

## Project Entry Points

- **Library**: `import operaton.tasks` — use `@operaton.tasks.task(...)` and `operaton.tasks.serve()`
- **CLI**: `operaton-tasks <module.py> [-- uvicorn-args...]` (requires `operaton-tasks[cli]`)
- **Programmatic**: Import and compose the FastAPI `app` from `operaton.tasks.main:app`
