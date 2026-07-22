"""META-7: CampaignSupervisor happy path + adversarial coverage.

Covers crash/timeout/cancel/worker-error/evaluator-error/late-result paths,
exactly-one terminal receipt, exact budget charging, at-cap-allowed/over-cap-
denied concurrency, interruption + idempotent restart-bounded resume, and
fail-closed journal replay (corrupt/gap/duplicate/mixed-manifest/illegal
transition).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from metaharness.discovery.models import (
    AttemptState,
    CampaignState,
    DiscoveryAssignment,
    DiscoveryEvent,
    DiscoveryEventType,
    DiscoveryRole,
    DiscoveryTerminalOutcome,
)
from metaharness.discovery.supervisor import (
    CampaignSupervisor,
    DiscoveryJournal,
    EvaluationOutcome,
    ExecutionOutcome,
    JournalError,
    SupervisorError,
    SupervisorPoisonedError,
)
from tests.test_discovery_models import make_budgets, make_manifest


def make_assignment(**overrides) -> DiscoveryAssignment:
    defaults: dict = dict(
        assignment_id="asg-1",
        campaign_id="camp-1",
        lineage_id="lin-1",
        attempt_id="att-1",
        role=DiscoveryRole.EXPLORER,
        seed=1,
        sequence=0,
        created_at=0,
    )
    defaults.update(overrides)
    return DiscoveryAssignment(**defaults)


def make_supervisor(tmp_path, *, budgets_overrides=None, ticking_clock=False):
    manifest = make_manifest(budgets=make_budgets(**(budgets_overrides or {})))
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    counter = {"clock": 0}

    def clock():
        if ticking_clock:
            counter["clock"] += 1
        return counter["clock"]

    supervisor = CampaignSupervisor(
        manifest=manifest,
        journal=journal,
        attempt_executor=None,  # set per-test
        proxy_evaluator=None,  # set per-test
        clock=clock,
        # Same deterministic fake clock doubles as the operational AND wall
        # clock so the immutable deadline anchors are reproducible instead
        # of depending on real wall-clock nanoseconds — tests that don't
        # care about timing precision still get fully deterministic replay
        # behavior.
        operational_clock=clock,
        wall_clock=clock,
    )
    return supervisor, manifest, journal


class ManualClock:
    """A clock the test drives explicitly, independent of how many times
    (or in what order) the supervisor internally calls it."""

    def __init__(self, start: int = 0) -> None:
        self.value = start

    def __call__(self) -> int:
        return self.value

    def advance(self, delta: int) -> None:
        self.value += delta


class StepClock:
    """Returns a caller-controlled SEQUENCE of distinct values, one per
    call. Used to prove a "single anchor" claim: if code under test took
    TWO separate clock reads meant to represent the same moment, they would
    observably diverge under a StepClock (unlike a ManualClock, which
    returns the same value for any number of reads between explicit
    `.advance()` calls and so cannot catch a spurious extra read)."""

    def __init__(self, values) -> None:
        self._values = iter(values)
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        try:
            return next(self._values)
        except StopIteration:
            raise AssertionError("StepClock exhausted — more wall-clock reads occurred than the test expected") from None


def make_supervisor_with_clock(tmp_path, *, budgets_overrides=None):
    """One ManualClock driving the event clock (observed_at), the
    operational clock, AND the wall clock — tests that need to simulate
    elapsed time (including across a simulated crash/recovery) deterministically
    for wall-budget/timeout assertions control all three together through the
    single returned clock."""

    manifest = make_manifest(budgets=make_budgets(**(budgets_overrides or {})))
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    clock = ManualClock()
    supervisor = CampaignSupervisor(
        manifest=manifest,
        journal=journal,
        attempt_executor=None,  # set per-test
        proxy_evaluator=None,  # set per-test
        clock=clock,
        operational_clock=clock,
        wall_clock=clock,
    )
    return supervisor, manifest, journal, clock


async def always_completes_executor(*, assignment, resume_from_checkpoint):
    return ExecutionOutcome(status="completed")


async def always_completes_evaluator(*, assignment):
    return EvaluationOutcome(status="completed", score_ref="score-1")


def _valid_terminal_event(
    manifest,
    *,
    attempt_id,
    lineage_id,
    sequence,
    resource_overrides=None,
    terminal_overrides=None,
    event_overrides=None,
):
    """Build a well-formed, self-hashed ATTEMPT_TERMINAL DiscoveryEvent so
    adversarial tests can start from a valid baseline and mutate one field."""

    from metaharness.discovery.models import DiscoveryResourceReceipt, DiscoveryTerminalReceipt

    resource_kwargs = dict(
        receipt_id=f"res-{attempt_id}",
        campaign_id=manifest.campaign_id,
        attempt_id=attempt_id,
        sequence=0,
        wall_seconds=0.0,
        evaluations_used=0,
        restarts_used=0,
    )
    resource_kwargs.update(resource_overrides or {})
    resource = DiscoveryResourceReceipt(**resource_kwargs)
    terminal_kwargs = dict(
        receipt_id=f"term-{attempt_id}",
        campaign_id=manifest.campaign_id,
        lineage_id=lineage_id,
        attempt_id=attempt_id,
        sequence=1,
        outcome=DiscoveryTerminalOutcome.COMPLETED,
        resource_receipt_id=resource.receipt_id,
        closest_protected_result="proxy-only:x",
        unresolved_gap="none",
    )
    terminal_kwargs.update(terminal_overrides or {})
    terminal = DiscoveryTerminalReceipt(**terminal_kwargs)

    event_kwargs = dict(
        event_id=f"evt-terminal-{attempt_id}",
        campaign_id=manifest.campaign_id,
        campaign_manifest_hash=manifest.manifest_hash,
        attempt_id=attempt_id,
        event_type=DiscoveryEventType.ATTEMPT_TERMINAL,
        sequence=sequence,
        observed_at=0,
        payload={
            "resource_receipt": resource.model_dump(mode="json"),
            "terminal_receipt": terminal.model_dump(mode="json"),
            "_wall_clock_seconds": 0.0,
        },
    )
    event_kwargs.update(event_overrides or {})
    return DiscoveryEvent(**event_kwargs)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_completes(tmp_path):
    supervisor, manifest, _ = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    assert supervisor.attempt_state("att-1") is AttemptState.COMPLETED
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED
    assert receipt.closest_protected_result == "proxy-only:score-1"
    assert supervisor.in_flight_count == 0


# ---------------------------------------------------------------------------
# Failure paths: crash / timeout / cancel / worker error / evaluator error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected_outcome",
    [
        ("crashed", DiscoveryTerminalOutcome.CRASHED),
        ("worker_error", DiscoveryTerminalOutcome.WORKER_ERROR),
        ("cancelled", DiscoveryTerminalOutcome.CANCELLED),
    ],
)
async def test_execution_failure_with_no_restart_budget_terminates_immediately(tmp_path, status, expected_outcome):
    supervisor, _, _ = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})

    async def failing_executor(*, assignment, resume_from_checkpoint):
        return ExecutionOutcome(status=status, detail="boom")

    supervisor._executor = failing_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is expected_outcome
    assert supervisor.attempt_state("att-1") in (AttemptState.CRASHED, AttemptState.FAILED, AttemptState.CANCELLED)


async def test_timeout_terminates_when_executor_exceeds_attempt_timeout(tmp_path):
    supervisor, _, _ = make_supervisor(
        tmp_path, budgets_overrides={"max_restarts_per_attempt": 0, "attempt_timeout_seconds": 1}
    )

    async def slow_executor(*, assignment, resume_from_checkpoint):
        await asyncio.sleep(3600)
        return ExecutionOutcome(status="completed")

    async def fast_timeout_executor(*, assignment, resume_from_checkpoint):
        raise asyncio.TimeoutError()

    supervisor._executor = fast_timeout_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT


async def test_evaluator_error_with_no_restart_budget_terminates(tmp_path):
    supervisor, _, _ = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})

    async def failing_evaluator(*, assignment):
        return EvaluationOutcome(status="evaluator_error", detail="proxy unavailable")

    supervisor._executor = always_completes_executor
    supervisor._evaluator = failing_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.EVALUATOR_ERROR


async def test_retry_within_restart_budget_then_succeeds(tmp_path):
    supervisor, _, _ = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 1})
    calls = {"n": 0}

    async def flaky_executor(*, assignment, resume_from_checkpoint):
        calls["n"] += 1
        if calls["n"] == 1:
            return ExecutionOutcome(status="crashed", detail="first attempt crashed")
        return ExecutionOutcome(status="completed")

    supervisor._executor = flaky_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    assert calls["n"] == 2
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED


# ---------------------------------------------------------------------------
# Exactly-one terminal / late result discard
# ---------------------------------------------------------------------------


async def test_late_launch_result_after_terminal_is_discarded(tmp_path):
    supervisor, _, _ = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor.run_available()

    first_receipt = supervisor.terminal_receipt("att-1")
    assert first_receipt is not None

    # Simulate a late result arriving for an attempt that is already terminal.
    late_exec = await supervisor._launch(assignment, resume_from_checkpoint=False)
    late_eval = await supervisor._evaluate(assignment)
    assert late_exec is None
    assert late_eval is None
    assert supervisor.terminal_receipt("att-1") is first_receipt  # unchanged: still exactly one


def test_finalize_terminal_is_idempotent_no_op_on_second_call(tmp_path):
    supervisor, _, _ = make_supervisor(tmp_path)
    assignment = make_assignment()
    supervisor.prepare()
    supervisor._assignments[assignment.attempt_id] = assignment
    supervisor._attempt_states[assignment.attempt_id] = AttemptState.RUNNING

    supervisor._finalize_terminal(
        assignment, outcome=DiscoveryTerminalOutcome.FAILED, closest_protected_result="x", unresolved_gap="y"
    )
    first = supervisor.terminal_receipt("att-1")
    supervisor._finalize_terminal(
        assignment, outcome=DiscoveryTerminalOutcome.COMPLETED, closest_protected_result="z", unresolved_gap="w"
    )
    assert supervisor.terminal_receipt("att-1") is first


# ---------------------------------------------------------------------------
# Budget charging / concurrency
# ---------------------------------------------------------------------------


async def test_evaluation_budget_exhausted_denies_evaluator_call_before_launch(tmp_path):
    supervisor, _, _ = make_supervisor(
        tmp_path, budgets_overrides={"max_evaluations": 1, "max_attempts": 2, "max_concurrency": 1}
    )
    eval_calls = {"n": 0}

    async def counting_evaluator(*, assignment):
        eval_calls["n"] += 1
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor._executor = always_completes_executor
    supervisor._evaluator = counting_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment(attempt_id="att-1", assignment_id="asg-1"))
    supervisor.submit(make_assignment(attempt_id="att-2", assignment_id="asg-2"))
    await supervisor.run_available()

    assert eval_calls["n"] == 1  # exactly the budget, never exceeded
    first = supervisor.terminal_receipt("att-1")
    second = supervisor.terminal_receipt("att-2")
    outcomes = {first.outcome, second.outcome}
    assert DiscoveryTerminalOutcome.COMPLETED in outcomes
    assert DiscoveryTerminalOutcome.FAILED in outcomes
    exhausted = first if first.outcome is DiscoveryTerminalOutcome.FAILED else second
    assert "evaluation budget" in exhausted.unresolved_gap


def test_submit_denied_at_attempt_budget_cap_before_launch(tmp_path):
    supervisor, _, _ = make_supervisor(tmp_path, budgets_overrides={"max_attempts": 1})
    supervisor.prepare()
    supervisor.submit(make_assignment(attempt_id="att-1", assignment_id="asg-1"))
    with pytest.raises(SupervisorError):
        supervisor.submit(make_assignment(attempt_id="att-2", assignment_id="asg-2"))


async def test_concurrency_never_exceeds_max_concurrency(tmp_path):
    supervisor, _, _ = make_supervisor(
        tmp_path, budgets_overrides={"max_concurrency": 2, "max_attempts": 4, "max_evaluations": 4}
    )
    active = {"current": 0, "peak": 0}
    lock = asyncio.Lock()

    async def tracking_executor(*, assignment, resume_from_checkpoint):
        async with lock:
            active["current"] += 1
            active["peak"] = max(active["peak"], active["current"])
        await asyncio.sleep(0.05)
        async with lock:
            active["current"] -= 1
        return ExecutionOutcome(status="completed")

    supervisor._executor = tracking_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    for i in range(4):
        supervisor.submit(make_assignment(attempt_id=f"att-{i}", assignment_id=f"asg-{i}"))
    await supervisor.run_available()

    assert active["peak"] <= 2
    for i in range(4):
        assert supervisor.attempt_state(f"att-{i}") is AttemptState.COMPLETED


# ---------------------------------------------------------------------------
# Interruption + idempotent restart-bounded resume
# ---------------------------------------------------------------------------


async def test_recover_synthesizes_interruption_for_mid_flight_attempt_then_resumes(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 1})
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)

    # Simulate a crash: only the launch happens, no evaluate/terminal is journaled.
    await supervisor._launch(assignment, resume_from_checkpoint=False)
    assert supervisor.attempt_state("att-1") is AttemptState.CHECKPOINTED

    events = journal.read_all()
    fresh = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
        wall_clock=supervisor._wall_clock,  # same non-resetting wall-clock domain as the original process
    )
    fresh.recover(events)
    assert fresh.attempt_state("att-1") is AttemptState.INTERRUPTED
    assert len(fresh._queue) == 1

    await fresh.run_available()
    receipt = fresh.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED
    assert receipt.resource_receipt_id


def test_recover_is_idempotent_no_duplicate_effects(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)

    events_after_submit = journal.read_all()
    event_count_before = len(events_after_submit)

    fresh_a = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    fresh_a.recover(events_after_submit)
    events_after_first_recover = journal.read_all()
    assert len(events_after_first_recover) == event_count_before + 1  # one synthesized INTERRUPTED

    fresh_b = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    fresh_b.recover(journal.read_all())
    events_after_second_recover = journal.read_all()
    assert len(events_after_second_recover) == len(events_after_first_recover)  # no new synthesis: already INTERRUPTED
    assert fresh_b.attempt_state("att-1") is AttemptState.INTERRUPTED


async def test_recovery_respects_exhausted_restart_budget(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED, fully resolved
    # Simulate the actual crash window for an ACTIVE attempt: the process
    # dies DURING evaluation, after the evaluate intent is journaled but
    # before any resolving outcome is (a bare post-checkpoint gap with no
    # pending intent is NOT genuine crash evidence and must not be charged
    # -- META-7 pre-commit fix brief #11, NEW-2 -- so this test must leave
    # one to actually exercise "restart budget exhausted -> FAILED").
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})

    fresh = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    fresh.recover(journal.read_all())
    receipt = fresh.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.FAILED
    assert len(fresh._queue) == 0


# ---------------------------------------------------------------------------
# Fail-closed journal replay
# ---------------------------------------------------------------------------


def test_journal_refuses_symlinked_path(tmp_path):
    real_file = tmp_path / "real.jsonl"
    real_file.write_text("")
    link_path = tmp_path / "link.jsonl"
    link_path.symlink_to(real_file)
    journal = DiscoveryJournal(str(link_path))
    with pytest.raises(JournalError):
        journal.read_all()


def test_journal_read_rejects_corrupt_line(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text("{not valid json\n")
    journal = DiscoveryJournal(str(path))
    with pytest.raises(JournalError):
        journal.read_all()


def test_journal_read_rejects_schema_invalid_line(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text('{"not": "a discovery event"}\n')
    journal = DiscoveryJournal(str(path))
    with pytest.raises(JournalError):
        journal.read_all()


def test_journal_read_rejects_non_regular_file(tmp_path):
    """Pre-commit fix brief 2, item 10: mirror the existing repository
    journal's regular-file fstat guard — O_NOFOLLOW alone blocks symlinks
    but not other non-regular file types at the path (here: a directory;
    O_RDONLY on a directory succeeds at open() on POSIX, so the guard must
    catch it via fstat afterward)."""

    dir_path = tmp_path / "not-a-file.jsonl"
    dir_path.mkdir()
    journal = DiscoveryJournal(str(dir_path))
    with pytest.raises(JournalError, match="not a regular file"):
        journal.read_all()


def test_journal_append_rejects_non_regular_file(tmp_path):
    from metaharness.discovery.models import DiscoveryEvent as _DiscoveryEvent

    dir_path = tmp_path / "not-a-file.jsonl"
    dir_path.mkdir()
    journal = DiscoveryJournal(str(dir_path))
    event = _DiscoveryEvent(
        event_id="e1",
        campaign_id="camp-1",
        campaign_manifest_hash="sha256:" + "1" * 64,
        attempt_id=None,
        event_type=DiscoveryEventType.CAMPAIGN_PREPARED,
        sequence=0,
        observed_at=0,
        payload={},
    )
    with pytest.raises(JournalError):
        journal.append(event)


def test_recover_rejects_duplicate_sequence(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    events = journal.read_all()
    duplicated = events + [events[-1]]
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")), attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    with pytest.raises(JournalError):
        fresh.recover(duplicated)


def test_recover_rejects_sequence_gap(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    supervisor.submit(make_assignment())
    events = journal.read_all()
    gapped = [events[0], _rebuild_event(events[-1], sequence=events[-1].sequence + 5)]
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")), attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    with pytest.raises(JournalError, match="gap"):
        fresh.recover(gapped)


def test_recover_rejects_mixed_manifest(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    events = journal.read_all()
    contaminated = [_rebuild_event(events[0], campaign_manifest_hash="sha256:" + "9" * 64)]
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")), attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    with pytest.raises(JournalError, match="mixed-manifest"):
        fresh.recover(contaminated)


def test_recover_rejects_illegal_transition_terminal_without_launch(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    events = journal.read_all()

    bogus_terminal = DiscoveryEvent(
        event_id="bogus-1",
        campaign_id=manifest.campaign_id,
        campaign_manifest_hash=manifest.manifest_hash,
        attempt_id="att-1",
        event_type=DiscoveryEventType.ATTEMPT_TERMINAL,
        sequence=len(events),
        observed_at=0,
        payload={},
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")), attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus_terminal])


def test_recover_rejects_duplicate_submit_intent(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    events = journal.read_all()
    submit_intent = next(e for e in events if e.event_type == DiscoveryEventType.ATTEMPT_SUBMIT_INTENT)
    duplicate_intent = _rebuild_event(submit_intent, event_id="dup-1", sequence=len(events))
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")), attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    with pytest.raises(JournalError, match="duplicate submit intent"):
        fresh.recover(events + [duplicate_intent])


# ---------------------------------------------------------------------------
# Campaign stop
# ---------------------------------------------------------------------------


def test_request_stop_clears_queue_and_blocks_submission(tmp_path):
    supervisor, _, _ = make_supervisor(tmp_path)
    supervisor.prepare()
    supervisor.submit(make_assignment())
    supervisor.request_stop()
    assert supervisor.campaign_state is CampaignState.STOPPING
    with pytest.raises(SupervisorError):
        supervisor.submit(make_assignment(attempt_id="att-2", assignment_id="asg-2"))
    supervisor.finish_stop()
    assert supervisor.campaign_state is CampaignState.STOPPED


def test_request_stop_terminalizes_every_queued_attempt_exactly_once(tmp_path):
    supervisor, _, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    for i in range(3):
        supervisor.submit(make_assignment(attempt_id=f"att-{i}", assignment_id=f"asg-{i}"))
    supervisor.request_stop()

    for i in range(3):
        receipt = supervisor.terminal_receipt(f"att-{i}")
        assert receipt is not None
        assert receipt.outcome is DiscoveryTerminalOutcome.CANCELLED
        assert supervisor.attempt_state(f"att-{i}") is AttemptState.CANCELLED
        assert "before this attempt was launched" in receipt.unresolved_gap

    # Exactly one ATTEMPT_TERMINAL event per attempt in the journal — no
    # attempt is left without a receipt, and none gets a second one.
    events = journal.read_all()
    terminal_events = [e for e in events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL]
    assert sorted(e.attempt_id for e in terminal_events) == ["att-0", "att-1", "att-2"]


async def test_request_stop_leaves_already_launched_attempts_to_finish_normally(tmp_path):
    supervisor, _, _ = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment(attempt_id="att-launched", assignment_id="asg-launched"))
    supervisor.submit(make_assignment(attempt_id="att-queued", assignment_id="asg-queued"))

    # Pop+launch the first attempt manually (as run_available() would), then
    # stop before the second ever gets a chance to launch.
    launched_assignment = supervisor._queue.popleft()
    launch_task = asyncio.create_task(supervisor._run_attempt(launched_assignment))
    await asyncio.sleep(0)  # let the in-flight task start
    supervisor.request_stop()
    await launch_task

    assert supervisor.terminal_receipt("att-launched").outcome is DiscoveryTerminalOutcome.COMPLETED
    assert supervisor.terminal_receipt("att-queued").outcome is DiscoveryTerminalOutcome.CANCELLED


def test_recover_accepts_a_valid_prelaunch_cancellation(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    supervisor.submit(make_assignment())
    supervisor.request_stop()

    events = journal.read_all()
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.CANCELLED


def test_recover_rejects_a_terminal_from_prepared_that_is_not_a_cancellation(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    supervisor.submit(make_assignment())
    events = journal.read_all()  # attempt is PREPARED, still queued, no launch happened

    bogus_terminal = _valid_terminal_event(
        manifest,
        attempt_id="att-1",
        lineage_id="lin-1",
        sequence=len(events),
        terminal_overrides={"outcome": DiscoveryTerminalOutcome.COMPLETED},  # not a valid PREPARED->terminal path
    )
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus_terminal])


# ---------------------------------------------------------------------------
# Fix 1: independent journal-event / receipt sequences — completed-run
# round-trip recovery (the P1 regression test)
# ---------------------------------------------------------------------------


async def test_completed_run_recovers_cleanly_round_trip(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()
    assert supervisor.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.COMPLETED

    events = journal.read_all()
    sequences = [e.sequence for e in events]
    assert sequences == list(range(len(events)))  # gapless: the P1 bug produced gaps here

    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # must not raise
    assert fresh.attempt_state("att-1") is AttemptState.COMPLETED
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.COMPLETED
    assert fresh.campaign_state is CampaignState.RUNNING


async def test_multi_attempt_completed_run_recovers_cleanly(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_attempts": 3, "max_evaluations": 3})
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    for i in range(3):
        supervisor.submit(make_assignment(attempt_id=f"att-{i}", assignment_id=f"asg-{i}"))
    await supervisor.run_available()

    events = journal.read_all()
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # must not raise
    for i in range(3):
        assert fresh.terminal_receipt(f"att-{i}").outcome is DiscoveryTerminalOutcome.COMPLETED


async def test_every_terminal_outcome_flavor_round_trips_through_recovery(tmp_path):
    """Pre-commit fix brief #1: a completed journal must round-trip through
    recovery for every terminal outcome, not just COMPLETED."""

    supervisor, manifest, journal = make_supervisor(
        tmp_path, budgets_overrides={"max_attempts": 5, "max_evaluations": 5, "max_restarts_per_attempt": 0}
    )

    async def executor(*, assignment, resume_from_checkpoint):
        if assignment.attempt_id == "att-crashed":
            return ExecutionOutcome(status="crashed", detail="boom")
        if assignment.attempt_id == "att-worker-error":
            return ExecutionOutcome(status="worker_error", detail="bad worker")
        return ExecutionOutcome(status="completed")

    async def evaluator(*, assignment):
        if assignment.attempt_id == "att-evaluator-error":
            raise RuntimeError("evaluator blew up")
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor._executor = executor
    supervisor._evaluator = evaluator
    supervisor.prepare()
    for attempt_id in ("att-completed", "att-crashed", "att-worker-error", "att-evaluator-error"):
        supervisor.submit(make_assignment(attempt_id=attempt_id, assignment_id=f"asg-{attempt_id}"))
    await supervisor.run_available()

    expected = {
        "att-completed": DiscoveryTerminalOutcome.COMPLETED,
        "att-crashed": DiscoveryTerminalOutcome.CRASHED,
        "att-worker-error": DiscoveryTerminalOutcome.WORKER_ERROR,
        "att-evaluator-error": DiscoveryTerminalOutcome.EVALUATOR_ERROR,
    }
    for attempt_id, outcome in expected.items():
        assert supervisor.terminal_receipt(attempt_id).outcome is outcome

    events = journal.read_all()
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=executor,
        proxy_evaluator=evaluator,
    )
    fresh.recover(events)  # must not raise for ANY of these terminal flavors
    for attempt_id, outcome in expected.items():
        assert fresh.terminal_receipt(attempt_id).outcome is outcome


# ---------------------------------------------------------------------------
# Fix 2: recovery requires paired intent/outcome events and validates
# embedded identities (submit assignment, resource/terminal receipts)
# ---------------------------------------------------------------------------


def test_recover_rejects_outcome_without_matching_intent(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    supervisor.submit(make_assignment())
    events = journal.read_all()

    # Fabricate a LAUNCH_OUTCOME with no preceding LAUNCH_INTENT.
    bogus_outcome = DiscoveryEvent(
        event_id="bogus-launch-outcome",
        campaign_id=manifest.campaign_id,
        campaign_manifest_hash=manifest.manifest_hash,
        attempt_id="att-1",
        event_type=DiscoveryEventType.ATTEMPT_LAUNCH_OUTCOME,
        sequence=len(events),
        observed_at=0,
        payload={"status": "completed", "detail": "", "_wall_clock_seconds": 0.0},
    )
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="outcome without intent"):
        fresh.recover(events + [bogus_outcome])


def test_recover_rejects_double_intent_without_resolving_the_first(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    supervisor.submit(make_assignment())
    events = journal.read_all()
    launch_intent = DiscoveryEvent(
        event_id="launch-intent-1",
        campaign_id=manifest.campaign_id,
        campaign_manifest_hash=manifest.manifest_hash,
        attempt_id="att-1",
        event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT,
        sequence=len(events),
        observed_at=0,
        payload={"resume_from_checkpoint": False, "_wall_clock_seconds": 0},
    )
    duplicate_intent = DiscoveryEvent(
        event_id="launch-intent-2",
        campaign_id=manifest.campaign_id,
        campaign_manifest_hash=manifest.manifest_hash,
        attempt_id="att-1",
        event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT,
        sequence=len(events) + 1,
        observed_at=0,
        payload={"resume_from_checkpoint": False, "_wall_clock_seconds": 0},
    )

    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="unresolved"):
        fresh.recover(events + [launch_intent, duplicate_intent])


def test_recover_rejects_submit_intent_with_mismatched_assignment_attempt_id(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    events = journal.read_all()

    mismatched_assignment = make_assignment(attempt_id="att-DIFFERENT")
    bogus_intent = DiscoveryEvent(
        event_id="bogus-submit-intent",
        campaign_id=manifest.campaign_id,
        campaign_manifest_hash=manifest.manifest_hash,
        attempt_id="att-1",  # event claims att-1
        event_type=DiscoveryEventType.ATTEMPT_SUBMIT_INTENT,
        sequence=len(events),
        observed_at=0,
        payload={
            "assignment": mismatched_assignment.model_dump(mode="json"),  # but embeds att-DIFFERENT
            "_wall_clock_seconds": 0.0,
        },
    )
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="does not match"):
        fresh.recover(events + [bogus_intent])


def test_recover_rejects_submit_intent_with_foreign_campaign_assignment(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    events = journal.read_all()

    foreign_assignment = make_assignment(campaign_id="camp-OTHER")
    bogus_intent = DiscoveryEvent(
        event_id="bogus-submit-intent-2",
        campaign_id=manifest.campaign_id,
        campaign_manifest_hash=manifest.manifest_hash,
        attempt_id="att-1",
        event_type=DiscoveryEventType.ATTEMPT_SUBMIT_INTENT,
        sequence=len(events),
        observed_at=0,
        payload={"assignment": foreign_assignment.model_dump(mode="json"), "_wall_clock_seconds": 0.0},
    )
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="foreign campaign"):
        fresh.recover(events + [bogus_intent])


async def _events_through_first_terminal(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()
    return manifest, journal.read_all()


def _rebuild_event(event: DiscoveryEvent, **overrides) -> DiscoveryEvent:
    """Construct a FRESH DiscoveryEvent with the given field(s) changed, going
    through the constructor (not model_copy) so its hash is correctly
    recomputed for the new content — isolates a test from the (separately
    tested) stale-hash revalidation check so it exercises the specific
    downstream rule it names."""

    fields = dict(
        event_id=event.event_id,
        campaign_id=event.campaign_id,
        campaign_manifest_hash=event.campaign_manifest_hash,
        attempt_id=event.attempt_id,
        event_type=event.event_type,
        sequence=event.sequence,
        observed_at=event.observed_at,
        payload=dict(event.payload),
    )
    fields.update(overrides)
    return DiscoveryEvent(**fields)


def _rebuild_event_with_payload(event: DiscoveryEvent, payload: dict) -> DiscoveryEvent:
    return _rebuild_event(event, payload=payload)


async def test_recover_rejects_terminal_missing_resource_receipt_payload(tmp_path):
    manifest, events = await _events_through_first_terminal(tmp_path)
    terminal_index = next(i for i, e in enumerate(events) if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL)
    events[terminal_index] = _rebuild_event_with_payload(
        events[terminal_index],
        {
            "terminal_receipt": events[terminal_index].payload["terminal_receipt"],
            "_wall_clock_seconds": events[terminal_index].payload["_wall_clock_seconds"],
        },
    )
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="resource_receipt"):
        fresh.recover(events)


async def test_recover_rejects_terminal_with_malformed_resource_receipt_payload(tmp_path):
    manifest, events = await _events_through_first_terminal(tmp_path)
    terminal_index = next(i for i, e in enumerate(events) if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL)
    payload = dict(events[terminal_index].payload)
    payload["resource_receipt"] = {"not": "a valid resource receipt"}
    events[terminal_index] = _rebuild_event_with_payload(events[terminal_index], payload)
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="malformed"):
        fresh.recover(events)


async def test_recover_rejects_terminal_receipt_foreign_campaign(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED, an active state
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest,
        attempt_id="att-1",
        lineage_id="lin-1",
        sequence=len(events),
        terminal_overrides={"campaign_id": "camp-OTHER"},
    )
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="foreign campaign/attempt"):
        fresh.recover(events + [bogus])


async def test_recover_rejects_terminal_receipt_wrong_attempt_id(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest,
        attempt_id="att-1",
        lineage_id="lin-1",
        sequence=len(events),
        terminal_overrides={"attempt_id": "att-SOMEONE-ELSE"},
    )
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="foreign campaign/attempt"):
        fresh.recover(events + [bogus])


async def test_recover_rejects_terminal_receipt_lineage_mismatch(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment(lineage_id="lin-1")
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest,
        attempt_id="att-1",
        lineage_id="lin-1",
        sequence=len(events),
        terminal_overrides={"lineage_id": "lin-WRONG"},
    )
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="lineage"):
        fresh.recover(events + [bogus])


async def test_recover_rejects_terminal_resource_receipt_id_mismatch(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest,
        attempt_id="att-1",
        lineage_id="lin-1",
        sequence=len(events),
        terminal_overrides={"resource_receipt_id": "res-SOMEONE-ELSE"},
    )
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="resource_receipt_id"):
        fresh.recover(events + [bogus])


# ---------------------------------------------------------------------------
# Fix 3: evaluation-budget reservation is atomic across concurrent attempts
# ---------------------------------------------------------------------------


async def test_evaluation_budget_reservation_is_atomic_under_concurrency(tmp_path):
    supervisor, _, _ = make_supervisor(
        tmp_path, budgets_overrides={"max_evaluations": 1, "max_attempts": 4, "max_concurrency": 4}
    )
    eval_calls = {"n": 0}

    async def slow_evaluator(*, assignment):
        eval_calls["n"] += 1
        await asyncio.sleep(0.02)  # widen the race window the old code was vulnerable to
        return EvaluationOutcome(status="completed", score_ref="x")

    async def fast_executor(*, assignment, resume_from_checkpoint):
        return ExecutionOutcome(status="completed")

    supervisor._executor = fast_executor
    supervisor._evaluator = slow_evaluator
    supervisor.prepare()
    for i in range(4):
        supervisor.submit(make_assignment(attempt_id=f"att-{i}", assignment_id=f"asg-{i}"))
    await supervisor.run_available()

    assert eval_calls["n"] == 1  # the port itself was never called over cap
    outcomes = [supervisor.terminal_receipt(f"att-{i}").outcome for i in range(4)]
    assert outcomes.count(DiscoveryTerminalOutcome.COMPLETED) == 1
    assert outcomes.count(DiscoveryTerminalOutcome.FAILED) == 3


# ---------------------------------------------------------------------------
# Fix 6: campaign wall budget enforced at launch/evaluate boundaries; honest
# elapsed wall_seconds; execution timeout capped by remaining wall budget
# ---------------------------------------------------------------------------


async def test_wall_budget_at_cap_allows_launch(tmp_path):
    supervisor, manifest, journal, clock = make_supervisor_with_clock(
        tmp_path, budgets_overrides={"max_wall_seconds": 10}
    )
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()  # campaign_started_at = 0
    supervisor.submit(make_assignment())
    clock.advance(9)  # elapsed=9 < max_wall_seconds=10: still allowed
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED


async def test_wall_budget_over_cap_denies_launch_before_executor_call(tmp_path):
    supervisor, manifest, journal, clock = make_supervisor_with_clock(
        tmp_path, budgets_overrides={"max_wall_seconds": 10}
    )
    calls = {"n": 0}

    async def counting_executor(*, assignment, resume_from_checkpoint):
        calls["n"] += 1
        return ExecutionOutcome(status="completed")

    supervisor._executor = counting_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()  # campaign_started_at = 0
    supervisor.submit(make_assignment())
    clock.advance(10)  # elapsed=10 >= max_wall_seconds=10: exhausted
    await supervisor.run_available()

    assert calls["n"] == 0  # denied before the executor port was ever called
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT
    assert "campaign wall budget" in receipt.unresolved_gap


async def test_wall_budget_denies_evaluate_when_exhausted_between_launch_and_evaluate(tmp_path):
    supervisor, manifest, journal, clock = make_supervisor_with_clock(
        tmp_path, budgets_overrides={"max_wall_seconds": 10}
    )
    eval_calls = {"n": 0}

    async def clock_advancing_executor(*, assignment, resume_from_checkpoint):
        clock.advance(10)  # campaign wall budget is exhausted during "execution"
        return ExecutionOutcome(status="completed")

    async def counting_evaluator(*, assignment):
        eval_calls["n"] += 1
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor._executor = clock_advancing_executor
    supervisor._evaluator = counting_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    assert eval_calls["n"] == 0  # denied before the evaluator port was ever called
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT
    assert "campaign wall budget" in receipt.unresolved_gap


async def test_terminal_receipt_records_truthful_elapsed_wall_seconds(tmp_path):
    supervisor, manifest, journal, clock = make_supervisor_with_clock(tmp_path)

    async def clock_advancing_executor(*, assignment, resume_from_checkpoint):
        clock.advance(7)
        return ExecutionOutcome(status="completed")

    supervisor._executor = clock_advancing_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    events = journal.read_all()
    terminal_event = next(e for e in events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL)
    assert terminal_event.payload["resource_receipt"]["wall_seconds"] == 7.0


def test_prelaunch_cancellation_records_zero_wall_seconds(tmp_path):
    supervisor, _, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    supervisor.submit(make_assignment())
    supervisor.request_stop()

    events = journal.read_all()
    terminal_event = next(e for e in events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL)
    assert terminal_event.payload["resource_receipt"]["wall_seconds"] == 0.0


# ---------------------------------------------------------------------------
# Pre-commit fix brief #3 (supervisor): deterministic EOF after submit intent
# ---------------------------------------------------------------------------


def test_recover_resolves_eof_crash_between_submit_intent_and_outcome(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    assignment = make_assignment()
    # Simulate a crash: only the submit intent was journaled, never its outcome.
    supervisor._emit(
        attempt_id=assignment.attempt_id,
        event_type=DiscoveryEventType.ATTEMPT_SUBMIT_INTENT,
        payload={"assignment": assignment.model_dump(mode="json")},
    )
    events = journal.read_all()

    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)

    # Must not be left as state=None, unqueued, and receiptless.
    assert fresh.attempt_state("att-1") is not None
    assert len(fresh._queue) == 0
    receipt = fresh.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.OMITTED
    assert receipt.omission_reason is not None
    assert "between intent and outcome" in receipt.omission_reason


async def test_eof_after_submit_intent_does_not_block_running_the_rest_of_the_campaign(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_attempts": 2})
    supervisor.prepare()
    orphaned = make_assignment(attempt_id="att-orphaned", assignment_id="asg-orphaned")
    supervisor._emit(
        attempt_id=orphaned.attempt_id,
        event_type=DiscoveryEventType.ATTEMPT_SUBMIT_INTENT,
        payload={"assignment": orphaned.model_dump(mode="json")},
    )
    supervisor.submit(make_assignment(attempt_id="att-normal", assignment_id="asg-normal"))
    events = journal.read_all()

    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
        wall_clock=supervisor._wall_clock,  # same non-resetting wall-clock domain as the original process
    )
    fresh.recover(events)
    assert fresh.terminal_receipt("att-orphaned").outcome is DiscoveryTerminalOutcome.OMITTED
    assert len(fresh._queue) == 1  # only the normally-submitted attempt is queued

    await fresh.run_available()
    assert fresh.terminal_receipt("att-normal").outcome is DiscoveryTerminalOutcome.COMPLETED


# ---------------------------------------------------------------------------
# Pre-commit fix brief #4 (supervisor): reservation is atomic under a real
# synchronized two-attempt gate; errors do not refund the reserved slot
# ---------------------------------------------------------------------------


async def test_evaluation_reservation_synchronized_two_attempt_gate(tmp_path):
    supervisor, _, _ = make_supervisor(
        tmp_path, budgets_overrides={"max_evaluations": 1, "max_attempts": 2, "max_concurrency": 2}
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    call_count = {"n": 0}

    async def gated_evaluator(*, assignment):
        call_count["n"] += 1
        entered.set()
        await release.wait()
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor._executor = always_completes_executor
    supervisor._evaluator = gated_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment(attempt_id="att-0", assignment_id="asg-0"))
    supervisor.submit(make_assignment(attempt_id="att-1", assignment_id="asg-1"))

    run_task = asyncio.create_task(supervisor.run_available())
    await entered.wait()  # the winner is now blocked inside the evaluator
    await asyncio.sleep(0)  # give the loser's synchronous deny-path a chance to run to completion
    assert call_count["n"] == 1  # the port was never called a second time while the first was still in flight

    release.set()
    await run_task

    assert call_count["n"] == 1
    outcomes = [supervisor.terminal_receipt("att-0").outcome, supervisor.terminal_receipt("att-1").outcome]
    assert outcomes.count(DiscoveryTerminalOutcome.COMPLETED) == 1
    assert outcomes.count(DiscoveryTerminalOutcome.FAILED) == 1


async def test_evaluator_exception_does_not_refund_the_reserved_evaluation_slot(tmp_path):
    supervisor, _, _ = make_supervisor(tmp_path, budgets_overrides={"max_evaluations": 1, "max_restarts_per_attempt": 0})

    async def failing_evaluator(*, assignment):
        raise RuntimeError("evaluator blew up")

    supervisor._executor = always_completes_executor
    supervisor._evaluator = failing_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    assert supervisor._evaluations_used == 1  # spent, not refunded
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.EVALUATOR_ERROR


# ---------------------------------------------------------------------------
# Pre-commit fix brief #5 (supervisor): evaluator lives inside the attempt
# timeout envelope; a blocked evaluator cannot hold run_available() forever
# ---------------------------------------------------------------------------


async def test_blocked_evaluator_times_out_within_attempt_timeout_envelope(tmp_path):
    supervisor, _, _ = make_supervisor(
        tmp_path, budgets_overrides={"attempt_timeout_seconds": 1, "max_restarts_per_attempt": 0}
    )

    async def hanging_evaluator(*, assignment):
        await asyncio.sleep(3600)  # would block run_available() forever without a timeout
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor._executor = always_completes_executor
    supervisor._evaluator = hanging_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await asyncio.wait_for(supervisor.run_available(), timeout=10)  # the test itself times out if unbounded

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT


# ---------------------------------------------------------------------------
# Pre-commit fix brief #6 (supervisor): stopped-campaign gating
# ---------------------------------------------------------------------------


async def test_run_available_no_ops_when_campaign_is_stopping(tmp_path):
    supervisor, _, _ = make_supervisor(tmp_path)
    supervisor.prepare()
    supervisor.submit(make_assignment())
    supervisor.request_stop()  # already drains+cancels the queue

    # Simulate a bug/edge-case where something still put an assignment in the
    # queue after stop was requested — run_available() must refuse it too.
    supervisor._queue.append(make_assignment(attempt_id="att-should-not-launch", assignment_id="asg-x"))
    calls = {"n": 0}

    async def counting_executor(*, assignment, resume_from_checkpoint):
        calls["n"] += 1
        return ExecutionOutcome(status="completed")

    supervisor._executor = counting_executor
    await supervisor.run_available()
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# Pre-commit fix brief 2, item 1: EOF-after-submit-intent recovery is
# idempotent across repeated same-journal recovery (not just the first pass)
# ---------------------------------------------------------------------------


def test_eof_submit_intent_recovery_idempotent_first_second_third_pass(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    assignment = make_assignment()
    supervisor._emit(
        attempt_id=assignment.attempt_id,
        event_type=DiscoveryEventType.ATTEMPT_SUBMIT_INTENT,
        payload={"assignment": assignment.model_dump(mode="json")},
    )

    def recover_fresh():
        events = journal.read_all()
        fresh = CampaignSupervisor(
            manifest=manifest,
            journal=journal,
            attempt_executor=always_completes_executor,
            proxy_evaluator=always_completes_evaluator,
        )
        fresh.recover(events)
        return fresh, len(events)

    first, count_before_first = recover_fresh()
    receipt_first = first.terminal_receipt("att-1")
    assert receipt_first is not None
    assert receipt_first.outcome is DiscoveryTerminalOutcome.OMITTED
    events_after_first = journal.read_all()
    assert len(events_after_first) == count_before_first + 1  # exactly one synthesized TERMINAL

    second, count_before_second = recover_fresh()  # must NOT raise "terminal from state None"
    receipt_second = second.terminal_receipt("att-1")
    assert receipt_second == receipt_first
    events_after_second = journal.read_all()
    assert len(events_after_second) == count_before_second  # no new synthesis: already resolved

    third, count_before_third = recover_fresh()
    assert third.terminal_receipt("att-1") == receipt_first
    events_after_third = journal.read_all()
    assert len(events_after_third) == count_before_third


# ---------------------------------------------------------------------------
# Pre-commit fix brief 2, item 2: a semaphore-waiting attempt must not launch
# after stop, even once capacity frees up
# ---------------------------------------------------------------------------


async def test_semaphore_waiting_attempt_is_cancelled_not_launched_after_stop(tmp_path):
    supervisor, _, _ = make_supervisor(tmp_path, budgets_overrides={"max_concurrency": 1, "max_attempts": 2})
    entered_att1 = asyncio.Event()
    release_att1 = asyncio.Event()
    calls = {"att-2": 0}

    async def executor(*, assignment, resume_from_checkpoint):
        if assignment.attempt_id == "att-1":
            entered_att1.set()
            await release_att1.wait()
            return ExecutionOutcome(status="completed")
        calls["att-2"] += 1
        return ExecutionOutcome(status="completed")

    supervisor._executor = executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment(attempt_id="att-1", assignment_id="asg-1"))
    supervisor.submit(make_assignment(attempt_id="att-2", assignment_id="asg-2"))

    run_task = asyncio.create_task(supervisor.run_available())
    await entered_att1.wait()  # att-1 holds the semaphore; att-2's task is waiting on it

    supervisor.request_stop()
    release_att1.set()
    await run_task

    assert calls["att-2"] == 0  # the executor was never called for att-2
    assert supervisor.terminal_receipt("att-2").outcome is DiscoveryTerminalOutcome.CANCELLED
    assert supervisor.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.COMPLETED


# ---------------------------------------------------------------------------
# Pre-commit fix brief 2, item 3: ONE shared attempt/campaign timeout
# envelope via a real monotonic operational clock; receipts are truthfully
# nonzero under ordinary (real-time) construction
# ---------------------------------------------------------------------------


async def test_execution_plus_evaluation_share_one_attempt_timeout_envelope(tmp_path):
    # Real asyncio.sleep() needs the REAL default operational clock
    # (time.monotonic) to be meaningfully measured — constructed directly
    # rather than via make_supervisor()'s deterministic fake clock.
    manifest = make_manifest(
        budgets=make_budgets(attempt_timeout_seconds=1, max_wall_seconds=1, max_restarts_per_attempt=0)
    )
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None
    )

    async def slow_executor(*, assignment, resume_from_checkpoint):
        await asyncio.sleep(0.6)
        return ExecutionOutcome(status="completed")

    async def slow_evaluator(*, assignment):
        await asyncio.sleep(0.6)
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor._executor = slow_executor
    supervisor._evaluator = slow_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is not DiscoveryTerminalOutcome.COMPLETED  # 0.6s + 0.6s > 1s shared envelope
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT


async def test_ordinary_construction_reports_nonzero_wall_seconds_by_default(tmp_path):
    """The DEFAULT operational clock is real (time.monotonic), so a receipt
    built through ordinary construction (no fake clock injected) must report
    genuinely nonzero elapsed wall time, not a structurally-fixed 0.0."""

    manifest = make_manifest(budgets=make_budgets())
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None
    )

    async def slow_executor(*, assignment, resume_from_checkpoint):
        await asyncio.sleep(0.05)
        return ExecutionOutcome(status="completed")

    async def slow_evaluator(*, assignment):
        await asyncio.sleep(0.05)
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor._executor = slow_executor
    supervisor._evaluator = slow_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED
    assert receipt.resource_receipt_id
    events = journal.read_all()
    terminal_event = next(e for e in events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL)
    assert terminal_event.payload["resource_receipt"]["wall_seconds"] > 0.0


# ---------------------------------------------------------------------------
# Pre-commit fix brief 2, item 4: evaluation budget is charged on the durable
# EVALUATE_INTENT (matching the live no-refund reservation); outcome must
# not double-charge; EOF after intent must not regain a slot on recovery
# ---------------------------------------------------------------------------


async def test_replay_charges_evaluation_budget_on_intent_not_outcome(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_evaluations": 1})
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)
    # Manually emit ONLY the evaluate intent (simulate a crash before the
    # evaluator call resolves) — no outcome, no terminal.
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
    events = journal.read_all()

    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)
    assert fresh._evaluations_used == 1  # charged on intent alone, no outcome needed


async def test_eof_after_evaluate_intent_does_not_regain_a_slot_or_call_evaluator_again(tmp_path):
    supervisor, manifest, journal = make_supervisor(
        tmp_path, budgets_overrides={"max_evaluations": 1, "max_restarts_per_attempt": 1}
    )
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
    events = journal.read_all()

    eval_calls = {"n": 0}

    async def counting_evaluator(*, assignment):
        eval_calls["n"] += 1
        return EvaluationOutcome(status="completed", score_ref="x")

    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=counting_evaluator,
        wall_clock=supervisor._wall_clock,  # same non-resetting wall-clock domain as the original process
    )
    fresh.recover(events)
    assert fresh._evaluations_used == 1
    await fresh.run_available()  # the retried attempt reaches _evaluate again

    # The budget was already spent by the crashed intent; the retried
    # attempt's own _evaluate() call must see it exhausted and must NOT
    # invoke the evaluator port a second time.
    assert eval_calls["n"] == 0
    receipt = fresh.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.FAILED
    assert "evaluation budget" in receipt.unresolved_gap


async def test_recovered_stopping_campaign_cancels_mid_flight_attempt_instead_of_requeuing(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 1})
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # mid-flight: CHECKPOINTED, no terminal yet
    supervisor.request_stop()  # att-1 isn't queued (already launched), so this only flips campaign_state

    events = journal.read_all()
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)

    assert fresh.campaign_state is CampaignState.STOPPING
    receipt = fresh.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.CANCELLED
    assert len(fresh._queue) == 0

    # run_available() must still refuse to launch anything for this campaign.
    calls = {"n": 0}

    async def counting_executor(*, assignment, resume_from_checkpoint):
        calls["n"] += 1
        return ExecutionOutcome(status="completed")

    fresh._executor = counting_executor
    await fresh.run_available()
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# Final P1: poisoned-supervisor contract for journal append failures of
# unknown durability
# ---------------------------------------------------------------------------


class FlakyJournal:
    """Wraps a REAL DiscoveryJournal. The Nth append whose event_type matches
    `fail_on_event_type` still durably writes to the underlying file (this is
    the "ambiguous durability" case: the byte landed) and THEN raises — the
    caller cannot distinguish this from a write that silently failed, which
    is exactly the point: both must poison identically."""

    def __init__(self, real_journal: DiscoveryJournal, *, fail_on_event_type: DiscoveryEventType) -> None:
        self._real = real_journal
        self._fail_on_event_type = fail_on_event_type
        self.call_count = 0
        self.failed_at_call_index: int | None = None

    def append(self, event: DiscoveryEvent) -> None:
        self.call_count += 1
        self._real.append(event)
        if event.event_type == self._fail_on_event_type and self.failed_at_call_index is None:
            self.failed_at_call_index = self.call_count
            raise RuntimeError(
                f"simulated append failure for {event.event_type.value} at call "
                f"#{self.call_count} (ambiguous durability: the write landed)"
            )

    def read_all(self) -> list[DiscoveryEvent]:
        return self._real.read_all()


async def _assert_fully_poisoned(supervisor: CampaignSupervisor) -> None:
    """Every public command and introspection must refuse once poisoned."""

    with pytest.raises(SupervisorError):
        _ = supervisor.campaign_state
    with pytest.raises(SupervisorError):
        supervisor.attempt_state("att-1")
    with pytest.raises(SupervisorError):
        supervisor.terminal_receipt("att-1")
    with pytest.raises(SupervisorError):
        _ = supervisor.in_flight_count
    with pytest.raises(SupervisorError):
        supervisor.prepare()
    with pytest.raises(SupervisorError):
        supervisor.recover([])
    with pytest.raises(SupervisorError):
        supervisor.submit(make_assignment(attempt_id="att-poison-probe", assignment_id="asg-poison-probe"))
    with pytest.raises(SupervisorError):
        await supervisor.run_available()
    with pytest.raises(SupervisorError):
        supervisor.request_stop()
    with pytest.raises(SupervisorError):
        supervisor.finish_stop()
    # Directly probing the internal mutating retry/finalize paths too.
    with pytest.raises(SupervisorError):
        supervisor._maybe_retry(make_assignment(), "crashed", "n/a")
    with pytest.raises(SupervisorError):
        supervisor._finalize_terminal(make_assignment(), outcome=DiscoveryTerminalOutcome.FAILED)


def _assert_gapless(events: list[DiscoveryEvent]) -> None:
    sequences = [e.sequence for e in events]
    assert sequences == list(range(len(events))), f"sequence gap in recovered journal: {sequences}"


async def test_poisoned_after_submit_outcome_append_failure_fresh_instance_recovers(tmp_path):
    manifest = make_manifest(budgets=make_budgets())
    real_journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    flaky = FlakyJournal(real_journal, fail_on_event_type=DiscoveryEventType.ATTEMPT_SUBMIT_OUTCOME)
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=flaky, attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    supervisor.prepare()
    with pytest.raises(JournalError):
        supervisor.submit(make_assignment())
    assert flaky.failed_at_call_index == 3  # prepare(1), submit-intent(2), submit-outcome(3)

    await _assert_fully_poisoned(supervisor)

    events = real_journal.read_all()
    _assert_gapless(events)
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # deterministic: no gaps, no false claims
    # The SUBMIT_OUTCOME durably landed (the append raised only AFTER
    # writing), so replay correctly sees a fully-submitted, never-launched
    # attempt — mid-flight recovery requeues it for retry rather than
    # leaving it stuck or fabricating a terminal for it.
    assert fresh.attempt_state("att-1") is AttemptState.INTERRUPTED
    assert len(fresh._queue) == 1
    assert fresh.campaign_state is CampaignState.RUNNING


async def test_poisoned_after_terminal_append_failure_fresh_instance_recovers(tmp_path):
    manifest = make_manifest(budgets=make_budgets())
    real_journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    flaky = FlakyJournal(real_journal, fail_on_event_type=DiscoveryEventType.ATTEMPT_TERMINAL)
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=flaky, attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    supervisor.prepare()
    supervisor.submit(make_assignment())
    with pytest.raises(JournalError):
        await supervisor.run_available()
    assert flaky.failed_at_call_index is not None

    await _assert_fully_poisoned(supervisor)

    events = real_journal.read_all()
    _assert_gapless(events)
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # no false terminal claim left unresolved and no raise
    receipt = fresh.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED


async def test_poisoned_after_stop_requested_append_failure_fresh_instance_recovers(tmp_path):
    manifest = make_manifest(budgets=make_budgets())
    real_journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    flaky = FlakyJournal(real_journal, fail_on_event_type=DiscoveryEventType.CAMPAIGN_STOP_REQUESTED)
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=flaky, attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    supervisor.prepare()
    with pytest.raises(JournalError):
        supervisor.request_stop()
    assert flaky.failed_at_call_index == 2  # prepare(1), stop-requested(2)

    await _assert_fully_poisoned(supervisor)

    events = real_journal.read_all()
    _assert_gapless(events)
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # no false "stopped" claim: the STOP_REQUESTED DID land
    assert fresh.campaign_state is CampaignState.STOPPING


async def test_poisoned_after_finish_stop_append_failure_fresh_instance_recovers(tmp_path):
    manifest = make_manifest(budgets=make_budgets())
    real_journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    flaky = FlakyJournal(real_journal, fail_on_event_type=DiscoveryEventType.CAMPAIGN_STOPPED)
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=flaky, attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    supervisor.prepare()
    supervisor.request_stop()
    with pytest.raises(JournalError):
        supervisor.finish_stop()
    assert flaky.failed_at_call_index == 3  # prepare(1), stop-requested(2), stopped(3)

    await _assert_fully_poisoned(supervisor)

    events = real_journal.read_all()
    _assert_gapless(events)
    fresh = CampaignSupervisor(
        manifest=manifest,
        journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # no false "still stopping" claim: CAMPAIGN_STOPPED DID land
    assert fresh.campaign_state is CampaignState.STOPPED


def test_poisoned_instance_rejects_recover_even_when_never_prepared(tmp_path):
    """A poison during prepare() itself (before self._prepared is ever set)
    must still permanently block recover() on the SAME instance — the poison
    check must win regardless of the _prepared flag's value."""

    manifest = make_manifest(budgets=make_budgets())
    real_journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    flaky = FlakyJournal(real_journal, fail_on_event_type=DiscoveryEventType.CAMPAIGN_PREPARED)
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=flaky, attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator
    )
    with pytest.raises(JournalError):
        supervisor.prepare()
    assert supervisor._prepared is False  # never got a chance to be set

    with pytest.raises(SupervisorPoisonedError):
        supervisor.recover(real_journal.read_all())


