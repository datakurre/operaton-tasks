from datetime import timedelta

from pydantic import BaseModel
from pydantic import ValidationError
import pytest

from operaton.tasks.types import CaseDefinitionDto
from operaton.tasks.types import DecisionDefinitionDto
from operaton.tasks.types import HistoryTimeToLiveDto
from operaton.tasks.types import LockedExternalTaskDto
from operaton.tasks.types import ProcessDefinitionDto


def test_locked_external_task_724_fields() -> None:
    task = LockedExternalTaskDto.model_validate(
        {
            "id": "external-task",
            "workerId": "worker",
            "topicName": "topic",
            "createTime": "2026-11-04T13:00:00.000+0200",
            "lockExpirationTime": "2026-11-04T13:30:00.000+0200",
            "extensionProperties": {"source": "invoice"},
        }
    )

    assert task.createTime is not None
    assert task.createTime.utcoffset() == timedelta(hours=2)
    assert task.extensionProperties == {"source": "invoice"}


@pytest.mark.parametrize(
    "model",
    [
        CaseDefinitionDto,
        DecisionDefinitionDto,
        HistoryTimeToLiveDto,
        ProcessDefinitionDto,
    ],
)
def test_history_time_to_live_allows_zero_but_not_negative(
    model: type[BaseModel],
) -> None:
    assert (
        model.model_validate({"historyTimeToLive": 0}).model_dump()["historyTimeToLive"]
        == 0
    )
    with pytest.raises(ValidationError):
        model.model_validate({"historyTimeToLive": -1})
