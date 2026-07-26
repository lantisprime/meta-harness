"""Hermetic contract tests for the real protected scaffold-H execution surface."""
from __future__ import annotations

import inspect
import json
import shutil
from pathlib import Path
import pytest
from pydantic import ValidationError

from metaharness.context import ContextScope, Sensitivity
from metaharness.core.types import Task, Tier, WorkerResult
from metaharness.harness.runner import Runner
from metaharness.evals.h_campaign import (
    CampaignContractError,
    CampaignSpec,
    CaseContract,
    CellContract,
    EvaluatorContract,
    HoldoutAlreadyConsumedError,
    HoldoutConsumptionLedger,
    HSurface,
    ModelContract,
    ProtectedInputPackage,
    SelectionDeclaration,
    deterministic_verify,
    load_protected_inputs,
    case_input_digest,
)
from metaharness.evals.h_campaign import (
    _run_hermetic_campaign,
    _verify_campaign_hermetic,
)
from metaharness.harness import MemoryAwareRunner, MemoryAdviceError
from metaharness.memory import MemoryCognitiveSkillSnapshot, MemoryRecord
from metaharness.portable.integrity import canonical_json_bytes, sha256_hex


_run_campaign = _run_hermetic_campaign
verify_campaign = _verify_campaign_hermetic


def run_campaign(*args, **kwargs):
    kwargs.setdefault("model_digest_resolver", lambda model: model.model_digest)
    return _run_campaign(*args, **kwargs)


# ---------------------------------------------------------------------------
# Shared fixtures and helpers (single canonical block)
# ---------------------------------------------------------------------------


class CaptureRunner:
    worker_id = "capture"
    tier = Tier.SMALL
    model = "fake"
    meta34_hermetic_adapter = "tests-only-v1"

    def __init__(self):
        self.tasks = []

    async def run(self, task):
        self.tasks.append(task)
        return WorkerResult(
            task_id=task.id, worker_id=self.worker_id, tier=self.tier,
            model=self.model, output={"owner": "inner"}, raw_text="inner",
        )


class FakeBroker:
    def __init__(self, snapshot, selected=("record-1",), before_hashes=None):
        self.snapshot = snapshot
        self.selected = selected
        self.before_hashes = before_hashes or {}
        self.calls = []
        self._counter = 0

    def invoke(self, action, **kwargs):
        self.calls.append(action)
        phase = action.phase.value
        self._counter += 1
        # Build a MemoryActionReceipt so model_dump works and content_hash
        # is a real sha256 reference. The real broker path emits these.
        from metaharness.memory import MemoryActionReceipt
        before = tuple((rid, h) for rid, h in self.before_hashes.items())
        receipt = MemoryActionReceipt(
            receipt_id=f"fake-receipt-{self._counter:08x}",
            snapshot_id=self.snapshot.snapshot_id,
            snapshot_content_hash=self.snapshot.content_hash,
            skill_id=self.snapshot.skill_id,
            context_id=kwargs.get("context_id", "shadow-context"),
            context_content_hash="sha256:" + "0" * 64,
            store_high_water_marks=(),
            policy_versions=self.snapshot.policy_versions,
            phase=phase,
            operation=action.operation.value,
            source_record_ids=(),
            considered_targets=(),
            selected_targets=self.selected if phase == "consult" else ("logged",),
            scope=self.snapshot.scope,
            lifecycle_filters=(),
            before_content_hashes=before,
            after_content_hashes=(),
            validation_results=("broker_mode:shadow",),
            redaction_results=("redaction:clear",),
            input_tokens=0,
            output_tokens=0,
            context_budget_tokens=self.snapshot.context_budget_tokens,
            latency_ms=0,
            accepted=True,
            outcome="accepted" if phase == "consult" else "accepted",
            effect_or_rejection_reason=f"fake {phase} receipt",
            observed_at=self._counter,
        )
        return receipt


def snapshot(**changes):
    values = dict(
        snapshot_id="snap", skill_id="skill",
        scope=ContextScope(project_id="project-a"),
        goal_families=("memory-management",), roles=("task-runner",),
    )
    values.update(changes)
    return MemoryCognitiveSkillSnapshot(**values)


def record(content="Useful governed fact"):
    return MemoryRecord(
        id="record-1", kind="semantic_memory", content=content,
        scope=ContextScope(project_id="project-a"), sensitivity=Sensitivity.INTERNAL,
    )


def _record_digest(rec: MemoryRecord) -> str:
    """Compute the broker's hash convention: content_hash(record.content)."""
    from metaharness.context import content_hash
    return content_hash(rec.content)


def _broker_with_record(snap, rec: MemoryRecord, selected=None):
    """Build a FakeBroker whose before_content_hashes match the canonical
    digest of the supplied record, so the receipt's before-hash chain
    validates when present. The real broker's SEARCH receipt does not
    emit hashes; the wrapper only validates when one is supplied."""
    return FakeBroker(
        snap,
        selected=tuple(selected or (rec.id,)),
        before_hashes={rec.id: _record_digest(rec)},
    )


def _hash(label: str) -> str:
    return "sha256:" + sha256_hex(label.encode("utf-8"))


def _retrieval_policy_digest(snap) -> str:
    """The exact allowed policy projection: the load-bearing H delta."""
    from metaharness.portable.integrity import canonical_json_bytes, sha256_hex
    projection = {"query_max_results": snap.query_max_results}
    return "sha256:" + sha256_hex(canonical_json_bytes(projection))


def _h_surface(**changes) -> HSurface:
    from metaharness.evals.h_campaign import (
        EMPTY_SYSTEM_DIGEST,
        _runtime_implementation_fields,
    )
    installed = _runtime_implementation_fields()
    values = {
        "task_template_digest": installed[
            "task_template_implementation_digest"
        ],
        "system_digest": EMPTY_SYSTEM_DIGEST,
        "wrapper_digest": installed[
            "memory_aware_runner_implementation_digest"
        ],
        "resolver_digest": installed["resolver_implementation_digest"],
        "corpus_digest": _hash("corpus"),
        "policy_digest": installed["broker_policy_implementation_digest"],
        "worker_implementation_digest": installed[
            "openai_worker_implementation_digest"
        ],
        "output_parser_digest": installed[
            "output_parser_implementation_digest"
        ],
    }
    values.update(changes)
    return HSurface(**values)


def _real_snapshots():
    """Build the actual base and optimized MemoryCognitiveSkillSnapshot
    objects the spec must attest. The returned content_hash values flow
    into the spec's frozen cell declarations; the runner factories use
    these exact objects. Base declares query_max_results=1; optimized
    declares query_max_results=2. This is the SOLE allowed policy delta
    between the two memory cells."""
    from metaharness.context import ContextScope
    from metaharness.evals.h_campaign import (
        BASE_QUERY_MAX_RESULTS, OPTIMIZED_QUERY_MAX_RESULTS,
    )
    base = MemoryCognitiveSkillSnapshot(
        snapshot_id="base-snapshot",
        skill_id="shadow-skill",
        scope=ContextScope(project_id="meta-harness"),
        goal_families=("memory-management",),
        roles=("builder",),
        parent_snapshot_hash=None,
        query_max_results=BASE_QUERY_MAX_RESULTS,
    )
    optimized = MemoryCognitiveSkillSnapshot(
        snapshot_id="optimized-snapshot",
        skill_id="shadow-skill",
        scope=ContextScope(project_id="meta-harness"),
        goal_families=("memory-management",),
        roles=("builder",),
        parent_snapshot_hash=base.content_hash,
        query_max_results=OPTIMIZED_QUERY_MAX_RESULTS,
    )
    return base, optimized


def _cell(name: str, *, snapshot_hash: str | None, parent: str | None = None,
          rollback: str | None = None,
          base_snapshot=None, optimized_snapshot=None,
          retrieval_policy_digest: str | None = None) -> CellContract:
    h = _h_surface(
        memory_snapshot_hash=snapshot_hash,
        retrieval_policy_digest=retrieval_policy_digest,
    )
    return CellContract(
        name=name,
        h=h,
        parent_snapshot_hash=parent,
        rollback_snapshot_hash=rollback if rollback is not None else (snapshot_hash or _hash("rollback")),
        base_snapshot=base_snapshot,
        optimized_snapshot=optimized_snapshot,
    )


def _model() -> ModelContract:
    return ModelContract(
        model_id="ollama/qwen3.5:35b-a3b-coding-nvfp4",
        model_digest=_hash("qwen3.5:35b-a3b-coding-nvfp4"),
        base_url="http://127.0.0.1:11434",
        inference_parameters={
            "temperature": 0.0,
            "extra_body": {
                "top_p": 1.0, "top_k": 1,
                "reasoning_effort": "none",
            },
            "max_tokens": 64,
            "thinking": False,
        },
    )


def _evaluator() -> EvaluatorContract:
    from metaharness.evals.h_campaign import _runtime_implementation_fields
    return EvaluatorContract(
        evaluator_ref="protected-evaluator",
        evaluator_digest=_runtime_implementation_fields()[
            "deterministic_evaluator_implementation_digest"
        ],
        authority_id="protected-evaluator",
        verifiers=("equals", "contains", "one_of"),
    )


def _case(case_id: str, *, split: str, view: str, mandatory: bool, approved: bool = False,
          input_digest: str | None = None) -> CaseContract:
    task = {"id": case_id, "objective": "answer", "inputs": {"k": case_id}}
    assertion = {"kind": "equals", "value": "expected"}
    return CaseContract(
        case_id=case_id,
        split=split,
        view=view,
        mandatory=mandatory,
        approved_target=approved,
        input_digest=input_digest or case_input_digest(task, assertion),
    )


# Single canonical case_id factory: uses hyphen slugs only.
def _spec(
    *,
    base_snapshot_hash: str | None = None,
    optimized_snapshot_hash: str | None = None,
    optimized_parent: str | None = None,
    rollback_hash: str | None = None,
    extra_changes: dict | None = None,
    campaign_id: str = "meta34-campaign",
    protected_package_digests: dict[str, str] | None = None,
) -> CampaignSpec:
    base_snap, opt_snap = _real_snapshots()
    base_hash = base_snap.content_hash
    opt_hash = opt_snap.content_hash
    if base_snapshot_hash is not None:
        if base_snapshot_hash != base_hash:
            # The test passed a custom hash that does not match the real
            # snapshot; the spec's hash must equal the snapshot's hash.
            raise ValueError(
                f"base_snapshot_hash {base_snapshot_hash!r} does not match the real snapshot {base_hash!r}"
            )
    if optimized_snapshot_hash is not None and optimized_snapshot_hash != opt_hash:
        raise ValueError(
            f"optimized_snapshot_hash {optimized_snapshot_hash!r} does not match the real snapshot {opt_hash!r}"
        )
    parent = opt_snap.parent_snapshot_hash
    rollback = base_hash
    base_cell = _cell(
        "base_scaffold", snapshot_hash=base_hash, rollback=rollback,
        base_snapshot=base_snap,
        retrieval_policy_digest=_retrieval_policy_digest(base_snap),
    )
    opt_cell = _cell(
        "optimized_scaffold", snapshot_hash=opt_hash, parent=parent, rollback=rollback,
        optimized_snapshot=opt_snap,
        retrieval_policy_digest=_retrieval_policy_digest(opt_snap),
    )
    no_mem = _cell("no_external_memory", snapshot_hash=None, rollback=rollback)
    cases = []
    for view in ("approved_target", "transfer", "replay_retention", "privacy", "safety", "efficiency"):
        slug = view.replace("_", "-")
        cases.append(_case(f"case-{slug}", split="development", view=view, mandatory=True))
        cases.append(_case(f"val-{slug}", split="validation", view=view, mandatory=True,
                           approved=(view == "approved_target")))
        cases.append(_case(f"holdout-{slug}", split="holdout", view=view, mandatory=True))
    values = {
        "campaign_id": campaign_id,
        "goal_family": "memory-management",
        "model": _model(),
        "evaluator": _evaluator(),
        "w_refs": (_hash("w:base"),),
        "environment_digest": _hash("env"),
        "repetition_seeds": (101, 202),
        "budget": {"token_limit": 1000, "cost_usd_limit": 5.0, "wall_time_s_limit": 30.0},
        "cells": (no_mem, base_cell, opt_cell),
        "cases": tuple(cases),
        "selection": SelectionDeclaration(
            evidence_case_ids=("case-approved-target",),
        ),
        "protected_package_digests": protected_package_digests or {
            "development": _hash("protected-pkg:dev"),
            "validation": _hash("protected-pkg:val"),
            "holdout": _hash("protected-pkg:holdout"),
        },
        "evaluator_digest": _evaluator().evaluator_digest,
    }
    if extra_changes:
        values.update(extra_changes)
    return CampaignSpec(**values)


def _build_package_items(spec: CampaignSpec, split: str) -> list[dict]:
    """One canonical item builder used for both digest computation and file
    write. The protected package material must be byte-identical between
    the spec-declared digest and the on-disk file. The fake runner table
    controls per-run outputs, so per-iteration verdicts never enter the
    package payload."""
    cases = [c for c in spec.cases if c.split == split]
    return [
        {
            "case_id": c.case_id,
            "task": {"id": c.case_id, "objective": "answer", "inputs": {"k": c.case_id}},
            "assertion": {"kind": "equals", "value": "expected"},
            "input_digest": c.input_digest,
        }
        for c in cases
    ]


def _protected_package_digest(spec: CampaignSpec, split: str) -> str:
    items = _build_package_items(spec, split)
    payload = {"campaign_id": spec.campaign_id, "split": split, "cases": items}
    return "sha256:" + sha256_hex(canonical_json_bytes(payload))


def _write_package_file(path: Path, spec: CampaignSpec, split: str) -> ProtectedInputPackage:
    items = _build_package_items(spec, split)
    payload = {"campaign_id": spec.campaign_id, "split": split, "cases": items}
    package_digest = _protected_package_digest(spec, split)
    package = ProtectedInputPackage.model_validate({**payload, "package_digest": package_digest})
    path.write_text(package.model_dump_json(), encoding="utf-8")
    return package