# ---------------------------------------------------------------------------
# Final P1 #1: repeated recovery must not strand a durable INTERRUPTED
# attempt — every fresh recovery re-queues unfinished interrupted work
# exactly once, with no duplicate execution/refund/receipt.
# ---------------------------------------------------------------------------


async def test_repeated_recovery_requeues_interrupted_attempt_exactly_once_and_completes(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 1})
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    # Simulate a genuine mid-flight crash: launch reaches CHECKPOINTED
    # (fully resolved), then the process dies DURING evaluation -- a
    # dangling evaluate intent is the actual crash evidence for an ACTIVE
    # attempt (a bare post-checkpoint gap with no pending intent is not
    # genuine crash evidence and is no longer charged -- META-7 pre-commit
    # fix brief #11, NEW-2).
    await supervisor._launch(assignment, resume_from_checkpoint=False)
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})

    events_before_first_recovery = journal.read_all()
    first = CampaignSupervisor(
        manifest=manifest,
        journal=journal,  # SAME durable file: recovery #1's synthesis lands here too
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
        wall_clock=supervisor._wall_clock,  # same non-resetting wall-clock domain as the original process
    )
    first.recover(events_before_first_recovery)
    assert len(first._queue) == 1
    assert first.attempt_state("att-1") is AttemptState.INTERRUPTED
    events_after_first_recovery = journal.read_all()
    assert len(events_after_first_recovery) == len(events_before_first_recovery) + 1  # one synthesized INTERRUPTED

    # SECOND fresh recovery from the SAME (now-updated) journal — this is
    # exactly the bug: INTERRUPTED was excluded from the requeue set, so the
    # attempt was silently stranded (queue=0) instead of requeued again.
    second = CampaignSupervisor(
        manifest=manifest,
        journal=journal,
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
        wall_clock=supervisor._wall_clock,  # same non-resetting wall-clock domain as the original process
    )
    second.recover(events_after_first_recovery)
    assert len(second._queue) == 1
    assert second.attempt_state("att-1") is AttemptState.INTERRUPTED
    events_after_second_recovery = journal.read_all()
    assert len(events_after_second_recovery) == len(events_after_first_recovery)  # idempotent: no new event, no double charge
    assert second._restarts_used["att-1"] == 1  # charged exactly once, not twice

    # THIRD fresh recovery for good measure — still exactly one queued item.
    third = CampaignSupervisor(
        manifest=manifest,
        journal=journal,
        attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
        wall_clock=supervisor._wall_clock,  # same non-resetting wall-clock domain as the original process
    )
    third.recover(journal.read_all())
    assert len(third._queue) == 1
    assert len(journal.read_all()) == len(events_after_second_recovery)  # still idempotent

    # Finally: it actually runs and reaches exactly one terminal receipt.
    third._evaluator = always_completes_evaluator
    await third.run_available()
    receipt = third.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED


