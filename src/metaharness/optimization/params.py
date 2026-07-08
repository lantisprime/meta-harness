"""The tunable surface of the harness — the optimizer's search space.

Per memory/knowledge_base/meta-harness-optimization.md (arXiv 2603.28052), the
outer loop searches over the code AROUND a fixed model, not the model. v1
searches config-space: enrichment-stack composition plus additive prompt
directives. Directives are deliberately additive-only — the paper's proposer
learned across six consecutive regressions that prompt/control-flow REWRITES
are high-risk and pivoted to purely additive changes.

Pydantic bounds double as the paper's interface-validation gate: a proposal
that doesn't parse into a valid HarnessParams is rejected loudly and recorded,
never silently evaluated.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from metaharness.harness.enrichment import (
    SchemaGuard,
    SelfConsistency,
    SelfCritique,
    ToolOffload,
    _Wrapper,
)
from metaharness.harness.runner import Runner

MAX_DIRECTIVES = 4
MAX_DIRECTIVE_CHARS = 300


class HarnessParams(BaseModel):
    """One candidate harness configuration. Defaults describe the bare stack a
    worker gets today, so the seed candidate IS the incumbent harness."""

    model_config = ConfigDict(extra="forbid")  # unknown knobs fail validation

    tool_offload: bool = False
    self_consistency_k: int = Field(default=1, ge=1, le=7)
    schema_guard_retries: int = Field(default=0, ge=0, le=3)
    self_critique_rounds: int = Field(default=0, ge=0, le=2)
    prompt_directives: list[str] = Field(default_factory=list, max_length=MAX_DIRECTIVES)

    @field_validator("prompt_directives")
    @classmethod
    def _directives_short_and_nonempty(cls, v: list[str]) -> list[str]:
        for d in v:
            if not d.strip():
                raise ValueError("empty prompt directive")
            if len(d) > MAX_DIRECTIVE_CHARS:
                raise ValueError(f"directive over {MAX_DIRECTIVE_CHARS} chars: {d[:60]}…")
        return v

    def with_delta(self, delta: dict[str, Any]) -> "HarnessParams":
        """Merge a proposer delta over these params, re-validating everything.
        Raises pydantic.ValidationError on unknown knobs or out-of-bounds
        values — the caller records that as a rejected candidate."""
        return HarnessParams.model_validate({**self.model_dump(), **delta})

    def build(self, base: Runner) -> Runner:
        """Compose the enrichment stack this candidate describes around a bare
        worker. Order mirrors the existing convention: offload innermost, then
        consistency voting, then schema retries, then critique; directives
        outermost so every inner call sees the amended contract."""
        runner = base
        if self.tool_offload:
            runner = ToolOffload(runner)
        if self.self_consistency_k > 1:
            runner = SelfConsistency(runner, k=self.self_consistency_k)
        if self.schema_guard_retries > 0:
            runner = SchemaGuard(runner, max_retries=self.schema_guard_retries)
        if self.self_critique_rounds > 0:
            runner = SelfCritique(runner, rounds=self.self_critique_rounds)
        if self.prompt_directives:
            runner = PromptDirectives(runner, self.prompt_directives)
        return runner


class PromptDirectives(_Wrapper):
    """Additive prompt-space search: append candidate directives to the task's
    boundaries (the delegation contract) without touching anything else."""

    def __init__(self, inner: Runner, directives: list[str]) -> None:
        super().__init__(inner)
        self.directives = list(directives)

    async def run(self, task):
        amended = task.model_copy(deep=True)
        amended.boundaries = list(task.boundaries) + self.directives
        result = await self.inner.run(amended)
        result.task_id = task.id
        return result


# Knob documentation handed to the LLM proposer — kept next to the fields so a
# new knob can't ship without the proposer learning it exists.
KNOB_DOCS = """\
Tunable knobs (the delta may set any subset):
- tool_offload (bool): PAL — arithmetic tasks emit a program that is evaluated
  exactly instead of computed by the model. Helps when arithmetic answers are wrong.
- self_consistency_k (int, 1..7): sample k answers, majority-vote. Helps when wrong
  answers scatter across attempts. Multiplies token cost by ~k.
- schema_guard_retries (int, 0..3): re-ask naming schema violations. Helps when
  failures are schema-shaped (missing keys, wrong types).
- self_critique_rounds (int, 0..2): draft->critique->revise for tasks with NO
  checkable signal. Useless on deterministic eval suites; costs tokens.
- prompt_directives (list of <=4 strings, <=300 chars each): ADDITIVE instructions
  appended to every task's boundaries. Never rewrites existing prompts."""
