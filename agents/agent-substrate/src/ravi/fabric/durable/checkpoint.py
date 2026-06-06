from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

@dataclass
class Checkpoint:
    run_id: str
    flow_id: str
    step_index: int
    state: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now())
    resumed_at: datetime | None = None
