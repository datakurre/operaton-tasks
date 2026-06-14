from operaton.tasks.types import CompleteExternalTaskDto
from operaton.tasks.types import ExternalTaskBpmnError
from operaton.tasks.types import ExternalTaskFailureDto
from operaton.tasks.types import LockedExternalTaskDto
from pydantic import BaseModel
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Union


class NoOp(BaseModel):
    """Do nothing."""


class ExternalTaskComplete(BaseModel):
    """Completed external task and its response."""

    def __init__(self, **data: Any) -> None:
        """Init."""
        super().__init__(**data)
        if any(
            [
                isinstance(data.get("response"), NoOp),
                isinstance(data.get("response"), CompleteExternalTaskDto),
                isinstance(data.get("response"), ExternalTaskBpmnError),
            ]
        ):
            self.response = data["response"]

    task: LockedExternalTaskDto
    response: Union[CompleteExternalTaskDto, ExternalTaskBpmnError, NoOp]


class ExternalTaskFailure(BaseModel):
    """Failed external task and its response."""

    task: LockedExternalTaskDto
    response: ExternalTaskFailureDto


ExternalTaskHandler = Callable[
    [LockedExternalTaskDto],
    Awaitable[Union[ExternalTaskComplete, ExternalTaskFailure]],
]


class ExternalTaskTopic(BaseModel):
    """External task topic configuration"""

    handler: ExternalTaskHandler
    localVariables: bool


__all__ = [
    "ExternalTaskComplete",
    "ExternalTaskFailure",
    "ExternalTaskHandler",
    "ExternalTaskTopic",
    "NoOp",
]