# ---------------------------------------------------------------------------
# Final P1 #2: recovery must not renew the frozen campaign/attempt time
# envelopes — elapsed budget consumed before a crash stays durably charged
# across one or repeated recoveries.
# ---------------------------------------------------------------------------


async def test_campaign_wall_budget_pre_crash_elapsed_survives_recovery(tmp_path):
    """max_wall_seconds=10; the campaign is prepared, then 9 seconds pass on
    the non-resetting wall clock — INCLUDING a hard crash with no event
    emitted during the gap — before a fresh recovery. Only 1 second of REAL
    remaining budget exists — attempting 2 more seconds of "work" must time
    out, not complete, proving the immutable deadline anchor (not an
    elapsed-event snapshot that only knows about durable events) survives
    the crash gap."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10, max_restarts_per_attempt=1))
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    # A single SHARED wall clock: real wall-clock time does not reset just
    # because a process crashed, so the test drives one continuous clock
    # across both the "old" and "recovered" supervisor instances.
    wall_clock = ManualClock(start=0)
    supervisor1 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None, wall_clock=wall_clock
    )
    supervisor1.prepare()
    supervisor1.submit(make_assignment())
    wall_clock.advance(9)  # 9 seconds pass, including the crash — no event emitted during the gap
    events = journal.read_all()  # simulate crash: never launched

    async def executor(*, assignment, resume_from_checkpoint):
        wall_clock.advance(2)  # "2 seconds of recovery work"
        return ExecutionOutcome(status="completed")

    async def evaluator(*, assignment):
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor2 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=executor, proxy_evaluator=evaluator, wall_clock=wall_clock,
    )
    supervisor2.recover(events)
    assert len(supervisor2._queue) == 1  # the never-launched attempt was requeued
    assert supervisor2._remaining_wall_seconds() == pytest.approx(1.0)

    await supervisor2.run_available()
    receipt = supervisor2.terminal_receipt("att-1")
    assert receipt is not None
    # 9 (pre-crash, including the invisible gap) + 2 (recovery work) = 11 >
    # max_wall=10: must NOT complete.
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT
    assert "wall budget" in receipt.unresolved_gap


async def test_attempt_timeout_pre_crash_elapsed_survives_recovery(tmp_path):
    """attempt_timeout_seconds=5 (max_wall_seconds large so it isn't the
    binding constraint); the attempt launches (anchoring its first-launch
    deadline), then 4 seconds pass on the non-resetting wall clock before a
    hard crash with no outcome event. A fresh recovery has only 1 second of
    REAL remaining envelope — attempting 4 more seconds of "recovery work"
    must time out, not complete."""

    manifest = make_manifest(
        budgets=make_budgets(attempt_timeout_seconds=5, max_wall_seconds=1000, max_restarts_per_attempt=1)
    )
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    # A single SHARED wall clock spans both the "old" and "recovered"
    # supervisor instances — real wall-clock time does not reset on crash.
    wall_clock = ManualClock(start=0)

    async def crashing_executor(*, assignment, resume_from_checkpoint):
        wall_clock.advance(4)  # 4 seconds pass during this attempt's own execution
        return ExecutionOutcome(status="completed")

    supervisor1 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=crashing_executor, proxy_evaluator=None, wall_clock=wall_clock
    )
    supervisor1.prepare()
    assignment = make_assignment()
    supervisor1.submit(assignment)
    await supervisor1._launch(assignment, resume_from_checkpoint=False)  # reaches CHECKPOINTED at wall+4, then "crashes"

    events = journal.read_all()

    async def executor2(*, assignment, resume_from_checkpoint):
        wall_clock.advance(4)  # "4 more seconds of recovery work" (only 1s remains: 5-4=1)
        return ExecutionOutcome(status="completed")

    async def evaluator2(*, assignment):
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor2 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=executor2, proxy_evaluator=evaluator2, wall_clock=wall_clock,
    )
    supervisor2.recover(events)
    assert len(supervisor2._queue) == 1
    assert supervisor2._attempt_deadline_wall["att-1"] == pytest.approx(5.0)  # first-launch anchor, unchanged

    await supervisor2.run_available()
    receipt = supervisor2.terminal_receipt("att-1")
    assert receipt is not None
    # 4 (pre-crash) + 4 (recovery work) = 8 > attempt_timeout=5: must time out.
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT


async def test_repeated_recovery_does_not_renew_elapsed_budget(tmp_path):
    """Recovering the SAME crash point twice must not reset (or move) the
    immutable campaign deadline anchor — the second recovery's
    reconstructed deadline and remaining budget must exactly match the
    first's, not be renewed to a fresh 10s envelope measured from "now"."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10, max_restarts_per_attempt=1))
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    # A single SHARED, non-resetting wall clock spans all three instances.
    wall_clock = ManualClock(start=0)
    supervisor1 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None, wall_clock=wall_clock
    )
    supervisor1.prepare()
    supervisor1.submit(make_assignment())
    wall_clock.advance(9)
    events_1 = journal.read_all()

    supervisor2 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator, wall_clock=wall_clock,
    )
    supervisor2.recover(events_1)  # synthesizes an INTERRUPTED event
    assert supervisor2._campaign_deadline_wall == pytest.approx(10.0)  # anchored at prepare(): 0 + max_wall_seconds
    assert supervisor2._remaining_wall_seconds() == pytest.approx(1.0)

    events_2 = journal.read_all()
    supervisor3 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator, wall_clock=wall_clock,
    )
    supervisor3.recover(events_2)
    # NOT renewed to wall_clock_now()+10=19 (which would give remaining=10) —
    # still anchored at the ORIGINAL 10, so remaining is still ~1.
    assert supervisor3._campaign_deadline_wall == pytest.approx(10.0)
    assert supervisor3._remaining_wall_seconds() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Final P1 #3: the elapsed-SNAPSHOT design undercharged the interval between
