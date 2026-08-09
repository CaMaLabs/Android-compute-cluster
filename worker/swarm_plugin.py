from __future__ import annotations

from typing import Any, Callable

TaskHandler = Callable[[dict[str, Any]], Any]
TASKS: dict[str, TaskHandler] = {}
CAPABILITIES: set[str] = set()


def _validate_name(name: str, label: str) -> str:
    value = str(name).strip()
    if not value or len(value) > 200:
        raise ValueError(f"{label} must be 1..200 characters")
    return value


def advertise(name: str) -> None:
    """Advertise a capability provided by locally installed worker code/hardware."""
    CAPABILITIES.add(_validate_name(name, "capability name"))


def task(name: str) -> Callable[[TaskHandler], TaskHandler]:
    """Register a locally installed task handler under a stable swarm task name."""
    task_name = _validate_name(name, "task name")

    def register(fn: TaskHandler) -> TaskHandler:
        if task_name in TASKS and TASKS[task_name] is not fn:
            raise ValueError(f"task already registered: {task_name}")
        TASKS[task_name] = fn
        return fn

    return register