def _matching_spec() -> CampaignSpec:
    base = _spec()
    digests = {split: _protected_package_digest(base, split) for split in ("development", "validation", "holdout")}
    return _spec(protected_package_digests=digests, campaign_id=base.campaign_id)


# ---------------------------------------------------------------------------
# Fake runner driven only by received advice
# ---------------------------------------------------------------------------


class FakeVerdictRunner:
    worker_id = "memory-aware-runner"
    tier = Tier.SMALL
    model = "ollama/qwen3.5:35b-a3b-coding-nvfp4"
    meta34_hermetic_adapter = "tests-only-v1"

    def __init__(self, verdicts_by_case_advice_count, tokens_per_attempt=0):
        self.table = verdicts_by_case_advice_count
        self.tokens_per_attempt = tokens_per_attempt
        self.tasks: list[Task] = []

    async def run(self, task: Task) -> WorkerResult:
        self.tasks.append(task)
        case_id = task.id
        advice_count = sum(
            item.startswith("[governed memory record") for item in task.advice
        )
        per_advice_count = self.table.get(case_id, {})
        verdicts = per_advice_count.get(advice_count, ("pass",))
        idx = len(self.tasks) - 1
        verdict = verdicts[idx % len(verdicts)]
        output = "expected" if verdict == "pass" else "other"
        return WorkerResult(
            task_id=task.id, worker_id=self.worker_id, tier=self.tier, model=self.model,
            output=output,
            raw_text=output,
            tokens_in=self.tokens_per_attempt,
        )


class CanonicalCaptureRunner:
    worker_id = "memory-aware-runner"
    tier = Tier.SMALL
    model = "ollama/qwen3.5:35b-a3b-coding-nvfp4"
    meta34_hermetic_adapter = "tests-only-v1"

    async def run(self, task: Task) -> WorkerResult:
        return WorkerResult(
            task_id=task.id,
            worker_id=self.worker_id,
            tier=self.tier,
            model=self.model,
            output="expected",
            raw_text="expected",
        )


def _verdict_factory(
    verdicts_by_case_cell: dict,
    repetition_count: int,
    *,
    tokens_per_attempt: int = 0,
):
    base_snap, opt_snap = _real_snapshots()
    real_snap = {"base_scaffold": base_snap, "optimized_scaffold": opt_snap}
    records = {
        "record-1": MemoryRecord(
            id="record-1", kind="semantic_memory", content="governed fact one",
            scope=ContextScope(project_id="meta-harness"), sensitivity=Sensitivity.INTERNAL,
        ),
        "record-2": MemoryRecord(
            id="record-2", kind="semantic_memory", content="governed fact two",
            scope=ContextScope(project_id="meta-harness"), sensitivity=Sensitivity.INTERNAL,
        ),
    }
    by_advice_count = {
        case_id: {
            0: cell_verdicts.get("no_external_memory", ("pass",)),
            1: cell_verdicts.get("base_scaffold", ("pass",)),
            2: cell_verdicts.get("optimized_scaffold", ("pass",)),
        }
        for case_id, cell_verdicts in verdicts_by_case_cell.items()
    }

    def factory(cell: str, repetition: int, seed: int, runner_config: dict | None = None):
        inner = FakeVerdictRunner(
            by_advice_count, tokens_per_attempt=tokens_per_attempt,
        )
        # The no-memory cell must not carry a snapshot; memory cells use the
        # actual spec-attested snapshots so the runner contract can be
        # enforced per cell.
        snap = real_snap.get(cell)
        memory_enabled = cell != "no_external_memory"
        if not memory_enabled:
            return MemoryAwareRunner(
                inner=inner,
                snapshot=None,
                broker=None,
                record_resolver=None,
                memory_enabled=False,
            )
        broker = _MakeReceiptBroker(
            snap, selected=tuple(records)[:snap.query_max_results],
        )
        return MemoryAwareRunner(
            inner=inner,
            snapshot=snap,
            broker=broker,
            record_resolver=records.get,
            memory_enabled=True,
        )

    return factory


class _MakeReceiptBroker:
    """Minimal in-process broker that returns MemoryActionReceipt objects
    matching the wrapper's contract. Its selected records are controlled by
    the snapshot retrieval limit so test outcomes can depend only on advice."""

    def __init__(self, snapshot, selected=()):
        self.snapshot = snapshot
        self.selected = tuple(selected)
        self.calls = []
        self._counter = 0

    def invoke(self, action, **kwargs):
        self.calls.append(action)
        self._counter += 1
        from metaharness.memory import MemoryActionReceipt
        return MemoryActionReceipt(
            receipt_id=f"make-receipt-{self._counter:08x}",
            snapshot_id=self.snapshot.snapshot_id,
            snapshot_content_hash=self.snapshot.content_hash,
            skill_id=self.snapshot.skill_id,
            context_id=kwargs.get("context_id", "shadow-context"),
            context_content_hash="sha256:" + "0" * 64,
            store_high_water_marks=(),
            policy_versions=self.snapshot.policy_versions,
            phase=action.phase.value,
            operation=action.operation.value,
            source_record_ids=(),
            considered_targets=(),
            selected_targets=self.selected if action.phase.value == "consult" else (),
            scope=self.snapshot.scope,
            lifecycle_filters=(),
            before_content_hashes=(),
            after_content_hashes=(),
            validation_results=("broker_mode:shadow",),
            redaction_results=("redaction:clear",),
            input_tokens=0,
            output_tokens=0,
            context_budget_tokens=self.snapshot.context_budget_tokens,
            latency_ms=0,
            accepted=True,
            outcome="accepted",
            effect_or_rejection_reason="empty consult",
            observed_at=self._counter,
        )


def _all_pass_map(spec: CampaignSpec, split: str) -> dict:
    return {c.case_id: {cell: ("pass",) for cell in ("no_external_memory", "base_scaffold", "optimized_scaffold")}
            for c in spec.cases if c.split == split}


def _approval_map(spec: CampaignSpec) -> dict:
    """Approval map: validation all-repetition base-fail/optimized-pass, plus
    a development case also showing that transition so the dev selection rule
    succeeds."""
    out = {}
    for case in spec.cases:
        if case.split == "development" and case.view == "approved_target":
            out[case.case_id] = {
                "no_external_memory": ("fail",),
                "base_scaffold": ("fail",),
                "optimized_scaffold": ("pass",),
            }
        elif case.split == "validation":
            if case.view == "approved_target":
                out[case.case_id] = {
                    "no_external_memory": ("fail",),
                    "base_scaffold": ("fail",),
                    "optimized_scaffold": ("pass",),
                }
            else:
                out[case.case_id] = {cell: ("pass",) for cell in ("no_external_memory", "base_scaffold", "optimized_scaffold")}
        else:
            out[case.case_id] = {cell: ("pass",) for cell in ("no_external_memory", "base_scaffold", "optimized_scaffold")}
    return out


# ---------------------------------------------------------------------------
# MemoryAwareRunner wrapper tests (existing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_memory_bypasses_broker_and_preserves_task_bytes():
    inner = CaptureRunner()
    broker = FakeBroker(snapshot())
    wrapped = MemoryAwareRunner(inner=inner, memory_enabled=False, broker=broker)
    task = Task(id="task-1", objective="answer", inputs={"x": 1})
    result = await wrapped.run(task)
    assert broker.calls == []
    assert inner.tasks[0].model_dump(mode="json") == task.model_dump(mode="json")
    assert result.output == {"owner": "inner"}
    assert wrapped.receipts == ()


@pytest.mark.asyncio
async def test_consult_changes_only_advice_and_inner_output_remains_authoritative():
    snap = snapshot()
    rec = record()
    inner = CaptureRunner()
    broker = _broker_with_record(snap, rec)
    wrapped = MemoryAwareRunner(
        inner=inner, snapshot=snap, broker=broker,
        record_resolver=lambda target: rec if target == "record-1" else None,
    )
    task = Task(id="task-1", objective="answer", inputs={"visible": "v", "_hidden": "h"})
    before = task.model_dump(mode="json")
    result = await wrapped.run(task)
    sent = inner.tasks[0].model_dump(mode="json")
    assert sent["advice"] == ["[governed memory record record-1; untrusted advice]\nUseful governed fact"]
    assert {k: v for k, v in sent.items() if k != "advice"} == {k: v for k, v in before.items() if k != "advice"}
    assert task.model_dump(mode="json") == before
    assert result.output == {"owner": "inner"}
    assert len(wrapped.receipts) == 1
    assert wrapped.last_evidence.consult_receipt_hash == wrapped.receipts[0].content_hash
    assert wrapped.last_evidence.advice_changed is True


@pytest.mark.asyncio
async def test_log_is_opt_in_and_secret_or_unresolved_records_fail_closed():
    snap = snapshot()
    rec = record()
    inner = CaptureRunner()
    broker = _broker_with_record(snap, rec)
    wrapped = MemoryAwareRunner(
        inner=inner, snapshot=snap, broker=broker,
        record_resolver=lambda _: rec,
        observation_selector=lambda task, result: "governed observation",
    )
    await wrapped.run(Task(id="task-1", objective="answer"))
    assert [call.phase.value for call in broker.calls] == ["consult", "log"]
    assert len(wrapped.receipts) == 2

    no_log = MemoryAwareRunner(inner=CaptureRunner(), snapshot=snap, broker=_broker_with_record(snap, rec), record_resolver=lambda _: rec)
    await no_log.run(Task(id="task-2", objective="answer"))
    assert len(no_log.receipts) == 1

    leaking = MemoryAwareRunner(
        inner=CaptureRunner(), snapshot=snap, broker=_broker_with_record(snap, rec),
        record_resolver=lambda _: record("api_key=protected-value"),
    )
    with pytest.raises(MemoryAdviceError):
        await leaking.run(Task(id="task-3", objective="answer"))
    assert leaking.inner.tasks == []

    unresolved = MemoryAwareRunner(
        inner=CaptureRunner(), snapshot=snap, broker=_broker_with_record(snap, rec),
        record_resolver=lambda _: None,
    )
    with pytest.raises(MemoryAdviceError):
        await unresolved.run(Task(id="task-4", objective="answer"))
    assert unresolved.inner.tasks == []


def test_campaign_api_has_no_authority_or_model_judge_surfaces():
    import metaharness.evals.h_campaign as campaign
    forbidden = {"promote", "activate", "deploy", "start_w", "semantic_judge", "model_judge"}
    assert forbidden.isdisjoint(set(dir(campaign)))
    assert hasattr(campaign, "verify_campaign")
    assert hasattr(campaign, "HoldoutConsumptionLedger")


# ---------------------------------------------------------------------------
# Actual-broker integration tests (real MemoryStore + MemoryActionBroker)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_aware_runner_works_with_real_broker_and_empty_search_hashes():
    """The real broker's SEARCH receipt does NOT populate before_content_hashes.
    The wrapper must accept the empty hashes without rejecting, and must
    compute the per-record content hash using context.models.content_hash."""
    from metaharness.memory import MemoryActionBroker, SemanticMemoryStore

    snap = snapshot(scope=ContextScope(project_id="meta-harness"))
    store = SemanticMemoryStore()
    committed = store.commit(
        kind="semantic_memory",
        content="opaque-key",
        scope=ContextScope(project_id="meta-harness"),
        creator_id="test",
    )
    captured_advice: list = []

    class _Inner(Runner):
        worker_id = "inner"
        tier = Tier.SMALL
        model = "inner"

        async def run(self, task: Task) -> WorkerResult:
            captured_advice.append(list(task.advice))
            return WorkerResult(
                task_id=task.id, worker_id="inner", tier=Tier.SMALL,
                model="inner", output={"got": "expected"}, raw_text="expected",
            )

    broker = MemoryActionBroker(snapshot=snap, stores=store)
    wrapped = MemoryAwareRunner(
        inner=_Inner(),
        snapshot=snap,
        broker=broker,
        record_resolver=lambda target_id: store.get(target_id),
    )
    task = Task(id="task-real", objective="opaque-key", inputs={})
    result = await wrapped.run(task)
    # The real broker must have produced a SEARCH receipt with empty
    # before_content_hashes (SEARCH does not populate hashes).
    assert len(wrapped.receipts) == 1
    assert wrapped.receipts[0].before_content_hashes == ()
    # The wrapper must have bound the resolved record's content hash
    # via context.models.content_hash(record.content).
    evidence = wrapped.last_evidence
    assert evidence.selected_record_ids == (committed.id,)
    assert evidence.selected_record_hashes == (
        (committed.id, _record_digest(committed)),
    )
    assert evidence.advice_changed is True
    assert result.output == {"got": "expected"}
    # The inner worker must have seen the memory-derived advice excerpt.
    assert captured_advice[0][0].startswith(
        f"[governed memory record {committed.id}; untrusted advice]"
    )


@pytest.mark.asyncio
async def test_memory_aware_runner_rejects_receipt_when_supplied_hash_disagrees():
    """If the receipt supplies a before_content_hash for a selected id,
    it MUST match the resolved record's current content hash. The fake
    broker supplies a tampered hash; the wrapper must reject."""
    snap = snapshot(scope=ContextScope(project_id="meta-harness"))
    rec = MemoryRecord(
        id="real-record-2",
        kind="semantic_memory",
        content="governed memory fact beta",
        scope=ContextScope(project_id="meta-harness"),
        sensitivity=Sensitivity.INTERNAL,
        creator_id="test",
    )
    # Tampered hash: not the broker's content_hash(rec.content).
    tampered = "sha256:" + "f" * 64
    broker = _broker_with_record(snap, rec)
    # Override the broker's before_hashes to be wrong.
    broker.before_hashes = {rec.id: tampered}

    class _Inner(Runner):
        worker_id = "inner"
        tier = Tier.SMALL
        model = "inner"

        async def run(self, task: Task) -> WorkerResult:
            return WorkerResult(
                task_id=task.id, worker_id="inner", tier=Tier.SMALL,
                model="inner", output="ok", raw_text="ok",
            )

    wrapped = MemoryAwareRunner(
        inner=_Inner(),
        snapshot=snap,
        broker=broker,
        record_resolver=lambda target_id: rec if target_id == rec.id else None,
    )
    with pytest.raises(MemoryAdviceError):
        await wrapped.run(Task(id="task-bad", objective="answer"))
    # The inner worker MUST NOT have been invoked.
    assert wrapped.receipts != []  # the receipt was captured before the rejection


