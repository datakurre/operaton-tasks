from aiohttp import ClientResponse
from aiohttp import ClientSession
from asyncio import FIRST_COMPLETED
from asyncio import Future
from asyncio import Lock
from datetime import datetime
from operaton.tasks.config import settings
from operaton.tasks.types import ExtendLockOnExternalTaskDto
from operaton.tasks.types import ExternalTaskBpmnError
from operaton.tasks.types import ExternalTaskComplete
from operaton.tasks.types import ExternalTaskFailure
from operaton.tasks.types import ExternalTaskFailureDto
from operaton.tasks.types import ExternalTaskHandler
from operaton.tasks.types import ExternalTaskTopic
from operaton.tasks.types import FetchExternalTasksDto
from operaton.tasks.types import FetchExternalTaskTopicDto
from operaton.tasks.types import LockedExternalTaskDto
from operaton.tasks.types import NoOp
from operaton.tasks.utils import operaton_session
from operaton.tasks.utils import verify_response_status
from typing import Dict
from typing import Set
from typing import Union
import asyncio
import logging
import random
import traceback


logger = logging.getLogger(__name__)


async def executor(
    handler: ExternalTaskHandler, task: LockedExternalTaskDto
) -> Union[ExternalTaskComplete, ExternalTaskFailure]:
    """Execute task handler and convert exception into external task failure."""
    # noinspection PyBroadException
    try:
        return await handler(task)
    except Exception as e:  # pylint: disable=W0703
        logger.exception("Unexpected error: %s.", getattr(e, "detail", str(e)))
        response = ExternalTaskFailureDto(
            workerId=task.workerId,
            errorMessage=f'{getattr(e, "detail", str(e))}',
            errorDetails=traceback.format_exc(),
            retries=0,
            retryTimeout=0,
        )
        return ExternalTaskFailure(task=task, response=response)


MUTEX = Lock()


async def complete_task(
    http: ClientSession,
    result: ExternalTaskComplete,
) -> Union[ExternalTaskComplete, ExternalTaskFailure]:
    """Report external task as complete or as BPMN error."""
    assert result.task.topicName, "External task is missing 'topicName'."

    if not result.task.topicName.endswith(".heartbeat"):
        logger.info("Completing %s:%s.", result.task.topicName, result.task.id)

    if isinstance(result.response, ExternalTaskBpmnError):
        url = (
            f"{settings.ENGINE_REST_BASE_URL}/external-task/{result.task.id}/bpmnError"
        )
    else:
        url = f"{settings.ENGINE_REST_BASE_URL}/external-task/{result.task.id}/complete"

    async with MUTEX:
        response = await http.post(url, data=result.response.model_dump_json())

    if response.status not in [204, 404]:
        msg = await response.text()
        logger.error("Task completion failed: %s.", msg)
        return ExternalTaskFailure(
            task=result.task,
            response=ExternalTaskFailureDto(
                workerId=result.task.workerId,
                errorMessage="Task completion failed",
                errorDetails=msg,
                retries=0,
                retryTimeout=0,
            ),
        )

    if not result.task.topicName.endswith("heartbeat"):
        logger.debug("Completed %s.", response)

    return result


async def extend_lock(
    http: ClientSession,
    pending: Set[
        Union[
            Future[ClientResponse],
            Future[ExternalTaskComplete],
            Future[ExternalTaskFailure],
            asyncio.Task[
                Union[ClientResponse, ExternalTaskComplete, ExternalTaskFailure]
            ],
        ],
    ] = set(),
) -> None:
    """Extend external task worker lock."""
    for task in [t for t in pending if isinstance(t, asyncio.Task)]:
        task_id = task.get_name().rsplit(":", 1)[-1]
        url = f"{settings.ENGINE_REST_BASE_URL}/external-task/{task_id}/extendLock"
        await http.post(
            url,
            data=ExtendLockOnExternalTaskDto(
                workerId=settings.TASKS_WORKER_ID,
                newDuration=settings.ENGINE_REST_LOCK_TTL_SECONDS * 1000,
            ).model_dump_json(),
        )


async def unlock_all(http: ClientSession) -> None:
    """Unlock all external tasks owned by this worker."""
    url = f"{settings.ENGINE_REST_BASE_URL}/external-task"
    params = {"workerId": settings.TASKS_WORKER_ID}
    response = await (await http.get(url, params=params)).json()
    for task in response:
        url = f"{settings.ENGINE_REST_BASE_URL}/external-task/{task['id']}/unlock"
        await http.post(url)