# the last durable event and a hard crash. Replaced with immutable
# wall-clock-anchored deadlines. These tests reproduce the user's exact
# counterexample and its required companions.
# ---------------------------------------------------------------------------


async def test_hard_crash_4_9s_before_outcome_then_recovery_cannot_complete_another_4_9s(tmp_path):
    """THE exact counterexample: attempt_timeout_seconds=5; ATTEMPT_LAUNCH_INTENT
    is emitted, 4.9s elapse, then a hard crash with NO outcome event at all —
    not even a LAUNCH_OUTCOME. A fresh recovery must not be able to spend
    another 4.9s and still report "completed": only 0.1s of the ORIGINAL 5s
    envelope remains, so the attempt must time out, not complete."""

    manifest = make_manifest(
        budgets=make_budgets(attempt_timeout_seconds=5, max_wall_seconds=1000, max_restarts_per_attempt=1)
    )
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    # A single SHARED, non-resetting wall clock spans the "old" and
    # "recovered" instances — real wall-clock time does not reset on crash.
    wall_clock = ManualClock(start=0)
    supervisor1 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None, wall_clock=wall_clock
    )
    supervisor1.prepare()
    assignment = make_assignment()
    supervisor1.submit(assignment)
    supervisor1._emit(
        attempt_id="att-1",
        event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT,
        payload={"resume_from_checkpoint": False},
    )
    wall_clock.advance(4.9)  # hard crash after this — no outcome event ever recorded
    events = journal.read_all()

    async def executor(*, assignment, resume_from_checkpoint):
        wall_clock.advance(4.9)  # "another 4.9s of recovery work"
        return ExecutionOutcome(status="completed")

    async def evaluator(*, assignment):
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor2 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=executor, proxy_evaluator=evaluator, wall_clock=wall_clock,
    )
    supervisor2.recover(events)
    assert len(supervisor2._queue) == 1
    assert supervisor2._attempt_deadline_wall["att-1"] == pytest.approx(5.0)  # restored remaining is 0.1, never 5.0

    await supervisor2.run_available()
    receipt = supervisor2.terminal_receipt("att-1")
    assert receipt is not None
    # Cumulative 4.9 + 4.9 = 9.8s inside a frozen 5s envelope: must NOT
    # report "completed" — the exact bug the counterexample targets.
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT


async def test_analogous_campaign_wall_gap_hard_crash_4_9s_before_any_further_event(tmp_path):
    """The campaign-level analogue of the primary probe: max_wall_seconds=5;
    CAMPAIGN_PREPARED is emitted, 4.9s elapse with NO further durable event
    at all, then a hard crash. A fresh recovery must not be able to spend
    another 4.9s of campaign wall time — cumulative 9.8s inside a frozen 5s
    envelope must time out, not complete."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=5, max_restarts_per_attempt=1))
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    wall_clock = ManualClock(start=0)
    supervisor1 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None, wall_clock=wall_clock
    )
    supervisor1.prepare()
    wall_clock.advance(4.9)  # hard crash after this — no further event ever recorded
    events = journal.read_all()

    async def executor(*, assignment, resume_from_checkpoint):
        return ExecutionOutcome(status="completed")

    async def evaluator(*, assignment):
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor2 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=executor, proxy_evaluator=evaluator, wall_clock=wall_clock,
    )
    supervisor2.recover(events)
    assert supervisor2._remaining_wall_seconds() == pytest.approx(0.1)

    wall_clock.advance(4.9)  # "another 4.9s of recovery work" before submitting/launching
    supervisor2.submit(make_assignment())
    await supervisor2.run_available()
    receipt = supervisor2.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT
    assert "wall budget" in receipt.unresolved_gap


async def test_repeated_hard_crashes_cannot_extend_either_deadline(tmp_path):
    """Three SEPARATE hard-crash-then-recover cycles, each recovering the
    SAME original crash point. If either recovery renewed the deadline, the
    attempt would still have budget left after all three; instead the
    immutable first-launch anchor must stay pinned at its original value
    across all three, and cumulative real elapsed time (invisible to any of
    them individually) must still be charged when a final attempt runs."""

    manifest = make_manifest(
        budgets=make_budgets(attempt_timeout_seconds=5, max_wall_seconds=1000, max_restarts_per_attempt=5)
    )
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    wall_clock = ManualClock(start=0)
    supervisor1 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None, wall_clock=wall_clock
    )
    supervisor1.prepare()
    assignment = make_assignment()
    supervisor1.submit(assignment)
    supervisor1._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT, payload={"resume_from_checkpoint": False}
    )
    wall_clock.advance(1.5)  # first crash

    deadlines_seen = []
    for i in range(3):
        events = journal.read_all()
        recovered = CampaignSupervisor(
            manifest=manifest, journal=DiscoveryJournal(str(tmp_path / f"other-{i}.jsonl")),
            attempt_executor=None, proxy_evaluator=None, wall_clock=wall_clock,
        )
        recovered.recover(events)
        deadlines_seen.append(recovered._attempt_deadline_wall["att-1"])
        wall_clock.advance(1.5)  # another crash's worth of downtime, invisible to any durable event

    assert all(d == pytest.approx(5.0) for d in deadlines_seen)  # NEVER renewed, across all 3 recoveries

    # Cumulative real elapsed: 1.5 (pre-first-crash) + 3*1.5 (loop) = 6.0s,
    # exceeding the frozen 5s envelope — the final attempt must time out.
    events = journal.read_all()
    final = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "final.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator, wall_clock=wall_clock,
    )
    final.recover(events)
    await final.run_available()
    receipt = final.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT


async def test_first_launch_deadline_remains_unchanged_across_restart_retry(tmp_path):
    """A later retry's ATTEMPT_LAUNCH_INTENT must never move an already-
    anchored attempt deadline — "first-launch wins", both live (within one
    process, across a real retry) and replayed (across a recovery)."""

    manifest = make_manifest(
        budgets=make_budgets(attempt_timeout_seconds=5, max_wall_seconds=1000, max_restarts_per_attempt=2)
    )
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    wall_clock = ManualClock(start=0)
    call_count = {"n": 0}

    async def flaky_then_completes_executor(*, assignment, resume_from_checkpoint):
        call_count["n"] += 1
        wall_clock.advance(1.0)
        if call_count["n"] == 1:
            return ExecutionOutcome(status="crashed", detail="simulated transient failure")
        return ExecutionOutcome(status="completed")

    async def evaluator(*, assignment):
        return EvaluationOutcome(status="completed", score_ref="x")

    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=flaky_then_completes_executor,
        proxy_evaluator=evaluator, wall_clock=wall_clock,
    )
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)  # queues the attempt
    await supervisor.run_available()  # first launch fails at wall=1, retries, second launch completes

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED
    # Anchored at the FIRST launch (wall=0): deadline=5, NOT re-anchored at
    # the retry's later wall-clock reading (wall=1, which would give 6).
    assert supervisor._attempt_deadline_wall["att-1"] == pytest.approx(5.0)

    # And it survives a fresh recovery identically — never re-anchored.
    events = journal.read_all()
    recovered = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=None, proxy_evaluator=None, wall_clock=wall_clock,
    )
    recovered.recover(events)
    assert recovered._attempt_deadline_wall["att-1"] == pytest.approx(5.0)


def test_missing_wall_clock_evidence_for_unfinished_attempt_fails_closed(tmp_path):
    """An ATTEMPT_LAUNCH_INTENT for an attempt that never reached a terminal
    state, hand-crafted WITHOUT its _wall_clock_seconds deadline anchor,
    must fail closed on recovery — an unfinished attempt with no provable
    deadline must never be silently treated as unbounded. (Every event now
    requires a _wall_clock_seconds sample universally, not just anchors, so
    this is caught by that general check rather than an anchor-specific one.)"""

    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    events = journal.read_all()

    bad_launch_intent = DiscoveryEvent(
        event_id="bad-launch-intent",
        campaign_id=manifest.campaign_id,
        campaign_manifest_hash=manifest.manifest_hash,
        attempt_id="att-1",
        event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT,
        sequence=len(events),
        observed_at=0,
        payload={"resume_from_checkpoint": False},  # no _wall_clock_seconds
    )

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="missing its required _wall_clock_seconds sample"):
        fresh.recover(events + [bad_launch_intent])


def test_contradictory_campaign_deadline_evidence_fails_closed(tmp_path):
    """A tampered/corrupted journal containing two CAMPAIGN_PREPARED-shaped
    entries with DIFFERENT _wall_clock_seconds anchors must fail closed —
    the deadline anchor is inferred once from durable evidence, and any
    contradiction is proof the journal cannot be trusted."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))
    prepared_1 = DiscoveryEvent(
        event_id="prepared-1", campaign_id=manifest.campaign_id, campaign_manifest_hash=manifest.manifest_hash,
        attempt_id=None, event_type=DiscoveryEventType.CAMPAIGN_PREPARED, sequence=0, observed_at=0,
        payload={"_wall_clock_seconds": 0.0},
    )
    prepared_2 = DiscoveryEvent(
        event_id="prepared-2", campaign_id=manifest.campaign_id, campaign_manifest_hash=manifest.manifest_hash,
        attempt_id=None, event_type=DiscoveryEventType.CAMPAIGN_PREPARED, sequence=1, observed_at=1,
        payload={"_wall_clock_seconds": 100.0},  # contradicts the deadline implied by prepared_1
    )

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "j.jsonl")),
        attempt_executor=None, proxy_evaluator=None,
    )
    with pytest.raises(JournalError, match="contradicts the already-anchored campaign"):
        fresh.recover([prepared_1, prepared_2])


