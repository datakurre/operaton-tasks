# operaton-tasks

[![GitHub Actions CI](https://github.com/vasara-bpm/operaton-tasks/actions/workflows/ci.yml/badge.svg)](https://github.com/vasara-bpm/operaton-tasks/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-yellow.svg)](https://opensource.org/licenses/Apache-2.0)

External task library and worker for https://operaton.org/

Use this package when you want to implement Operaton external service task workers in Python.

## Installation

Install as a Python dependency:

```bash
pip install operaton-tasks
```

Install with CLI support:

```bash
pip install "operaton-tasks[cli]"
```

## What this package provides

- A decorator API for registering handlers by topic.
- A long-polling worker that fetches, locks, executes, and completes/fails external tasks.
- Optional CLI command to run the worker app.
- Built-in health endpoint at /healthz.

## Authentication

Requests to Operaton REST API can be authenticated in two supported ways.

### Option 1: Static Authorization header

Set ENGINE_REST_AUTHORIZATION to the full header value.

Example:

```bash
export ENGINE_REST_AUTHORIZATION="Basic base64-user-pass"
# or
export ENGINE_REST_AUTHORIZATION="Bearer my-static-token"
```

### Option 2: OAuth2 client credentials

Set the OAuth2 settings:

```bash
export OAUTH2_CLIENT_ID="my-client-id"
export OAUTH2_CLIENT_SECRET="my-client-secret"
export OAUTH2_TOKEN_URL="https://idp.example.com/realms/operaton/protocol/openid-connect/token"
export OAUTH2_SCOPES="scope-a scope-b"  # optional, space-separated
```

When OAuth2 is configured, the worker automatically fetches and refreshes bearer tokens.

### Authentication precedence

Authorization is resolved in this order:

1. Explicit authorization argument passed to session/request helpers.
2. OAuth2 client credentials token (if fully configured).
3. ENGINE_REST_AUTHORIZATION.

With OAuth2 enabled, a 401 response triggers one token invalidation and one retry.

## Configuration

Main environment variables:

- ENGINE_REST_BASE_URL (default: http://localhost:8080/engine-rest)
- ENGINE_REST_AUTHORIZATION (optional)
- OAUTH2_CLIENT_ID (optional)
- OAUTH2_CLIENT_SECRET (optional)
- OAUTH2_TOKEN_URL (optional)
- OAUTH2_SCOPES (optional)
- ENGINE_REST_TIMEOUT_SECONDS (default: 20)
- ENGINE_REST_POLL_TTL_SECONDS (default: 10)
- ENGINE_REST_LOCK_TTL_SECONDS (default: 30)
- TASKS_WORKER_ID (default: operaton-tasks-client)
- TASKS_HEARTBEAT_TOPIC (default: operaton.tasks.heartbeat)
- TASKS_MODULE (used by CLI/app startup to load your handlers module)
- TASKS_LIMIT (default: 0, stop after processing this many tasks)
- TASKS_RUN_TIMEOUT_SECONDS (default: 0, exit after this many seconds)
- LOG_LEVEL (default: DEBUG)

## Usage as standalone worker (CLI)

This is the quickest way to run your handlers as a process.

1. Create a handlers module, for example my_tasks.py.
2. Run operaton-tasks and pass your module path.

Example handler module:

```python
from operaton.tasks.types import CompleteExternalTaskDto
from operaton.tasks.types import ExternalTaskComplete
from operaton.tasks.types import LockedExternalTaskDto
from operaton.tasks.types import VariableValueDto
from operaton.tasks.types import VariableValueType
import operaton.tasks


@operaton.tasks.register("hello-world", localVariables=True)
async def hello_world(task: LockedExternalTaskDto) -> ExternalTaskComplete:
	return ExternalTaskComplete(
		task=task,
		response=CompleteExternalTaskDto(
			workerId=task.workerId,
			localVariables={
				"message": VariableValueDto(
					value="Hello World",
					type=VariableValueType.String,
				),
			},
		),
	)
```

Run it:

```bash
operaton-tasks serve ./my_tasks.py -- --host 0.0.0.0 --port 8000
```

Pass auth options directly on the CLI if preferred:

```bash
operaton-tasks serve ./my_tasks.py \
  --base-url http://localhost:8080/engine-rest \
  --authorization "Basic base64-user-pass"
```

Or with OAuth2:

```bash
operaton-tasks serve ./my_tasks.py \
  --base-url http://localhost:8080/engine-rest \
  --oauth2-client-id my-client-id \
  --oauth2-client-secret my-client-secret \
  --oauth2-token-url https://idp.example.com/realms/operaton/protocol/openid-connect/token \
  --oauth2-scopes "scope-a scope-b"
```

Note: arguments after -- are passed through to uvicorn.

If you want the CLI to process a bounded number of tasks and then exit, pass the
worker controls directly:

```bash
operaton-tasks serve ./my_tasks.py --limit 10 --run-timeout 60
```

When either `--limit` or `--run-timeout` is set, the CLI loads the handler module,
runs the worker in one-shot mode, and exits when the limit or timeout is reached.

## Usage as a library/dependency

Use this mode when you already have your own Python app/runtime and want to embed the worker.

### A minimal worker process without CLI

```python
import asyncio
import operaton.tasks
from operaton.tasks.api import external_task_worker
from operaton.tasks.api import handlers
from operaton.tasks.types import CompleteExternalTaskDto
from operaton.tasks.types import ExternalTaskComplete
from operaton.tasks.types import LockedExternalTaskDto


@operaton.tasks.task("hello-world")
async def hello_world(task: LockedExternalTaskDto) -> ExternalTaskComplete:
	return ExternalTaskComplete(
		task=task,
		response=CompleteExternalTaskDto(workerId=task.workerId),
	)


if __name__ == "__main__":
	asyncio.run(external_task_worker(handlers))
```

Configure auth and connection through environment variables, or set values programmatically through operaton.tasks.settings before starting the worker.

### Embed into an existing FastAPI app

If you want to reuse built-in routes such as /healthz and run the worker in your own FastAPI service:

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from operaton.tasks.api import external_task_worker
from operaton.tasks.api import handlers
from operaton.tasks.api import router


@asynccontextmanager
async def lifespan(app: FastAPI):
	asyncio.create_task(external_task_worker(handlers))
	yield


app = FastAPI(lifespan=lifespan)
app.include_router(router)
```

## Handler contract

- Handlers are async functions registered with @task(topic) or @register(topic).
- Input is LockedExternalTaskDto.
- Return ExternalTaskComplete for success (with CompleteExternalTaskDto or ExternalTaskBpmnError).
- If your handler raises an exception, it is converted to ExternalTaskFailure automatically.

## Health endpoint

GET /healthz is included on the built-in router.

- Without heartbeat activity, it checks Operaton engine reachability.
- With heartbeat activity, it verifies recent heartbeat timestamps.

## Development

This project uses [devenv.sh](https://devenv.sh/) for reproducible development environments with Nix.

### Local Development

Enter the development shell:

```bash
devenv shell
# or
make shell
```

Build and link the virtual environment:

```bash
make env
```

### Running Tests Locally

Run all checks (linting, type checking, unit tests):

```bash
make test
```

Run individual checks:

```bash
make check           # treefmt, flake8, mypy
make test-pytest     # Unit tests only
make devenv-test     # Integration tests with Operaton + Keycloak services
```

Start background services (Operaton + Keycloak) for manual testing:

```bash
make devenv-up       # Start services
make devenv-down     # Stop services
```

Watch mode for development:

```bash
make watch           # Run app in reload mode
make watch-tests     # Continuously run mypy + pytest
```

### CI/CD Pipelines

This project includes GitHub Actions and GitLab CI pipelines configured with `devenv.sh` best practices.

**GitHub Actions** (`.github/workflows/`):
- `ci.yml`: Runs on push, pull requests, and manual dispatch. Executes linting, type checking, and integration tests.
- `release.yml`: Publishes to PyPI on version tags (v*).

**GitLab CI** (`.gitlab-ci.yml`):
- Lint stage: treefmt, flake8, mypy
- Test stage: Unit and integration tests with live Operaton + Keycloak services
- Build & Publish stages: Triggered on tags

Both pipelines use:
- Nix for reproducible environments (via `devenv`)
- Magic Nix Cache (GitHub) or Nix cache directories (GitLab) for faster builds
- `devenv shell -- make <target>` to ensure identical environments between local development and CI

For PyPI publishing, configure:
- **GitHub Actions**: Use [trusted publishers (OIDC)](https://docs.pypi.org/trusted-publishers/) or set `PYPI_API_TOKEN` secret
- **GitLab CI**: Set `PYPI_TOKEN` CI/CD variable with your PyPI token