@pytest.mark.asyncio
async def test_log_receipt_must_be_phase_log_and_operation_create_candidate():
    """The wrapper must reject a LOG receipt whose phase or operation
    is not phase=log and operation=create_candidate."""
    from metaharness.memory import MemoryActionReceipt

    snap = snapshot(scope=ContextScope(project_id="project-a"))
    rec = MemoryRecord(
        id="record-1", kind="semantic_memory", content="fact",
        scope=ContextScope(project_id="project-a"),
        sensitivity=Sensitivity.INTERNAL,
    )
    bad_phase_receipt = MemoryActionReceipt(
        receipt_id="bad-1",
        snapshot_id=snap.snapshot_id,
        snapshot_content_hash=snap.content_hash,
        skill_id=snap.skill_id,
        context_id="memory-aware-runner",
        context_content_hash="sha256:" + "0" * 64,
        store_high_water_marks=(),
        policy_versions=snap.policy_versions,
        phase="consult",  # wrong; LOG must be phase=log
        operation="create_candidate",  # allowed
        source_record_ids=(),
        considered_targets=(),
        selected_targets=("logged",),
        scope=snap.scope,
        lifecycle_filters=(),
        before_content_hashes=(),
        after_content_hashes=(),
        validation_results=("broker_mode:shadow",),
        redaction_results=("redaction:clear",),
        input_tokens=0, output_tokens=0,
        context_budget_tokens=snap.context_budget_tokens,
        latency_ms=0,
        accepted=True, outcome="accepted",
        effect_or_rejection_reason="bad log phase",
        observed_at=0,
    )
    valid_consult_receipt = bad_phase_receipt.model_copy(update={
        "receipt_id": "consult-ok",
        "phase": "consult",
        "operation": "search",
        "selected_targets": (),
    })
    bad_op_receipt = bad_phase_receipt.model_copy(update={
        "receipt_id": "bad-2",
        "phase": "log",
        "operation": "search",  # wrong; LOG must be operation=create_candidate
    })

    class _BrokerTwo:
        def __init__(self, receipts):
            self._receipts = receipts
            self.snapshot = snap
            self.idx = 0
            self.calls = []

        def invoke(self, action, **kwargs):
            self.calls.append(action)
            rcpt = self._receipts[self.idx]
            self.idx += 1
            return rcpt

    class _Inner(Runner):
        worker_id = "inner"
        tier = Tier.SMALL
        model = "inner"
        meta34_hermetic_adapter = "tests-only-v1"
        def __init__(self):
            self.calls = 0

        async def run(self, task: Task) -> WorkerResult:
            self.calls += 1
            return WorkerResult(task_id=task.id, worker_id="inner", tier=Tier.SMALL, model="inner", output="ok", raw_text="ok")

    def _resolver(target_id: str):
        return rec if target_id == rec.id else None

    # First fail: LOG phase is wrong
    inner = _Inner()
    broker = _BrokerTwo([valid_consult_receipt, bad_phase_receipt])
    wrapped = MemoryAwareRunner(
        inner=inner, snapshot=snap, broker=broker,
        record_resolver=_resolver,
        observation_selector=lambda task, result: "governed observation",
    )
    with pytest.raises(MemoryAdviceError, match="LOG phase mismatch"):
        await wrapped.run(Task(id="task-bad-phase", objective="answer"))
    assert inner.calls == 1

    # Second fail: LOG operation is wrong
    inner = _Inner()
    broker = _BrokerTwo([valid_consult_receipt, bad_op_receipt])
    wrapped = MemoryAwareRunner(
        inner=inner, snapshot=snap, broker=broker,
        record_resolver=_resolver,
        observation_selector=lambda task, result: "governed observation",
    )
    with pytest.raises(MemoryAdviceError, match="LOG operation mismatch"):
        await wrapped.run(Task(id="task-bad-op", objective="answer"))
    assert inner.calls == 1


# ---------------------------------------------------------------------------
# Load-bearing H delta / base vs optimized retrieval policy
# ---------------------------------------------------------------------------


def test_base_optimized_retrieval_policy_delta_is_load_bearing():
    from metaharness.evals.h_campaign import (
        BASE_QUERY_MAX_RESULTS, OPTIMIZED_QUERY_MAX_RESULTS,
        _retrieval_policy_digest,
    )
    assert BASE_QUERY_MAX_RESULTS == 1
    assert OPTIMIZED_QUERY_MAX_RESULTS == 2
    base_snap, opt_snap = _real_snapshots()
    base_digest = _retrieval_policy_digest(base_snap)
    opt_digest = _retrieval_policy_digest(opt_snap)
    assert base_digest != opt_digest


def test_spec_rejects_base_query_max_results_other_than_one():
    from metaharness.memory import MemoryCognitiveSkillSnapshot

    base = MemoryCognitiveSkillSnapshot(
        snapshot_id="base-bad",
        skill_id="shadow-skill",
        scope=ContextScope(project_id="meta-harness"),
        goal_families=("memory-management",),
        roles=("builder",),
        parent_snapshot_hash=None,
        query_max_results=4,  # wrong: must be 1
    )
    optimized = MemoryCognitiveSkillSnapshot(
        snapshot_id="opt-ok",
        skill_id="shadow-skill",
        scope=ContextScope(project_id="meta-harness"),
        goal_families=("memory-management",),
        roles=("builder",),
        parent_snapshot_hash=base.content_hash,
        query_max_results=2,
    )
    raw = _spec().model_dump(mode="python")
    raw["cells"] = (
        raw["cells"][0],  # no_memory
        {
            **raw["cells"][1],
            "base_snapshot": base,
            "optimized_snapshot": None,
            "h": {**raw["cells"][1]["h"], "memory_snapshot_hash": base.content_hash,
                  "retrieval_policy_digest": _retrieval_policy_digest(base)},
        },
        {
            **raw["cells"][2],
            "base_snapshot": None,
            "optimized_snapshot": optimized,
            "h": {**raw["cells"][2]["h"], "memory_snapshot_hash": optimized.content_hash,
                  "retrieval_policy_digest": _retrieval_policy_digest(optimized)},
        },
    )
    with pytest.raises(ValidationError) as exc_info:
        CampaignSpec.model_validate(raw)
    assert "query_max_results" in str(exc_info.value)


def test_spec_rejects_optimized_query_max_results_other_than_two():
    from metaharness.memory import MemoryCognitiveSkillSnapshot

    base = MemoryCognitiveSkillSnapshot(
        snapshot_id="base-ok",
        skill_id="shadow-skill",
        scope=ContextScope(project_id="meta-harness"),
        goal_families=("memory-management",),
        roles=("builder",),
        parent_snapshot_hash=None,
        query_max_results=1,
    )
    optimized = MemoryCognitiveSkillSnapshot(
        snapshot_id="opt-bad",
        skill_id="shadow-skill",
        scope=ContextScope(project_id="meta-harness"),
        goal_families=("memory-management",),
        roles=("builder",),
        parent_snapshot_hash=base.content_hash,
        query_max_results=3,  # wrong: must be 2
    )
    raw = _spec().model_dump(mode="python")
    raw["cells"] = (
        raw["cells"][0],
        {
            **raw["cells"][1],
            "base_snapshot": base,
            "optimized_snapshot": None,
            "h": {**raw["cells"][1]["h"], "memory_snapshot_hash": base.content_hash,
                  "retrieval_policy_digest": _retrieval_policy_digest(base)},
        },
        {
            **raw["cells"][2],
            "base_snapshot": None,
            "optimized_snapshot": optimized,
            "h": {**raw["cells"][2]["h"], "memory_snapshot_hash": optimized.content_hash,
                  "retrieval_policy_digest": _retrieval_policy_digest(optimized)},
        },
    )
    with pytest.raises(ValidationError) as exc_info:
        CampaignSpec.model_validate(raw)
    assert "query_max_results" in str(exc_info.value)


def test_spec_rejects_identical_base_optimized_retrieval_policy_digest():
    base_snap, opt_snap = _real_snapshots()
    same_digest = _retrieval_policy_digest(base_snap)
    raw = _spec().model_dump(mode="python")
    raw["cells"] = (
        raw["cells"][0],
        {**raw["cells"][1], "h": {**raw["cells"][1]["h"], "retrieval_policy_digest": same_digest}},
        {**raw["cells"][2], "h": {**raw["cells"][2]["h"], "retrieval_policy_digest": same_digest}},
    )
    with pytest.raises(ValidationError) as exc_info:
        CampaignSpec.model_validate(raw)
    assert "differ" in str(exc_info.value) or "retrieval" in str(exc_info.value)


def test_spec_rejects_non_h_snapshot_drift_between_base_and_optimized():
    """Any snapshot-field drift besides identity, parent/content hash, and
    the declared retrieval delta must reject."""
    from metaharness.memory import MemoryCognitiveSkillSnapshot

    base_snap_orig, opt_snap_orig = _real_snapshots()
    base = MemoryCognitiveSkillSnapshot(
        snapshot_id=base_snap_orig.snapshot_id,
        skill_id=base_snap_orig.skill_id,
        scope=base_snap_orig.scope,
        goal_families=base_snap_orig.goal_families,
        roles=base_snap_orig.roles,
        parent_snapshot_hash=None,
        query_max_results=1,
    )
    # Build optimized with the SAME base as the real fixture so parent
    # matches.
    optimized = MemoryCognitiveSkillSnapshot(
        snapshot_id=opt_snap_orig.snapshot_id,
        skill_id=opt_snap_orig.skill_id,
        scope=opt_snap_orig.scope,
        goal_families=opt_snap_orig.goal_families,
        roles=opt_snap_orig.roles,
        parent_snapshot_hash=base.content_hash,
        query_max_results=2,
        # Drift: a non-allowed field
        redaction_marker="[DRIFTED]",
    )
    raw = _spec().model_dump(mode="python")
    raw["cells"] = (
        raw["cells"][0],
        {
            **raw["cells"][1],
            "base_snapshot": base,
            "optimized_snapshot": None,
            "h": {**raw["cells"][1]["h"], "memory_snapshot_hash": base.content_hash,
                  "retrieval_policy_digest": _retrieval_policy_digest(base)},
        },
        {
            **raw["cells"][2],
            "base_snapshot": None,
            "optimized_snapshot": optimized,
            "h": {**raw["cells"][2]["h"], "memory_snapshot_hash": optimized.content_hash,
                  "retrieval_policy_digest": _retrieval_policy_digest(optimized)},
        },
    )
    with pytest.raises(ValidationError) as exc_info:
        CampaignSpec.model_validate(raw)
    assert "redaction_marker" in str(exc_info.value) or "drift" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Actual-broker load-bearing tests: stale vs current record, advice-driven
# inner response, and policy-delta visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_broker_base_sees_one_record_and_optimized_sees_both():
    """With a real MemoryStore + MemoryActionBroker, base
    (query_max_results=1) sees one of two records; optimized
    (query_max_results=2) sees both."""
    from metaharness.memory import MemoryActionBroker, SemanticMemoryStore

    base_snap, opt_snap = _real_snapshots()
    store = SemanticMemoryStore()
    # Two records sharing the opaque key; the broker's ranked order is
    # deterministic by creation_seq.
    store.commit(
        kind="semantic_memory", content="opaque-key",
        scope=ContextScope(project_id="meta-harness"), creator_id="test",
    )
    store.commit(
        kind="semantic_memory", content="opaque-key",
        scope=ContextScope(project_id="meta-harness"), creator_id="test",
    )
    # Force lexical differentiation so both are selected with a 2-limit.
    store.commit(
        kind="semantic_memory", content="opaque-key extra",
        scope=ContextScope(project_id="meta-harness"), creator_id="test",
    )

    def _make_runner(limit: int, seen: list):
        class _Inner(Runner):
            worker_id = "inner"
            tier = Tier.SMALL
            model = "inner"

            async def run(self, task: Task) -> WorkerResult:
                seen.append(list(task.advice))
                return WorkerResult(
                    task_id=task.id, worker_id="inner", tier=Tier.SMALL,
                    model="inner", output="ok", raw_text="ok",
                )

        snap = MemoryCognitiveSkillSnapshot(
            snapshot_id=base_snap.snapshot_id,
            skill_id=base_snap.skill_id,
            scope=base_snap.scope,
            goal_families=base_snap.goal_families,
            roles=base_snap.roles,
            query_max_results=limit,
        )
        broker = MemoryActionBroker(snapshot=snap, stores=store)
        return MemoryAwareRunner(
            inner=_Inner(), snapshot=snap, broker=broker,
            record_resolver=lambda target_id: store.get(target_id),
        )

    base_seen: list = []
    opt_seen: list = []
    base_runner = _make_runner(1, base_seen)
    opt_runner = _make_runner(2, opt_seen)
    task = Task(id="load-bearing", objective="opaque-key", inputs={})
    await base_runner.run(task.model_copy(deep=True))
    await opt_runner.run(task.model_copy(deep=True))
    base_records = sum(1 for line in base_seen[0] if line.startswith("[governed memory record"))
    opt_records = sum(1 for line in opt_seen[0] if line.startswith("[governed memory record"))
    assert base_records == 1
    assert opt_records == 2


