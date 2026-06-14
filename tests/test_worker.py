from datetime import datetime
from operaton.tasks.runtime import ExternalTaskComplete
from operaton.tasks.runtime import ExternalTaskFailure
from operaton.tasks.runtime import ExternalTaskTopic
from operaton.tasks.runtime import NoOp
from operaton.tasks.types import CompleteExternalTaskDto
from operaton.tasks.types import ExternalTaskBpmnError
from operaton.tasks.types import ExternalTaskFailureDto
from operaton.tasks.types import LockedExternalTaskDto
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
import asyncio
import operaton.tasks.worker as worker_module
import pytest


class FakeClientResponse:
    def __init__(
        self,
        status: int,
        payload: Optional[Any] = None,
        text_body: str = "",
    ) -> None:
        self.status = status
        self._payload = payload if payload is not None else []
        self._text_body = text_body

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return self._text_body


class FakeTask:
    def __init__(self, name: str, result: Any) -> None:
        self._name = name
        self._result = result

    def done(self) -> bool:
        return True

    def result(self) -> Any:
        return self._result

    def get_name(self) -> str:
        return self._name


class WorkerStop(Exception):
    pass


class DetailedError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class FakeSessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


def _locked_task(
    topic: str = "topic-a", task_id: str = "task-id"
) -> LockedExternalTaskDto:
    return LockedExternalTaskDto.model_construct(
        id=task_id,
        topicName=topic,
        workerId="worker-id",
    )


def _complete_result(
    topic: str = "topic-a", task_id: str = "task-id"
) -> ExternalTaskComplete:
    return ExternalTaskComplete(
        task=_locked_task(topic=topic, task_id=task_id),
        response=CompleteExternalTaskDto.model_construct(workerId="worker-id"),
    )


def _failure_result(
    topic: str = "topic-a",
    task_id: str = "task-id",
    retry_timeout: int = 0,
) -> ExternalTaskFailure:
    return ExternalTaskFailure(
        task=_locked_task(topic=topic, task_id=task_id),
        response=ExternalTaskFailureDto.model_construct(
            workerId="worker-id",
            errorMessage="failed",
            errorDetails="details",
            retries=0,
            retryTimeout=retry_timeout,
        ),
    )


def test_executor_returns_handler_result() -> None:
    expected = _complete_result()

    async def handler(_: LockedExternalTaskDto) -> ExternalTaskComplete:
        return expected

    result = asyncio.run(worker_module.executor(handler, _locked_task()))

    assert result is expected


def test_executor_converts_exception_to_failure() -> None:
    async def handler(_: LockedExternalTaskDto) -> ExternalTaskComplete:
        raise DetailedError("boom")

    result = asyncio.run(worker_module.executor(handler, _locked_task()))

    assert isinstance(result, ExternalTaskFailure)
    assert result.response.errorMessage == "boom"
    assert result.response.errorDetails is not None
    assert "DetailedError" in result.response.errorDetails


def test_complete_task_posts_complete_endpoint(monkeypatch: Any) -> None:
    calls: List[Dict[str, Any]] = []
    result = _complete_result()

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeClientResponse(204)

    monkeypatch.setattr(
        worker_module, "request_with_auth_retry", fake_request_with_auth_retry
    )

    returned = asyncio.run(worker_module.complete_task(object(), result))

    assert returned is result
    assert calls[0]["url"].endswith("/external-task/task-id/complete")


def test_complete_task_uses_bpmn_error_endpoint_for_heartbeat(monkeypatch: Any) -> None:
    calls: List[str] = []
    result = ExternalTaskComplete(
        task=_locked_task(topic="topic.heartbeat", task_id="heartbeat-id"),
        response=ExternalTaskBpmnError.model_construct(errorCode="error-code"),
    )

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        calls.append(url)
        return FakeClientResponse(404)

    monkeypatch.setattr(
        worker_module, "request_with_auth_retry", fake_request_with_auth_retry
    )

    returned = asyncio.run(worker_module.complete_task(object(), result))

    assert returned is result
    assert calls == [
        f"{worker_module.settings.ENGINE_REST_BASE_URL}/external-task/heartbeat-id/bpmnError"
    ]