def test_wall_clock_rollback_fails_closed(tmp_path):
    """A later-sequence event whose observed_at moves BACKWARD relative to
    an earlier event already processed in the same journal is proof the
    wall-clock domain is not trustworthy — recovery must fail closed."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=100))
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    clock = ManualClock(start=0)
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None, clock=clock, wall_clock=clock,
    )
    supervisor.prepare()  # observed_at=0
    clock.advance(5)
    supervisor._emit(attempt_id=None, event_type=DiscoveryEventType.CAMPAIGN_STOP_REQUESTED, payload={})  # observed_at=5
    events = journal.read_all()

    rollback_event = DiscoveryEvent(
        event_id="rollback",
        campaign_id=manifest.campaign_id,
        campaign_manifest_hash=manifest.manifest_hash,
        attempt_id=None,
        event_type=DiscoveryEventType.CAMPAIGN_STOPPED,
        sequence=len(events),
        observed_at=2,  # BEFORE the previously observed 5 — a rollback
        payload={},
    )

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=None, proxy_evaluator=None,
    )
    with pytest.raises(JournalError, match="wall-clock rollback"):
        fresh.recover(events + [rollback_event])


async def test_terminal_receipt_is_truthful_and_exactly_once_across_crash_recovery(tmp_path):
    """The receipt's wall_seconds must truthfully include BOTH the pre-crash
    and post-recovery elapsed time (not just what happened after recovery),
    and a repeated recovery of an already-terminal attempt must never
    produce a second receipt or re-finalize it."""

    manifest = make_manifest(
        budgets=make_budgets(attempt_timeout_seconds=1000, max_wall_seconds=1000, max_restarts_per_attempt=1)
    )
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    wall_clock = ManualClock(start=0)
    supervisor1 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None, wall_clock=wall_clock
    )
    supervisor1.prepare()
    assignment = make_assignment()
    supervisor1.submit(assignment)
    supervisor1._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT, payload={"resume_from_checkpoint": False}
    )
    wall_clock.advance(3.0)  # pre-crash elapsed
    events = journal.read_all()

    async def executor(*, assignment, resume_from_checkpoint):
        wall_clock.advance(2.0)  # post-recovery elapsed
        return ExecutionOutcome(status="completed")

    async def evaluator(*, assignment):
        return EvaluationOutcome(status="completed", score_ref="x")

    # SAME durable file: supervisor2's synthesized/live events land here too
    # (a separate file would have a sequence gap where the pre-crash events
    # already live — exactly as the other repeated-recovery tests above).
    supervisor2 = CampaignSupervisor(
        manifest=manifest, journal=journal,
        attempt_executor=executor, proxy_evaluator=evaluator, wall_clock=wall_clock,
    )
    supervisor2.recover(events)
    await supervisor2.run_available()
    receipt = supervisor2.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED  # 3+2=5s, well inside the 1000s budget

    final_events = journal.read_all()
    terminal_events = [e for e in final_events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL]
    assert len(terminal_events) == 1  # exactly one terminal event, ever
    resource_receipt_payload = terminal_events[0].payload["resource_receipt"]
    assert resource_receipt_payload["wall_seconds"] == pytest.approx(5.0)  # 3 (pre-crash) + 2 (post-recovery), truthfully

    # A second recovery from the now-terminal journal must be a pure no-op:
    # exactly-one-terminal, no duplicate receipt, no re-finalize, no new
    # durable event written by this instance at all.
    third_journal_path = str(tmp_path / "third.jsonl")
    supervisor3 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(third_journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator, wall_clock=wall_clock,
    )
    supervisor3.recover(final_events)
    assert supervisor3.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.COMPLETED
    assert len(DiscoveryJournal(third_journal_path).read_all()) == 0


# ---------------------------------------------------------------------------
# Final P1 #4 / exactness defect: (1) an anchor read separately for live
# state vs. the durable payload can desynchronize live and replayed
# deadlines; (2) a wall-clock rollback mid-campaign was neither validated
# live nor bounded by any per-instance monotonic cap, letting a rolled-back
# clock "renew" the frozen campaign budget.
# ---------------------------------------------------------------------------


def test_campaign_anchor_is_captured_with_exactly_one_wall_clock_read(tmp_path):
    """THE first exactness probe: if prepare() took two separate wall-clock
    reads — one to compute the live deadline, one inside _emit for the
    durable payload — a StepClock returning a DIFFERENT value per call
    would desynchronize them (live deadline=110 from read #1=100, but the
    durable payload would carry read #2=101, and a fresh replay would
    reconstruct deadline=111). Exactly one read means both are identical."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    step_clock = StepClock([100.0, 101.0, 102.0, 103.0])  # buggy code would consume more than one
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None, wall_clock=step_clock,
    )
    supervisor.prepare()
    assert step_clock.calls == 1  # exactly one wall-clock read for the anchor
    assert supervisor._campaign_deadline_wall == pytest.approx(110.0)  # 100 + 10, NOT 101 + 10

    durable_wall = journal.read_all()[0].payload["_wall_clock_seconds"]
    assert durable_wall == pytest.approx(100.0)  # the SAME reading, not a second, later one

    recovered = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=None, proxy_evaluator=None, wall_clock=StepClock([100.0]),
    )
    recovered.recover(journal.read_all())
    assert recovered._campaign_deadline_wall == pytest.approx(110.0)  # matches the LIVE deadline exactly, not 111


async def test_first_launch_anchor_is_captured_with_exactly_one_wall_clock_read(tmp_path):
    """The attempt-level analogue: if _launch() took two separate reads for
    the first-launch anchor, the live `_attempt_deadline_wall` and the
    durably-stamped ATTEMPT_LAUNCH_INTENT payload would diverge under a
    StepClock. They must be built from the IDENTICAL sample."""

    manifest = make_manifest(
        budgets=make_budgets(attempt_timeout_seconds=5, max_wall_seconds=1000, max_restarts_per_attempt=1)
    )
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    # Every call returns a NEW, strictly increasing value — any TWO reads
    # meant to represent "the same moment" would observably diverge.
    step_clock = StepClock([float(i) for i in range(100, 200)])

    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator, wall_clock=step_clock,
    )
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor.run_available()

    launch_intent_events = [e for e in journal.read_all() if e.event_type == DiscoveryEventType.ATTEMPT_LAUNCH_INTENT]
    assert len(launch_intent_events) == 1
    durable_anchor = launch_intent_events[0].payload["_wall_clock_seconds"]

    live_first_launch_wall = supervisor._attempt_deadline_wall["att-1"] - 5.0
    assert live_first_launch_wall == pytest.approx(durable_anchor)  # the SAME sample, not two different ones


def test_primary_probe_2_wall_clock_rollback_during_live_campaign_fails_closed(tmp_path):
    """THE second primary probe: max_wall_seconds=10; prepare at wall=100,
    monotonic=0. The wall clock then rolls back to 80 while monotonic
    correctly advances to 9. `_remaining_wall_seconds()` must NOT report 30
    (the old bug: 110 - 80) — it must fail closed on the live rollback,
    before returning any value at all."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    wall = {"value": 100.0}
    monotonic = {"value": 0.0}
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None,
        wall_clock=lambda: wall["value"], operational_clock=lambda: monotonic["value"],
    )
    supervisor.prepare()
    assert supervisor._campaign_deadline_wall == pytest.approx(110.0)

    wall["value"] = 80.0
    monotonic["value"] = 9.0

    with pytest.raises(JournalError, match="wall-clock rollback"):
        supervisor._remaining_wall_seconds()
    assert supervisor._poisoned  # this instance's local authority is now untrustworthy, full stop


def test_primary_probe_2_wall_clock_rollback_during_recovery_fails_closed(tmp_path):
    """Fresh recovery whose CURRENT wall reading (80) is behind the
    journal's own last durable wall sample (100, from CAMPAIGN_PREPARED)
    must fail closed BEFORE synthesizing any event or exposing runnable
    state — a recovering process cannot "renew" the frozen campaign budget
    just because its own clock happens to read behind the record."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    supervisor1 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None, wall_clock=lambda: 100.0,
    )
    supervisor1.prepare()
    events = journal.read_all()

    recovered = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=None, proxy_evaluator=None, wall_clock=lambda: 80.0,
    )
    with pytest.raises(JournalError, match="wall-clock rollback"):
        recovered.recover(events)
    assert recovered._poisoned
    assert not recovered._prepared  # never exposed as runnable
    assert len(recovered._queue) == 0  # nothing was synthesized or queued
    assert len(DiscoveryJournal(str(tmp_path / "other.jsonl")).read_all()) == 0  # no event was ever appended


def test_stuck_wall_clock_is_still_caught_by_the_monotonic_campaign_cap(tmp_path):
    """Defense in depth: a wall clock that STOPS advancing (stuck at the
    same value, so the strict rollback check alone never fires — it is
    never LESS than the last accepted sample) must still be caught by the
    per-instance monotonic cap. Real operational time elapsed in this
    process bounds the reported remaining budget regardless of what a
    stuck/lying wall clock claims."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    monotonic = {"value": 0.0}
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None,
        wall_clock=lambda: 100.0,  # STUCK: always the same value, never decreases, never rolls back
        operational_clock=lambda: monotonic["value"],
    )
    supervisor.prepare()
    assert supervisor._remaining_wall_seconds() == pytest.approx(10.0)

    monotonic["value"] = 15.0  # 15 REAL seconds have genuinely passed in this process
    # The wall clock alone (stuck at 100) would still report the full 10s
    # remaining — only the monotonic cap catches this.
    assert supervisor._remaining_wall_seconds() == pytest.approx(-5.0)


def test_nonfinite_live_wall_clock_sample_fails_closed(tmp_path):
    """A live wall_clock() callable returning NaN or Infinity must fail
    closed immediately — before any state change or journal append."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))

    for bad_value in (float("nan"), float("inf"), float("-inf")):
        journal = DiscoveryJournal(str(tmp_path / f"journal-{bad_value}.jsonl"))
        supervisor = CampaignSupervisor(
            manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None,
            wall_clock=lambda v=bad_value: v,
        )
        with pytest.raises(JournalError, match="non-finite"):
            supervisor.prepare()
        assert supervisor._poisoned
        assert not supervisor._prepared
        assert journal.read_all() == []  # nothing was ever appended


def test_nonfinite_replay_wall_clock_payload_fails_closed(tmp_path):
    """`_observe_wall_clock_domain` must reject a NaN/Infinity
    _wall_clock_seconds payload directly: `NaN < 0` and `Infinity < 0` are
    both False, so the old "reject negative values" check ALONE would have
    silently accepted them. (In this codebase's actual recover() path,
    pydantic's JSON-mode round-trip during revalidation already sanitizes
    NaN/Infinity down to `None` before this method ever sees them — this
    test exercises the validator itself directly, as an independent second
    line of defense against exactly this class of value.)"""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))

    for bad_value in (float("nan"), float("inf"), float("-inf")):
        supervisor = CampaignSupervisor(
            manifest=manifest, journal=DiscoveryJournal(str(tmp_path / f"j-{bad_value}.jsonl")),
            attempt_executor=None, proxy_evaluator=None,
        )
        bad_event = DiscoveryEvent(
            event_id=f"prepared-{bad_value}",
            campaign_id=manifest.campaign_id,
            campaign_manifest_hash=manifest.manifest_hash,
            attempt_id=None,
            event_type=DiscoveryEventType.CAMPAIGN_PREPARED,
            sequence=0,
            observed_at=0,
            payload={"_wall_clock_seconds": bad_value},
        )
        with pytest.raises(JournalError, match="non-finite"):
            supervisor._observe_wall_clock_domain(bad_event)


def test_bool_and_negative_replay_wall_clock_payload_fails_closed(tmp_path):
    """Unlike NaN/Infinity, a bool or a negative number survives pydantic's
    JSON round-trip unchanged — these must be rejected via the PUBLIC
    recover() path, hand-crafting a journal that carries each directly."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))

    bool_event = DiscoveryEvent(
        event_id="prepared-bool", campaign_id=manifest.campaign_id, campaign_manifest_hash=manifest.manifest_hash,
        attempt_id=None, event_type=DiscoveryEventType.CAMPAIGN_PREPARED, sequence=0, observed_at=0,
        payload={"_wall_clock_seconds": True},
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "j-bool.jsonl")),
        attempt_executor=None, proxy_evaluator=None,
    )
    with pytest.raises(JournalError, match="non-numeric"):
        fresh.recover([bool_event])

    negative_event = DiscoveryEvent(
        event_id="prepared-neg", campaign_id=manifest.campaign_id, campaign_manifest_hash=manifest.manifest_hash,
        attempt_id=None, event_type=DiscoveryEventType.CAMPAIGN_PREPARED, sequence=0, observed_at=0,
        payload={"_wall_clock_seconds": -5.0},
    )
    fresh2 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "j-neg.jsonl")),
        attempt_executor=None, proxy_evaluator=None,
    )
    with pytest.raises(JournalError, match="negative"):
        fresh2.recover([negative_event])


# ---------------------------------------------------------------------------
# Fresh P1 #1: recover([]) marked the campaign prepared/RUNNING with no
# durable evidence at all, exposing an unbounded fresh budget. Recovery must
# now require exactly one valid CAMPAIGN_PREPARED event with a finite
# anchor, and EVERY event (not just the two anchor types) must carry a
# valid _wall_clock_seconds sample to prevent a stripped/forged non-anchor
# event from bypassing the monotonic-domain check entirely.
# ---------------------------------------------------------------------------


def test_empty_recovery_fails_closed(tmp_path):
    """recover([]) must fail closed — never mark the campaign
    prepared/RUNNING, expose a fresh full budget, or allow any work to
    proceed, purely because CampaignState.PREPARED happens to be the
    in-memory DEFAULT rather than durably-observed evidence."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "j.jsonl")),
        attempt_executor=None, proxy_evaluator=None,
    )
    with pytest.raises(JournalError, match="requires exactly one durable CAMPAIGN_PREPARED"):
        supervisor.recover([])
    assert not supervisor._prepared
    assert supervisor._campaign_deadline_wall is None
    assert len(supervisor._queue) == 0


def test_missing_wall_clock_sample_on_non_anchor_event_fails_closed(tmp_path):
    """A non-anchor event (ATTEMPT_SUBMIT_INTENT, not CAMPAIGN_PREPARED or
    ATTEMPT_LAUNCH_INTENT) hand-crafted WITHOUT its required
    _wall_clock_seconds sample must fail closed too — this is not tolerated
    just because the event isn't one of the two deadline anchors; every
    supervisor-produced event carries the field unconditionally."""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))
    prepared = DiscoveryEvent(
        event_id="prepared", campaign_id=manifest.campaign_id, campaign_manifest_hash=manifest.manifest_hash,
        attempt_id=None, event_type=DiscoveryEventType.CAMPAIGN_PREPARED, sequence=0, observed_at=0,
        payload={"_wall_clock_seconds": 0.0},
    )
    submit_intent = DiscoveryEvent(
        event_id="submit-intent", campaign_id=manifest.campaign_id, campaign_manifest_hash=manifest.manifest_hash,
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_SUBMIT_INTENT, sequence=1, observed_at=0,
        payload={"assignment": make_assignment().model_dump(mode="json")},  # no _wall_clock_seconds
    )
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "j.jsonl")),
        attempt_executor=None, proxy_evaluator=None,
    )
    with pytest.raises(JournalError, match="missing its required _wall_clock_seconds sample"):
        supervisor.recover([prepared, submit_intent])


