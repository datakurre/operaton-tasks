from operaton.tasks.config import handlers
from operaton.tasks.runtime import ExternalTaskHandler
from operaton.tasks.runtime import ExternalTaskTopic
from typing import Callable


def task(
    topic: str,
    localVariables: bool = True,
) -> Callable[[ExternalTaskHandler], ExternalTaskHandler]:
    """Register function as a service task."""

    def decorator(func: ExternalTaskHandler) -> ExternalTaskHandler:
        handlers[topic] = ExternalTaskTopic(handler=func, localVariables=localVariables)
        return func

    return decorator
