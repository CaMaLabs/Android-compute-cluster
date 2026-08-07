from __future__ import annotations

from typing import Any, Callable

TaskHandler = Callable[[dict[str, Any]], Any]
TASKS: dict[str, TaskHandler] = {}


def task(name: str) -> Callable[[TaskHandler], TaskHandler]:
    """Register a locally installed task handler under a stable swarm task name."""
    if not name or len(name) > 200:
        raise ValueError("task name must be 1..200 characters")

    def register(fn: TaskHandler) -> TaskHandler:
        if name in TASKS and TASKS[name] is not fn:
            raise ValueError(f"task already registered: {name}")
        TASKS[name] = fn
        return fn

    return register