def test_nonfinite_wall_clock_sample_on_non_anchor_event_fails_closed(tmp_path):
    """`_observe_wall_clock_domain` must reject a NaN/Infinity
    _wall_clock_seconds sample on a NON-anchor event too — the universal
    per-event requirement, not just the two anchor types. (As with the
    anchor-type case, pydantic's JSON round-trip inside recover()'s own
    revalidation sanitizes NaN/Infinity to None before this method would
    normally see them — this test exercises the validator directly, an
    independent second line of defense.)"""

    manifest = make_manifest(budgets=make_budgets(max_wall_seconds=10))
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "j.jsonl")),
        attempt_executor=None, proxy_evaluator=None,
    )
    for bad_value in (float("nan"), float("inf"), float("-inf")):
        bad_event = DiscoveryEvent(
            event_id=f"stop-requested-{bad_value}", campaign_id=manifest.campaign_id,
            campaign_manifest_hash=manifest.manifest_hash, attempt_id=None,
            event_type=DiscoveryEventType.CAMPAIGN_STOP_REQUESTED, sequence=0, observed_at=0,
            payload={"_wall_clock_seconds": bad_value},
        )
        with pytest.raises(JournalError, match="non-finite"):
            supervisor._observe_wall_clock_domain(bad_event)


# ---------------------------------------------------------------------------
# Fresh P1 #2: a repeated-resumed crash was not distinguished from a fully-
# resolved one. INTERRUPTED with a NEW pending launch/evaluate intent (a
# SECOND crash after the first requeue) must charge exactly one more
# restart and durably resolve it — or terminalize once the budget is
# exhausted — never blindly free-requeue (which left a dangling duplicate
# intent that failed a later, otherwise-complete journal's replay).
# ---------------------------------------------------------------------------


async def test_two_crash_cycles_with_restart_budget_available_eventually_completes(tmp_path):
    """Two SEPARATE crashes of the SAME attempt, each after its own launch
    intent with no resolving outcome. Recovery #2 must see INTERRUPTED with
    a NEW pending intent (proof of the second crash) and charge exactly one
    MORE restart — not free-requeue as if nothing new happened. With budget
    still available, it then completes normally, and the full journal
    (both crashes, both resolutions, and the completion) replays cleanly."""

    manifest = make_manifest(
        budgets=make_budgets(max_restarts_per_attempt=3, max_wall_seconds=1000, attempt_timeout_seconds=1000)
    )
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    supervisor1 = CampaignSupervisor(manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None)
    supervisor1.prepare()
    assignment = make_assignment()
    supervisor1.submit(assignment)
    # Crash #1: launch intent emitted, no outcome — simulated hard crash.
    supervisor1._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT, payload={"resume_from_checkpoint": False}
    )
    events = journal.read_all()

    recovery1 = CampaignSupervisor(manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None)
    recovery1.recover(events)
    assert len(recovery1._queue) == 1
    assert recovery1._restarts_used["att-1"] == 1
    assert recovery1.attempt_state("att-1") is AttemptState.INTERRUPTED

    # A fresh recovery of the SAME point must be fully idempotent (crash #1
    # is already fully resolved — no pending intent survives it).
    idempotent_check = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "check1.jsonl")),
        attempt_executor=None, proxy_evaluator=None,
    )
    idempotent_check.recover(journal.read_all())
    assert idempotent_check._restarts_used["att-1"] == 1  # NOT double-charged
    assert len(DiscoveryJournal(str(tmp_path / "check1.jsonl")).read_all()) == 0  # nothing new synthesized

    # Crash #2: recovery1 relaunches (a SECOND launch intent), crashes again.
    recovery1._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT, payload={"resume_from_checkpoint": False}
    )
    events = journal.read_all()

    recovery2 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    recovery2.recover(events)
    assert len(recovery2._queue) == 1
    assert recovery2._restarts_used["att-1"] == 2  # charged exactly one MORE, not stuck at 1

    await recovery2.run_available()
    receipt = recovery2.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED
    assert recovery2._restarts_used["att-1"] == 2  # completion never charges another restart

    # The FULL final journal — both crashes, both resolutions, and the
    # eventual completion — replays cleanly with no duplicate/unpaired
    # intents, and shows exactly one terminal receipt.
    final_events = journal.read_all()
    verifier = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "verify.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    verifier.recover(final_events)  # must not raise
    assert verifier.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.COMPLETED
    terminal_events = [e for e in final_events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL]
    assert len(terminal_events) == 1


async def test_three_crash_cycles_exhausts_restart_budget_and_terminalizes_failed(tmp_path):
    """THREE separate crashes of the same attempt with
    max_restarts_per_attempt=2: crash #1 charges restart 1 (requeue), crash
    #2 charges restart 2 (requeue — budget now exhausted), crash #3's
    recovery must see INTERRUPTED with a pending intent, find the budget
    already exhausted, and terminalize FAILED directly FROM state
    INTERRUPTED — never requeue a fourth time, and never leave the intent
    unresolved/duplicated in the final journal."""

    manifest = make_manifest(
        budgets=make_budgets(max_restarts_per_attempt=2, max_wall_seconds=1000, attempt_timeout_seconds=1000)
    )
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    supervisor = CampaignSupervisor(manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None)
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    supervisor._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT, payload={"resume_from_checkpoint": False}
    )
    events = journal.read_all()

    recovery1 = CampaignSupervisor(manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None)
    recovery1.recover(events)
    assert recovery1._restarts_used["att-1"] == 1
    assert len(recovery1._queue) == 1

    recovery1._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT, payload={"resume_from_checkpoint": False}
    )
    events = journal.read_all()

    recovery2 = CampaignSupervisor(manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None)
    recovery2.recover(events)
    assert recovery2._restarts_used["att-1"] == 2  # budget now exhausted (== max_restarts_per_attempt)
    assert len(recovery2._queue) == 1  # still requeued: budget WAS available at the moment of this charge
    assert recovery2.attempt_state("att-1") is AttemptState.INTERRUPTED

    recovery2._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT, payload={"resume_from_checkpoint": False}
    )
    events = journal.read_all()

    recovery3 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    recovery3.recover(events)
    # Budget was already exhausted (2 == max_restarts_per_attempt=2) BEFORE
    # this third crash — terminalize directly, never requeue a third time.
    assert len(recovery3._queue) == 0
    assert recovery3._restarts_used["att-1"] == 2  # NOT charged a third time
    receipt = recovery3.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.FAILED
    assert "restart budget" in receipt.unresolved_gap

    # The FULL final journal replays cleanly with no duplicate/unpaired
    # intents, and shows exactly one terminal receipt.
    final_events = journal.read_all()
    verifier = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "verify.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    verifier.recover(final_events)  # must not raise
    assert verifier.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.FAILED
    terminal_events = [e for e in final_events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL]
    assert len(terminal_events) == 1

    # And repeated recovery of this now-terminal journal is a pure no-op.
    verifier2 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "verify2.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    verifier2.recover(final_events)
    assert len(DiscoveryJournal(str(tmp_path / "verify2.jsonl")).read_all()) == 0


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #8, P1-3: journal evidence contract
# ---------------------------------------------------------------------------


def test_journal_rejects_duplicate_key_json_line(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text('{"a": 1, "a": 2}\n')
    with pytest.raises(JournalError, match="duplicate key"):
        DiscoveryJournal(str(path)).read_all()


def test_journal_rejects_duplicate_key_nested_in_payload(tmp_path):
    path = tmp_path / "journal.jsonl"
    # A duplicate key nested inside `payload.resource_receipt` (the exact
    # rehashed-terminal shape the independent reproduction used) must be
    # caught too -- `object_pairs_hook` fires at every nesting level.
    path.write_text('{"outer": {"resource_receipt": {"x": 1, "x": 2}}}\n')
    with pytest.raises(JournalError, match="duplicate key"):
        DiscoveryJournal(str(path)).read_all()


async def test_forged_in_budget_completed_terminal_past_deadline_rejected(tmp_path):
    """Independent reproduction: a rehashed COMPLETED terminal at wall 20
    with campaign deadline 10 and a forged in-budget resource_receipt.wall_seconds
    of 0 must be rejected -- the terminal event's OWN wall-clock evidence
    must be cross-checked against the honestly reconstructed elapsed time,
    not merely internally self-consistent."""

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_wall_seconds": 1000})
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED (an ACTIVE state)
    await supervisor._evaluate(assignment)  # -> a genuine EVALUATE_OUTCOME(completed), satisfying the F1 precursor
    events = journal.read_all()

    # A forged terminal claiming COMPLETED at wall 20 (the event's own
    # `_wall_clock_seconds`) but an untruthfully low resource_receipt.wall_seconds
    # of 0 (as if the attempt used none of its budget).
    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        resource_overrides={"evaluations_used": 1},
    )
    forged_payload = dict(bogus.payload)
    forged_payload["_wall_clock_seconds"] = 20.0
    bogus = _rebuild_event_with_payload(bogus, forged_payload)

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="does not match the wall-clock evidence"):
        fresh.recover(events + [bogus])


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #8, P1-5: own-journal terminal replay
# (unprocessed brief 7, verbatim requirements)
# ---------------------------------------------------------------------------


async def test_primary_probe_wall_20_overrun_recovers_timed_out_then_fresh_replay_succeeds(tmp_path):
    """Primary probe: max_wall_seconds=10, attempt_timeout_seconds=100.
    Prepare/submit/emit first launch intent at wall=0, a process gap
    advances wall to 20, recover-and-run correctly terminalizes TIMED_OUT
    with truthful resource wall_seconds=20 across six durable events -- and
    a FRESH recovery of that exact journal must ALSO succeed, not reject
    "illegal transition terminal from AttemptState.INTERRUPTED". Also
    covers repeated final replay: exactly one terminal, both times."""

    journal_path = str(tmp_path / "journal.jsonl")
    manifest = make_manifest(
        budgets=make_budgets(max_wall_seconds=10, attempt_timeout_seconds=100, max_restarts_per_attempt=1)
    )
    clock = ManualClock()
    process_a = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
        clock=clock, operational_clock=clock, wall_clock=clock,
    )
    process_a.prepare()  # wall=0
    process_a.submit(make_assignment())
    process_a._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT,
        payload={"resume_from_checkpoint": False},
    )
    # process_a crashes here -- no launch outcome is ever recorded.

    clock.advance(20)  # real wall time keeps advancing through the crash gap

    events_before_recovery = DiscoveryJournal(journal_path).read_all()
    process_b = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
        clock=clock, operational_clock=clock, wall_clock=clock,
    )
    process_b.recover(events_before_recovery)
    await process_b.run_available()

    receipt = process_b.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT

    all_events = DiscoveryJournal(journal_path).read_all()
    assert len(all_events) == 6
    terminal_event = next(e for e in all_events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL)
    assert terminal_event.payload["resource_receipt"]["wall_seconds"] == pytest.approx(20.0)

    # A FRESH recovery of that exact journal must succeed.
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "unused.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(all_events)
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.TIMED_OUT

    # Repeated final replay: exactly one terminal, still.
    fresh_again = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "unused2.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh_again.recover(all_events)
    assert fresh_again.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.TIMED_OUT
    terminals = [e for e in all_events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL]
    assert len(terminals) == 1


async def test_attempt_deadline_overrun_from_interrupted_recovers_and_replays(tmp_path):
    """The attempt-timeout analogue of the primary probe: the ATTEMPT's own
    deadline (not the campaign's) is what's exhausted after the crash gap."""

    journal_path = str(tmp_path / "journal.jsonl")
    manifest = make_manifest(
        budgets=make_budgets(max_wall_seconds=1000, attempt_timeout_seconds=10, max_restarts_per_attempt=1)
    )
    clock = ManualClock()
    process_a = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
        clock=clock, operational_clock=clock, wall_clock=clock,
    )
    process_a.prepare()
    process_a.submit(make_assignment())
    process_a._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT,
        payload={"resume_from_checkpoint": False},
    )
    clock.advance(20)

    process_b = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
        clock=clock, operational_clock=clock, wall_clock=clock,
    )
    process_b.recover(DiscoveryJournal(journal_path).read_all())
    await process_b.run_available()
    assert process_b.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.TIMED_OUT

    all_events = DiscoveryJournal(journal_path).read_all()
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "fresh.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(all_events)  # must not raise
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.TIMED_OUT


async def test_prepared_campaign_expiry_before_first_launch_terminalizes_timed_out_directly(tmp_path):
    """A live (non-crash) campaign-expiry: wall time already exceeds the
    campaign budget by the time this attempt's FIRST-EVER launch happens --
    terminalizes TIMED_OUT directly FROM state PREPARED, never through
    INTERRUPTED at all."""

    supervisor, manifest, journal, clock = make_supervisor_with_clock(
        tmp_path, budgets_overrides={"max_wall_seconds": 5, "attempt_timeout_seconds": 100}
    )
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()  # wall=0, deadline=5
    supervisor.submit(make_assignment())
    clock.advance(10)  # budget already exhausted before this attempt ever launches
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT
    assert supervisor.attempt_state("att-1") is AttemptState.TIMED_OUT

    events = journal.read_all()
    interrupted_events = [e for e in events if e.event_type == DiscoveryEventType.ATTEMPT_INTERRUPTED]
    assert interrupted_events == []  # never transitioned through INTERRUPTED

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "fresh.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # must not raise "illegal transition terminal from state PREPARED"
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.TIMED_OUT


async def test_max_restarts_zero_prepared_crash_terminalizes_failed_from_prepared(tmp_path):
    """max_restarts_per_attempt=0: a mid-flight crash discovered at recovery
    while still PREPARED (never even launched) exhausts restart capacity on
    its very first crash and terminalizes FAILED directly from PREPARED --
    it never transitions through INTERRUPTED at all."""

    journal_path = str(tmp_path / "journal.jsonl")
    manifest = make_manifest(budgets=make_budgets(max_restarts_per_attempt=0))
    process_a = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    process_a.prepare()
    process_a.submit(make_assignment())
    # process_a crashes here -- submitted/PREPARED, never launched.

    process_b = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    process_b.recover(DiscoveryJournal(journal_path).read_all())
    receipt = process_b.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.FAILED
    assert process_b.attempt_state("att-1") is AttemptState.FAILED

    all_events = DiscoveryJournal(journal_path).read_all()
    interrupted_events = [e for e in all_events if e.event_type == DiscoveryEventType.ATTEMPT_INTERRUPTED]
    assert interrupted_events == []  # never transitioned through INTERRUPTED at all

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "fresh.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(all_events)  # must not raise "illegal transition terminal from state PREPARED"
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.FAILED


def test_forged_failed_terminal_from_resolved_interrupted_with_restart_capacity_remaining_rejected(tmp_path):
    """A self-hashed FAILED terminal from an already-resolved INTERRUPTED
    state (no pending intent) with restart capacity remaining must be
    rejected -- an attacker cannot forge premature exhaustion."""

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 3})
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    # Genuinely interrupt (crash) and resolve it: one restart charged, two
    # of the three restart slots remain, and there is no pending intent.
    supervisor._restarts_used["att-1"] = 1
    supervisor._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_INTERRUPTED,
        payload={"reason": "test", "restarts_used": 1},
    )
    events = journal.read_all()

    # `_valid_terminal_event`'s override mechanism can't override a field
    # that already has an explicit default kwarg (a pre-existing, unrelated
    # helper limitation — no prior test actually passed a non-None
    # resource_overrides), so this one builds the receipts directly.
    from metaharness.discovery.models import DiscoveryResourceReceipt, DiscoveryTerminalReceipt

    resource = DiscoveryResourceReceipt(
        receipt_id="res-att-1", campaign_id=manifest.campaign_id, attempt_id="att-1",
        sequence=0, wall_seconds=0.0, evaluations_used=0, restarts_used=1,
    )
    terminal = DiscoveryTerminalReceipt(
        receipt_id="term-att-1", campaign_id=manifest.campaign_id, lineage_id="lin-1", attempt_id="att-1",
        sequence=1, outcome=DiscoveryTerminalOutcome.FAILED, resource_receipt_id=resource.receipt_id,
        closest_protected_result="proxy-only:x", unresolved_gap="none",
    )
    bogus = DiscoveryEvent(
        event_id="evt-terminal-att-1", campaign_id=manifest.campaign_id, campaign_manifest_hash=manifest.manifest_hash,
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_TERMINAL, sequence=len(events), observed_at=0,
        payload={
            "resource_receipt": resource.model_dump(mode="json"),
            "terminal_receipt": terminal.model_dump(mode="json"),
            "_wall_clock_seconds": 0.0,
        },
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])


async def test_restart_exhausted_pending_crash_terminalizes_failed_from_interrupted(tmp_path):
    """The LEGITIMATE counterpart: a genuine second crash after requeue (a
    pending, unresolved launch intent) with the restart budget already at
    its frozen cap terminalizes FAILED directly from INTERRUPTED -- and a
    fresh replay of that exact journal must accept it."""

    journal_path = str(tmp_path / "journal.jsonl")
    manifest = make_manifest(
        budgets=make_budgets(max_restarts_per_attempt=1, max_wall_seconds=1000, attempt_timeout_seconds=1000)
    )
    process_a = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    process_a.prepare()
    process_a.submit(make_assignment())
    process_a._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT,
        payload={"resume_from_checkpoint": False},
    )
    # process_a crashes -- no outcome.

    process_b = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    process_b.recover(DiscoveryJournal(journal_path).read_all())  # charges restart 1/1, requeues
    assert process_b.attempt_state("att-1") is AttemptState.INTERRUPTED

    # process_b relaunches (a NEW launch intent) and crashes again before any outcome.
    process_b._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT, payload={"resume_from_checkpoint": True}
    )

    process_c = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    process_c.recover(DiscoveryJournal(journal_path).read_all())  # budget (1) already spent -> FAILED from INTERRUPTED
    receipt = process_c.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.FAILED
    assert process_c.attempt_state("att-1") is AttemptState.FAILED

    all_events = DiscoveryJournal(journal_path).read_all()
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "fresh.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(all_events)  # must not reject the legitimate restart-exhausted pending crash
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.FAILED


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #8, P2: injected clock/operational port and
# adapter outcome validation
# ---------------------------------------------------------------------------


