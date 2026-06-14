from operaton.tasks.config import handlers
from operaton.tasks.deco import task
from operaton.tasks.runtime import ExternalTaskComplete
from operaton.tasks.runtime import NoOp
from operaton.tasks.types import CompleteExternalTaskDto
from operaton.tasks.types import ExternalTaskBpmnError
from operaton.tasks.types import LockedExternalTaskDto
from typing import Any
from typing import Dict


def _task() -> LockedExternalTaskDto:
    return LockedExternalTaskDto.model_construct(
        id="task-id",
        topicName="topic-name",
        workerId="worker-id",
    )


def test_task_decorator_registers_handler_and_local_variables() -> None:
    original_handlers: Dict[str, Any] = dict(handlers)
    handlers.clear()

    try:

        @task("topic-a", localVariables=False)
        async def handler(_: LockedExternalTaskDto) -> ExternalTaskComplete:
            return ExternalTaskComplete(task=_task(), response=NoOp())

        assert handlers["topic-a"].handler is handler
        assert handlers["topic-a"].localVariables is False
    finally:
        handlers.clear()
        handlers.update(original_handlers)


def test_external_task_complete_preserves_noop_response() -> None:
    result = ExternalTaskComplete(task=_task(), response=NoOp())

    assert isinstance(result.response, NoOp)


def test_external_task_complete_preserves_complete_response() -> None:
    response = CompleteExternalTaskDto.model_construct(workerId="worker-id")

    result = ExternalTaskComplete(task=_task(), response=response)

    assert result.response is response


def test_external_task_complete_preserves_bpmn_error_response() -> None:
    response = ExternalTaskBpmnError.model_construct(errorCode="error-code")

    result = ExternalTaskComplete(task=_task(), response=response)

    assert result.response is response


def test_external_task_complete_parses_non_instance_response() -> None:
    result = ExternalTaskComplete(task=_task(), response={"workerId": "worker-id"})

    assert isinstance(result.response, CompleteExternalTaskDto)
    assert result.response.workerId == "worker-id"


def test_legacy_runtime_type_imports_remain_available_from_types_module() -> None:
    from operaton.tasks.runtime import ExternalTaskComplete as RuntimeExternalTaskComplete
    from operaton.tasks.runtime import ExternalTaskFailure as RuntimeExternalTaskFailure
    from operaton.tasks.runtime import ExternalTaskTopic as RuntimeExternalTaskTopic
    from operaton.tasks.runtime import NoOp as RuntimeNoOp
    from operaton.tasks.types import ExternalTaskComplete as TypesExternalTaskComplete
    from operaton.tasks.types import ExternalTaskFailure as TypesExternalTaskFailure
    from operaton.tasks.types import ExternalTaskTopic as TypesExternalTaskTopic
    from operaton.tasks.types import NoOp as TypesNoOp

    assert TypesExternalTaskComplete is RuntimeExternalTaskComplete
    assert TypesExternalTaskFailure is RuntimeExternalTaskFailure
    assert TypesExternalTaskTopic is RuntimeExternalTaskTopic
    assert TypesNoOp is RuntimeNoOp