async def fail_task(
    http: ClientSession,
    result: ExternalTaskFailure,
) -> ExternalTaskFailure:
    """Report external task as failure."""
    logger.warning("Failing %s:%s.", result.task.topicName, result.task.id)

    url = f"{settings.ENGINE_REST_BASE_URL}/external-task/{result.task.id}/failure"

    async with MUTEX:
        response = await http.post(url, data=result.response.model_dump_json())
    if response.status not in [204, 404]:
        logger.error("Unexpected error: %s", await response.text())

    if response.status not in [404] and not result.response.retryTimeout:
        url = f"{settings.ENGINE_REST_BASE_URL}/external-task/{result.task.id}/unlock"
        await http.post(url)

    logger.debug("Failed %s.", result.response)

    return result


def poll_topics(
    handlers: Dict[str, ExternalTaskTopic],
    tasks: int = 10,
    timeout: int = settings.ENGINE_REST_POLL_TTL_SECONDS * 1000,
    lock: int = settings.ENGINE_REST_LOCK_TTL_SECONDS * 1000,
) -> FetchExternalTasksDto:
    """Get external task query payload."""
    return FetchExternalTasksDto(
        workerId=settings.TASKS_WORKER_ID,
        maxTasks=tasks,
        asyncResponseTimeout=timeout,
        topics=[
            FetchExternalTaskTopicDto(
                topicName=topic,
                lockDuration=lock,
                localVariables=handlers[topic].localVariables,
            )
            for topic in handlers
        ],
    )


async def fetch_and_lock_and_complete(
    http: ClientSession,
    handlers: Dict[str, ExternalTaskTopic],
) -> None:
    """Poll and process external task until connection fails."""

    poll_url = f"{settings.ENGINE_REST_BASE_URL}/external-task/fetchAndLock"
    poll_task = None

    # Reset locks for current worker and workaround strange Operaton 7.14(?) long poll
    # issue where first poll was lost until the first lock timeout was reached
    await unlock_all(http)
    await http.post(poll_url, data=poll_topics(handlers, 1000, 0, 1).model_dump_json())
    await unlock_all(http)

    pending: Set[
        Union[
            Future[ClientResponse],
            Future[ExternalTaskComplete],
            Future[ExternalTaskFailure],
            asyncio.Task[
                Union[ClientResponse, ExternalTaskComplete, ExternalTaskFailure]
            ],
        ],
    ] = set()

    while True:
        logger.debug(
            "Waiting for %s pending asyncio task%s: %s.",
            len(pending),
            "s" if len(pending) > 1 else "",
            [getattr(t, "get_name", lambda: "n/a")() for t in pending],
        )

        poll_task = (
            asyncio.create_task(
                http.post(poll_url, data=poll_topics(handlers).model_dump_json()),
                name="fetchAndLock",
            )
            if poll_task is None or poll_task.done()
            else poll_task
        )
        done, pending = await asyncio.wait(
            pending | {poll_task}, return_when=FIRST_COMPLETED
        )
        if pending and len(done) == 1 and poll_task.done():
            await extend_lock(http, pending)
        for future in done:
            result: Union[ClientResponse, ExternalTaskComplete, ExternalTaskFailure] = (
                future.result()
            )

            if isinstance(result, ClientResponse):
                await verify_response_status(result, status=(200,))
                tasks = [
                    LockedExternalTaskDto(**x)
                    for x in await result.json()
                    if x.get("topicName") in handlers
                ]
                for task in tasks:
                    assert task.topicName, "External task is missing 'topicName'."
                    if not task.topicName.endswith(".heartbeat"):
                        logger.info("Scheduling %s:%s.", task.topicName, task.id)
                    topic_name = task.topicName
                    pending = pending | {
                        asyncio.create_task(
                            executor(handlers[topic_name].handler, task),
                            name=f"{task.topicName}:{task.id}",
                        )
                    }

            if isinstance(result, ExternalTaskComplete):
                if not isinstance(result.response, NoOp):
                    result = await complete_task(http, result)

            if isinstance(result, ExternalTaskFailure):
                result = await fail_task(http, result)


async def external_task_worker(
    handlers: Dict[str, ExternalTaskTopic],
) -> None:
    """Reconnecting external task worker."""
    retry_in_seconds = 0.0
    logger.info("External task worker started.")
    while True:
        restart_dt = datetime.utcnow()
        # noinspection PyBroadException
        try:
            async with operaton_session() as http:
                await fetch_and_lock_and_complete(http, handlers)
        except Exception as e:  # pylint: disable=W0703
            logger.exception(
                "External task worker disconnected: %s", getattr(e, "detail", str(e))
            )

        finally:
            exception_dt = datetime.utcnow()
            if (exception_dt - restart_dt).total_seconds() > 60:
                retry_in_seconds = 0
            logger.warning(
                "External task worker reconnecting in %s seconds.", retry_in_seconds
            )
            await asyncio.sleep(retry_in_seconds)
            if (exception_dt - restart_dt).total_seconds() < 10:
                retry_in_seconds = min(
                    (max(retry_in_seconds, 1)) * (1.0 + random.random()), 60
                )
