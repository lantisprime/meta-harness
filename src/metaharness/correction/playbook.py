"""ACE-style playbook: durable, curated advice that survives across runs.

The ACE lesson (Agentic Context Engineering): don't rewrite the whole context on
every failure — *brevity bias* erodes hard-won detail. Instead keep a playbook of
small bullets and apply deltas: add, amend, deprecate. Bullets carry effectiveness
counters so the slow loop can retire advice that doesn't pay its way.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from metaharness.core.types import Task, TaskType


class PlaybookBullet(BaseModel):
    id: str = Field(default_factory=lambda: f"pb_{uuid.uuid4().hex[:10]}")
    text: str
    task_type: Optional[TaskType] = None    # None = applies to every task type
    helpful: int = 0
    harmful: int = 0
    active: bool = True
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    origin: str = ""                         # what produced it (e.g. "curation:schema_invalid")

    def score(self) -> float:
        """Laplace-smoothed usefulness; 0.5 = no evidence either way."""
        return (self.helpful + 1) / (self.helpful + self.harmful + 2)


class Playbook:
    """Delta-updated bullet store. There is deliberately no `rewrite()`."""

    def __init__(self) -> None:
        self._bullets: dict[str, PlaybookBullet] = {}

    # -- deltas -----------------------------------------------------------------

    def add(self, text: str, task_type: Optional[TaskType] = None, origin: str = "") -> PlaybookBullet:
        bullet = PlaybookBullet(text=text, task_type=task_type, origin=origin)
        self._bullets[bullet.id] = bullet
        return bullet

    def amend(self, bullet_id: str, text: str) -> PlaybookBullet:
        bullet = self._bullets[bullet_id]
        bullet.text = text
        bullet.updated_at = time.time()
        return bullet

    def deprecate(self, bullet_id: str) -> None:
        bullet = self._bullets[bullet_id]
        bullet.active = False
        bullet.updated_at = time.time()

    def mark(self, bullet_id: str, helpful: bool) -> None:
        bullet = self._bullets.get(bullet_id)
        if bullet is None:
            return
        if helpful:
            bullet.helpful += 1
        else:
            bullet.harmful += 1
        bullet.updated_at = time.time()

    # -- reads ------------------------------------------------------------------

    def get(self, bullet_id: str) -> Optional[PlaybookBullet]:
        return self._bullets.get(bullet_id)

    def bullets(self, include_deprecated: bool = False) -> list[PlaybookBullet]:
        items = list(self._bullets.values())
        if not include_deprecated:
            items = [b for b in items if b.active]
        return items

    def find(self, text_contains: str, task_type: Optional[TaskType] = None) -> Optional[PlaybookBullet]:
        for b in self._bullets.values():
            if text_contains.lower() in b.text.lower() and b.task_type == task_type:
                return b
        return None

    def bullets_for(self, task: Task, limit: int = 5) -> list[PlaybookBullet]:
        """Active bullets scoped to this task's type (plus universal ones),
        best-scoring first."""
        applicable = [
            b for b in self._bullets.values()
            if b.active and (b.task_type is None or b.task_type == task.task_type)
        ]
        applicable.sort(key=lambda b: (-b.score(), b.created_at))
        return applicable[:limit]

    def hints_for(self, task: Task, limit: int = 5) -> list[str]:
        """The executor-facing view: plain advice strings."""
        return [b.text for b in self.bullets_for(task, limit=limit)]

    # -- persistence --------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        data = [b.model_dump(mode="json") for b in self._bullets.values()]
        Path(path).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Playbook":
        playbook = cls()
        for item in json.loads(Path(path).read_text(encoding="utf-8")):
            bullet = PlaybookBullet.model_validate(item)
            playbook._bullets[bullet.id] = bullet
        return playbook
