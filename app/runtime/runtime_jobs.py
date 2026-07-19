from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from app.runtime.runtime_priority import Priority


@dataclass
class RuntimeJob:

    name: str
    priority: Priority
    func: Callable[..., Any]
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    cancelable: bool = True
    scan_id: str | None = None
    job_id: str = field(default_factory=lambda: str(uuid4()))