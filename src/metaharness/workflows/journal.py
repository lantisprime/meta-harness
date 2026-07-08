"""Append-only run journal — the durability spine.

Every state transition in a workflow run is appended here before the engine acts
on it (write-ahead). If the process dies mid-run, a new engine replays the
journal and resumes exactly where the run stopped: completed steps keep their
recorded outputs, in-flight steps re-execute. Same idea as Temporal's event
history, sized for this harness.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class JournalEntry(BaseModel):
    seq: int
    at: float
    kind: str            # run.started | step.started | step.completed | step.failed
    #                      | hitl.requested | hitl.resolved | run.finished
    run_id: str
    step_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Journal:
    """In-memory journal with optional write-through to a JSONL file."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self._entries: list[JournalEntry] = []
        self._path = Path(path) if path else None

    def append(
        self,
        kind: str,
        run_id: str,
        step_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> JournalEntry:
        entry = JournalEntry(
            seq=len(self._entries),
            at=time.time(),
            kind=kind,
            run_id=run_id,
            step_id=step_id,
            payload=payload or {},
        )
        self._entries.append(entry)
        if self._path is not None:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.model_dump(), sort_keys=True) + "\n")
                fh.flush()
        return entry

    def entries(self, kind: Optional[str] = None) -> list[JournalEntry]:
        if kind is None:
            return list(self._entries)
        return [e for e in self._entries if e.kind == kind]

    def __len__(self) -> int:
        return len(self._entries)

    @classmethod
    def load(cls, path: str | Path) -> "Journal":
        """Rehydrate a journal from disk. Appends continue into the same file."""
        journal = cls(path=None)
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    journal._entries.append(JournalEntry.model_validate(json.loads(line)))
        journal._path = Path(path)
        return journal