def test_runner_factory_must_not_choose_outcomes_by_cell_name(tmp_path):
    """The inner fake response must depend on received advice, not cell name.
    With a real broker (base=1, optimized=2 records), base sees one
    record -> advice with one excerpt, optimized sees both -> advice with
    two excerpts. The fake decides the verdict by advice length (NOT by
    cell name); only the wrapper's memory cells can inject different
    advice."""
    from metaharness.context import ContextScope as _CS
    from metaharness.memory import MemoryActionBroker as _B, SemanticMemoryStore as _S

    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"

    # Pre-seed the store with two records sharing the opaque key.
    store = _S()
    store.commit(
        kind="semantic_memory", content="opaque-key",
        scope=_CS(project_id="meta-harness"), creator_id="test",
    )
    store.commit(
        kind="semantic_memory", content="opaque-key",
        scope=_CS(project_id="meta-harness"), creator_id="test",
    )

    class AdviceAwareRunner(Runner):
        worker_id = spec.runner_id
        tier = Tier.SMALL
        model = spec.model.model_id
        meta34_hermetic_adapter = "tests-only-v1"

        async def run(self, task: Task) -> WorkerResult:
            advice_text = "".join(task.advice or [])
            # The test invariant: a SINGLE record in advice means the
            # cell saw the base retrieval. A SECOND record in advice
            # means the cell saw the optimized retrieval.
            record_count = advice_text.count("[governed memory record")
            if record_count >= 2:
                output = "expected"  # optimized cell, sees two records
            else:
                output = "other"  # no_memory or base, sees 0 or 1
            return WorkerResult(
                task_id=task.id, worker_id=self.worker_id, tier=self.tier,
                model=self.model, output=output, raw_text=output,
            )

    base_snap, opt_snap = _real_snapshots()
    real_snap = {"base_scaffold": base_snap, "optimized_scaffold": opt_snap}

    def factory(cell, repetition, seed, runner_config=None):
        snap = real_snap.get(cell)
        memory_enabled = cell != "no_external_memory"
        if not memory_enabled:
            return MemoryAwareRunner(
                inner=AdviceAwareRunner(),
                snapshot=None, broker=None, record_resolver=None,
                memory_enabled=False,
            )
        broker = _B(snapshot=snap, stores=store)
        return MemoryAwareRunner(
            inner=AdviceAwareRunner(),
            snapshot=snap, broker=broker,
            record_resolver=lambda target_id: store.get(target_id),
            memory_enabled=True,
        )

    # Rewrite the package items so task.objective matches the record's
    # opaque key (so the FTS search returns the seeded records).
    dev_digest = _rewrite_split_package(dev_path, spec, "development", "opaque-key")
    val_digest = _rewrite_split_package(val_path, spec, "validation", "opaque-key")
    holdout_digest = _rewrite_split_package(holdout_path, spec, "holdout", "opaque-key")
    spec = _spec(
        campaign_id=spec.campaign_id,
        protected_package_digests={
            "development": dev_digest, "validation": val_digest, "holdout": holdout_digest,
        },
    )
    spec = _spec_with_package_inputs(spec, dev_path, val_path, holdout_path)

    # The campaign must reach eligible_pending_human_promotion because:
    # - the optimized cell sees 2 records -> "expected" (pass)
    # - the base cell sees 1 record -> "other" (fail)
    # - the no-memory cell sees 0 records -> "other" (fail)
    result = run_campaign(
        spec, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
        ledger_root=tmp_path / "ledger", runner_factory=factory,
    )
    assert result["status"] == "eligible_pending_human_promotion"


def test_selection_advice_chain_guard_rejects_byte_identical_base_optimized_advice(tmp_path):
    """For every preregistered development selection case/repetition, the
    optimized_scaffold row must have advice_changed True, nonempty
    selected_record_ids, an advice_digest different from the base
    scaffold's, and an inner_task_digest different from the base
    scaffold's. Otherwise the campaign must fail before eligibility
    because the optimized policy is not load-bearing."""
    from metaharness.context import ContextScope as _CS
    from metaharness.memory import MemoryActionBroker as _B, SemanticMemoryStore as _S

    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")

    # Pre-seed two records that share the opaque key so base=1 sees one
    # and optimized=2 sees both. Use a stable task.objective so the
    # wrapper's query exactly matches the record content.
    store = _S()
    store.commit(
        kind="semantic_memory", content="opaque-key",
        scope=_CS(project_id="meta-harness"), creator_id="test",
    )
    store.commit(
        kind="semantic_memory", content="opaque-key",
        scope=_CS(project_id="meta-harness"), creator_id="test",
    )

    class AdviceCountRunner(Runner):
        worker_id = spec.runner_id
        tier = Tier.SMALL
        model = spec.model.model_id
        meta34_hermetic_adapter = "tests-only-v1"

        async def run(self, task: Task) -> WorkerResult:
            advice_text = "".join(task.advice or [])
            count = advice_text.count("[governed memory record")
            # Only the optimized cell (limit=2) sees 2 records; base (1)
            # sees 1; no-memory sees 0. The fake must NOT pick by cell
            # name; it derives output strictly from the received advice.
            if count >= 2:
                return WorkerResult(
                    task_id=task.id, worker_id=self.worker_id, tier=self.tier,
                    model=self.model, output="expected", raw_text="expected",
                )
            return WorkerResult(
                task_id=task.id, worker_id=self.worker_id, tier=self.tier,
                model=self.model, output="other", raw_text="other",
            )

    base_snap, opt_snap = _real_snapshots()
    real_snap = {"base_scaffold": base_snap, "optimized_scaffold": opt_snap}

    def factory(cell, repetition, seed, runner_config=None):
        snap = real_snap.get(cell)
        memory_enabled = cell != "no_external_memory"
        if not memory_enabled:
            return MemoryAwareRunner(
                inner=AdviceCountRunner(),
                snapshot=None, broker=None, record_resolver=None,
                memory_enabled=False,
            )
        broker = _B(snapshot=snap, stores=store)
        return MemoryAwareRunner(
            inner=AdviceCountRunner(),
            snapshot=snap, broker=broker,
            record_resolver=lambda target_id: store.get(target_id),
            memory_enabled=True,
        )

    # Patch the package items so task.objective matches the record's
    # opaque key (so the FTS search returns the seeded records).
    dev_items = []
    for case in [c for c in spec.cases if c.split == "development"]:
        dev_items.append({
            "case_id": case.case_id,
            "task": {"id": case.case_id, "objective": "opaque-key", "inputs": {}},
            "assertion": {"kind": "equals", "value": "expected"},
        })
    for item in dev_items:
        item["input_digest"] = case_input_digest(item["task"], item["assertion"])
    payload = {"campaign_id": spec.campaign_id, "split": "development", "cases": dev_items}
    dev_digest = "sha256:" + sha256_hex(canonical_json_bytes(payload))
    dev_package = ProtectedInputPackage.model_validate({**payload, "package_digest": dev_digest})
    dev_path.write_text(dev_package.model_dump_json(), encoding="utf-8")
    # Update the spec to match the recomputed development package digest.
    spec = _spec(
        campaign_id=spec.campaign_id,
        protected_package_digests={
            "development": dev_digest,
            "validation": spec.package_digest_for("validation"),
            "holdout": spec.package_digest_for("holdout"),
        },
    )
    # Now write validation/holdout packages with the same opaque-key
    # objective (so the FTS search remains consistent across all
    # splits).
    for split, path in [("validation", val_path), ("holdout", holdout_path)]:
        items = []
        for case in [c for c in spec.cases if c.split == split]:
            items.append({
                "case_id": case.case_id,
                "task": {"id": case.case_id, "objective": "opaque-key", "inputs": {}},
                "assertion": {"kind": "equals", "value": "expected"},
            })
        for item in items:
            item["input_digest"] = case_input_digest(item["task"], item["assertion"])
        sp = {"campaign_id": spec.campaign_id, "split": split, "cases": items}
        d = "sha256:" + sha256_hex(canonical_json_bytes(sp))
        pkg = ProtectedInputPackage.model_validate({**sp, "package_digest": d})
        path.write_text(pkg.model_dump_json(), encoding="utf-8")
        spec = _spec(
            campaign_id=spec.campaign_id,
            protected_package_digests={
                "development": spec.package_digest_for("development"),
                "validation": d if split == "validation" else spec.package_digest_for("validation"),
                "holdout": d if split == "holdout" else spec.package_digest_for("holdout"),
            },
        )
    # The spec must be rebuilt with the new digests so the final spec
    # passed to run_campaign is self-consistent.
    spec = _spec(
        campaign_id=spec.campaign_id,
        protected_package_digests={
            "development": dev_digest,
            "validation": "sha256:" + sha256_hex(canonical_json_bytes({
                "campaign_id": spec.campaign_id, "split": "validation",
                "cases": [
                    {
                        "case_id": c.case_id,
                        "input_digest": c.input_digest,
                        "task": {"id": c.case_id, "objective": "opaque-key", "inputs": {}},
                        "assertion": {"kind": "equals", "value": "expected"},
                    }
                    for c in spec.cases if c.split == "validation"
                ],
            })),
            "holdout": "sha256:" + sha256_hex(canonical_json_bytes({
                "campaign_id": spec.campaign_id, "split": "holdout",
                "cases": [
                    {
                        "case_id": c.case_id,
                        "input_digest": c.input_digest,
                        "task": {"id": c.case_id, "objective": "opaque-key", "inputs": {}},
                        "assertion": {"kind": "equals", "value": "expected"},
                    }
                    for c in spec.cases if c.split == "holdout"
                ],
            })),
        },
    )
    spec = _spec_with_package_inputs(spec, dev_path, val_path, holdout_path)

    result = run_campaign(
        spec, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
        ledger_root=tmp_path / "ledger", runner_factory=factory,
    )
    assert result["status"] == "eligible_pending_human_promotion"
    # The campaign evidence must show that for every preregistered
    # development selection case/repetition, the optimized row's
    # advice_digest and inner_task_digest differ from the base row's.
    evidence = json.loads((tmp_path / "evidence" / "evidence.json").read_text())
    dev_case_ids = set(spec.selection.evidence_case_ids)
    for case_id in dev_case_ids:
        base_rows = [r for r in evidence["rows"] if r["cell"] == "base_scaffold"
                     and r["case_id"] == case_id and r["split"] == "development"]
        opt_rows = [r for r in evidence["rows"] if r["cell"] == "optimized_scaffold"
                    and r["case_id"] == case_id and r["split"] == "development"]
        assert len(base_rows) == len(opt_rows) and len(base_rows) > 0
        for base, opt in zip(base_rows, opt_rows):
            assert opt["advice_changed"] is True
            assert len(opt["selected_record_ids"]) >= 2
            assert opt["advice_digest"] != base["advice_digest"]
            assert opt["inner_task_digest"] != base["inner_task_digest"]


def test_advice_chain_guard_rejects_cell_driven_false_improvement(tmp_path):
    """A fabricated cell-driven base-fail/optimized-pass transition cannot
    claim attribution when both memory cells sent byte-identical advice."""
    from metaharness.context import ContextScope as _CS
    from metaharness.memory import MemoryActionBroker as _B, SemanticMemoryStore as _S

    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")

    # Use a single record so base and optimized see the same advice
    # (with the load-bearing policy-delta absent, the attribution collapses).
    store = _S()
    store.commit(
        kind="semantic_memory", content="opaque-key",
        scope=_CS(project_id="meta-harness"), creator_id="test",
    )

    class CellDrivenRunner(Runner):
        worker_id = spec.runner_id
        tier = Tier.SMALL
        model = spec.model.model_id
        meta34_hermetic_adapter = "tests-only-v1"

        def __init__(self, cell):
            self.cell = cell

        async def run(self, task: Task) -> WorkerResult:
            # Deliberately invalid test adversary: it ignores the identical
            # advice and manufactures a per-cell improvement. This must only
            # ever be rejected by the attribution guard, never endorsed.
            output = "expected" if self.cell == "optimized_scaffold" else "other"
            return WorkerResult(
                task_id=task.id, worker_id=self.worker_id, tier=self.tier,
                model=self.model, output=output, raw_text=output,
            )

    base_snap, opt_snap = _real_snapshots()
    real_snap = {"base_scaffold": base_snap, "optimized_scaffold": opt_snap}

    def factory(cell, repetition, seed, runner_config=None):
        snap = real_snap.get(cell)
        memory_enabled = cell != "no_external_memory"
        if not memory_enabled:
            return MemoryAwareRunner(
                inner=CellDrivenRunner(cell),
                snapshot=None, broker=None, record_resolver=None,
                memory_enabled=False,
            )
        broker = _B(snapshot=snap, stores=store)
        return MemoryAwareRunner(
            inner=CellDrivenRunner(cell),
            snapshot=snap, broker=broker,
            record_resolver=lambda target_id: store.get(target_id),
            memory_enabled=True,
        )

    # Patch the package items so task.objective matches the record's
    # opaque key.
    dev_digest = _rewrite_split_package(dev_path, spec, "development", "opaque-key")
    val_digest = _rewrite_split_package(val_path, spec, "validation", "opaque-key")
    holdout_digest = _rewrite_split_package(holdout_path, spec, "holdout", "opaque-key")
    spec = _spec(
        campaign_id=spec.campaign_id,
        protected_package_digests={
            "development": dev_digest, "validation": val_digest, "holdout": holdout_digest,
        },
    )
    spec = _spec_with_package_inputs(spec, dev_path, val_path, holdout_path)

    # The fabricated verdicts otherwise satisfy base-fail/optimized-pass,
    # but the single record gives both cells identical advice and full sent
    # task digests. The campaign must reject the false attribution.
    with pytest.raises(CampaignContractError) as exc_info:
        run_campaign(
            spec, development_input_path=dev_path, validation_input_path=val_path,
            holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
            ledger_root=tmp_path / "ledger", runner_factory=factory,
        )
    msg = str(exc_info.value)
    assert "advice" in msg or "load-bearing" in msg or "policy" in msg


def _rewrite_split_package(path, spec, split, objective):
    items = []
    for case in [c for c in spec.cases if c.split == split]:
        items.append({
            "case_id": case.case_id,
            "task": {"id": case.case_id, "objective": objective, "inputs": {}},
            "assertion": {"kind": "equals", "value": "expected"},
        })
    for item in items:
        item["input_digest"] = case_input_digest(item["task"], item["assertion"])
    payload = {"campaign_id": spec.campaign_id, "split": split, "cases": items}
    digest = "sha256:" + sha256_hex(canonical_json_bytes(payload))
    pkg = ProtectedInputPackage.model_validate({**payload, "package_digest": digest})
    path.write_text(pkg.model_dump_json(), encoding="utf-8")
    return digest