def test_complete_task_returns_failure_on_unexpected_status(monkeypatch: Any) -> None:
    result = _complete_result()

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        return FakeClientResponse(500, text_body="broken")

    monkeypatch.setattr(
        worker_module, "request_with_auth_retry", fake_request_with_auth_retry
    )

    returned = asyncio.run(worker_module.complete_task(object(), result))

    assert isinstance(returned, ExternalTaskFailure)
    assert returned.response.errorMessage == "Task completion failed"
    assert returned.response.errorDetails == "broken"


def test_extend_lock_posts_only_for_asyncio_tasks(monkeypatch: Any) -> None:
    calls: List[Dict[str, Any]] = []

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeClientResponse(204)

    async def run() -> None:
        task = asyncio.create_task(asyncio.sleep(0), name="topic-a:task-123")
        future = asyncio.get_running_loop().create_future()
        future.set_result(None)
        try:
            monkeypatch.setattr(
                worker_module, "request_with_auth_retry", fake_request_with_auth_retry
            )
            await worker_module.extend_lock(object(), {task, future})
        finally:
            await task

    asyncio.run(run())

    assert len(calls) == 1
    assert calls[0]["url"].endswith("/external-task/task-123/extendLock")
    assert '"newDuration":30000' in calls[0]["kwargs"]["data"]


def test_unlock_all_unlocks_each_task(monkeypatch: Any) -> None:
    calls: List[Dict[str, Any]] = []
    responses = [
        FakeClientResponse(200, payload=[{"id": "task-1"}, {"id": "task-2"}]),
        FakeClientResponse(204),
        FakeClientResponse(204),
    ]

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return responses.pop(0)

    monkeypatch.setattr(
        worker_module, "request_with_auth_retry", fake_request_with_auth_retry
    )

    asyncio.run(worker_module.unlock_all(object()))

    assert calls[0]["method"] == "GET"
    assert calls[1]["url"].endswith("/external-task/task-1/unlock")
    assert calls[2]["url"].endswith("/external-task/task-2/unlock")


def test_fail_task_unlocks_after_terminal_failure(monkeypatch: Any) -> None:
    calls: List[Dict[str, Any]] = []
    result = _failure_result(retry_timeout=0)

    responses = [FakeClientResponse(204), FakeClientResponse(204)]

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return responses.pop(0)

    monkeypatch.setattr(
        worker_module, "request_with_auth_retry", fake_request_with_auth_retry
    )

    returned = asyncio.run(worker_module.fail_task(object(), result))

    assert returned is result
    assert calls[0]["url"].endswith("/external-task/task-id/failure")
    assert calls[1]["url"].endswith("/external-task/task-id/unlock")


def test_fail_task_skips_unlock_on_404(monkeypatch: Any) -> None:
    calls: List[str] = []
    result = _failure_result(retry_timeout=0)

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        calls.append(url)
        return FakeClientResponse(404)

    monkeypatch.setattr(
        worker_module, "request_with_auth_retry", fake_request_with_auth_retry
    )

    asyncio.run(worker_module.fail_task(object(), result))

    assert calls == [
        f"{worker_module.settings.ENGINE_REST_BASE_URL}/external-task/task-id/failure"
    ]


def test_fail_task_logs_error_and_skips_unlock_when_retry_timeout_present(
    monkeypatch: Any,
) -> None:
    calls: List[str] = []
    result = _failure_result(retry_timeout=100)

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        calls.append(url)
        return FakeClientResponse(500, text_body="bad")

    monkeypatch.setattr(
        worker_module, "request_with_auth_retry", fake_request_with_auth_retry
    )

    asyncio.run(worker_module.fail_task(object(), result))

    assert calls == [
        f"{worker_module.settings.ENGINE_REST_BASE_URL}/external-task/task-id/failure"
    ]


def test_poll_topics_builds_fetch_payload() -> None:
    handlers = {
        "topic-a": ExternalTaskTopic(handler=lambda task: None, localVariables=True),
        "topic-b": ExternalTaskTopic(handler=lambda task: None, localVariables=False),
    }

    payload = worker_module.poll_topics(handlers, tasks=3, timeout=4000, lock=5000)

    assert payload.workerId == worker_module.settings.TASKS_WORKER_ID
    assert payload.maxTasks == 3
    assert payload.asyncResponseTimeout == 4000
    assert [topic.topicName for topic in payload.topics or []] == ["topic-a", "topic-b"]
    assert [topic.localVariables for topic in payload.topics or []] == [True, False]


