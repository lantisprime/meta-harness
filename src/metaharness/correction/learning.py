"""The two-speed learning loop.

Fast loop (per attempt, seconds): grounded reflection — advice injected into the
very next attempt of the same task. Lives in `reflexion.py`, wired via the
executor's `reflector` hook.

Slow loop (across runs, minutes-to-days): failure clustering + playbook curation.
Outcomes accumulate into MAST clusters; when a cluster is big enough it earns a
playbook bullet (a delta — never a rewrite). Bullets are scored by whether tasks
that used them passed, and consistently harmful ones get deprecated. This is the
part that makes the harness *stay* fixed instead of re-learning the same lesson
every run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from metaharness.core.types import MASTMode, Task, TaskOutcome, TaskType, Verdict
from metaharness.correction.mast import FailureStats
from metaharness.correction.playbook import Playbook

# curation templates: cluster mode -> durable advice
CURATION_TEMPLATES: dict[MASTMode, str] = {
    MASTMode.SCHEMA_INVALID: (
        "Outputs for {task_type} tasks are machine-parsed: return a single JSON object "
        "matching the given schema exactly — all required keys, correct types, no prose."
    ),
    MASTMode.DISOBEY_TASK_SPEC: (
        "{task_type} tasks here are verified against a precise expected result. "
        "Re-read the objective and every input before answering; answer exactly what "
        "is asked, in the exact format asked."
    ),
    MASTMode.TOOL_ERROR: (
        "Tool calls in {task_type} tasks have been failing. Prefer the simplest tool "
        "invocation that can work, and validate inputs before calling."
    ),
    MASTMode.STEP_REPETITION: (
        "If an approach failed once on a {task_type} task, do not repeat it verbatim — "
        "change the approach materially before retrying."
    ),
}


class LearningLoop:
    def __init__(
        self,
        playbook: Playbook,
        min_cluster: int = 3,
        deprecate_after: int = 4,
        deprecate_below_score: float = 0.35,
        auto_curate: bool = False,
        persist_path: Optional[str | Path] = None,
    ) -> None:
        self.playbook = playbook
        self.stats = FailureStats()
        self.min_cluster = min_cluster
        self.deprecate_after = deprecate_after
        self.deprecate_below_score = deprecate_below_score
        # in a long-running harness the slow loop must actually run: curate on
        # every observation (cheap + idempotent) and persist what was learned
        self.auto_curate = auto_curate
        self.persist_path = Path(persist_path) if persist_path else None
        self.stats_path: Optional[Path] = None
        self.last_deltas: list[str] = []
        self._applied: dict[str, list[str]] = {}   # task_id -> bullet ids injected

    # -- fast-path integration (executor hooks) -------------------------------------

    def hints_for(self, task: Task) -> list[str]:
        """Playbook advice for this task; remembers which bullets were used so
        `observe` can credit or blame them."""
        bullets = self.playbook.bullets_for(task)
        self._applied[task.id] = [b.id for b in bullets]
        return [b.text for b in bullets]

    # -- slow-path accumulation ------------------------------------------------------

    def observe(self, outcome: TaskOutcome) -> None:
        """Feed a finished task into the stats and score the bullets it used.
        With auto_curate, the slow loop runs here too, and every change (bullet
        scores, deltas) is persisted when a path is configured."""
        self.stats.observe(outcome)
        used = self._applied.pop(outcome.task.id, [])
        if outcome.final_verdict != Verdict.UNVERIFIED:  # no signal, no credit
            passed = outcome.final_verdict == Verdict.PASS
            for bullet_id in used:
                self.playbook.mark(bullet_id, helpful=passed)
        if self.auto_curate:
            deltas = self.curate()
            if deltas:
                self.last_deltas = deltas
        if self.persist_path is not None:
            self.playbook.save(self.persist_path)
        if self.stats_path is not None:
            self.stats.save(self.stats_path)

    def curate(self) -> list[str]:
        """The slow loop's write phase: turn big failure clusters into playbook
        deltas and retire bullets that keep failing. Returns human-readable
        descriptions of every delta applied."""
        deltas: list[str] = []
        for task_type_value, mode, count in self.stats.top_clusters(n=20):
            if count < self.min_cluster:
                continue
            template = CURATION_TEMPLATES.get(mode)
            if template is None:
                continue
            task_type = TaskType(task_type_value)
            origin = f"curation:{mode.value}:{task_type_value}"
            already = any(b.origin == origin for b in self.playbook.bullets(include_deprecated=True))
            if already:
                continue
            text = template.format(task_type=task_type_value)
            bullet = self.playbook.add(text, task_type=task_type, origin=origin)
            deltas.append(f"add {bullet.id} [{origin}] ({count} failures): {text[:80]}...")

        for bullet in self.playbook.bullets():
            uses = bullet.helpful + bullet.harmful
            if uses >= self.deprecate_after and bullet.score() < self.deprecate_below_score:
                self.playbook.deprecate(bullet.id)
                deltas.append(
                    f"deprecate {bullet.id} (score {bullet.score():.2f} over {uses} uses)"
                )
        return deltas