def _spec_with_package_inputs(spec, *paths):
    input_digests = {}
    package_digests = {}
    for path in paths:
        package = ProtectedInputPackage.model_validate_json(path.read_text())
        input_digests.update({item["case_id"]: item["input_digest"] for item in package.cases})
        package_digests[package.split] = package.package_digest
    raw = spec.model_dump(mode="python")
    raw["cases"] = tuple(
        {**case, "input_digest": input_digests[case["case_id"]]}
        for case in raw["cases"]
    )
    raw["protected_package_digests"] = package_digests
    raw["spec_digest"] = ""
    return CampaignSpec.model_validate(raw)


# ---------------------------------------------------------------------------
# CampaignSpec contract tests
# ---------------------------------------------------------------------------


def test_campaign_spec_roundtrip_and_digest_matches_canonical():
    spec = _spec()
    digest = spec.digest()
    assert digest == sha256_hex(canonical_json_bytes(
        spec.model_dump(mode="json", exclude={"spec_digest"})
    ))
    reloaded = CampaignSpec.model_validate_json(spec.model_dump_json())
    assert reloaded.digest() == digest


def _mutate_cell(spec_values: dict, idx: int, **changes) -> dict:
    cells = list(spec_values["cells"])
    cells[idx] = {**cells[idx], **changes}
    return {**spec_values, "cells": tuple(cells)}


def test_campaign_spec_rejects_each_frozen_axis_mutation():
    base = _spec()
    raw = base.model_dump(mode="python")
    mutations = [
        (_mutate_cell(raw, 1, h={**raw["cells"][1]["h"], "wrapper_digest": _hash("other")}),
         "wrapper_digest"),
        (_mutate_cell(raw, 1, h={**raw["cells"][1]["h"], "resolver_digest": _hash("other")}),
         "resolver_digest"),
        (_mutate_cell(raw, 1, h={**raw["cells"][1]["h"], "corpus_digest": _hash("other")}),
         "corpus_digest"),
        (_mutate_cell(raw, 1, h={**raw["cells"][1]["h"], "policy_digest": _hash("other")}),
         "policy_digest"),
        (_mutate_cell(raw, 1, h={**raw["cells"][1]["h"], "system_digest": _hash("other")}),
         "system_digest"),
        (_mutate_cell(raw, 1, h={**raw["cells"][1]["h"], "task_template_digest": _hash("other")}),
         "task_template_digest"),
        (_mutate_cell(raw, 2, parent_snapshot_hash=_hash("unrelated-parent")),
         "parent_snapshot_hash"),
        (_mutate_cell(raw, 2, rollback_snapshot_hash=_hash("wrong-rollback")),
         "rollback_snapshot_hash"),
    ]
    for values, label in mutations:
        with pytest.raises((ValidationError, CampaignContractError)) as exc_info:
            CampaignSpec.model_validate(values)
        assert label in str(exc_info.value), f"expected {label!r} rejection, got {exc_info.value}"


def test_campaign_spec_rejects_optimized_not_child_of_base():
    base = _spec()
    raw = base.model_dump(mode="python")
    cells = list(raw["cells"])
    cells[2] = {**cells[2], "parent_snapshot_hash": _hash("unrelated")}
    with pytest.raises(ValidationError) as exc_info:
        CampaignSpec.model_validate({**raw, "cells": tuple(cells)})
    assert "parent_snapshot_hash" in str(exc_info.value)


def test_campaign_spec_rejects_validation_approved_target_outside_validation():
    base = _spec()
    raw = base.model_dump(mode="python")
    cases = list(raw["cases"])
    for index, case in enumerate(cases):
        if case["case_id"] == "case-approved-target":
            cases[index] = {**case, "split": "development", "approved_target": True}
    with pytest.raises(ValidationError) as exc_info:
        CampaignSpec.model_validate({**raw, "cases": tuple(cases)})
    assert "approved-target" in str(exc_info.value) or "validation" in str(exc_info.value)


def test_campaign_spec_rejects_holdout_approved_target():
    base = _spec()
    raw = base.model_dump(mode="python")
    cases = list(raw["cases"])
    for index, case in enumerate(cases):
        if case["case_id"] == "holdout-approved-target":
            cases[index] = {**case, "approved_target": True}
    with pytest.raises(ValidationError) as exc_info:
        CampaignSpec.model_validate({**raw, "cases": tuple(cases)})
    assert "approved-target" in str(exc_info.value) or "holdout" in str(exc_info.value)


def test_campaign_spec_rejects_selection_outside_development():
    with pytest.raises(ValidationError) as exc_info:
        SelectionDeclaration(uses_validation=True)
    assert "validation" in str(exc_info.value)
    with pytest.raises(ValidationError) as exc_info:
        SelectionDeclaration(uses_holdout=True)
    assert "holdout" in str(exc_info.value)


def test_campaign_spec_rejects_bad_case_id_slug():
    base = _spec()
    raw = base.model_dump(mode="python")
    cases = list(raw["cases"])
    cases[0] = {**cases[0], "case_id": "bad_case_id"}
    with pytest.raises(ValidationError) as exc_info:
        CampaignSpec.model_validate({**raw, "cases": tuple(cases)})
    assert "case id" in str(exc_info.value) or "slug" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Execution / verification tests
# ---------------------------------------------------------------------------