def test_fetch_and_lock_and_complete_processes_complete_failure_and_noop(
    monkeypatch: Any,
) -> None:
    unlock_calls: List[object] = []
    startup_posts: List[Dict[str, Any]] = []
    completed: List[str] = []
    failed: List[str] = []
    created_tasks: Dict[str, FakeTask] = {}
    wait_calls = 0

    async def handler(_: LockedExternalTaskDto) -> ExternalTaskComplete:
        return _complete_result()

    handlers = {"topic-a": ExternalTaskTopic(handler=handler, localVariables=True)}

    async def fake_unlock_all(http: object) -> None:
        unlock_calls.append(http)

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        startup_posts.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeClientResponse(204)

    async def fake_verify_response_status(
        response: FakeClientResponse,
        status: tuple[int, ...],
    ) -> FakeClientResponse:
        assert response.status == 200
        assert status == (200,)
        return response

    async def fake_complete_task(
        http: object, result: ExternalTaskComplete
    ) -> ExternalTaskComplete:
        completed.append(result.task.id or "")
        return result

    async def fake_fail_task(
        http: object, result: ExternalTaskFailure
    ) -> ExternalTaskFailure:
        failed.append(result.task.id or "")
        return result

    def fake_create_task(coro: Any, name: str) -> FakeTask:
        nonlocal created_tasks
        coro.close()
        if name == "fetchAndLock":
            task = FakeTask(
                name,
                FakeClientResponse(
                    200,
                    payload=[
                        {
                            "id": "task-complete",
                            "topicName": "topic-a",
                            "workerId": "worker-id",
                        },
                        {
                            "id": "task-failure",
                            "topicName": "topic-a",
                            "workerId": "worker-id",
                        },
                        {
                            "id": "task-noop",
                            "topicName": "topic-a",
                            "workerId": "worker-id",
                        },
                        {
                            "id": "task-ignored",
                            "topicName": "other-topic",
                            "workerId": "worker-id",
                        },
                    ],
                ),
            )
        elif name == "topic-a:task-complete":
            task = FakeTask(name, _complete_result(task_id="task-complete"))
        elif name == "topic-a:task-failure":
            task = FakeTask(name, _failure_result(task_id="task-failure"))
        else:
            task = FakeTask(
                name,
                ExternalTaskComplete(
                    task=_locked_task(task_id="task-noop"),
                    response=NoOp(),
                ),
            )
        created_tasks[name] = task
        return task

    async def fake_wait(
        pending: Set[Any],
        return_when: Any,
    ) -> tuple[Set[Any], Set[Any]]:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            return {created_tasks["fetchAndLock"]}, set()
        if wait_calls == 2:
            return {
                created_tasks["topic-a:task-complete"],
                created_tasks["topic-a:task-failure"],
                created_tasks["topic-a:task-noop"],
            }, set()
        raise WorkerStop()

    monkeypatch.setattr(worker_module, "unlock_all", fake_unlock_all)
    monkeypatch.setattr(
        worker_module, "request_with_auth_retry", fake_request_with_auth_retry
    )
    monkeypatch.setattr(
        worker_module, "verify_response_status", fake_verify_response_status
    )
    monkeypatch.setattr(worker_module, "complete_task", fake_complete_task)
    monkeypatch.setattr(worker_module, "fail_task", fake_fail_task)
    monkeypatch.setattr(worker_module, "ClientResponse", FakeClientResponse)
    monkeypatch.setattr(worker_module.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(worker_module.asyncio, "wait", fake_wait)

    with pytest.raises(WorkerStop):
        asyncio.run(worker_module.fetch_and_lock_and_complete(object(), handlers))

    assert len(unlock_calls) == 2
    assert startup_posts[0]["url"].endswith("/external-task/fetchAndLock")
    assert completed == ["task-complete"]
    assert failed == ["task-failure"]
    assert "topic-a:task-noop" in created_tasks
    assert "topic-a:task-ignored" not in created_tasks


def test_fetch_and_lock_and_complete_extends_lock_for_pending_tasks(
    monkeypatch: Any,
) -> None:
    extend_calls: List[Set[Any]] = []
    created_tasks: Dict[str, FakeTask] = {}
    pending_task: Optional[asyncio.Task[Any]] = None
    wait_calls = 0

    async def handler(_: LockedExternalTaskDto) -> ExternalTaskComplete:
        return _complete_result()

    handlers = {"topic-a": ExternalTaskTopic(handler=handler, localVariables=True)}
    real_create_task = asyncio.create_task

    async def fake_unlock_all(http: object) -> None:
        return None

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        return FakeClientResponse(204)

    async def fake_verify_response_status(
        response: FakeClientResponse,
        status: tuple[int, ...],
    ) -> FakeClientResponse:
        return response

    async def fake_extend_lock(http: object, pending: Set[Any]) -> None:
        extend_calls.append(pending)

    def fake_create_task(coro: Any, name: str) -> FakeTask:
        coro.close()
        task = FakeTask(name, FakeClientResponse(200, payload=[]))
        created_tasks[name] = task
        return task

    async def fake_wait(
        pending: Set[Any],
        return_when: Any,
    ) -> tuple[Set[Any], Set[Any]]:
        nonlocal pending_task
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            pending_task = real_create_task(asyncio.sleep(0), name="topic-a:pending")
            return {created_tasks["fetchAndLock"]}, {pending_task}
        raise WorkerStop()

    monkeypatch.setattr(worker_module, "unlock_all", fake_unlock_all)
    monkeypatch.setattr(
        worker_module, "request_with_auth_retry", fake_request_with_auth_retry
    )
    monkeypatch.setattr(
        worker_module, "verify_response_status", fake_verify_response_status
    )
    monkeypatch.setattr(worker_module, "extend_lock", fake_extend_lock)
    monkeypatch.setattr(worker_module, "ClientResponse", FakeClientResponse)
    monkeypatch.setattr(worker_module.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(worker_module.asyncio, "wait", fake_wait)

    try:
        with pytest.raises(WorkerStop):
            asyncio.run(worker_module.fetch_and_lock_and_complete(object(), handlers))
    finally:
        if pending_task is not None:
            pending_task.cancel()

    assert len(extend_calls) == 1


def test_fetch_and_lock_and_complete_schedules_heartbeat_tasks(
    monkeypatch: Any,
) -> None:
    created_tasks: Dict[str, FakeTask] = {}
    wait_calls = 0

    async def handler(_: LockedExternalTaskDto) -> ExternalTaskComplete:
        return _complete_result(topic="topic.heartbeat", task_id="task-heartbeat")

    handlers = {
        "topic.heartbeat": ExternalTaskTopic(handler=handler, localVariables=True)
    }

    async def fake_unlock_all(http: object) -> None:
        return None

    async def fake_request_with_auth_retry(
        http: object,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> FakeClientResponse:
        return FakeClientResponse(204)

    async def fake_verify_response_status(
        response: FakeClientResponse,
        status: tuple[int, ...],
    ) -> FakeClientResponse:
        return response

    async def fake_complete_task(
        http: object, result: ExternalTaskComplete
    ) -> ExternalTaskComplete:
        return result

    def fake_create_task(coro: Any, name: str) -> FakeTask:
        nonlocal created_tasks
        coro.close()
        if name == "fetchAndLock":
            task = FakeTask(
                name,
                FakeClientResponse(
                    200,
                    payload=[
                        {
                            "id": "task-heartbeat",
                            "topicName": "topic.heartbeat",
                            "workerId": "worker-id",
                        }
                    ],
                ),
            )
        else:
            task = FakeTask(
                name,
                ExternalTaskComplete(
                    task=_locked_task(
                        topic="topic.heartbeat", task_id="task-heartbeat"
                    ),
                    response=NoOp(),
                ),
            )
        created_tasks[name] = task
        return task

    async def fake_wait(
        pending: Set[Any],
        return_when: Any,
    ) -> tuple[Set[Any], Set[Any]]:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            return {created_tasks["fetchAndLock"]}, set()
        if wait_calls == 2:
            return {created_tasks["topic.heartbeat:task-heartbeat"]}, set()
        raise WorkerStop()

    monkeypatch.setattr(worker_module, "unlock_all", fake_unlock_all)
    monkeypatch.setattr(
        worker_module, "request_with_auth_retry", fake_request_with_auth_retry
    )
    monkeypatch.setattr(
        worker_module, "verify_response_status", fake_verify_response_status
    )
    monkeypatch.setattr(worker_module, "complete_task", fake_complete_task)
    monkeypatch.setattr(worker_module, "ClientResponse", FakeClientResponse)
    monkeypatch.setattr(worker_module.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(worker_module.asyncio, "wait", fake_wait)

    with pytest.raises(WorkerStop):
        asyncio.run(worker_module.fetch_and_lock_and_complete(object(), handlers))

    assert "topic.heartbeat:task-heartbeat" in created_tasks


def test_external_task_worker_retries_with_backoff_after_short_failures(
    monkeypatch: Any,
) -> None:
    sleep_calls: List[float] = []
    fetch_calls = 0
    random_calls = 0
    times = [
        datetime(2024, 1, 1, 0, 0, 0),
        datetime(2024, 1, 1, 0, 0, 5),
        datetime(2024, 1, 1, 0, 0, 5),
        datetime(2024, 1, 1, 0, 0, 10),
    ]

    async def fake_fetch_and_lock_and_complete(
        http: object, handlers: Dict[str, Any]
    ) -> None:
        nonlocal fetch_calls
        fetch_calls += 1
        raise RuntimeError("boom")

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) == 2:
            raise WorkerStop()

    def fake_random() -> float:
        nonlocal random_calls
        random_calls += 1
        return 0.5

    class FakeDateTime:
        @staticmethod
        def utcnow() -> datetime:
            return times.pop(0)

    monkeypatch.setattr(worker_module, "operaton_session", FakeSessionContext)
    monkeypatch.setattr(
        worker_module,
        "fetch_and_lock_and_complete",
        fake_fetch_and_lock_and_complete,
    )
    monkeypatch.setattr(worker_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(worker_module.random, "random", fake_random)
    monkeypatch.setattr(worker_module, "datetime", FakeDateTime)

    with pytest.raises(WorkerStop):
        asyncio.run(worker_module.external_task_worker({}))

    assert fetch_calls == 2
    assert sleep_calls == [0.0, 1.5]
    assert random_calls == 1


def test_external_task_worker_resets_retry_after_long_run(monkeypatch: Any) -> None:
    sleep_calls: List[float] = []
    times = [
        datetime(2024, 1, 1, 0, 0, 0),
        datetime(2024, 1, 1, 0, 1, 1),
    ]

    async def fake_fetch_and_lock_and_complete(
        http: object, handlers: Dict[str, Any]
    ) -> None:
        return None

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        raise WorkerStop()

    class FakeDateTime:
        @staticmethod
        def utcnow() -> datetime:
            return times.pop(0)

    monkeypatch.setattr(worker_module, "operaton_session", FakeSessionContext)
    monkeypatch.setattr(
        worker_module,
        "fetch_and_lock_and_complete",
        fake_fetch_and_lock_and_complete,
    )
    monkeypatch.setattr(worker_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        worker_module.random,
        "random",
        lambda: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    monkeypatch.setattr(worker_module, "datetime", FakeDateTime)

    with pytest.raises(WorkerStop):
        asyncio.run(worker_module.external_task_worker({}))

    assert sleep_calls == [0]


def test_external_task_worker_loops_without_jitter_after_medium_run(
    monkeypatch: Any,
) -> None:
    sleep_calls: List[float] = []
    fetch_calls = 0
    times = [
        datetime(2024, 1, 1, 0, 0, 0),
        datetime(2024, 1, 1, 0, 0, 20),
        datetime(2024, 1, 1, 0, 0, 20),
        datetime(2024, 1, 1, 0, 0, 40),
    ]

    async def fake_fetch_and_lock_and_complete(
        http: object, handlers: Dict[str, Any]
    ) -> None:
        nonlocal fetch_calls
        fetch_calls += 1
        raise RuntimeError("boom")

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) == 2:
            raise WorkerStop()

    class FakeDateTime:
        @staticmethod
        def utcnow() -> datetime:
            return times.pop(0)

    monkeypatch.setattr(worker_module, "operaton_session", FakeSessionContext)
    monkeypatch.setattr(
        worker_module,
        "fetch_and_lock_and_complete",
        fake_fetch_and_lock_and_complete,
    )
    monkeypatch.setattr(worker_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        worker_module.random,
        "random",
        lambda: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    monkeypatch.setattr(worker_module, "datetime", FakeDateTime)

    with pytest.raises(WorkerStop):
        asyncio.run(worker_module.external_task_worker({}))

    assert fetch_calls == 2
    assert sleep_calls == [0.0, 0.0]