def test_injected_event_clock_negative_sample_poisons_and_fails_closed(tmp_path):
    manifest = make_manifest(budgets=make_budgets())
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None,
        clock=lambda: -1, wall_clock=lambda: 0.0, operational_clock=lambda: 0.0,
    )
    with pytest.raises(JournalError):
        supervisor.prepare()
    with pytest.raises(SupervisorPoisonedError):
        _ = supervisor.campaign_state


def test_injected_operational_clock_nonfinite_sample_poisons_and_fails_closed(tmp_path):
    manifest = make_manifest(budgets=make_budgets())
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None,
        clock=lambda: 0, wall_clock=lambda: 0.0, operational_clock=lambda: float("nan"),
    )
    with pytest.raises(JournalError):
        supervisor.prepare()
    with pytest.raises(SupervisorPoisonedError):
        _ = supervisor.campaign_state


def test_injected_operational_clock_rollback_poisons_and_fails_closed(tmp_path):
    values = iter([10.0, 5.0])
    manifest = make_manifest(budgets=make_budgets())
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=None, proxy_evaluator=None,
        clock=lambda: 0, wall_clock=lambda: 0.0, operational_clock=lambda: next(values),
    )
    supervisor.prepare()  # first operational read: 10.0, ok
    with pytest.raises(JournalError):
        supervisor._read_validated_operational_clock()  # second read: 5.0, rollback
    with pytest.raises(SupervisorPoisonedError):
        _ = supervisor.campaign_state


async def test_executor_returning_foreign_object_is_treated_as_worker_error_not_trusted(tmp_path):
    class FakeOutcome:
        status = "completed"  # impersonates success without being an ExecutionOutcome

    async def foreign_executor(*, assignment, resume_from_checkpoint):
        return FakeOutcome()

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = foreign_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.WORKER_ERROR


async def test_executor_returning_unknown_status_is_treated_as_worker_error(tmp_path):
    async def weird_executor(*, assignment, resume_from_checkpoint):
        return ExecutionOutcome(status="totally-not-a-real-status")

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = weird_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.WORKER_ERROR


async def test_evaluator_returning_foreign_object_is_treated_as_evaluator_error(tmp_path):
    class FakeEvalOutcome:
        status = "completed"
        score_ref = "fake"

    async def foreign_evaluator(*, assignment):
        return FakeEvalOutcome()

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor._evaluator = foreign_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.EVALUATOR_ERROR


async def test_evaluator_returning_unknown_status_is_treated_as_evaluator_error(tmp_path):
    async def weird_evaluator(*, assignment):
        return EvaluationOutcome(status="totally-not-a-real-status", score_ref="x")

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor._evaluator = weird_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.EVALUATOR_ERROR


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #9, F6: submit() revalidates the assignment
# ---------------------------------------------------------------------------


def test_submit_rejects_stale_hash_assignment_before_journaling(tmp_path):
    """A `model_copy(update=...)` assignment has a STALE assignment_hash
    relative to its (post-mutation) fields. Journaling it as-is would durably
    embed a stale-hash object that a later recover() (which always
    revalidates) rejects -- a live/replay divergence."""

    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    genuine = make_assignment()
    tampered = genuine.model_copy(update={"seed": 999})  # stale hash after the mutation
    with pytest.raises(SupervisorError):
        supervisor.submit(tampered)
    # Nothing was journaled by the rejected submit (only prepare()'s own
    # CAMPAIGN_PREPARED event exists).
    events = journal.read_all()
    assert not any(e.event_type == DiscoveryEventType.ATTEMPT_SUBMIT_INTENT for e in events)


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #9, F7: completed evaluation requires evidence
# ---------------------------------------------------------------------------


async def test_completed_evaluation_with_empty_score_ref_is_evaluator_error(tmp_path):
    async def empty_score_evaluator(*, assignment):
        return EvaluationOutcome(status="completed", score_ref="")

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor._evaluator = empty_score_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.EVALUATOR_ERROR


async def test_completed_evaluation_with_blank_score_ref_is_evaluator_error(tmp_path):
    async def blank_score_evaluator(*, assignment):
        return EvaluationOutcome(status="completed", score_ref="   ")

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor._evaluator = blank_score_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.EVALUATOR_ERROR


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #10, F7r: evaluation evidence replay parity
# + non-string guard
# ---------------------------------------------------------------------------


async def test_completed_evaluator_returning_non_string_score_ref_is_evaluator_error_not_a_crash(tmp_path):
    """F7r(b): a non-string score_ref (e.g. an int) from an untrusted
    evaluator must normalize to evaluator_error, not raise AttributeError
    from a bare `.strip()` call and strand the attempt."""

    async def non_string_score_evaluator(*, assignment):
        return EvaluationOutcome(status="completed", score_ref=12345)  # not a str

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor._evaluator = non_string_score_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()  # must not raise AttributeError
    receipt = supervisor.terminal_receipt("att-1")
    assert receipt is not None
    assert receipt.outcome is DiscoveryTerminalOutcome.EVALUATOR_ERROR


async def test_replay_rejects_completed_evaluate_outcome_with_blank_score_ref(tmp_path):
    """F7r(a): replay used to accept a "completed" ATTEMPT_EVALUATE_OUTCOME
    with a blank score_ref with no evidence check at all -- live now refuses
    to ever produce that shape (brief #9, F7), so replay must reject it too
    instead of being MORE permissive than live."""

    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED
    supervisor._evaluations_used += 1
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
    supervisor._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_OUTCOME,
        payload={"status": "completed", "score_ref": ""},
    )
    events = journal.read_all()

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="no evidence"):
        fresh.recover(events)


async def test_replay_rejects_completed_evaluate_outcome_with_non_string_score_ref(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED
    supervisor._evaluations_used += 1
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
    supervisor._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_OUTCOME,
        payload={"status": "completed", "score_ref": 12345},
    )
    events = journal.read_all()

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError, match="no evidence"):
        fresh.recover(events)


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #9, F5: transactional recovery (supervisor)
# ---------------------------------------------------------------------------


def test_failed_mid_replay_recovery_poisons_the_instance(tmp_path):
    """CAMPAIGN_PREPARED/SUBMIT_INTENT/SUBMIT_OUTCOME replay cleanly (already
    mutating campaign_state/attempt_states) BEFORE a bogus terminal raises.
    The instance must come out poisoned/unusable, not a partially-recovered
    instance that happily answers campaign_state/attempt_state queries."""

    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    supervisor.submit(make_assignment())
    events = journal.read_all()

    bogus_terminal = DiscoveryEvent(
        event_id="bogus-1", campaign_id=manifest.campaign_id, campaign_manifest_hash=manifest.manifest_hash,
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_TERMINAL, sequence=len(events), observed_at=0,
        payload={},
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus_terminal])

    with pytest.raises(SupervisorPoisonedError):
        _ = fresh.campaign_state
    with pytest.raises(SupervisorPoisonedError):
        fresh.attempt_state("att-1")
    with pytest.raises(SupervisorPoisonedError):
        fresh.terminal_receipt("att-1")


def test_request_stop_requires_prepared(tmp_path):
    manifest = make_manifest(budgets=make_budgets())
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(SupervisorError):
        supervisor.request_stop()


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #9, F1: terminal replay outcome-consistent
# evidence for ALL precursors
# ---------------------------------------------------------------------------


async def test_forged_completed_terminal_after_crashed_launch_outcome_rejected(tmp_path):
    async def crashing_executor(*, assignment, resume_from_checkpoint):
        return ExecutionOutcome(status="crashed", detail="boom")

    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = crashing_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> RUNNING, last_launch_status="crashed"
    events = journal.read_all()

    bogus = _valid_terminal_event(manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events))
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])


async def test_forged_cancelled_terminal_from_running_with_campaign_running_rejected(tmp_path):
    """kimi repro: a forged CANCELLED from an ACTIVE state used to be
    unconditionally accepted regardless of the campaign's own state."""

    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        terminal_overrides={"outcome": DiscoveryTerminalOutcome.CANCELLED},
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])  # campaign never stopped -- CANCELLED has no evidence


async def test_forged_omitted_terminal_from_running_rejected(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        terminal_overrides={
            "outcome": DiscoveryTerminalOutcome.OMITTED,
            "closest_protected_result": None,
            "unresolved_gap": None,
            "omission_reason": "forged",
        },
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])  # OMITTED is only legitimate from state None


async def test_honest_completed_with_evaluate_outcome_still_replays(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()
    assert supervisor.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.COMPLETED

    events = journal.read_all()
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # must not raise
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.COMPLETED


async def test_full_live_journal_from_run_available_still_replays_byte_identically(tmp_path):
    supervisor, manifest, journal = make_supervisor(
        tmp_path, budgets_overrides={"max_attempts": 3, "max_evaluations": 3}
    )
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    for i in range(3):
        supervisor.submit(make_assignment(attempt_id=f"att-{i}", assignment_id=f"asg-{i}"))
    await supervisor.run_available()
    events = journal.read_all()

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # must not raise
    for i in range(3):
        original = supervisor.terminal_receipt(f"att-{i}")
        recovered = fresh.terminal_receipt(f"att-{i}")
        assert original.receipt_hash == recovered.receipt_hash
        assert original == recovered


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #9, F3: issuance, not just integrity
# ---------------------------------------------------------------------------


def test_issued_assignment_reflects_only_what_this_supervisor_actually_submitted(tmp_path):
    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    assert supervisor.issued_assignment("att-1") is None
    supervisor.submit(make_assignment())
    issued = supervisor.issued_assignment("att-1")
    assert issued is not None
    assert issued.attempt_id == "att-1"


def test_make_issuance_verifier_rejects_a_self_hashed_but_never_submitted_assignment(tmp_path):
    """A self-hashed DiscoveryAssignment proves integrity, not issuance --
    anyone with model access can mint one. The verifier must reject it
    unless THIS supervisor actually accepted it via submit()."""

    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor.prepare()
    verify = supervisor.make_issuance_verifier()

    forged = make_assignment(attempt_id="att-forged", assignment_id="asg-forged")
    assert verify(forged) is False

    supervisor.submit(make_assignment())
    genuine = supervisor.issued_assignment("att-1")
    assert verify(genuine) is True


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #10, F3r: issuance requires an accepted
# submit outcome, not merely an intent
# ---------------------------------------------------------------------------


def test_issued_assignment_requires_durable_submit_outcome_not_just_intent(tmp_path):
    """An unresolved submit intent (its SUBMIT_OUTCOME never durable)
    terminalizes OMITTED on recovery -- `issued_assignment()`/
    `make_issuance_verifier()` must return False/None for it, since its
    acceptance was never actually confirmed durable."""

    manifest = make_manifest(budgets=make_budgets())
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))
    supervisor = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator,
    )
    supervisor.prepare()
    assignment = make_assignment()
    supervisor._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_SUBMIT_INTENT,
        payload={"assignment": assignment.model_dump(mode="json")},
    )
    # process crashes here -- no ATTEMPT_SUBMIT_OUTCOME ever lands.
    events = DiscoveryJournal(str(tmp_path / "journal.jsonl")).read_all()

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # synthesizes an OMITTED terminal for att-1
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.OMITTED

    assert fresh.issued_assignment("att-1") is None
    verify = fresh.make_issuance_verifier()
    assert verify(assignment) is False


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #10, F1r: replay must accept every honest
# terminal producer
# ---------------------------------------------------------------------------


async def test_honest_cancelled_terminal_from_active_while_campaign_running_replays(tmp_path):
    """F1r(a): live _maybe_retry() produces a CANCELLED terminal from an
    executor-returned "cancelled" status while the campaign is RUNNING the
    whole time (never stopped) -- the campaign-stop-state rule alone cannot
    explain this, so replay needs the evidence-matched alternative too."""

    async def cancelling_executor(*, assignment, resume_from_checkpoint):
        return ExecutionOutcome(status="cancelled", detail="worker self-cancelled")

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = cancelling_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.CANCELLED
    assert supervisor.campaign_state is CampaignState.RUNNING  # never stopped

    events = journal.read_all()
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=cancelling_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # must not raise
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.CANCELLED


async def test_recovery_synthesized_failed_from_active_crash_max_restarts_zero_replays(tmp_path):
    """F1r(b)/brief-11 NEW-2: recovery synthesizes FAILED directly from an
    ACTIVE (never-interrupted) mid-flight crash when max_restarts=0 -- but
    ONLY when a pending, unresolved evaluate intent proves something was
    actually in flight when the process died. A bare post-checkpoint gap
    with no pending intent is not crash evidence (that is the honest
    completed-launch journal a forged FAILED would otherwise piggyback on)
    and must requeue via INTERRUPTED without a charge instead."""

    journal_path = str(tmp_path / "journal.jsonl")
    manifest = make_manifest(budgets=make_budgets(max_restarts_per_attempt=0))
    process_a = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    process_a.prepare()
    assignment = make_assignment()
    process_a.submit(assignment)
    await process_a._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED, fully resolved
    # The process dies DURING evaluation: dangling evaluate intent, no
    # resolving outcome ever journaled.
    process_a._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
    events_before = DiscoveryJournal(journal_path).read_all()

    process_b = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    process_b.recover(events_before)  # synthesizes FAILED directly from CHECKPOINTED
    receipt = process_b.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.FAILED
    assert process_b.attempt_state("att-1") is AttemptState.FAILED

    all_events = DiscoveryJournal(journal_path).read_all()
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "fresh.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(all_events)  # must not raise
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.FAILED


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #11 mandatory parity-10 specifics (NEW-1, NEW-2)
# ---------------------------------------------------------------------------


async def test_honest_early_executor_timed_out_from_running_replays(tmp_path):
    """Parity-10 NEW-1: an executor may honestly self-report "timed_out"
    well before the frozen deadline is actually exhausted (e.g. its own
    internal sub-timeout fired first). With max_restarts=0 this finalizes
    directly from RUNNING via `_maybe_retry`. The prior (batch-10) rule
    required `deadline_exhausted` unconditionally and wrongly rejected this
    honest, non-deadline-driven TIMED_OUT on replay."""

    supervisor, manifest, journal, clock = make_supervisor_with_clock(
        tmp_path, budgets_overrides={"max_restarts_per_attempt": 0, "max_wall_seconds": 1000, "attempt_timeout_seconds": 1000}
    )

    async def early_timeout_executor(*, assignment, resume_from_checkpoint):
        # No clock advance: the deadline is nowhere near exhausted.
        return ExecutionOutcome(status="timed_out", detail="internal sub-timeout fired early")

    supervisor._executor = early_timeout_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT

    events = journal.read_all()
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=early_timeout_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events)  # must not raise
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.TIMED_OUT


async def test_honest_early_evaluator_timed_out_from_checkpointed_replays(tmp_path):
    """Parity-10 NEW-1: same as above, for the evaluator: an honest early
    "timed_out" report from an evaluator finalizes directly from
    CHECKPOINTED with max_restarts=0, and must replay cleanly even though
    the campaign/attempt deadlines were never actually exhausted."""

    supervisor, manifest, journal, clock = make_supervisor_with_clock(
        tmp_path, budgets_overrides={"max_restarts_per_attempt": 0, "max_wall_seconds": 1000, "attempt_timeout_seconds": 1000}
    )

    async def early_timeout_evaluator(*, assignment):
        return EvaluationOutcome(status="timed_out", detail="internal sub-timeout fired early")

    supervisor._executor = always_completes_executor
    supervisor._evaluator = early_timeout_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.TIMED_OUT

    events = journal.read_all()
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=early_timeout_evaluator,
    )
    fresh.recover(events)  # must not raise
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.TIMED_OUT


async def test_forged_timed_out_terminal_from_running_without_evidence_rejected(tmp_path):
    """Forged-variant pair for the two honest tests above: neither the
    deadline is exhausted, nor did the journaled launch/evaluate status
    ever report "timed_out" -- a forged TIMED_OUT from RUNNING must still be
    rejected."""

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED, last_launch_status="completed"
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        terminal_overrides={"outcome": DiscoveryTerminalOutcome.TIMED_OUT},
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])  # no deadline exhaustion, no timed_out status anywhere


async def test_forged_failed_terminal_on_honest_completed_launch_no_pending_intent_rejected(tmp_path):
    """Parity-10 NEW-2: the exact codex repro. An honest, fully-resolved
    completed-launch journal (max_restarts=0, CHECKPOINTED, no pending
    intent -- nothing was ever in flight when the journal ends) must NOT
    accept a forged FAILED appended directly to it: the batch-10 rule
    dropped the pending-intent requirement and treated restart-budget-
    exhaustion alone as sufficient evidence, which this forged terminal
    would otherwise satisfy trivially (max_restarts=0). The FAILED-from-
    active rule now requires pending intent + restarts exhausted,
    symmetric with the INTERRUPTED rule."""

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED, fully resolved, no pending intent
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        terminal_overrides={"outcome": DiscoveryTerminalOutcome.FAILED},
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])  # no pending intent: nothing was actually in flight


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #12: evidence currency and contradiction
# (codex parity-11 sweep -- six accepted forgery probes, one root cause:
# evidence was not bound to the CURRENT round, the correct precursor
# sub-state, or the absence of contradicting better evidence)
# ---------------------------------------------------------------------------