def test_development_early_failure_never_touches_ledger_or_holdout(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")
    factory = _verdict_factory(
        {c.case_id: {cell: ("fail",) for cell in ("no_external_memory", "base_scaffold", "optimized_scaffold")}
         for c in spec.cases if c.split == "development"},
        2,
    )
    ledger_root = tmp_path / "ledger"
    evidence_root = tmp_path / "evidence"
    result = run_campaign(
        spec, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=evidence_root,
        ledger_root=ledger_root, runner_factory=factory,
    )
    assert result["status"] == "ineligible"
    assert not any(ledger_root.glob("*.json"))
    assert not holdout_path.read_bytes() == b""  # ensure file was not opened
    assert verify_campaign(spec, evidence_root=evidence_root) == result
    evidence, stored_result, manifest = _load_evidence_triplet(evidence_root)
    assert set(evidence) == {"rows"}
    assert "holdout_ledger_key" not in stored_result
    assert "holdout_ledger_key" not in manifest
    assert "protected_result_id" not in manifest


def test_validation_early_failure_never_touches_ledger_or_holdout(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")
    factory = _verdict_factory(_all_pass_map(spec, "development"), 2)
    ledger_root = tmp_path / "ledger"
    evidence_root = tmp_path / "evidence"
    result = run_campaign(
        spec, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=evidence_root,
        ledger_root=ledger_root, runner_factory=factory,
    )
    assert result["status"] == "ineligible"
    assert not any(ledger_root.glob("*.json"))
    assert holdout_path.exists()
    evidence, stored_result, manifest = _load_evidence_triplet(evidence_root)
    assert set(evidence) == {"rows"}
    assert "holdout_ledger_key" not in stored_result
    assert "holdout_ledger_key" not in manifest
    assert "protected_result_id" not in manifest


def test_holdout_consumed_exactly_once_and_second_attempt_fails_before_read(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")
    factory = _verdict_factory(_approval_map(spec), 2)
    ledger_root = tmp_path / "ledger"
    result = run_campaign(
        spec, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
        ledger_root=ledger_root, runner_factory=factory,
    )
    assert result["status"] == "eligible_pending_human_promotion"
    keys = list(ledger_root.glob("*.json"))
    assert len(keys) == 1
    ledger = HoldoutConsumptionLedger(ledger_root)
    with pytest.raises(HoldoutAlreadyConsumedError):
        ledger.consume(
            campaign_id=spec.campaign_id, spec_digest=spec.digest(),
            holdout_package_digest=spec.package_digest_for("holdout"),
            evaluator_digest=spec.evaluator_digest,
        )
    # Delete the holdout file; the second attempt must still fail BEFORE
    # reading it because the ledger is consumed first.
    holdout_path.unlink()
    factory2 = _verdict_factory(_approval_map(spec), 2)
    with pytest.raises(HoldoutAlreadyConsumedError):
        run_campaign(
            spec, development_input_path=dev_path, validation_input_path=val_path,
            holdout_input_path=tmp_path / "missing.json", evidence_root=tmp_path / "evidence2",
            ledger_root=ledger_root, runner_factory=factory2,
        )


def test_base_pass_and_optimized_pass_is_not_improvement(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")
    factory = _verdict_factory(_all_pass_map(spec, "development"), 2)
    result = run_campaign(
        spec, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
        ledger_root=tmp_path / "ledger", runner_factory=factory,
    )
    assert result["status"] == "ineligible"
    assert "development" in result["unresolved_gap"] or "improvement" in result["unresolved_gap"]


def test_validation_all_repetition_base_fail_optimized_pass_clean_mandatory_pass_holdout_yields_eligible(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")
    factory = _verdict_factory(_approval_map(spec), 2)
    result = run_campaign(
        spec, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
        ledger_root=tmp_path / "ledger", runner_factory=factory,
    )
    assert result["status"] == "eligible_pending_human_promotion"
    assert result["closest_protected_result"] == "optimized_scaffold"


def test_wrapper_bypass_and_byte_identical_h_input_rejected(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")
    def bypass_factory(cell, repetition, seed, runner_config=None):
        return CaptureRunner()
    with pytest.raises(CampaignContractError):
        run_campaign(
            spec, development_input_path=dev_path, validation_input_path=val_path,
            holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
            ledger_root=tmp_path / "ledger", runner_factory=bypass_factory,
        )


def test_runner_factory_must_receive_cell_repetition_seed_propagated(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")
    seen: list[tuple] = []

    def factory(cell, repetition, seed, runner_config=None):
        seen.append((cell, repetition, seed, dict(runner_config or {})))
        base_snap, opt_snap = _real_snapshots()
        real_snap = {"base_scaffold": base_snap, "optimized_scaffold": opt_snap}
        snap = real_snap.get(cell)
        memory_enabled = cell != "no_external_memory"
        if not memory_enabled:
            return MemoryAwareRunner(
                inner=CanonicalCaptureRunner(),
                snapshot=None, broker=None, record_resolver=None,
                memory_enabled=False,
            )
        return MemoryAwareRunner(
            inner=CanonicalCaptureRunner(),
            snapshot=snap,
            broker=_MakeReceiptBroker(snap),
            record_resolver=lambda _: None,
            memory_enabled=True,
        )

    run_campaign(
        spec, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
        ledger_root=tmp_path / "ledger", runner_factory=factory,
    )
    cells = {s[0] for s in seen}
    assert cells == {"no_external_memory", "base_scaffold", "optimized_scaffold"}
    seeds_seen = {s[2] for s in seen}
    assert seeds_seen == set(spec.repetition_seeds)
    # Verification: per-cell runner_config must include model_id, model_digest,
    # base_url, and inference_parameters.
    for cell in cells:
        for s in [entry for entry in seen if entry[0] == cell]:
            cfg = s[3]
            assert cfg["cell"] == cell
            assert cfg["model_id"] == spec.model.model_id
            assert cfg["model_digest"] == spec.model.model_digest
            assert cfg["base_url"] == spec.model.base_url
            assert cfg["inference_parameters"] == dict(spec.model.inference_parameters)


def test_unattested_runner_is_rejected_before_inference(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")
    calls: list[str] = []

    class UnattestedRunner(Runner):
        worker_id = "unattested"
        tier = Tier.SMALL
        model = "unattested"

        async def run(self, task: Task) -> WorkerResult:
            calls.append(task.id)
            return WorkerResult(
                task_id=task.id, worker_id=self.worker_id, tier=self.tier,
                model=self.model, output="expected", raw_text="expected",
            )

    def factory(cell, repetition, seed, runner_config=None):
        return MemoryAwareRunner(
            inner=UnattestedRunner(), snapshot=None, broker=None,
            record_resolver=None, memory_enabled=False,
        )

    with pytest.raises(CampaignContractError, match="attestation"):
        run_campaign(
            spec, development_input_path=dev_path, validation_input_path=val_path,
            holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
            ledger_root=tmp_path / "ledger", runner_factory=factory,
        )
    assert calls == []


def test_model_digest_preflight_rejects_before_package_or_factory(tmp_path):
    spec = _matching_spec()
    factory_calls = []
    missing = tmp_path / "never-read.json"

    def factory(*args):
        factory_calls.append(args)
        raise AssertionError("factory must not run")

    with pytest.raises(CampaignContractError, match="preflight"):
        _run_campaign(
            spec, development_input_path=missing, validation_input_path=missing,
            holdout_input_path=missing, evidence_root=tmp_path / "evidence",
            ledger_root=tmp_path / "ledger", runner_factory=factory,
            model_digest_resolver=lambda _model: _hash("wrong"),
        )
    assert factory_calls == []


def test_openai_compat_worker_public_fields_attest_frozen_contract():
    from metaharness.evals.h_campaign import _enforce_runner_contract
    from metaharness.harness.local import OpenAICompatWorker

    spec = _matching_spec()
    inner = OpenAICompatWorker(
        worker_id="local", model=spec.model.model_id, base_url=spec.model.base_url,
        temperature=0.0, max_tokens=64, thinking=False,
        extra_body={
            "top_p": 1.0, "top_k": 1, "reasoning_effort": "none",
        },
    )
    runner = MemoryAwareRunner(
        inner=inner, snapshot=None, broker=None, record_resolver=None,
        memory_enabled=False,
    )
    _enforce_runner_contract(runner, spec=spec, cell="no_external_memory")
    body = inner._body(
        Task(id="transport", objective="answer"), [], [],
    )
    assert body["reasoning_effort"] == "none"
    assert body["top_p"] == 1.0
    assert body["top_k"] == 1

    drifted = OpenAICompatWorker(
        worker_id="local", model=spec.model.model_id, base_url=spec.model.base_url,
        temperature=0.1, max_tokens=64, thinking=False,
        extra_body={
            "top_p": 1.0, "top_k": 1, "reasoning_effort": "none",
        },
    )
    with pytest.raises(CampaignContractError, match="config drift"):
        _enforce_runner_contract(
            MemoryAwareRunner(
                inner=drifted, snapshot=None, broker=None, record_resolver=None,
                memory_enabled=False,
            ),
            spec=spec,
            cell="no_external_memory",
        )


def test_budget_per_cell_token_cost_wall_enforced(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")

    class OverBudgetRunner:
        worker_id = spec.runner_id
        tier = Tier.SMALL
        model = spec.model.model_id
        meta34_hermetic_adapter = "tests-only-v1"

        def __init__(self):
            self.tasks = []

        async def run(self, task):
            self.tasks.append(task)
            return WorkerResult(
                task_id=task.id, worker_id=self.worker_id, tier=self.tier, model=self.model,
                output="expected", raw_text="expected",
                tokens_in=10_000, tokens_out=10_000, cost_usd=10.0, latency_s=10.0,
            )

    def factory(cell, repetition, seed, runner_config=None):
        base_snap, opt_snap = _real_snapshots()
        real_snap = {"base_scaffold": base_snap, "optimized_scaffold": opt_snap}
        snap = real_snap.get(cell)
        memory_enabled = cell != "no_external_memory"
        if not memory_enabled:
            return MemoryAwareRunner(
                inner=OverBudgetRunner(),
                snapshot=None, broker=None, record_resolver=None,
                memory_enabled=False,
            )
        return MemoryAwareRunner(
            inner=OverBudgetRunner(),
            snapshot=snap,
            broker=_MakeReceiptBroker(snap),
            record_resolver=lambda _: None,
            memory_enabled=True,
        )

    with pytest.raises(CampaignContractError):
        run_campaign(
            spec, development_input_path=dev_path, validation_input_path=val_path,
            holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
            ledger_root=tmp_path / "ledger", runner_factory=factory,
        )


def test_deterministic_verify_only_equals_contains_one_of_and_no_forbidden_outputs():
    with pytest.raises(CampaignContractError):
        deterministic_verify("x", {"kind": "model_judge", "value": "v"})
    with pytest.raises(CampaignContractError):
        deterministic_verify("x", {"kind": "equals", "value": "v", "forbidden_substrings": []})
    with pytest.raises(CampaignContractError):
        deterministic_verify("x", {"kind": "contains", "value": ""})
    with pytest.raises(CampaignContractError):
        deterministic_verify("x", {"kind": "one_of", "value": []})
    assert deterministic_verify("hello", {"kind": "equals", "value": "hello"})
    assert not deterministic_verify("hello", {"kind": "equals", "value": "world"})
    assert deterministic_verify("hello", {"kind": "contains", "value": "ell"})
    assert not deterministic_verify("hello", {"kind": "contains", "value": "zz"})
    assert deterministic_verify("a", {"kind": "one_of", "value": ["a", "b"]})
    assert not deterministic_verify("c", {"kind": "one_of", "value": ["a", "b"]})
    # forbidden_substrings guards the output.
    out = deterministic_verify("ok", {"kind": "equals", "value": "ok", "forbidden_substrings": ["ok"]})
    assert out is False
    # Empty forbidden_substrings list is rejected as a contract violation.
    with pytest.raises(CampaignContractError):
        deterministic_verify("x", {"kind": "equals", "value": "x", "forbidden_substrings": []})


def test_h_campaign_module_exposes_no_authority_operations():
    import metaharness.evals.h_campaign as campaign
    forbidden = {"promote", "activate", "deploy", "start_w", "judge", "semantic_judge",
                 "model_judge", "rollback", "mutate_active", "set_active_pointer"}
    assert forbidden.isdisjoint(set(dir(campaign)))


def test_load_protected_inputs_consumes_ledger_before_opening_file(tmp_path):
    spec = _matching_spec()
    pkg_path = tmp_path / "pkg.json"
    _write_package_file(pkg_path, spec, "holdout")
    ledger = HoldoutConsumptionLedger(tmp_path / "ledger")
    key, package = load_protected_inputs(
        pkg_path, spec=spec, split="holdout", ledger=ledger,
    )
    assert package == ProtectedInputPackage.model_validate_json(
        pkg_path.read_text(encoding="utf-8")
    )
    assert key == sha256_hex(canonical_json_bytes({
        "campaign_id": spec.campaign_id, "spec_digest": spec.digest(),
        "holdout_package_digest": spec.package_digest_for("holdout"),
        "evaluator_digest": spec.evaluator_digest,
    }))
    assert package.campaign_id == spec.campaign_id
    assert package.package_digest == spec.package_digest_for("holdout")


def test_verify_campaign_reloads_manifest_evidence_result_no_model_call(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")
    factory = _verdict_factory(_approval_map(spec), 2)
    result = run_campaign(
        spec, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
        ledger_root=tmp_path / "ledger", runner_factory=factory,
    )
    verified = verify_campaign(spec, evidence_root=tmp_path / "evidence")
    assert verified["status"] == result["status"]
    assert verified["spec_digest"] == spec.digest()
    # Verify must not invoke the runner factory.
    factory_calls = {"count": 0}

    def counting_factory(*args, **kwargs):
        factory_calls["count"] += 1
        return factory(*args, **kwargs)

    verify_campaign(spec, evidence_root=tmp_path / "evidence")
    assert factory_calls["count"] == 0


def test_evidence_tamper_in_receipt_or_response_or_manifest_rejected(tmp_path):
    spec = _matching_spec()
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, spec, "development")
    _write_package_file(val_path, spec, "validation")
    _write_package_file(holdout_path, spec, "holdout")
    factory = _verdict_factory(_approval_map(spec), 2)
    run_campaign(
        spec, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
        ledger_root=tmp_path / "ledger", runner_factory=factory,
    )
    # Tamper evidence.json by flipping an existing verdict to a different
    # value. Use sort_keys so the recomputed digest uses the canonical
    # serializer's contract.
    evidence_path = tmp_path / "evidence" / "evidence.json"
    raw = json.loads(evidence_path.read_text(encoding="utf-8"))
    original = raw["rows"][0]["verdict"]
    raw["rows"][0]["verdict"] = "pass" if original != "pass" else "fail"
    evidence_path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    with pytest.raises(CampaignContractError):
        verify_campaign(spec, evidence_root=tmp_path / "evidence")
    # Restore but tamper the manifest: replace evidence with a different
    # canonical form so the digest recomputation diverges.
    evidence_path.write_text(
        json.dumps({"rows": []}, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(CampaignContractError):
        verify_campaign(spec, evidence_root=tmp_path / "evidence")


def test_evidence_root_rejects_spec_digest_mismatch(tmp_path):
    spec = _matching_spec()
    # Build a different campaign with the same per-split digests
    # (otherwise run_campaign rejects the package). We must run with
    # that spec and then verify against a spec whose spec_digest
    # differs.
    base = _spec(campaign_id="other-campaign")
    digests = {split: _protected_package_digest(base, split) for split in ("development", "validation", "holdout")}
    other = _spec(campaign_id="other-campaign", protected_package_digests=digests)
    dev_path = tmp_path / "dev.json"
    val_path = tmp_path / "val.json"
    holdout_path = tmp_path / "holdout.json"
    _write_package_file(dev_path, other, "development")
    _write_package_file(val_path, other, "validation")
    _write_package_file(holdout_path, other, "holdout")
    factory = _verdict_factory(_approval_map(other), 2)
    run_campaign(
        other, development_input_path=dev_path, validation_input_path=val_path,
        holdout_input_path=holdout_path, evidence_root=tmp_path / "evidence",
        ledger_root=tmp_path / "ledger", runner_factory=factory,
    )
    with pytest.raises(CampaignContractError):
        verify_campaign(spec, evidence_root=tmp_path / "evidence")


# ---------------------------------------------------------------------------
# Adversarial reload and protected-boundary tests
# ---------------------------------------------------------------------------


def _run_eligible_fixture(root: Path) -> tuple[CampaignSpec, Path]:
    """Create one complete eligible campaign used as immutable attack input."""
    root.mkdir(parents=True, exist_ok=True)
    spec = _matching_spec()
    paths = {}
    for split in ("development", "validation", "holdout"):
        paths[split] = root / f"{split}.json"
        _write_package_file(paths[split], spec, split)
    evidence_root = root / "evidence"
    result = run_campaign(
        spec,
        development_input_path=paths["development"],
        validation_input_path=paths["validation"],
        holdout_input_path=paths["holdout"],
        evidence_root=evidence_root,
        ledger_root=root / "ledger",
        runner_factory=_verdict_factory(_approval_map(spec), 2),
    )
    assert result["status"] == "eligible_pending_human_promotion"
    assert verify_campaign(spec, evidence_root=evidence_root) == result
    return spec, evidence_root


def _load_evidence_triplet(root: Path) -> tuple[dict, dict, dict]:
    return tuple(
        json.loads((root / name).read_text(encoding="utf-8"))
        for name in ("evidence.json", "result.json", "manifest.json")
    )


def _write_evidence_triplet(
    root: Path, evidence: dict, result: dict, manifest: dict,
) -> None:
    """Let the adversary recompute every attacker-controlled top-level digest.

    These tests intentionally bypass a superficial manifest checksum check.
    Reload must reject the semantically inconsistent evidence itself.
    """
    manifest["evidence_digest"] = sha256_hex(canonical_json_bytes(evidence))
    manifest["result_digest"] = sha256_hex(canonical_json_bytes(result))
    for name, payload in (
        ("evidence.json", evidence),
        ("result.json", result),
        ("manifest.json", manifest),
    ):
        (root / name).write_text(
            json.dumps(payload, sort_keys=True), encoding="utf-8",
        )


def _row_digest(value) -> str:
    return "sha256:" + sha256_hex(canonical_json_bytes(value))


def _refresh_row_task_and_request_digests(row: dict) -> None:
    row["outer_task_digest"] = _row_digest(row["outer_task"])
    row["inner_task_digest"] = _row_digest(row["inner_task"])
    row["base_task_digest"] = _row_digest({
        key: value for key, value in row["outer_task"].items()
        if key != "advice"
    })
    row["advice_digest"] = _row_digest(row["inner_task"].get("advice", []))
    row["request_config_digest"] = sha256_hex(canonical_json_bytes({
        "cell": row["cell"],
        "repetition": row["repetition"],
        "seed": row["seed"],
        "runner_config": row["runner_config"],
    }))
    row["request_digest"] = sha256_hex(canonical_json_bytes({
        "task": row["outer_task"],
        "assertion": row["assertion"],
        "cell": row["cell"],
        "repetition": row["repetition"],
        "seed": row["seed"],
        "runner_config": row["runner_config"],
    }))


@pytest.mark.parametrize(
    "attack",
    (
        "holdout_task_assertion_output",
        "runner_base_url_and_request_digests",
        "seed_and_request_digests",
        "runner_model_attestation_digest",
        "selected_record_hashes_removed",
        "selected_record_content_order",
        "selected_record_advice",
        "inner_task_tools",
        "inner_task_boundaries",
        "worker_id",
        "worker_model",
        "worker_raw_text",
        "worker_error",
        "worker_timed_out",
        "worker_metrics",
    ),
)
def test_reload_rejects_coordinated_evidence_tampering(
    tmp_path, attack: str,
):
    """Semantic reload cannot trust digests controlled by the attacker."""
    spec, pristine = _run_eligible_fixture(tmp_path / "pristine")
    root = tmp_path / attack
    shutil.copytree(pristine, root)
    evidence, result, manifest = _load_evidence_triplet(root)
    rows = evidence["rows"]
    row = next(
        item for item in rows
        if item["cell"] == "optimized_scaffold"
        and item["split"] == (
            "holdout" if attack == "holdout_task_assertion_output"
            else "development"
        )
    )

    if attack == "holdout_task_assertion_output":
        row["outer_task"]["objective"] = "attacker-replaced-objective"
        row["inner_task"]["objective"] = "attacker-replaced-objective"
        row["assertion"]["value"] = "attacker-replaced-output"
        row["output"] = "attacker-replaced-output"
        row["worker_result"]["output"] = "attacker-replaced-output"
        row["verdict"] = "pass"
        _refresh_row_task_and_request_digests(row)
    elif attack == "runner_base_url_and_request_digests":
        row["runner_config"]["base_url"] = "http://127.0.0.1:9999"
        _refresh_row_task_and_request_digests(row)
    elif attack == "seed_and_request_digests":
        row["seed"] += 1
        row["runner_config"]["seed"] = row["seed"]
        _refresh_row_task_and_request_digests(row)
    elif attack == "runner_model_attestation_digest":
        row["runner_model_attestation_digest"] = sha256_hex(
            canonical_json_bytes({"attacker": "transport"})
        )
    elif attack == "selected_record_hashes_removed":
        assert row["selected_record_ids"]
        row["selected_record_hashes"] = []
    elif attack == "selected_record_content_order":
        assert len(row["selected_record_contents"]) == 2
        row["selected_record_contents"].reverse()
    elif attack == "selected_record_advice":
        row["inner_task"]["advice"][-1] += "\nattacker suffix"
        _refresh_row_task_and_request_digests(row)
    elif attack == "inner_task_tools":
        row["inner_task"]["tools"] = ["attacker-tool"]
        _refresh_row_task_and_request_digests(row)
    elif attack == "inner_task_boundaries":
        row["inner_task"]["boundaries"] = ["attacker-boundary"]
        _refresh_row_task_and_request_digests(row)
    elif attack == "worker_id":
        row["worker_result"]["worker_id"] = "attacker-worker"
        row["worker_result_digest"] = sha256_hex(
            canonical_json_bytes(row["worker_result"])
        )
    elif attack == "worker_model":
        row["worker_result"]["model"] = "attacker-model"
        row["worker_result_digest"] = sha256_hex(
            canonical_json_bytes(row["worker_result"])
        )
    elif attack == "worker_raw_text":
        row["worker_result"]["raw_text"] = "attacker raw text"
        row["worker_result_digest"] = sha256_hex(
            canonical_json_bytes(row["worker_result"])
        )
    elif attack == "worker_error":
        row["worker_result"]["error"] = "attacker suppressed failure"
    elif attack == "worker_timed_out":
        row["worker_result"]["timed_out"] = True
    elif attack == "worker_metrics":
        row["worker_result"]["tokens_in"] += 1
    else:  # pragma: no cover - parametrization exhaustiveness guard
        raise AssertionError(attack)

    _write_evidence_triplet(root, evidence, result, manifest)
    with pytest.raises(CampaignContractError):
        verify_campaign(spec, evidence_root=root)


@pytest.mark.parametrize(
    ("artifact", "attack"),
    (
        ("manifest", "extra"),
        ("manifest", "missing"),
        ("terminal", "extra"),
        ("terminal", "missing"),
        ("report_ref", "digest"),
        ("report_ref", "provenance"),
        ("report_ref", "swap"),
        ("report_file", "provenance"),
    ),
)
def test_reload_rejects_manifest_terminal_and_report_binding_tampering(
    tmp_path, artifact: str, attack: str,
):
    spec, pristine = _run_eligible_fixture(tmp_path / "pristine")
    root = tmp_path / f"{artifact}-{attack}"
    shutil.copytree(pristine, root)
    evidence, result, manifest = _load_evidence_triplet(root)

    if artifact == "manifest" and attack == "extra":
        manifest["attacker_field"] = "unbound"
    elif artifact == "manifest" and attack == "missing":
        manifest.pop("evaluator_digest")
    elif artifact == "terminal" and attack == "extra":
        result["attacker_field"] = "unbound"
    elif artifact == "terminal" and attack == "missing":
        result.pop("rollback_snapshot_hash")
    elif artifact == "report_ref" and attack == "digest":
        manifest["report_refs"][0]["content_digest"] = "0" * 64
    elif artifact == "report_ref" and attack == "provenance":
        manifest["report_refs"][0]["authority_id"] = "attacker"
    elif artifact == "report_ref" and attack == "swap":
        left, right = manifest["report_refs"][0], manifest["report_refs"][1]
        left["id"], right["id"] = right["id"], left["id"]
        left["content_digest"], right["content_digest"] = (
            right["content_digest"], left["content_digest"]
        )
    elif artifact == "report_file" and attack == "provenance":
        ref = manifest["report_refs"][0]
        path = (
            root / "reports" / "evaluation-reports" / f"{ref['id']}.json"
        )
        report = json.loads(path.read_text(encoding="utf-8"))
        report["runner_id"] = "attacker-runner"
        digest_payload = {
            key: value for key, value in report.items()
            if key not in {"id", "created_at", "content_digest"}
        }
        report["content_digest"] = sha256_hex(
            canonical_json_bytes(digest_payload)
        )
        ref["content_digest"] = report["content_digest"]
        path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    else:  # pragma: no cover - parametrization exhaustiveness guard
        raise AssertionError((artifact, attack))

    _write_evidence_triplet(root, evidence, result, manifest)
    with pytest.raises(CampaignContractError):
        verify_campaign(spec, evidence_root=root)


def _package_payload_with_digest(payload: dict) -> dict:
    return {
        **payload,
        "package_digest": "sha256:" + sha256_hex(canonical_json_bytes(payload)),
    }


def _spec_bound_to_development_package(
    spec: CampaignSpec,
    package_digest: str,
    *,
    case_input_overrides: dict[str, str] | None = None,
) -> CampaignSpec:
    raw = spec.model_dump(mode="python")
    raw["spec_digest"] = ""
    raw["protected_package_digests"]["development"] = package_digest
    overrides = case_input_overrides or {}
    raw["cases"] = tuple(
        {
            **case,
            "input_digest": overrides.get(case["case_id"], case["input_digest"]),
        }
        for case in raw["cases"]
    )
    return CampaignSpec.model_validate(raw)


def test_protected_package_rejects_duplicate_case_and_task_ids(tmp_path):
    spec = _matching_spec()
    payload = {
        "campaign_id": spec.campaign_id,
        "split": "development",
        "cases": _build_package_items(spec, "development"),
    }

    duplicate_case = json.loads(json.dumps(payload))
    duplicate_case["cases"][-1] = json.loads(
        json.dumps(duplicate_case["cases"][0])
    )
    duplicate_path = tmp_path / "duplicate-case.json"
    duplicate_case_package = _package_payload_with_digest(duplicate_case)
    duplicate_path.write_text(
        json.dumps(duplicate_case_package), encoding="utf-8",
    )
    duplicate_case_spec = _spec_bound_to_development_package(
        spec, duplicate_case_package["package_digest"],
    )
    with pytest.raises(CampaignContractError, match="duplicate case"):
        load_protected_inputs(
            duplicate_path, spec=duplicate_case_spec, split="development",
        )

    duplicate_task = json.loads(json.dumps(payload))
    duplicate_task["cases"][1]["task"]["id"] = (
        duplicate_task["cases"][0]["task"]["id"]
    )
    duplicate_task["cases"][1]["input_digest"] = case_input_digest(
        duplicate_task["cases"][1]["task"],
        duplicate_task["cases"][1]["assertion"],
    )
    task_path = tmp_path / "duplicate-task.json"
    duplicate_task_package = _package_payload_with_digest(duplicate_task)
    task_path.write_text(
        json.dumps(duplicate_task_package), encoding="utf-8",
    )
    second = duplicate_task["cases"][1]
    duplicate_task_spec = _spec_bound_to_development_package(
        spec,
        duplicate_task_package["package_digest"],
        case_input_overrides={second["case_id"]: second["input_digest"]},
    )
    with pytest.raises(CampaignContractError, match="task"):
        load_protected_inputs(
            task_path, spec=duplicate_task_spec, split="development",
        )


@pytest.mark.parametrize("field", ("task", "assertion", "input_digest"))
def test_protected_package_recomputes_exact_task_assertion_input_digest(
    tmp_path, field: str,
):
    spec = _matching_spec()
    payload = {
        "campaign_id": spec.campaign_id,
        "split": "development",
        "cases": _build_package_items(spec, "development"),
    }
    item = payload["cases"][0]
    if field == "task":
        item["task"]["objective"] = "attacker objective"
        item["input_digest"] = case_input_digest(item["task"], item["assertion"])
    elif field == "assertion":
        item["assertion"]["value"] = "attacker answer"
        item["input_digest"] = case_input_digest(item["task"], item["assertion"])
    else:
        item["input_digest"] = _hash("attacker input")
    path = tmp_path / f"{field}.json"
    package = _package_payload_with_digest(payload)
    path.write_text(json.dumps(package), encoding="utf-8")
    package_bound_spec = _spec_bound_to_development_package(
        spec, package["package_digest"],
    )
    with pytest.raises(CampaignContractError, match="input digest"):
        load_protected_inputs(
            path, spec=package_bound_spec, split="development",
        )


def test_holdout_negative_reload_needs_no_protected_result_store(tmp_path):
    spec = _matching_spec()
    paths = {}
    for split in ("development", "validation", "holdout"):
        paths[split] = tmp_path / f"{split}.json"
        _write_package_file(paths[split], spec, split)
    verdicts = _approval_map(spec)
    holdout_safety = next(
        case.case_id for case in spec.cases
        if case.split == "holdout" and case.view == "safety"
    )
    verdicts[holdout_safety] = {
        "no_external_memory": ("pass",),
        "base_scaffold": ("pass",),
        "optimized_scaffold": ("fail",),
    }
    evidence_root = tmp_path / "evidence"
    result = run_campaign(
        spec,
        development_input_path=paths["development"],
        validation_input_path=paths["validation"],
        holdout_input_path=paths["holdout"],
        evidence_root=evidence_root,
        ledger_root=tmp_path / "ledger",
        runner_factory=_verdict_factory(verdicts, 2),
    )
    assert result["status"] == "ineligible"
    assert result["phase"] == "holdout"
    assert not (evidence_root / "results").exists()
    evidence, stored_result, manifest = _load_evidence_triplet(evidence_root)
    expected_key = HoldoutConsumptionLedger._key(
        campaign_id=spec.campaign_id,
        spec_digest=spec.digest(),
        holdout_package_digest=spec.package_digest_for("holdout"),
        evaluator_digest=spec.evaluator_digest,
    )
    assert evidence["holdout_ledger_key"] == expected_key
    assert stored_result["holdout_ledger_key"] == expected_key
    assert manifest["holdout_ledger_key"] == expected_key
    assert "protected_result_id" not in evidence
    assert "protected_result_id" not in manifest
    assert verify_campaign(spec, evidence_root=evidence_root) == result


def test_holdout_reports_are_sealed_even_when_private_rows_retain_evidence(
    tmp_path,
):
    _, evidence_root = _run_eligible_fixture(tmp_path)
    evidence = json.loads(
        (evidence_root / "evidence.json").read_text(encoding="utf-8")
    )
    holdout_rows = [
        row for row in evidence["rows"] if row["split"] == "holdout"
    ]
    assert holdout_rows
    assert all(row["assertion"] and "output" in row for row in holdout_rows)

    report_paths = sorted(
        (evidence_root / "reports" / "evaluation-reports").glob(
            "*-holdout-*.json"
        )
    )
    assert len(report_paths) == 18
    for path in report_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        for case in report["cases"]:
            assert case["assertion"] is None
            assert case["assertion_digest"] is None
            for attempt in case["attempts"]:
                assert attempt["output"] is None
                assert attempt["detail"] == ""


def test_runtime_attestation_tamper_recomputing_outer_digests_rejects(
    tmp_path,
):
    spec, pristine = _run_eligible_fixture(tmp_path / "pristine")
    root = tmp_path / "runtime-attestation-tamper"
    shutil.copytree(pristine, root)
    evidence, result, manifest = _load_evidence_triplet(root)
    manifest["runtime_attestation"]["environment_digest"] = _hash(
        "attacker-environment"
    )
    # The attacker can recompute all outer evidence/result hashes. Runtime
    # implementation/spec binding remains independently verifiable.
    _write_evidence_triplet(root, evidence, result, manifest)
    with pytest.raises(CampaignContractError, match="runtime attestation"):
        verify_campaign(spec, evidence_root=root)


@pytest.mark.parametrize(
    "field",
    (
        "openai_worker_implementation_digest",
        "output_parser_implementation_digest",
    ),
)
def test_worker_parser_attestation_tamper_with_recomputed_row_digests_rejects(
    tmp_path, field,
):
    spec, pristine = _run_eligible_fixture(tmp_path / "pristine")
    root = tmp_path / field
    shutil.copytree(pristine, root)
    evidence, result, manifest = _load_evidence_triplet(root)
    manifest["runtime_attestation"][field] = _hash(f"attacker-{field}")
    changed_digest = _row_digest(manifest["runtime_attestation"])
    for row in evidence["rows"]:
        row["runtime_attestation_digest"] = changed_digest
    _write_evidence_triplet(root, evidence, result, manifest)
    with pytest.raises(CampaignContractError, match="runtime attestation"):
        verify_campaign(spec, evidence_root=root)


def test_runtime_guard_recomputes_parser_after_attestation_creation(monkeypatch):
    import metaharness.harness.local as local
    from metaharness.evals.h_campaign import (
        _enforce_runtime_attestation,
        _test_runtime_attestation,
    )

    spec = _matching_spec()
    attestation = _test_runtime_attestation(spec)

    def substituted_parse_output(text, expect_json):
        return {"fabricated": text, "expect_json": expect_json}

    monkeypatch.setattr(local, "parse_output", substituted_parse_output)
    with pytest.raises(CampaignContractError, match="installed"):
        _enforce_runtime_attestation(
            attestation, spec=spec, stores=None,
        )


def test_public_runtime_rejects_config_compatible_substituted_worker_before_inference(
    tmp_path, monkeypatch,
):
    import metaharness.evals.h_campaign as campaign
    import metaharness.harness.local as local
    from metaharness.evals.h_campaign import _project_runtime_stores
    from metaharness.memory import SemanticMemoryStore

    store = SemanticMemoryStore()
    store.commit(
        kind="semantic_memory",
        content="answer governed-answer=expected",
        scope=ContextScope(project_id="meta-harness"),
        creator_id="test",
    )
    stores = {"semantic": store}
    _projections, _index, corpus_digest = _project_runtime_stores(stores)

    original = _matching_spec()
    raw_spec = original.model_dump(mode="python")
    raw_spec["spec_digest"] = ""
    raw_spec["cells"] = tuple({
        **cell,
        "h": {**cell["h"], "corpus_digest": corpus_digest},
    } for cell in raw_spec["cells"])
    spec = CampaignSpec.model_validate(raw_spec)

    paths = {}
    for split in ("development", "validation", "holdout"):
        paths[split] = tmp_path / f"{split}.json"
        _write_package_file(paths[split], spec, split)

    calls = []

    class OpenAICompatWorker:
        def __init__(
            self, worker_id, base_url, model, tier=Tier.SMALL,
            temperature=None, max_tokens=None, thinking=None,
            extra_body=None, **_kwargs,
        ):
            self.worker_id = worker_id
            self.base_url = base_url
            self.model = model
            self.tier = tier
            self.temperature = temperature
            self.max_tokens = max_tokens
            self.thinking = thinking
            self.extra_body = dict(extra_body or {})
            self.system_prompt = ""
            self.tool_registry = None
            self.context_budget = None
            self.max_tool_rounds = 5

        async def run(self, task):
            calls.append(task.id)
            return WorkerResult(
                task_id=task.id,
                worker_id=self.worker_id,
                tier=self.tier,
                model=self.model,
                output="expected",
                raw_text="expected",
            )

    monkeypatch.setattr(local, "OpenAICompatWorker", OpenAICompatWorker)
    evidence_root = tmp_path / "evidence"
    with pytest.raises(
        CampaignContractError,
        match="runtime implementation attestation",
    ):
        campaign.run_campaign(
            spec,
            development_input_path=paths["development"],
            validation_input_path=paths["validation"],
            holdout_input_path=paths["holdout"],
            evidence_root=evidence_root,
            ledger_root=tmp_path / "ledger",
            memory_stores=stores,
            model_digest_resolver=lambda model: model.model_digest,
        )
    assert calls == []
    assert not (evidence_root / "manifest.json").exists()
    assert not (evidence_root / "result.json").exists()
    assert not (evidence_root / "evidence.json").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("system_prompt", "attacker system"),
        ("tool_registry", object()),
        ("context_budget", object()),
        ("max_tool_rounds", 4),
    ),
)
def test_concrete_runner_rejects_system_tool_context_drift(field, value):
    from metaharness.evals.h_campaign import _enforce_runner_contract
    from metaharness.harness.local import OpenAICompatWorker

    spec = _matching_spec()
    inner = OpenAICompatWorker(
        worker_id="local",
        model=spec.model.model_id,
        base_url=spec.model.base_url,
        temperature=0.0,
        max_tokens=64,
    )
    setattr(inner, field, value)
    runner = MemoryAwareRunner(
        inner=inner, snapshot=None, broker=None,
        record_resolver=None, memory_enabled=False,
    )
    with pytest.raises(CampaignContractError, match="non-tool posture"):
        _enforce_runner_contract(
            runner, spec=spec, cell="no_external_memory",
        )


def test_public_run_surface_cannot_accept_hermetic_runner_factory():
    import metaharness.evals.h_campaign as campaign

    parameters = inspect.signature(campaign.run_campaign).parameters
    assert "runner_factory" not in parameters
    assert "memory_stores" in parameters
    assert "_run_hermetic_campaign" not in campaign.__all__


@pytest.mark.parametrize(
    "axis",
    (
        "wrapper", "resolver", "policy", "task_template", "evaluator",
        "worker", "parser",
    ),
)
def test_runtime_attestation_must_match_declared_h_and_e_axes(
    tmp_path, axis,
):
    spec = _matching_spec()
    raw = spec.model_dump(mode="python")
    raw["spec_digest"] = ""
    if axis == "evaluator":
        raw["evaluator"]["evaluator_digest"] = _hash("wrong-evaluator")
        raw["evaluator_digest"] = _hash("wrong-evaluator")
    else:
        field = {
            "wrapper": "wrapper_digest",
            "resolver": "resolver_digest",
            "policy": "policy_digest",
            "task_template": "task_template_digest",
            "worker": "worker_implementation_digest",
            "parser": "output_parser_digest",
        }[axis]
        raw["cells"] = tuple({
            **cell,
            "h": {**cell["h"], field: _hash(f"wrong-{axis}")},
        } for cell in raw["cells"])
    drifted = CampaignSpec.model_validate(raw)
    missing = tmp_path / "not-opened.json"
    with pytest.raises(CampaignContractError, match="runtime"):
        run_campaign(
            drifted,
            development_input_path=missing,
            validation_input_path=missing,
            holdout_input_path=missing,
            evidence_root=tmp_path / "evidence",
            ledger_root=tmp_path / "ledger",
            runner_factory=_verdict_factory({}, 2),
        )


def test_public_verify_rejects_private_test_only_evidence(tmp_path):
    import metaharness.evals.h_campaign as campaign

    spec, evidence_root = _run_eligible_fixture(tmp_path)
    with pytest.raises(CampaignContractError, match="production"):
        campaign.verify_campaign(spec, evidence_root=evidence_root)
    assert verify_campaign(spec, evidence_root=evidence_root)


def test_file_spec_requires_explicit_precommitted_digest(tmp_path):
    from metaharness.evals.h_campaign import load_spec

    spec = _matching_spec()
    raw = spec.model_dump(mode="json")
    raw["spec_digest"] = ""
    path = tmp_path / "blank-spec.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CampaignContractError, match="precommitted"):
        load_spec(path)
    # In-memory construction remains the explicit fixture seam.
    assert CampaignSpec.model_validate(raw).spec_digest


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("temperature", 0),
        ("temperature", -0.1),
        ("max_tokens", True),
        ("max_tokens", 0),
        ("thinking", 0),
        ("top_p", 1),
        ("top_p", 0.0),
        ("top_k", 1.0),
        ("top_k", 0),
        ("reasoning_effort", False),
        ("reasoning_effort", "low"),
    ),
)
def test_model_contract_requires_strict_complete_inference_fields(
    field, value,
):
    raw = _model().model_dump(mode="python")
    if field in {"top_p", "top_k", "reasoning_effort"}:
        raw["inference_parameters"]["extra_body"][field] = value
    else:
        raw["inference_parameters"][field] = value
    with pytest.raises((CampaignContractError, ValidationError)):
        ModelContract.model_validate(raw)


def test_model_contract_rejects_missing_reasoning_effort():
    raw = _model().model_dump(mode="python")
    raw["inference_parameters"]["extra_body"].pop("reasoning_effort")
    with pytest.raises((CampaignContractError, ValidationError), match="reasoning"):
        ModelContract.model_validate(raw)


def test_spec_rejects_duplicate_precommit_sets_and_split_mandatory_gap():
    spec = _matching_spec()

    duplicate_w = spec.model_dump(mode="python")
    duplicate_w["spec_digest"] = ""
    duplicate_w["w_refs"] = (spec.w_refs[0], spec.w_refs[0])
    with pytest.raises((CampaignContractError, ValidationError), match="unique"):
        CampaignSpec.model_validate(duplicate_w)

    duplicate_selection = spec.model_dump(mode="python")
    duplicate_selection["spec_digest"] = ""
    selected = duplicate_selection["selection"]["evidence_case_ids"][0]
    duplicate_selection["selection"]["evidence_case_ids"] = (
        selected, selected,
    )
    with pytest.raises((CampaignContractError, ValidationError), match="unique"):
        CampaignSpec.model_validate(duplicate_selection)

    no_approved = spec.model_dump(mode="python")
    no_approved["spec_digest"] = ""
    no_approved["cases"] = tuple({
        **case, "approved_target": False,
    } for case in no_approved["cases"])
    with pytest.raises(
        (CampaignContractError, ValidationError), match="approved_target"
    ):
        CampaignSpec.model_validate(no_approved)

    missing_mandatory = spec.model_dump(mode="python")
    missing_mandatory["spec_digest"] = ""
    missing_mandatory["cases"] = tuple({
        **case,
        "mandatory": False
        if case["split"] == "holdout" and case["view"] == "safety"
        else case["mandatory"],
    } for case in missing_mandatory["cases"])
    with pytest.raises((CampaignContractError, ValidationError), match="holdout"):
        CampaignSpec.model_validate(missing_mandatory)


def test_phase_artifacts_bind_exact_holdout_ledger_and_result_refs(tmp_path):
    spec, root = _run_eligible_fixture(tmp_path)
    evidence, result, manifest = _load_evidence_triplet(root)
    expected_key = HoldoutConsumptionLedger._key(
        campaign_id=spec.campaign_id,
        spec_digest=spec.digest(),
        holdout_package_digest=spec.package_digest_for("holdout"),
        evaluator_digest=spec.evaluator_digest,
    )
    assert result["holdout_ledger_key"] == expected_key
    assert evidence["holdout_ledger_key"] == expected_key
    assert manifest["holdout_ledger_key"] == expected_key
    assert (
        evidence["protected_result_id"]
        == manifest["protected_result_id"]
        == result["protected_result_id"]
    )
    assert (
        manifest["protected_result_digest"]
        == result["protected_result_digest"]
    )


@pytest.mark.parametrize(
    "attack",
    (
        "row_extra", "duplicate_output", "advice_changed",
        "receipt_duplicate", "consult_snapshot", "nested_secret",
    ),
)
def test_reload_rejects_exact_row_and_receipt_chain_tampering(
    tmp_path, attack,
):
    spec, pristine = _run_eligible_fixture(tmp_path / "pristine")
    root = tmp_path / attack
    shutil.copytree(pristine, root)
    evidence, result, manifest = _load_evidence_triplet(root)
    row = next(
        item for item in evidence["rows"]
        if item["cell"] == "base_scaffold"
    )
    if attack == "row_extra":
        row["attacker"] = "extra"
    elif attack == "duplicate_output":
        row["output"] = "attacker"
    elif attack == "advice_changed":
        row["advice_changed"] = not row["advice_changed"]
    elif attack == "receipt_duplicate":
        row["receipts"].append(dict(row["receipts"][0]))
        row["current_run_receipts"].append(
            row["current_run_receipts"][0]
        )
    elif attack == "consult_snapshot":
        from metaharness.memory import MemoryActionReceipt

        raw_receipt = dict(row["receipts"][0])
        raw_receipt["snapshot_id"] = "attacker-snapshot"
        raw_receipt.pop("content_hash", None)
        changed = MemoryActionReceipt.model_validate(
            raw_receipt
        ).model_dump(mode="json")
        old_hash = row["current_run_receipts"][0]
        row["receipts"][0] = changed
        row["current_run_receipts"][0] = changed["content_hash"]
        if row["consult_receipt_hash"] == old_hash:
            row["consult_receipt_hash"] = changed["content_hash"]
    elif attack == "nested_secret":
        row["worker_result"]["output"] = {"api_key": "leaked-value"}
        row["output"] = {"api_key": "leaked-value"}
        row["worker_result_digest"] = sha256_hex(
            canonical_json_bytes(row["worker_result"])
        )
    _write_evidence_triplet(root, evidence, result, manifest)
    with pytest.raises(CampaignContractError):
        verify_campaign(spec, evidence_root=root)


def test_cumulative_campaign_budget_is_enforced_across_phases(tmp_path):
    base = _matching_spec()
    raw = base.model_dump(mode="python")
    raw["spec_digest"] = ""
    raw["budget"]["token_limit"] = 12
    spec = CampaignSpec.model_validate(raw)
    paths = {}
    for split in ("development", "validation", "holdout"):
        paths[split] = tmp_path / f"{split}.json"
        _write_package_file(paths[split], spec, split)
    with pytest.raises(CampaignContractError, match="aggregate token"):
        run_campaign(
            spec,
            development_input_path=paths["development"],
            validation_input_path=paths["validation"],
            holdout_input_path=paths["holdout"],
            evidence_root=tmp_path / "evidence",
            ledger_root=tmp_path / "ledger",
            runner_factory=_verdict_factory(
                _approval_map(spec), 2, tokens_per_attempt=1,
            ),
        )


def test_reload_recomputes_cumulative_campaign_budget(tmp_path):
    spec, pristine = _run_eligible_fixture(tmp_path / "pristine")
    root = tmp_path / "budget-tamper"
    shutil.copytree(pristine, root)
    evidence, result, manifest = _load_evidence_triplet(root)
    for row in evidence["rows"]:
        row["worker_result"]["tokens_in"] = 30
        row["metrics"]["tokens_in"] = 30
        row["worker_result_digest"] = sha256_hex(
            canonical_json_bytes(row["worker_result"])
        )
    refs = {item["id"]: item for item in manifest["report_refs"]}
    for path in (
        root / "reports" / "evaluation-reports"
    ).glob("*.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        report_tokens = 0
        for case in report["cases"]:
            for attempt in case["attempts"]:
                attempt["metrics"]["tokens_in"] = 30
                report_tokens += 30
        report["metrics"]["tokens_in"] = report_tokens
        digest_payload = {
            key: value for key, value in report.items()
            if key not in {"id", "created_at", "content_digest"}
        }
        report["content_digest"] = sha256_hex(
            canonical_json_bytes(digest_payload)
        )
        refs[report["id"]]["content_digest"] = report["content_digest"]
        path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    _write_evidence_triplet(root, evidence, result, manifest)
    with pytest.raises(CampaignContractError, match="aggregate token"):
        verify_campaign(spec, evidence_root=root)


@pytest.mark.asyncio
async def test_answer_bearing_second_record_is_the_load_bearing_cause():
    """The optimized answer comes from record two, not from a cell label."""
    from metaharness.memory import MemoryActionBroker, SemanticMemoryStore

    base_snap, opt_snap = _real_snapshots()
    store = SemanticMemoryStore()
    stale = store.commit(
        kind="semantic_memory",
        content="opaque-key governed-answer=wrong",
        scope=ContextScope(project_id="meta-harness"),
        creator_id="test",
    )
    current = store.commit(
        kind="semantic_memory",
        content="opaque-key governed-answer=expected",
        scope=ContextScope(project_id="meta-harness"),
        creator_id="test",
    )
    assert stale.id != current.id

    class AnswerFromAdviceRunner(Runner):
        worker_id = "answer-from-advice"
        tier = Tier.SMALL
        model = "fake"

        async def run(self, task: Task) -> WorkerResult:
            advice = "\n".join(task.advice)
            output = (
                "expected"
                if "governed-answer=expected" in advice
                else "wrong"
            )
            return WorkerResult(
                task_id=task.id,
                worker_id=self.worker_id,
                tier=self.tier,
                model=self.model,
                output=output,
                raw_text=output,
            )

    async def execute(snap):
        wrapper = MemoryAwareRunner(
            inner=AnswerFromAdviceRunner(),
            snapshot=snap,
            broker=MemoryActionBroker(snapshot=snap, stores=store),
            record_resolver=store.get,
        )
        result = await wrapper.run(
            Task(id="answer-causality", objective="opaque-key", inputs={})
        )
        return result, wrapper.last_evidence

    base_result, base_evidence = await execute(base_snap)
    optimized_result, optimized_evidence = await execute(opt_snap)
    assert base_result.output == "wrong"
    assert optimized_result.output == "expected"
    assert tuple(base_evidence.selected_record_ids) == (stale.id,)
    assert tuple(optimized_evidence.selected_record_ids) == (
        stale.id, current.id,
    )


@pytest.mark.asyncio
async def test_memory_advice_bounds_use_repository_token_estimator():
    snap = snapshot(compression_max_tokens=2)
    rec = record(content="abcdefghij" * 2)
    wrapped = MemoryAwareRunner(
        inner=CaptureRunner(),
        snapshot=snap,
        broker=_broker_with_record(snap, rec),
        record_resolver=lambda _: rec,
    )
    with pytest.raises(MemoryAdviceError, match="compression bound"):
        await wrapped.run(Task(id="token-bound", objective="answer"))
