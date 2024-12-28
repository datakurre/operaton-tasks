from operaton.tasks import router
from operaton.tasks import task
from operaton.tasks.api import operaton_session
from operaton.tasks.config import settings
from operaton.tasks.types import CompleteExternalTaskDto
from operaton.tasks.types import ExternalTaskComplete
from operaton.tasks.types import LockedExternalTaskDto
from operaton.tasks.types import VariableValueDto
from operaton.tasks.utils import verify_response_status
from pydantic import BaseModel
from pydantic import Field
from pydantic.dataclasses import dataclass
from starlette.exceptions import HTTPException
from typing import Optional
import datetime


class Heartbeat(BaseModel):
    """Health check response."""

    timestamp: Optional[str] = Field(
        None,
        description="UTC timestamp of the last recorded heartbeat.",
    )


@dataclass
class State:
    """Service health check state."""

    timestamp: Optional[str] = None


state = State()


@task(topic=settings.TASKS_HEARTBEAT_TOPIC)
async def handler(task: LockedExternalTaskDto) -> ExternalTaskComplete:
    """Update health check timestamp."""
    state.timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return ExternalTaskComplete(
        task=task,
        response=CompleteExternalTaskDto(
            workerId=task.workerId,
            variables={
                "timestamp": VariableValueDto(value=state.timestamp, type="string"),
            },
        ),
    )


@router.get(
    "/healthz", response_model=Heartbeat, summary="Service health status", tags=["Meta"]
)
async def healthz() -> Heartbeat:
    """Service health status."""

    # Without heartbeat external task triggered
    if state.timestamp is None:
        async with operaton_session() as session:
            get = await session.get(settings.ENGINE_REST_BASE_URL + "/engine")
            await verify_response_status(get, (200,))
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return Heartbeat(timestamp=timestamp)

    # With heartbeat external task triggered at least once
    now = datetime.datetime.now(datetime.timezone.utc)
    if (now - datetime.timedelta(seconds=45)).isoformat() < state.timestamp:
        return Heartbeat(timestamp=state.timestamp)
    age = (now - datetime.datetime.fromisoformat(state.timestamp)).total_seconds()
    raise HTTPException(status_code=500, detail=f"No heartbeat for {age} seconds")
