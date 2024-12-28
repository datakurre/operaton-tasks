# /// script
# dependencies = [
#   "operaton-tasks@https://github.com/datakurre/operaton-tasks.git",
#   "uvicorn",
#   "typer",
# ]
# ///
from operaton.tasks.types import CompleteExternalTaskDto
from operaton.tasks.types import ExternalTaskComplete
from operaton.tasks.types import LockedExternalTaskDto
from operaton.tasks.types import VariableValueDto
from operaton.tasks.types import VariableValueType
import operaton.tasks


@operaton.tasks.register("hello-world", localVariables=True)
async def handler(task: LockedExternalTaskDto) -> ExternalTaskComplete:
    return ExternalTaskComplete(
        task=task,
        response=CompleteExternalTaskDto(
            workerId=task.workerId,
            localVariables={
                "message": VariableValueDto(
                    value="Hello World", type=VariableValueType.String
                ),
            },
        ),
    )


operaton.tasks.serve()
