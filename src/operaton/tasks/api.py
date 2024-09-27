from operaton.tasks.config import handlers
from operaton.tasks.config import router
from operaton.tasks.types import ExternalTaskHandler
from operaton.tasks.types import ExternalTaskTopic
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


__all__ = ["router", "task"]