async def test_forged_completed_terminal_using_stale_round1_evaluate_status_after_relaunch_rejected(tmp_path):
    """Probe 1 (rule 1, round currency): round 1 reaches CHECKPOINTED and
    journals a successful completed evaluate outcome, then the process
    crashes BEFORE the terminal is emitted. Recovery resumes it (no charge:
    nothing was actually in flight). Round 2 relaunches and reaches
    CHECKPOINTED again WITHOUT evaluating at all this round. A forged
    COMPLETED terminal citing round 1's now-stale "completed" evaluate
    status must be rejected -- a new round invalidates prior rounds'
    evidence."""

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 1})
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)

    await supervisor._launch(assignment, resume_from_checkpoint=False)  # round 1 -> CHECKPOINTED
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
    supervisor._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_OUTCOME,
        payload={"status": "completed", "score_ref": "round1-score"},
    )  # crash right here, before the terminal is ever emitted

    supervisor2 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator, clock=supervisor._clock, wall_clock=supervisor._wall_clock,
    )
    supervisor2.recover(journal.read_all())
    assert supervisor2.attempt_state("att-1") is AttemptState.INTERRUPTED

    await supervisor2._launch(assignment, resume_from_checkpoint=True)  # round 2 -> CHECKPOINTED, no evaluate yet
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        # evaluations_used=1 (round 1's evaluate intent charged the sole
        # slot; round 2 never re-evaluates) / restarts_used=0 (the ACTIVE-
        # no-pending-intent recovery path never charges a restart) --
        # matching what fresh replay will independently track, so the OTHER
        # cross-checks can't mask whether round currency is the thing
        # actually gating acceptance here.
        resource_overrides={"evaluations_used": 1, "restarts_used": 0},
        terminal_overrides={
            "outcome": DiscoveryTerminalOutcome.COMPLETED,
            "closest_protected_result": "proxy-only:round1-score",
        },
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])


async def test_forged_timed_out_terminal_using_stale_round1_evaluator_timed_out_after_retry_rejected(tmp_path):
    """Probe 2 (rule 1, round currency): same shape as probe 1, but round
    1's evaluate outcome is "timed_out" instead of "completed" -- round 2
    must not be able to cite it as evidence for a forged TIMED_OUT.
    max_restarts=0 so restart-budget-exhaustion is trivially satisfied,
    isolating round currency (rather than restart-budget non-exhaustion)
    as the thing that must reject this forgery."""

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)

    await supervisor._launch(assignment, resume_from_checkpoint=False)  # round 1 -> CHECKPOINTED
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
    supervisor._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_OUTCOME,
        payload={"status": "timed_out", "score_ref": ""},
    )  # crash right here, before the terminal is ever emitted

    supervisor2 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator, clock=supervisor._clock, wall_clock=supervisor._wall_clock,
    )
    supervisor2.recover(journal.read_all())
    assert supervisor2.attempt_state("att-1") is AttemptState.INTERRUPTED

    await supervisor2._launch(assignment, resume_from_checkpoint=True)  # round 2 -> CHECKPOINTED, no evaluate yet
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        resource_overrides={"evaluations_used": 1, "restarts_used": 0},
        terminal_overrides={"outcome": DiscoveryTerminalOutcome.TIMED_OUT},
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])  # round 1's stale "timed_out" evidence must not survive the relaunch


async def test_forged_evaluator_error_terminal_from_running_using_stale_evaluate_status_rejected(tmp_path):
    """Probe 3 (rules 1 + 2): round 1 reaches CHECKPOINTED with an
    evaluator_error outcome, crashes before finalizing, is resumed, and
    round 2's relaunch itself crashes (current=RUNNING, never reaching
    CHECKPOINTED this round). A forged EVALUATOR_ERROR terminal must be
    rejected both because the evidence is stale (rule 1) AND because
    evaluator-class evidence categorically requires precursor CHECKPOINTED,
    never RUNNING (rule 2). max_restarts=0 so restart-budget-exhaustion is
    trivially satisfied, isolating rules 1/2 (rather than restart-budget
    non-exhaustion) as the thing that must reject this forgery."""

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 0})
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)

    await supervisor._launch(assignment, resume_from_checkpoint=False)  # round 1 -> CHECKPOINTED
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
    supervisor._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_OUTCOME,
        payload={"status": "evaluator_error", "score_ref": ""},
    )  # crash right here, before the terminal is ever emitted

    supervisor2 = CampaignSupervisor(
        manifest=manifest, journal=journal, attempt_executor=always_completes_executor,
        proxy_evaluator=always_completes_evaluator, clock=supervisor._clock, wall_clock=supervisor._wall_clock,
    )
    supervisor2.recover(journal.read_all())
    assert supervisor2.attempt_state("att-1") is AttemptState.INTERRUPTED

    async def crashing_executor(*, assignment, resume_from_checkpoint):
        return ExecutionOutcome(status="crashed", detail="round 2 crash")

    supervisor2._executor = crashing_executor
    await supervisor2._launch(assignment, resume_from_checkpoint=True)  # round 2 -> RUNNING (never checkpoints)
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        resource_overrides={"evaluations_used": 1, "restarts_used": 0},
        terminal_overrides={"outcome": DiscoveryTerminalOutcome.EVALUATOR_ERROR},
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])


async def test_forged_failed_terminal_via_global_eval_budget_from_running_rejected(tmp_path):
    """Probe 4 (rule 4, row 9): the global evaluation budget is already
    exhausted (by a DIFFERENT, already-terminal attempt), but THIS attempt
    crashed during launch and never even reached CHECKPOINTED -- it was
    never blocked by the evaluation budget at all. A forged FAILED citing
    the exhausted budget must be rejected: row 9 requires precursor
    CHECKPOINTED with no evaluate intent/outcome for this attempt this
    round, not merely "some ACTIVE state"."""

    supervisor, manifest, journal = make_supervisor(
        tmp_path,
        budgets_overrides={
            "max_restarts_per_attempt": 0, "max_evaluations": 1, "max_attempts": 2, "max_concurrency": 1,
        },
    )
    supervisor._executor = always_completes_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    other = make_assignment(attempt_id="att-0", assignment_id="asg-0")
    supervisor.submit(other)
    await supervisor.run_available()  # consumes the sole evaluation slot
    assert supervisor.terminal_receipt("att-0").outcome is DiscoveryTerminalOutcome.COMPLETED

    async def crashing_executor(*, assignment, resume_from_checkpoint):
        return ExecutionOutcome(status="crashed", detail="boom")

    supervisor._executor = crashing_executor
    assignment = make_assignment()  # att-1
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> RUNNING; never checkpointed/evaluated
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        # Unique receipt sequences (att-0's own honest terminal already used
        # 0/1) and evaluations_used/restarts_used matching what fresh replay
        # independently tracks, so the OTHER cross-checks can't mask whether
        # row 9's CHECKPOINTED-plus-no-evaluate-this-round binding is what
        # actually rejects this forgery.
        resource_overrides={"sequence": 100, "evaluations_used": 1, "restarts_used": 0},
        terminal_overrides={"sequence": 101, "outcome": DiscoveryTerminalOutcome.FAILED},
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=crashing_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])


async def test_forged_timed_out_terminal_despite_current_round_completed_evaluation_rejected(tmp_path):
    """Probe 5 (rule 3, contradiction): the campaign deadline genuinely IS
    exhausted by the terminal event's own wall-clock sample (row 4 would
    otherwise unconditionally accept it), but THIS round already journaled
    a successful completed evaluate outcome -- no live producer ever emits
    TIMED_OUT after a completed evaluation. The completed-evaluation
    evidence must override even a truthful deadline-exhaustion claim."""

    supervisor, manifest, journal = make_supervisor(
        tmp_path, budgets_overrides={"max_wall_seconds": 5, "max_restarts_per_attempt": 0}
    )
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
    supervisor._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_OUTCOME,
        payload={"status": "completed", "score_ref": "score-1"},
    )
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        # Matches what fresh replay will independently track/reconstruct
        # (evaluations_used=1 from the one evaluate intent; wall_seconds
        # equal to the forged wall-clock sample below, since the fake clock
        # never advances so first_launch_wall is exactly 0) so the OTHER
        # cross-checks can't mask whether the contradiction rule itself is
        # what rejects this forgery.
        resource_overrides={"evaluations_used": 1, "restarts_used": 0, "wall_seconds": 999.0},
        terminal_overrides={"outcome": DiscoveryTerminalOutcome.TIMED_OUT},
    )
    bogus = _rebuild_event_with_payload(bogus, {**bogus.payload, "_wall_clock_seconds": 999.0})
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])  # deadline IS exhausted, but the round already completed cleanly


async def test_forged_completed_terminal_score_mismatch_rejected(tmp_path):
    """Probe 6 (rule 5, receipt binding): an honest completed evaluate
    outcome journaled a real score_ref, but the forged terminal receipt
    claims a DIFFERENT closest_protected_result -- the receipt's claim must
    be bound to exactly what was journaled, never trusted on its own say-
    so."""

    supervisor, manifest, journal = make_supervisor(tmp_path)
    supervisor._executor = always_completes_executor
    supervisor.prepare()
    assignment = make_assignment()
    supervisor.submit(assignment)
    await supervisor._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED
    supervisor._emit(attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
    supervisor._emit(
        attempt_id="att-1", event_type=DiscoveryEventType.ATTEMPT_EVALUATE_OUTCOME,
        payload={"status": "completed", "score_ref": "real-score"},
    )
    events = journal.read_all()

    bogus = _valid_terminal_event(
        manifest, attempt_id="att-1", lineage_id="lin-1", sequence=len(events),
        resource_overrides={"evaluations_used": 1, "restarts_used": 0},
        terminal_overrides={
            "outcome": DiscoveryTerminalOutcome.COMPLETED,
            "closest_protected_result": "proxy-only:forged-better-score",
        },
    )
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    with pytest.raises(JournalError):
        fresh.recover(events + [bogus])


async def test_honest_crash_interrupted_relaunch_completed_evaluation_round_trips_through_double_recovery(tmp_path):
    """Round currency must NOT reject honest second-round terminals: a
    genuine crash (round 1, before checkpoint) -> synthesized INTERRUPTED
    -> relaunch (round 2) -> completed evaluation -> COMPLETED terminal
    must round-trip through double recovery cleanly."""

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 1})
    assignment = make_assignment()
    calls = {"n": 0}

    async def crash_once_executor(*, assignment, resume_from_checkpoint):
        calls["n"] += 1
        if calls["n"] == 1:
            return ExecutionOutcome(status="crashed", detail="round 1 crash")
        return ExecutionOutcome(status="completed")

    supervisor._executor = crash_once_executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(assignment)
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED

    events = journal.read_all()
    fresh1 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "r1.jsonl")),
        attempt_executor=crash_once_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh1.recover(events)  # must not raise -- round-reset must not reject the honest round-2 terminal
    assert fresh1.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.COMPLETED

    fresh2 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "r2.jsonl")),
        attempt_executor=crash_once_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh2.recover(events)
    assert fresh2.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.COMPLETED


async def test_honest_first_round_evaluator_timed_out_second_round_completed_replays(tmp_path):
    """An honest retry journal whose FIRST round's evaluation timed out and
    SECOND round completed must replay COMPLETED -- round currency clearing
    round 1's "timed_out" evidence must not somehow prevent round 2's
    legitimate "completed" evidence from being accepted."""

    supervisor, manifest, journal = make_supervisor(tmp_path, budgets_overrides={"max_restarts_per_attempt": 1})
    assignment = make_assignment()
    calls = {"n": 0}

    async def flaky_evaluator(*, assignment):
        calls["n"] += 1
        if calls["n"] == 1:
            return EvaluationOutcome(status="timed_out", detail="round 1 evaluator timeout")
        return EvaluationOutcome(status="completed", score_ref="round2-score")

    supervisor._executor = always_completes_executor
    supervisor._evaluator = flaky_evaluator
    supervisor.prepare()
    supervisor.submit(assignment)
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED
    assert calls["n"] == 2

    events = journal.read_all()
    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "other.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=flaky_evaluator,
    )
    fresh.recover(events)  # must not raise
    assert fresh.terminal_receipt("att-1").outcome is DiscoveryTerminalOutcome.COMPLETED


# ---------------------------------------------------------------------------
# F1r round-trip property tests: enumerate every _finalize_terminal producer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("max_restarts", [0, 1])
@pytest.mark.parametrize("executor_status", ["completed", "crashed", "worker_error", "cancelled", "timed_out"])
async def test_live_run_round_trips_through_double_recovery_property(tmp_path, executor_status, max_restarts):
    """For every executor status the live supervisor can honestly produce,
    crossed with max_restarts in {0, 1}: the resulting journal must recover
    cleanly, and recovering the SAME journal a SECOND time (fresh) must ALSO
    succeed, with exactly one terminal receipt throughout."""

    supervisor, manifest, journal, clock = make_supervisor_with_clock(
        tmp_path,
        budgets_overrides={
            "max_restarts_per_attempt": max_restarts,
            "max_wall_seconds": 1000,
            "attempt_timeout_seconds": 1000,
        },
    )

    async def executor(*, assignment, resume_from_checkpoint):
        if executor_status == "timed_out":
            clock.advance(1000)  # honest: real time actually exhausts the budget
            return ExecutionOutcome(status="timed_out", detail="ran out of time")
        return ExecutionOutcome(status=executor_status)

    supervisor._executor = executor
    supervisor._evaluator = always_completes_evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt is not None, "exactly one terminal must have been produced"

    events = journal.read_all()
    terminal_events = [e for e in events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL]
    assert len(terminal_events) == 1

    fresh1 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "r1.jsonl")),
        attempt_executor=executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh1.recover(events)  # must not raise
    assert fresh1.terminal_receipt("att-1").outcome == receipt.outcome

    fresh2 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "r2.jsonl")),
        attempt_executor=executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh2.recover(events)  # recovered again, fresh
    assert fresh2.terminal_receipt("att-1").outcome == receipt.outcome


@pytest.mark.parametrize("max_restarts", [0, 1])
@pytest.mark.parametrize("evaluator_status", ["completed", "evaluator_error", "timed_out"])
async def test_live_run_with_varying_evaluator_status_round_trips_through_double_recovery_property(
    tmp_path, evaluator_status, max_restarts
):
    supervisor, manifest, journal, clock = make_supervisor_with_clock(
        tmp_path,
        budgets_overrides={
            "max_restarts_per_attempt": max_restarts,
            "max_wall_seconds": 1000,
            "attempt_timeout_seconds": 1000,
        },
    )

    async def evaluator(*, assignment):
        if evaluator_status == "timed_out":
            clock.advance(1000)  # honest: real time actually exhausts the budget
            return EvaluationOutcome(status="timed_out", detail="ran out of time")
        if evaluator_status == "evaluator_error":
            return EvaluationOutcome(status="evaluator_error", detail="boom")
        return EvaluationOutcome(status="completed", score_ref="score-1")

    supervisor._executor = always_completes_executor
    supervisor._evaluator = evaluator
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt is not None

    events = journal.read_all()
    terminal_events = [e for e in events if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL]
    assert len(terminal_events) == 1

    fresh1 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "r1.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=evaluator,
    )
    fresh1.recover(events)
    assert fresh1.terminal_receipt("att-1").outcome == receipt.outcome

    fresh2 = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "r2.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=evaluator,
    )
    fresh2.recover(events)
    assert fresh2.terminal_receipt("att-1").outcome == receipt.outcome


@pytest.mark.parametrize("max_restarts", [0, 1])
@pytest.mark.parametrize("crash_point", ["prepared", "running_crashed_launch", "checkpointed_no_evaluate"])
async def test_mid_flight_crash_round_trips_through_double_recovery_property(tmp_path, crash_point, max_restarts):
    """The 'crash' dimension: the process dies at various points with NO
    resolving event ever journaled, forcing recover() to SYNTHESIZE the
    resolution (charge a restart + requeue, or finalize directly if the
    restart budget is already exhausted). The resulting (possibly extended)
    journal must ALSO round-trip through a SECOND, fresh recovery cleanly."""

    journal_path = str(tmp_path / "journal.jsonl")
    manifest = make_manifest(
        budgets=make_budgets(max_restarts_per_attempt=max_restarts, max_wall_seconds=1000, attempt_timeout_seconds=1000)
    )

    async def crashing_executor(*, assignment, resume_from_checkpoint):
        return ExecutionOutcome(status="crashed", detail="boom")

    process_a = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    process_a.prepare()
    assignment = make_assignment()
    process_a.submit(assignment)  # -> PREPARED

    if crash_point == "running_crashed_launch":
        process_a._executor = crashing_executor
        await process_a._launch(assignment, resume_from_checkpoint=False)  # -> RUNNING; _maybe_retry never runs
    elif crash_point == "checkpointed_no_evaluate":
        await process_a._launch(assignment, resume_from_checkpoint=False)  # -> CHECKPOINTED; evaluate never starts
    # "prepared": no launch attempt at all before the crash.

    events_before = DiscoveryJournal(journal_path).read_all()

    process_b = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(journal_path),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    process_b.recover(events_before)  # must not raise -- charges restart/requeues, or finalizes if exhausted

    events_after_recovery = DiscoveryJournal(journal_path).read_all()

    fresh = CampaignSupervisor(
        manifest=manifest, journal=DiscoveryJournal(str(tmp_path / "fresh.jsonl")),
        attempt_executor=always_completes_executor, proxy_evaluator=always_completes_evaluator,
    )
    fresh.recover(events_after_recovery)  # must not raise, whether or not a terminal already exists

    if process_b.terminal_receipt("att-1") is not None:
        assert fresh.terminal_receipt("att-1") is not None
        assert fresh.terminal_receipt("att-1").outcome == process_b.terminal_receipt("att-1").outcome
        terminal_events = [
            e for e in events_after_recovery if e.event_type == DiscoveryEventType.ATTEMPT_TERMINAL
        ]
        assert len(terminal_events) == 1
