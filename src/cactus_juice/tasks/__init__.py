"""Continuously running worker tasks.

These are the tasks runnable via `juice task <name>` (see cactus_juice.cli). They are intended to be run as
long-lived processes (eg a dedicated container/pod) as opposed to the request/response FastAPI app."""

from collections.abc import Callable, Coroutine
from typing import Any

from cactus_juice.settings import CactusJuiceSettings
from cactus_juice.tasks.csipausclient import run_csipaus_client_task

TaskFn = Callable[[CactusJuiceSettings], Coroutine[Any, Any, None]]

# Registry of all tasks runnable via `juice task <name>`.
TASKS: dict[str, TaskFn] = {
    "csipausclient": run_csipaus_client_task,
}
