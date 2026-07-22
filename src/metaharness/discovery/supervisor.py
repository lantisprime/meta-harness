"""CampaignSupervisor: FIFO submit/run/stop/resume over a durable journal.

The journal (one append-only, symlink-refusing JSONL file) is the source of
truth — every external effect is bracketed by an intent event before it and
an outcome event after it. Live operation trusts its own bookkeeping (it is
the one producing events); `recover()` treats a journal as untrusted input
and replays it through an explicit per-attempt state machine, failing closed
on any corrupt, gapped, duplicate-sequence, mixed-manifest, or illegal
transition before touching the executor, evaluator, or any attempt state.

META-8 owns scheduling *policy* — this module only implements bounded FIFO
submission, launch, restart-on-failure within a frozen budget, and exactly
one terminal receipt per attempt.
"""
from __future__ import annotations

import asyncio
import errno
import itertools
import json
import math
import os
import re
import stat
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from pydantic import ValidationError

from metaharness.discovery.models import (
    AttemptState,
    CampaignState,
    DiscoveryAssignment,
    DiscoveryCampaignManifest,
    DiscoveryEvent,
    DiscoveryEventType,
    DiscoveryResourceReceipt,
    DiscoveryTerminalOutcome,
    DiscoveryTerminalReceipt,
)


class SupervisorError(ValueError):
    """A supervisor operation was rejected — fail closed, never guessed around."""


class JournalError(SupervisorError):
    """The durable journal is corrupt, gapped, mixed-manifest, or transition-illegal."""


class SupervisorPoisonedError(SupervisorError):
    """A prior journal append failed with unknown durability, so this
    instance's local state can no longer be trusted to reflect what is
    actually durable. Every public command and introspection refuses from
    here on — construct a fresh instance and recover() from the actual
    journal instead."""


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str  # "completed" | "crashed" | "worker_error" | "cancelled" | "timed_out"
    detail: str = ""


@dataclass(frozen=True)
class EvaluationOutcome:
    status: str  # "completed" | "evaluator_error"
    score_ref: str = ""
    detail: str = ""


class AttemptExecutor(Protocol):
    async def __call__(
        self, *, assignment: DiscoveryAssignment, resume_from_checkpoint: bool
    ) -> ExecutionOutcome: ...


class ProxyEvaluator(Protocol):
    async def __call__(self, *, assignment: DiscoveryAssignment) -> EvaluationOutcome: ...


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """`json.loads`' default object hook is last-wins on a duplicate key —
    silently accepting a rehashed/forged record whose visible fields don't
    match what its own hash was computed over. Used as `object_pairs_hook`
    (applied at every nesting level, so a duplicate anywhere in a journal
    line's structure — including inside `payload.resource_receipt`/
    `terminal_receipt` — fails closed here, before any event is even
    schema-validated)."""

    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise JournalError(f"duplicate key {key!r} in journal JSON object (fail closed)")
        seen[key] = value
    return seen


class DiscoveryJournal:
    """Append-only JSONL journal. Refuses to follow a symlinked path."""

    def __init__(self, path: str) -> None:
        self._path = path

    def append(self, event: DiscoveryEvent) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
        try:
            fd = os.open(self._path, flags, 0o600)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise JournalError(f"journal path {self._path!r} is a symlink; refusing to follow") from exc
            raise JournalError(f"journal path {self._path!r} could not be opened for append: {exc}") from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise JournalError(f"journal path {self._path!r} is not a regular file")
        with os.fdopen(fd, "a") as handle:
            handle.write(event.model_dump_json())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read_all(self) -> list[DiscoveryEvent]:
        # A single O_NOFOLLOW open is the atomic check-and-open: no separate
        # islink() probe, so nothing can swap a symlink in between the check
        # and the read (TOCTOU). O_NOFOLLOW alone only blocks symlinks, not
        # other non-regular file types the path might name (a directory, a
        # device) — mirror the existing repository journal's fstat guard to
        # catch those too, closing the fd rather than leaking it.
        try:
            fd = os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return []
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise JournalError(f"journal path {self._path!r} is a symlink; refusing to follow") from exc
            raise JournalError(f"journal path {self._path!r} could not be opened: {exc}") from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise JournalError(f"journal path {self._path!r} is not a regular file")

        events: list[DiscoveryEvent] = []
        with os.fdopen(fd, "r") as handle:
            for lineno, raw_line in enumerate(handle):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
                except json.JSONDecodeError as exc:
                    raise JournalError(f"journal line {lineno} is corrupt/truncated: {exc}") from exc
                try:
                    events.append(DiscoveryEvent.model_validate(payload))
                except ValidationError as exc:
                    raise JournalError(f"journal line {lineno} failed schema validation: {exc}") from exc
        return events


_TERMINAL_ATTEMPT_STATE_MAP = {
    DiscoveryTerminalOutcome.COMPLETED: AttemptState.COMPLETED,
    DiscoveryTerminalOutcome.FAILED: AttemptState.FAILED,
    DiscoveryTerminalOutcome.TIMED_OUT: AttemptState.TIMED_OUT,
    DiscoveryTerminalOutcome.CANCELLED: AttemptState.CANCELLED,
    DiscoveryTerminalOutcome.CRASHED: AttemptState.CRASHED,
    DiscoveryTerminalOutcome.WORKER_ERROR: AttemptState.FAILED,
    DiscoveryTerminalOutcome.EVALUATOR_ERROR: AttemptState.FAILED,
    DiscoveryTerminalOutcome.LATE_RESULT_DISCARDED: AttemptState.FAILED,
    DiscoveryTerminalOutcome.OMITTED: AttemptState.FAILED,
}
_EXECUTION_STATUS_TO_TERMINAL_OUTCOME = {
    "crashed": DiscoveryTerminalOutcome.CRASHED,
    "worker_error": DiscoveryTerminalOutcome.WORKER_ERROR,
    "cancelled": DiscoveryTerminalOutcome.CANCELLED,
    "timed_out": DiscoveryTerminalOutcome.TIMED_OUT,
    "evaluator_error": DiscoveryTerminalOutcome.EVALUATOR_ERROR,
}
# The closed set of statuses this supervisor trusts from the injected
# executor/evaluator ports (META-7 pre-commit fix brief #8, P2): an
# unrecognized status — or a foreign object that isn't even the expected
# outcome type — is never trusted as-is; it is normalized to a worker/
# evaluator error instead of being taken at face value.
_VALID_EXECUTION_STATUSES = frozenset({"completed", "crashed", "worker_error", "cancelled", "timed_out"})
_VALID_EVALUATION_STATUSES = frozenset({"completed", "evaluator_error", "timed_out"})
# ---------------------------------------------------------------------------
# ATTEMPT_TERMINAL replay evidence table (META-7 pre-commit fix brief #11)
# ---------------------------------------------------------------------------
# Every `_finalize_terminal` call site (live and recovery-synthesis) is
# enumerated below with the durable journal evidence GUARANTEED present at
# that exact emission. `_replay_event`'s ATTEMPT_TERMINAL precursor check
# (search "REPLAY EVIDENCE TABLE, ROW" below) is built FROM this table, one
# branch per row, so the code and the enumeration cannot drift. Rebuilt from
# scratch (not patched) after the rules oscillated between too-strict
# (batch-9 CANCELLED/FAILED; parity-10 early-TIMED_OUT) and too-loose
# (batch-10 FAILED-from-active dropped the pending-intent requirement).
#
# Call sites (12 in the current code; some map to more than one row below
# because `_maybe_retry` fans out across every non-completed status, and
# CANCELLED/TIMED_OUT/FAILED each have two structurally distinct evidence
# classes):
#   recover(): not-campaign-running synthesis; restart-exhaustion synthesis
#     (PREPARED / ACTIVE-with-pending / INTERRUPTED-with-pending); unresolved
#     submit-intent synthesis.
#   _run_attempt(): semaphore-wait-then-stop CANCELLED; post-evaluate
#     COMPLETED.
#   _launch(): pre-launch TIMED_OUT (campaign wall); pre-launch TIMED_OUT
#     (attempt timeout).
#   _evaluate(): pre-evaluate FAILED (evaluation budget); pre-evaluate
#     TIMED_OUT (campaign wall); pre-evaluate TIMED_OUT (attempt timeout).
#   _maybe_retry(): restart-exhaustion resolution of the executor/evaluator's
#     own reported non-completed status (CRASHED / WORKER_ERROR / CANCELLED /
#     TIMED_OUT / EVALUATOR_ERROR / the reserved, never-actually-produced
#     LATE_RESULT_DISCARDED).
#   request_stop(): queue-drain CANCELLED.
#
# | # | Outcome              | Precursor class                          | Durable evidence guaranteed at emission                                             |
# |---|----------------------|-------------------------------------------|--------------------------------------------------------------------------------------|
# | 1 | OMITTED              | current is None                           | assignment exists (SUBMIT_INTENT durable); no SUBMIT_OUTCOME ever landed              |
# | 2 | CANCELLED            | any in-flight state, not RUNNING campaign | campaign_state != RUNNING (durable CAMPAIGN_STOP_REQUESTED/STOPPED)                   |
# | 3 | CANCELLED            | ACTIVE (RUNNING)                          | last_launch_status=="cancelled" AND restarts_used>=max_restarts                       |
# | 4 | TIMED_OUT            | PREPARED / INTERRUPTED / ACTIVE           | the event's own wall sample proves campaign OR attempt deadline exhausted             |
# | 5 | TIMED_OUT            | ACTIVE (RUNNING or CHECKPOINTED)          | last_launch/evaluate_status=="timed_out" (an HONEST early report) AND restarts_used>=max_restarts |
# | 6 | COMPLETED            | ACTIVE (CHECKPOINTED)                     | last_evaluate_status=="completed" (with non-blank score evidence, brief #9/#10 F7)    |
# | 7 | FAILED               | PREPARED                                  | restarts_used>=max_restarts (no pending intent needed: never even launched)           |
# | 8 | FAILED               | INTERRUPTED, or ACTIVE with pending       | pending_intent present (crash DURING an in-flight op) AND restarts_used>=max_restarts |
# | 9 | FAILED               | ACTIVE (CHECKPOINTED)                     | evaluations_used>=max_evaluations (campaign evaluation budget exhausted)              |
# | 10| CRASHED              | ACTIVE (RUNNING)                          | last_launch_status=="crashed" AND restarts_used>=max_restarts                         |
# | 11| WORKER_ERROR         | ACTIVE (RUNNING)                          | last_launch_status=="worker_error" AND restarts_used>=max_restarts                    |
# | 12| EVALUATOR_ERROR      | ACTIVE (CHECKPOINTED)                     | last_evaluate_status=="evaluator_error" AND restarts_used>=max_restarts               |
# | 13| LATE_RESULT_DISCARDED| ACTIVE                                     | last_launch/evaluate_status=="late_result_discarded" AND restarts_used>=max_restarts (reserved: no live producer emits this status today, so this row is unreachable and fails closed) |
#
# Row 8's ACTIVE-with-pending case and recover()'s OWN synthesis are kept
# consistent BY CONSTRUCTION: an ACTIVE attempt with NO pending intent is
# reached via fully resolved events (nothing in-flight) and recover() now
# requeues it WITHOUT charging a restart (see the recovery loop above) —
# charging/failing it would be indistinguishable from a forged FAILED
# terminal appended to that same honest, fully-resolved journal (NEW-2).
#
# Rows 3/5/10/11/12/13 all require restarts_used>=max_restarts in addition to
# the status match: `_maybe_retry` never finalizes unless the restart budget
# is ALREADY exhausted, so replay must not accept a status-matched terminal
# claiming capacity remains.
_TERMINAL_OUTCOME_TO_EXPECTED_STATUS = {
    DiscoveryTerminalOutcome.CRASHED: "crashed",
    DiscoveryTerminalOutcome.WORKER_ERROR: "worker_error",
    DiscoveryTerminalOutcome.EVALUATOR_ERROR: "evaluator_error",
    DiscoveryTerminalOutcome.LATE_RESULT_DISCARDED: "late_result_discarded",
}
_MID_FLIGHT_STATES = (AttemptState.PREPARED, AttemptState.RUNNING, AttemptState.CHECKPOINTED)
_LAUNCHABLE_STATES = (AttemptState.PREPARED, AttemptState.INTERRUPTED)
_ACTIVE_STATES = (AttemptState.RUNNING, AttemptState.CHECKPOINTED)
# Post-replay requeue candidates: mid-flight (never reached an INTERRUPTED
# event) PLUS already-INTERRUPTED (durably charged already, just needs a
# repeated fresh recovery to re-queue it — see recover()'s requeue loop).
_UNFINISHED_STATES = _MID_FLIGHT_STATES + (AttemptState.INTERRUPTED,)

# Replay-only intent/outcome pairing (fail closed on an outcome with no
# matching, still-unresolved intent). Live operation never consults this —
# it is the one producing the pairs, so it trusts itself.
_INTENT_FOR_OUTCOME = {
    DiscoveryEventType.ATTEMPT_SUBMIT_OUTCOME: DiscoveryEventType.ATTEMPT_SUBMIT_INTENT,
    DiscoveryEventType.ATTEMPT_LAUNCH_OUTCOME: DiscoveryEventType.ATTEMPT_LAUNCH_INTENT,
    DiscoveryEventType.ATTEMPT_CHECKPOINT_OUTCOME: DiscoveryEventType.ATTEMPT_CHECKPOINT_INTENT,
    DiscoveryEventType.ATTEMPT_EVALUATE_OUTCOME: DiscoveryEventType.ATTEMPT_EVALUATE_INTENT,
    DiscoveryEventType.ATTEMPT_KNOWLEDGE_APPEND_OUTCOME: DiscoveryEventType.ATTEMPT_KNOWLEDGE_APPEND_INTENT,
}
_INTENT_EVENT_TYPES = frozenset(_INTENT_FOR_OUTCOME.values())
_DEFAULT_EVENT_ID_PATTERN = re.compile(r"^disc-([0-9a-f]{8})$")


class CampaignSupervisor:
    def __init__(
        self,
        *,
        manifest: DiscoveryCampaignManifest,
        journal: DiscoveryJournal,
        attempt_executor: AttemptExecutor,
        proxy_evaluator: ProxyEvaluator,
        clock: Callable[[], int] | None = None,
        operational_clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
        id_source: Callable[[], str] | None = None,
    ) -> None:
        self._manifest = manifest
        self._journal = journal
        self._executor = attempt_executor
        self._evaluator = proxy_evaluator
        # Three clocks with distinct, non-overlapping jobs:
        # - `_clock` stamps `observed_at` on every event (coarse int
        #   wall-clock, real time.time() by default).
        # - `_wall_clock` is a PRECISE (float), NON-RESETTING wall-clock used
        #   to derive IMMUTABLE absolute deadlines from two anchor points —
        #   CAMPAIGN_PREPARED and each attempt's FIRST ATTEMPT_LAUNCH_INTENT
        #   — durably stamped on those events and reconstructed identically
        #   on every recovery. Because wall-clock time keeps advancing
        #   through a crash, `deadline - wall_clock_now()` is correct
        #   whether or not any event was emitted during the crash gap — the
        #   bug an elapsed-duration SNAPSHOT design cannot close (a snapshot
        #   is only as fresh as the last durable event, so time between the
        #   last event and a hard crash is invisible and gets undercharged,
        #   letting repeated crashes extend the budget without bound).
        # - `_operational_clock` (monotonic) additionally bounds how much of
        #   the attempt envelope THIS process's own retries may consume,
        #   for jump-immune in-process precision; the wall-clock deadline
        #   remains the crash-surviving source of truth (`min` of both).
        self._clock = clock or (lambda: int(time.time()))
        self._operational_clock = operational_clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._counter = itertools.count(0)
        self._id_source = id_source or (lambda: f"disc-{next(self._counter):08x}")
        # Journal-event sequence and receipt sequence are independent counters.
        # Sharing one counter made _finalize_terminal "spend" two numbers on
        # the embedded resource/terminal receipts without a matching journal
        # event ever using them, so the *event* sequence had gaps that
        # recover() (correctly) rejected as corrupt — even for a campaign the
        # supervisor itself had just produced.
        self._sequence = itertools.count(0)
        self._receipt_sequence = itertools.count(0)

        self._prepared = False
        self._poisoned = False
        self._poison_reason: str | None = None
        self._campaign_state = CampaignState.PREPARED
        self._attempt_states: dict[str, AttemptState] = {}
        # Per-process operational anchors (monotonic) — never durable, always
        # (re)established fresh once THIS instance starts caring (live
        # prepare(), or the first post-replay measurement after recover()).
        # These are a SECOND, independent cap: even if the wall clock is
        # somehow fooled without tripping the rollback check below, real
        # monotonic time elapsed in THIS process still bounds how much
        # budget can be reported remaining — "tighter of wall/monotonic".
        self._attempt_started_operational: dict[str, float] = {}
        self._campaign_started_operational: float | None = None
        # Immutable absolute deadlines in the wall-clock domain, reconstructed
        # identically whether this instance came from prepare() or recover().
        # `None`/absent means "not yet anchored" (campaign not yet prepared;
        # attempt not yet launched for the first time, ever).
        self._campaign_deadline_wall: float | None = None
        self._attempt_deadline_wall: dict[str, float] = {}
        # Fail-closed wall-clock sanity: EVERY sample of the wall-clock
        # domain this instance accepts — durable (observed_at and the
        # precise _wall_clock_seconds payload, checked during replay) OR
        # live (every `self._wall_clock()` read this process makes,
        # checked by `_read_validated_wall_clock`) — must never move
        # backward relative to the last accepted sample. A live rollback is
        # exactly as untrustworthy as a corrupt durable event: it poisons
        # this instance immediately, before the read's caller can use it to
        # anchor a deadline, compute a budget, or emit a durable event.
        self._last_seen_observed_at: int | None = None
        self._last_seen_wall_clock_seconds: float | None = None
        # Same fail-closed contract extended to the OTHER two injected clock
        # ports (META-7 pre-commit fix brief #8, P2): the operational
        # (monotonic) clock's own last-accepted sample, shared between every
        # live read this process makes.
        self._last_seen_operational_clock: float | None = None
        self._pending_intent: dict[str, DiscoveryEventType] = {}
        self._terminal_attempt_ids: set[str] = set()
        self._terminal_receipts: dict[str, DiscoveryTerminalReceipt] = {}
        self._restarts_used: dict[str, int] = {}
        self._evaluations_used = 0
        self._attempts_submitted = 0
        self._checkpointed: set[str] = set()
        # Replay-tracked evidence of the LAST journaled launch/evaluate
        # outcome status per attempt (META-7 pre-commit fix brief #9, F1) —
        # used to gate the NEXT transition (checkpoint/evaluate intents) and
        # to require terminal outcomes to be consistent with what was
        # actually journaled, uniformly across every precursor state
        # (including ACTIVE), not merely a blanket "any outcome from
        # RUNNING/CHECKPOINTED is fine".
        self._last_launch_status: dict[str, str] = {}
        self._last_evaluate_status: dict[str, str] = {}
        # The CURRENT round's journaled score_ref evidence (only ever set
        # alongside a "completed" `_last_evaluate_status`; cleared in lockstep
        # with it at each new round's ATTEMPT_LAUNCH_INTENT) -- binds a
        # COMPLETED terminal's claimed `closest_protected_result` to what was
        # actually journaled instead of trusting the receipt's own say-so
        # (META-7 pre-commit fix brief #12, rule 5).
        self._last_evaluate_score_ref: dict[str, str] = {}
        self._assignments: dict[str, DiscoveryAssignment] = {}
        # Distinct from `_assignments` (META-7 pre-commit fix brief #10,
        # F3r): `_assignments` is populated at ATTEMPT_SUBMIT_INTENT time,
        # for VALIDATION purposes only (e.g. terminal-receipt lineage
        # cross-checks) — an intent whose SUBMIT_OUTCOME never durably
        # landed (an OMITTED terminal) still leaves an entry there.
        # `_issued_assignments` is populated ONLY once the accepted
        # ATTEMPT_SUBMIT_OUTCOME is itself durable (live or replayed) — this
        # is the ONLY set `issued_assignment()`/`make_issuance_verifier()`
        # may trust, so an assignment whose acceptance was never confirmed
        # durable can never authorize a hub read/write or a cross-lineage
        # receipt.
        self._issued_assignments: dict[str, DiscoveryAssignment] = {}
        self._queue: deque[DiscoveryAssignment] = deque()
        self._in_flight: set[str] = set()
        self._seen_embedded_receipt_ids: set[str] = set()
        self._seen_embedded_receipt_sequences: set[int] = set()
        self._semaphore = asyncio.Semaphore(manifest.budgets.max_concurrency)

    # -- poison ---------------------------------------------------------------

    def _check_not_poisoned(self) -> None:
        if self._poisoned:
            raise SupervisorPoisonedError(
                "supervisor is poisoned — a prior journal append failed with "
                f"unknown durability ({self._poison_reason}); construct a fresh "
                "instance and recover() from the actual journal instead of "
                "trusting this instance's local state"
            )

    def _poison(self, reason: str) -> None:
        self._poisoned = True
        self._poison_reason = reason

    # -- wall-clock validation --------------------------------------------

    @staticmethod
    def _validate_wall_sample(value: Any, *, context: str) -> None:
        """Shared finiteness/type contract for every wall-clock sample this
        instance ever accepts, live or durable: must be a real (non-bool)
        number, finite (never NaN/±Infinity — `value < 0` alone does NOT
        reject NaN, since every comparison with NaN is False), and
        non-negative. Missing evidence is checked separately by callers
        that require it (a `None` sample is a distinct, more specific
        failure than a present-but-invalid one)."""

        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise JournalError(f"{context} has a non-numeric wall-clock sample: {value!r}")
        if not math.isfinite(value):
            raise JournalError(f"{context} has a non-finite wall-clock sample: {value!r}")
        if value < 0:
            raise JournalError(f"{context} has a negative wall-clock sample: {value!r}")

    def _read_validated_wall_clock(self) -> float:
        """The ONLY sanctioned way to read `self._wall_clock()` for any use
        that expands local authority: anchoring a deadline, computing
        remaining budget, or stamping a durable event. Validates finiteness
        and, critically, checks the reading against the last accepted
        wall-clock sample (durable, from replay, or live, from an earlier
        call to this same method) — a live rollback is exactly as
        untrustworthy as a corrupt durable event and poisons this instance
        immediately, BEFORE the caller can act on the bad value, append a
        journal event, or continue past the check."""

        raw = self._wall_clock()
        try:
            self._validate_wall_sample(raw, context="live wall-clock read")
        except JournalError as exc:
            self._poison(str(exc))
            raise
        if self._last_seen_wall_clock_seconds is not None and raw < self._last_seen_wall_clock_seconds:
            reason = (
                f"live wall-clock read {raw} is before the last accepted "
                f"{self._last_seen_wall_clock_seconds} (wall-clock rollback)"
            )
            self._poison(reason)
            raise JournalError(reason)
        self._last_seen_wall_clock_seconds = float(raw)
        return float(raw)

    def _read_validated_clock(self) -> int:
        """The ONLY sanctioned way to read the injected event clock
        (`self._clock`) — the port that stamps every event's `observed_at`
        (META-7 pre-commit fix brief #8, P2). Validates type/non-negativity
        and, sharing the same `_last_seen_observed_at` tracker replay uses,
        rejects a rollback — poisoning this instance before a bad sample can
        be stamped on any durable event."""

        raw = self._clock()
        if not isinstance(raw, int) or isinstance(raw, bool):
            reason = f"injected event clock produced a non-integer sample: {raw!r}"
            self._poison(reason)
            raise JournalError(reason)
        if raw < 0:
            reason = f"injected event clock produced a negative sample: {raw!r}"
            self._poison(reason)
            raise JournalError(reason)
        if self._last_seen_observed_at is not None and raw < self._last_seen_observed_at:
            reason = (
                f"injected event clock read {raw} is before the last accepted "
                f"{self._last_seen_observed_at} (clock rollback)"
            )
            self._poison(reason)
            raise JournalError(reason)
        self._last_seen_observed_at = raw
        return raw

    def _read_validated_operational_clock(self) -> float:
        """The ONLY sanctioned way to read the injected operational
        (monotonic) clock port (META-7 pre-commit fix brief #8, P2).
        Validates finiteness/non-negativity and monotonicity against the
        last accepted sample this process has taken, poisoning the instance
        before a bad or backward-moving sample can anchor or measure any
        budget."""

        raw = self._operational_clock()
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            reason = f"injected operational clock produced a non-numeric sample: {raw!r}"
            self._poison(reason)
            raise JournalError(reason)
        if not math.isfinite(raw):
            reason = f"injected operational clock produced a non-finite sample: {raw!r}"
            self._poison(reason)
            raise JournalError(reason)
        if raw < 0:
            reason = f"injected operational clock produced a negative sample: {raw!r}"
            self._poison(reason)
            raise JournalError(reason)
        if self._last_seen_operational_clock is not None and raw < self._last_seen_operational_clock:
            reason = (
                f"injected operational clock read {raw} is before the last accepted "
                f"{self._last_seen_operational_clock} (non-monotonic)"
            )
            self._poison(reason)
            raise JournalError(reason)
        self._last_seen_operational_clock = float(raw)
        return float(raw)

    # -- introspection ------------------------------------------------------

    @property
    def campaign_state(self) -> CampaignState:
        self._check_not_poisoned()
        return self._campaign_state

    def attempt_state(self, attempt_id: str) -> AttemptState | None:
        self._check_not_poisoned()
        return self._attempt_states.get(attempt_id)

    def terminal_receipt(self, attempt_id: str) -> DiscoveryTerminalReceipt | None:
        self._check_not_poisoned()
        return self._terminal_receipts.get(attempt_id)

    def issued_assignment(self, attempt_id: str) -> DiscoveryAssignment | None:
        """The supervisor is the ONLY issuer of assignments (META-7
        pre-commit fix brief #9, F3): a self-hashed `DiscoveryAssignment`
        proves integrity (its fields weren't tampered after construction),
        not issuance (anyone with model access can mint a fresh one). This
        is the deterministic, journal/live-state-derived view of what THIS
        supervisor actually accepted via `submit()` — the only thing a
        downstream issuance verifier (see `make_issuance_verifier`) may
        trust."""

        self._check_not_poisoned()
        return self._issued_assignments.get(attempt_id)

    def make_issuance_verifier(self) -> Callable[[DiscoveryAssignment], bool]:
        """A bound closure suitable for `DiscoveryKnowledgeHub(issuance_verifier=...)`
        or `RoleContextPolicy` issuance checks: True only when the candidate
        assignment matches, byte-for-byte (via its own hash), an assignment
        this supervisor actually issued via `submit()`."""

        def verify(candidate: DiscoveryAssignment) -> bool:
            issued = self.issued_assignment(candidate.attempt_id)
            return issued is not None and issued.assignment_hash == candidate.assignment_hash

        return verify

    @property
    def in_flight_count(self) -> int:
        self._check_not_poisoned()
        return len(self._in_flight)

    # -- journal plumbing -----------------------------------------------------

    def _next_sequence(self) -> int:
        return next(self._sequence)

    def _next_receipt_sequence(self) -> int:
        return next(self._receipt_sequence)

    def _emit(
        self,
        *,
        attempt_id: str | None,
        event_type: DiscoveryEventType,
        payload: dict[str, Any],
        wall_clock_seconds: float | None = None,
    ) -> None:
        self._check_not_poisoned()
        # Stamp every event with a precise wall-clock reading — the domain
        # `_observe_wall_clock_domain` validates on replay for monotonicity,
        # and the domain CAMPAIGN_PREPARED/first-ATTEMPT_LAUNCH_INTENT
        # anchor their immutable deadlines from. This is an ABSOLUTE
        # reading (not a computed elapsed duration), so recovery never
        # depends on how much time passed since the LAST durable event —
        # only on the immutable anchor plus "now".
        #
        # `wall_clock_seconds`, when given, is a reading the CALLER already
        # took (and validated) for its OWN purpose — anchoring a deadline or
        # sampling a terminal receipt. Anchors especially must be captured
        # EXACTLY ONCE and reused identically for both the live in-memory
        # deadline and this durable payload: two separate `_wall_clock()`
        # calls a few instructions apart can legitimately return different
        # values, which would silently desynchronize live state from what a
        # future replay reconstructs. When no override is given, this is an
        # ordinary (non-anchor) event and it takes its own validated read.
        full_payload = dict(payload)
        full_payload["_wall_clock_seconds"] = (
            wall_clock_seconds if wall_clock_seconds is not None else self._read_validated_wall_clock()
        )

        event = DiscoveryEvent(
            event_id=self._id_source(),
            campaign_id=self._manifest.campaign_id,
            campaign_manifest_hash=self._manifest.manifest_hash,
            attempt_id=attempt_id,
            event_type=event_type,
            sequence=self._next_sequence(),
            observed_at=self._read_validated_clock(),
            payload=full_payload,
        )
        try:
            self._journal.append(event)
        except Exception as exc:
            # The append raised — durability is now UNKNOWN, not merely
            # "failed": the write may have landed (fsync succeeded but the
            # ack was lost), landed partially, or never happened at all.
            # There is no local check that can tell these apart, so every
            # one of them poisons the instance identically and fails closed.
            reason = f"append of {event_type.value} (sequence={event.sequence}) failed: {exc}"
            self._poison(reason)
            raise JournalError(
                f"journal append failed for {event_type.value} (sequence={event.sequence}); "
                f"durability is unknown and this supervisor instance is now poisoned: {exc}"
            ) from exc

    # -- prepare / recover (mutually exclusive, exactly once) ----------------

    def prepare(self) -> None:
        self._check_not_poisoned()
        if self._prepared:
            raise SupervisorError("campaign already prepared")
        # Validate + accept THIS moment's wall-clock reading exactly ONCE
        # (fails closed on a non-finite sample or a live rollback BEFORE any
        # state below is touched), then reuse that identical value for both
        # the live in-memory deadline and the durable event's own payload —
        # never a second, separately-timed `_wall_clock()` call, which could
        # legitimately return a different value and desynchronize what a
        # future replay reconstructs from what this process is using live.
        wall_now = self._read_validated_wall_clock()
        self._campaign_deadline_wall = wall_now + float(self._manifest.budgets.max_wall_seconds)
        self._campaign_started_operational = self._read_validated_operational_clock()
        self._emit(
            attempt_id=None,
            event_type=DiscoveryEventType.CAMPAIGN_PREPARED,
            payload={},
            wall_clock_seconds=wall_now,
        )
        self._campaign_state = CampaignState.RUNNING
        self._prepared = True

    def recover(self, events: Sequence[DiscoveryEvent]) -> None:
        self._check_not_poisoned()
        if self._prepared:
            raise SupervisorError("cannot recover: this supervisor instance is already prepared")

        # Revalidate every event from its own JSON dump before trusting it.
        # `model_copy(update=...)` bypasses pydantic validation entirely, so a
        # caller could hand us an object whose fields were mutated after
        # construction while its `event_hash` still reflects the ORIGINAL
        # (pre-mutation) content. Round-tripping through JSON re-runs the
        # self-hash wrap-validator, which rejects that stale-hash object.
        revalidated: list[DiscoveryEvent] = []
        for event in events:
            try:
                revalidated.append(DiscoveryEvent.model_validate(event.model_dump(mode="json")))
            except ValidationError as exc:
                raise JournalError(f"journal event failed self-hash revalidation: {exc}") from exc

        ordered = sorted(revalidated, key=lambda e: e.sequence)
        seen: set[int] = set()
        seen_event_ids: set[str] = set()
        for event in ordered:
            if event.sequence in seen:
                raise JournalError(f"duplicate journal sequence {event.sequence}")
            seen.add(event.sequence)
            if event.event_id in seen_event_ids:
                raise JournalError(f"duplicate journal event_id {event.event_id!r}")
            seen_event_ids.add(event.event_id)
        if ordered and sorted(seen) != list(range(len(ordered))):
            raise JournalError("journal sequence has a gap")

        try:
            self._sequence = itertools.count(len(ordered))

            # Advance the default event-ID generator past the durable high-water
            # mark, not merely len(ordered): a sparse or non-contiguous set of
            # default-pattern IDs (e.g. from an earlier recovery of the same
            # growing journal) could otherwise collide with an ID this instance
            # is about to generate.
            max_default_event_id_suffix = -1
            for event in ordered:
                match = _DEFAULT_EVENT_ID_PATTERN.match(event.event_id)
                if match:
                    max_default_event_id_suffix = max(max_default_event_id_suffix, int(match.group(1), 16))
            self._counter = itertools.count(max_default_event_id_suffix + 1)

            max_receipt_sequence_seen = -1
            for event in ordered:
                if event.event_type != DiscoveryEventType.ATTEMPT_TERMINAL:
                    continue
                for key in ("resource_receipt", "terminal_receipt"):
                    embedded = event.payload.get(key)
                    if isinstance(embedded, dict) and isinstance(embedded.get("sequence"), int):
                        max_receipt_sequence_seen = max(max_receipt_sequence_seen, embedded["sequence"])
            self._receipt_sequence = itertools.count(max_receipt_sequence_seen + 1)

            for event in ordered:
                if (
                    event.campaign_id != self._manifest.campaign_id
                    or event.campaign_manifest_hash != self._manifest.manifest_hash
                ):
                    raise JournalError(
                        f"journal event {event.event_id!r} references a foreign campaign/manifest "
                        "(mixed-manifest journal)"
                    )
                self._replay_event(event)

            # Recovery must never expose RUNNING state, queue/synthesize work,
            # or emit anything unless the journal actually proves the campaign
            # was durably prepared: exactly one valid CAMPAIGN_PREPARED event
            # with a finite immutable deadline anchor. `_observe_wall_clock_domain`
            # already fails closed on a CAMPAIGN_PREPARED event with a missing/
            # invalid anchor, and `_replay_event` already rejects a DUPLICATE
            # CAMPAIGN_PREPARED — so `_campaign_deadline_wall` still being `None`
            # here can only mean zero such events were ever found (an empty
            # journal, or one truncated/corrupt before prepare() ever completed).
            # Without this check, `recover([])` would otherwise fall through to
            # the unconditional PREPARED-→RUNNING flip below purely because that
            # is `_campaign_state`'s in-memory DEFAULT — never having actually
            # replayed evidence of a real campaign — and `_remaining_wall_seconds`
            # would then report a FRESH full budget forever (no deadline to
            # compare against), letting work "complete" arbitrarily long after
            # the frozen envelope should have expired.
            if self._campaign_deadline_wall is None:
                raise JournalError(
                    "recovery requires exactly one durable CAMPAIGN_PREPARED event with a "
                    "finite campaign deadline anchor; none was found in this journal "
                    "(fail closed — an empty or pre-prepare journal must never expose "
                    "RUNNING state, queue/synthesize work, or emit anything)"
                )

            # Before synthesizing ANY event or exposing runnable state (queueing
            # a retry, flipping `_prepared`), compare THIS process's current
            # wall-clock reading against the last DURABLE wall sample the replay
            # above just accepted. A recovering process whose wall clock reads
            # BEHIND the journal's own last known-good reading is exactly as
            # untrustworthy as a live rollback mid-campaign — poison and fail
            # closed immediately, before any synthesis loop below can run.
            self._read_validated_wall_clock()

            self._prepared = True
            if self._campaign_state == CampaignState.PREPARED:
                self._campaign_state = CampaignState.RUNNING
            campaign_is_running = self._campaign_state == CampaignState.RUNNING
            # `_campaign_deadline_wall`/`_attempt_deadline_wall` are already
            # fully reconstructed by `_observe_wall_clock_domain` above, from the
            # SAME immutable anchor events a live run would have produced —
            # nothing here resets or renews either envelope. `_attempt_started_
            # operational` intentionally stays empty: it is this PROCESS's own
            # fresh in-process refinement window, established the next time
            # `_launch` actually runs (live retries share it; a new process
            # never inherits a prior one's monotonic readings, nor needs to —
            # the wall-clock deadline is the source of truth across the crash).
            # `_campaign_started_operational`, in contrast, IS established here:
            # it is this recovering process's own fresh monotonic cap, the
            # tighter-of-wall/monotonic safety net for `_remaining_wall_seconds`.
            if self._campaign_deadline_wall is not None:
                self._campaign_started_operational = self._read_validated_operational_clock()

            max_restarts = self._manifest.budgets.max_restarts_per_attempt
            for attempt_id, state in list(self._attempt_states.items()):
                if attempt_id in self._terminal_attempt_ids or state not in _UNFINISHED_STATES:
                    continue

                if not campaign_is_running:
                    # A recovered STOPPING/STOPPED campaign must never queue or
                    # launch another attempt — terminalize the mid-flight crash
                    # directly instead of scheduling a retry that would violate
                    # the stop.
                    self._finalize_terminal(
                        self._assignments[attempt_id],
                        outcome=DiscoveryTerminalOutcome.CANCELLED,
                        closest_protected_result="proxy-only:none",
                        unresolved_gap=(
                            f"campaign was {self._campaign_state.value} when this mid-flight "
                            "crash was recovered; not retried"
                        ),
                    )
                    continue

                if state == AttemptState.INTERRUPTED and self._pending_intent.get(attempt_id) is None:
                    # Already durably interrupted AND fully resolved — no
                    # pending intent means nothing crashed again since that
                    # resolution (a live retry that hasn't relaunched yet, or a
                    # PRIOR recovery's synthesis below). The durable journal
                    # already reflects this attempt's interrupted state and its
                    # restart charge exactly once; this fresh recovery only
                    # needs to re-queue it for execution. No new restart charge,
                    # no new event: repeated recovery of the SAME already-
                    # resolved crash must not duplicate the charge, and must not
                    # silently strand the attempt (the bug this branch fixes —
                    # INTERRUPTED was previously excluded from the requeue set
                    # entirely).
                    self._queue.append(self._assignments[attempt_id])
                    continue

                if state in _ACTIVE_STATES and self._pending_intent.get(attempt_id) is None:
                    # An ACTIVE (RUNNING/CHECKPOINTED) attempt with NO pending
                    # intent was reached via FULLY RESOLVED events (e.g. a
                    # successful checkpoint) with nothing left in-flight —
                    # there is no genuine crash EVIDENCE at this exact
                    # boundary (the only real crash window for an ACTIVE
                    # state is DURING an executor/evaluator await, which
                    # always leaves a pending intent). Charging a restart
                    # here would be indistinguishable from a forged FAILED
                    # terminal appended directly to this same honest,
                    # fully-resolved journal (META-7 pre-commit fix brief
                    # #11, NEW-2). Transition through INTERRUPTED anyway —
                    # structurally required so a subsequent re-launch's
                    # LAUNCH_INTENT replays validly, since ACTIVE states are
                    # not themselves launchable — but WITHOUT incrementing
                    # restarts_used: this is a resume, not a charged retry.
                    self._attempt_states[attempt_id] = AttemptState.INTERRUPTED
                    self._emit(
                        attempt_id=attempt_id,
                        event_type=DiscoveryEventType.ATTEMPT_INTERRUPTED,
                        payload={
                            "reason": "recovered mid-flight (resumable, no crash evidence, not charged)",
                            "restarts_used": self._restarts_used.get(attempt_id, 0),
                        },
                    )
                    self._queue.append(self._assignments[attempt_id])
                    continue

                # Either genuinely not-yet-interrupted (PREPARED, or an ACTIVE
                # state WITH a pending intent proving a crash DURING an
                # in-flight operation), or ALREADY INTERRUPTED but with a NEW
                # pending, unresolved launch/evaluate intent — the requeued
                # attempt was relaunched and crashed AGAIN before its outcome.
                # All represent a crash that has never been durably resolved,
                # and all are charged/resolved through the exact same path:
                # charge one restart and durably resolve it, or terminalize if
                # the budget is already exhausted. Repeated recovery of the
                # SAME already-resolved second (or Nth) crash falls through to
                # the branch above instead, once its own ATTEMPT_INTERRUPTED
                # clears the pending intent — so this charge still happens
                # exactly once per real crash, however many times the journal
                # is recovered.
                self._restarts_used.setdefault(attempt_id, 0)
                if self._restarts_used[attempt_id] >= max_restarts:
                    self._finalize_terminal(
                        self._assignments[attempt_id],
                        outcome=DiscoveryTerminalOutcome.FAILED,
                        closest_protected_result="proxy-only:none",
                        unresolved_gap=(
                            f"restart budget ({max_restarts}) is already exhausted; the "
                            "mid-flight crash found at recovery cannot be retried"
                        ),
                    )
                    continue
                self._restarts_used[attempt_id] += 1
                self._attempt_states[attempt_id] = AttemptState.INTERRUPTED
                self._emit(
                    attempt_id=attempt_id,
                    event_type=DiscoveryEventType.ATTEMPT_INTERRUPTED,
                    payload={"reason": "recovered mid-flight", "restarts_used": self._restarts_used[attempt_id]},
                )
                self._queue.append(self._assignments[attempt_id])

            # A crash between ATTEMPT_SUBMIT_INTENT and ATTEMPT_SUBMIT_OUTCOME
            # leaves an assignment registered with no attempt state at all — not
            # PREPARED, not queued, not terminal. Resolve this deterministically:
            # the submission's own outcome was never confirmed durable, so this
            # is an explicit omission, never a guessed COMPLETED/FAILED result.
            for attempt_id, assignment in self._assignments.items():
                if attempt_id in self._attempt_states or attempt_id in self._terminal_attempt_ids:
                    continue
                self._finalize_terminal(
                    assignment,
                    outcome=DiscoveryTerminalOutcome.OMITTED,
                    omission_reason=(
                        "submit intent found without a resolving submit outcome "
                        "(journal ended between intent and outcome)"
                    ),
                )
        except Exception as exc:
            # ANY failure once replay has started mutating this instance's
            # state must never leave a partially-recovered, seemingly-usable
            # instance behind (an event replayed BEFORE the one that raised
            # may already have written to self._attempt_states/_campaign_state/
            # etc, or even appended a synthesized event to the durable
            # journal) — poison exactly like a journal append of unknown
            # durability does (META-7 pre-commit fix brief #9, F5). Avoid
            # double-poisoning: some failures (e.g. a wall-clock rollback)
            # already poisoned with a more specific reason.
            if not self._poisoned:
                self._poison(f"recover() failed mid-replay: {exc}")
            raise

    def _observe_wall_clock_domain(self, event: DiscoveryEvent) -> None:
        """Fail closed on any evidence that the non-resetting wall-clock
        domain moved backward, and anchor the two IMMUTABLE deadlines —
        campaign (from CAMPAIGN_PREPARED) and each attempt's first launch
        (from its EARLIEST ATTEMPT_LAUNCH_INTENT in sequence order) — from
        durable evidence. A later retry's LAUNCH_INTENT never moves an
        already-anchored attempt deadline (first one wins), which is
        exactly "first-launch deadline remains unchanged across
        restart/retry".

        A `_wall_clock_seconds` sample is REQUIRED on EVERY event, not just
        the two anchor types: `_emit` stamps it unconditionally on
        everything this supervisor ever produces (live or synthesized), so
        an event missing it — or carrying a non-finite/negative/bool one —
        is proof the journal was corrupted or hand-forged, not a tolerated
        gap. Validating it universally (rather than only on anchor types)
        closes the specific hole where a non-anchor event with a stripped
        or invalid sample could otherwise bypass this domain's monotonic
        rollback check entirely.
        """

        if self._last_seen_observed_at is not None and event.observed_at < self._last_seen_observed_at:
            raise JournalError(
                f"event {event.event_id!r} observed_at {event.observed_at} is before the "
                f"previously observed {self._last_seen_observed_at} (wall-clock rollback)"
            )
        self._last_seen_observed_at = event.observed_at

        raw_wall = event.payload.get("_wall_clock_seconds")
        if raw_wall is None:
            raise JournalError(
                f"event {event.event_id!r} ({event.event_type.value}) is missing its required "
                "_wall_clock_seconds sample (fail closed — every supervisor-produced event "
                "carries one, so a missing sample proves the journal is corrupt or forged)"
            )
        self._validate_wall_sample(raw_wall, context=f"event {event.event_id!r} payload")
        if self._last_seen_wall_clock_seconds is not None and raw_wall < self._last_seen_wall_clock_seconds:
            raise JournalError(
                f"event {event.event_id!r} _wall_clock_seconds {raw_wall} is before the "
                f"previously observed {self._last_seen_wall_clock_seconds} (wall-clock rollback)"
            )
        self._last_seen_wall_clock_seconds = float(raw_wall)

        if event.event_type == DiscoveryEventType.CAMPAIGN_PREPARED:
            expected = float(raw_wall) + float(self._manifest.budgets.max_wall_seconds)
            if self._campaign_deadline_wall is not None and self._campaign_deadline_wall != expected:
                raise JournalError(
                    f"event {event.event_id!r} contradicts the already-anchored campaign "
                    f"deadline ({self._campaign_deadline_wall} != {expected})"
                )
            self._campaign_deadline_wall = expected

        if event.event_type == DiscoveryEventType.ATTEMPT_LAUNCH_INTENT and event.attempt_id is not None:
            if event.attempt_id not in self._attempt_deadline_wall:
                self._attempt_deadline_wall[event.attempt_id] = float(raw_wall) + float(
                    self._manifest.budgets.attempt_timeout_seconds
                )
            # else: already anchored by an earlier (first) launch — a retry's
            # reading is observed for rollback-checking above but never
            # moves the deadline.

    def _replay_event(self, event: DiscoveryEvent) -> None:
        self._observe_wall_clock_domain(event)
        et = event.event_type

        if et == DiscoveryEventType.CAMPAIGN_PREPARED:
            if self._campaign_state != CampaignState.PREPARED:
                raise JournalError("illegal transition: duplicate CAMPAIGN_PREPARED")
            self._campaign_state = CampaignState.RUNNING
            return
        if et == DiscoveryEventType.CAMPAIGN_STOP_REQUESTED:
            if self._campaign_state != CampaignState.RUNNING:
                raise JournalError("illegal transition: CAMPAIGN_STOP_REQUESTED outside RUNNING")
            self._campaign_state = CampaignState.STOPPING
            return
        if et == DiscoveryEventType.CAMPAIGN_STOPPED:
            if self._campaign_state != CampaignState.STOPPING:
                raise JournalError("illegal transition: CAMPAIGN_STOPPED outside STOPPING")
            self._campaign_state = CampaignState.STOPPED
            return

        attempt_id = event.attempt_id
        if attempt_id is None:
            raise JournalError(f"attempt-scoped event {et.value} is missing attempt_id")
        if attempt_id in self._terminal_attempt_ids:
            raise JournalError(f"illegal transition: {et.value} follows a terminal for {attempt_id!r}")
        current = self._attempt_states.get(attempt_id)

        # Intent/outcome pairing: an outcome must resolve the most recently
        # opened, still-unresolved intent of the same phase; an intent must
        # not open while a prior one for this attempt is still unresolved.
        # This is a REPLAY-only check — live operation produces the pairs
        # itself and never consults `_pending_intent`.
        if et in _INTENT_EVENT_TYPES:
            pending = self._pending_intent.get(attempt_id)
            if pending is not None:
                raise JournalError(
                    f"illegal transition: {et.value} for {attempt_id!r} while a prior "
                    f"{pending.value} intent is still unresolved"
                )
            self._pending_intent[attempt_id] = et
        elif et in _INTENT_FOR_OUTCOME:
            expected_intent = _INTENT_FOR_OUTCOME[et]
            if self._pending_intent.get(attempt_id) != expected_intent:
                raise JournalError(
                    f"illegal transition: {et.value} for {attempt_id!r} has no matching "
                    f"{expected_intent.value} (outcome without intent)"
                )
            self._pending_intent.pop(attempt_id, None)

        if et == DiscoveryEventType.ATTEMPT_SUBMIT_INTENT:
            if current is not None or attempt_id in self._assignments:
                raise JournalError(f"illegal transition: duplicate submit intent for {attempt_id!r}")
            assignment_payload = event.payload.get("assignment")
            if not assignment_payload:
                raise JournalError(f"submit intent for {attempt_id!r} is missing its assignment payload")
            assignment = DiscoveryAssignment.model_validate(assignment_payload)
            if assignment.attempt_id != attempt_id:
                raise JournalError(
                    f"submit intent event attempt_id={attempt_id!r} does not match its "
                    f"embedded assignment.attempt_id={assignment.attempt_id!r}"
                )
            if assignment.campaign_id != self._manifest.campaign_id:
                raise JournalError(
                    f"submit intent for {attempt_id!r} embeds an assignment for a foreign "
                    f"campaign {assignment.campaign_id!r} (expected {self._manifest.campaign_id!r})"
                )
            self._assignments[attempt_id] = assignment
            return
        if et == DiscoveryEventType.ATTEMPT_SUBMIT_OUTCOME:
            if current is not None or attempt_id not in self._assignments:
                raise JournalError(f"illegal transition: submit outcome without a pending intent for {attempt_id!r}")
            if event.payload.get("accepted") is not True:
                raise JournalError(
                    f"submit outcome for {attempt_id!r} does not carry accepted=True "
                    f"(payload: {event.payload!r})"
                )
            # Issuance (META-7 pre-commit fix brief #10, F3r) is durable ONLY
            # once this accepted outcome itself is durable/replayed — never
            # at the earlier, not-yet-confirmed SUBMIT_INTENT.
            self._issued_assignments[attempt_id] = self._assignments[attempt_id]
            self._attempt_states[attempt_id] = AttemptState.PREPARED
            self._restarts_used.setdefault(attempt_id, 0)
            self._attempts_submitted += 1
            if self._attempts_submitted > self._manifest.budgets.max_attempts:
                raise JournalError(
                    f"submit outcome for {attempt_id!r} pushes attempts_submitted to "
                    f"{self._attempts_submitted}, exceeding the frozen budget "
                    f"{self._manifest.budgets.max_attempts}"
                )
            return
        if et == DiscoveryEventType.ATTEMPT_LAUNCH_INTENT:
            if current not in _LAUNCHABLE_STATES:
                raise JournalError(f"illegal transition: launch intent from state {current} for {attempt_id!r}")
            # ROUND CURRENCY (META-7 pre-commit fix brief #12, rule 1): a NEW
            # round invalidates every PRIOR round's launch/evaluate evidence
            # for this attempt -- clear it here so the ATTEMPT_TERMINAL
            # precursor check below can never accept stale evidence left
            # over from an earlier round (e.g. a "completed"/"timed_out"/
            # "evaluator_error" status from round 1 authorizing a forged
            # terminal after round 2 relaunches with different, unresolved
            # evidence). Live bookkeeping needs no mirror: these dicts are
            # populated and consulted ONLY during replay (`_emit`/live code
            # never reads them).
            self._last_launch_status.pop(attempt_id, None)
            self._last_evaluate_status.pop(attempt_id, None)
            self._last_evaluate_score_ref.pop(attempt_id, None)
            return
        if et == DiscoveryEventType.ATTEMPT_LAUNCH_OUTCOME:
            if current not in _LAUNCHABLE_STATES:
                raise JournalError(f"illegal transition: launch outcome from state {current} for {attempt_id!r}")
            launch_status = event.payload.get("status")
            if not isinstance(launch_status, str) or launch_status not in _VALID_EXECUTION_STATUSES:
                raise JournalError(
                    f"launch outcome for {attempt_id!r} has an invalid/unrecognized status: {launch_status!r}"
                )
            self._last_launch_status[attempt_id] = launch_status
            self._attempt_states[attempt_id] = AttemptState.RUNNING
            return
        if et == DiscoveryEventType.ATTEMPT_CHECKPOINT_INTENT:
            if current not in _ACTIVE_STATES:
                raise JournalError(f"illegal transition: checkpoint intent from state {current} for {attempt_id!r}")
            # Live code only ever checkpoints right after a COMPLETED launch
            # outcome (`_launch`'s `if result.status == "completed":` gate)
            # — mirror that semantic gate on replay, so a crashed/failed
            # launch outcome can never be followed by a checkpoint (and,
            # transitively, a forged COMPLETED terminal via a fake evaluate
            # chain) (META-7 pre-commit fix brief #9, F1a).
            if self._last_launch_status.get(attempt_id) != "completed":
                raise JournalError(
                    f"illegal transition: checkpoint intent for {attempt_id!r} follows a "
                    f"non-completed launch outcome ({self._last_launch_status.get(attempt_id)!r})"
                )
            return
        if et == DiscoveryEventType.ATTEMPT_CHECKPOINT_OUTCOME:
            if current not in _ACTIVE_STATES:
                raise JournalError(f"illegal transition: checkpoint outcome from state {current} for {attempt_id!r}")
            self._attempt_states[attempt_id] = AttemptState.CHECKPOINTED
            self._checkpointed.add(attempt_id)
            return
        if et == DiscoveryEventType.ATTEMPT_EVALUATE_INTENT:
            if current not in _ACTIVE_STATES:
                raise JournalError(f"illegal transition: evaluate intent from state {current} for {attempt_id!r}")
            if self._last_launch_status.get(attempt_id) != "completed":
                raise JournalError(
                    f"illegal transition: evaluate intent for {attempt_id!r} follows a "
                    f"non-completed launch outcome ({self._last_launch_status.get(attempt_id)!r})"
                )
            # Charge the evaluation slot on the durable INTENT, matching the
            # live no-refund reservation (charged before the evaluator is
            # ever called). The OUTCOME below must NOT charge again — an EOF
            # crash right after this intent (no outcome) still keeps the
            # slot spent on replay, exactly as it was spent live.
            self._evaluations_used += 1
            if self._evaluations_used > self._manifest.budgets.max_evaluations:
                raise JournalError(
                    f"evaluate intent for {attempt_id!r} pushes evaluations_used to "
                    f"{self._evaluations_used}, exceeding the frozen budget "
                    f"{self._manifest.budgets.max_evaluations}"
                )
            return
        if et == DiscoveryEventType.ATTEMPT_EVALUATE_OUTCOME:
            if current not in _ACTIVE_STATES:
                raise JournalError(f"illegal transition: evaluate outcome from state {current} for {attempt_id!r}")
            eval_status = event.payload.get("status")
            if not isinstance(eval_status, str) or eval_status not in _VALID_EVALUATION_STATUSES:
                raise JournalError(
                    f"evaluate outcome for {attempt_id!r} has an invalid/unrecognized status: {eval_status!r}"
                )
            if eval_status == "completed":
                # Live/replay parity (META-7 pre-commit fix brief #10,
                # F7r-a): live now refuses to ever produce a "completed"
                # evaluate outcome with blank/non-string score_ref evidence
                # (brief #9, F7) — replay must reject the same journal shape
                # rather than accepting it as if it were honest.
                score_ref = event.payload.get("score_ref")
                if not isinstance(score_ref, str) or not score_ref.strip():
                    raise JournalError(
                        f"evaluate outcome for {attempt_id!r} claims completed with an "
                        f"empty/blank/non-string score_ref {score_ref!r} (no evidence)"
                    )
                self._last_evaluate_score_ref[attempt_id] = score_ref
            self._last_evaluate_status[attempt_id] = eval_status
            return
        if et == DiscoveryEventType.ATTEMPT_KNOWLEDGE_APPEND_INTENT:
            if current not in _ACTIVE_STATES:
                raise JournalError(f"illegal transition: knowledge intent from state {current} for {attempt_id!r}")
            return
        if et == DiscoveryEventType.ATTEMPT_KNOWLEDGE_APPEND_OUTCOME:
            if current not in _ACTIVE_STATES:
                raise JournalError(f"illegal transition: knowledge outcome from state {current} for {attempt_id!r}")
            return
        if et == DiscoveryEventType.ATTEMPT_INTERRUPTED:
            # An attempt already INTERRUPTED (by a prior recovery's
            # synthesis) may legally receive ANOTHER interrupted event ONLY
            # when it has a pending, unresolved launch/evaluate intent —
            # proof it was requeued, relaunched, and crashed a SECOND time.
            # An INTERRUPTED attempt with NO pending intent receiving
            # another interrupted event is a genuine duplicate/unpaired
            # event and must still fail closed, same as before.
            valid_interrupted_to_interrupted_resolution = (
                current == AttemptState.INTERRUPTED and self._pending_intent.get(attempt_id) is not None
            )
            if current not in _MID_FLIGHT_STATES and not valid_interrupted_to_interrupted_resolution:
                raise JournalError(f"illegal transition: interrupted from state {current} for {attempt_id!r}")
            previous_restarts = self._restarts_used.get(attempt_id, 0)
            raw_restarts = event.payload.get("restarts_used", previous_restarts)
            if not isinstance(raw_restarts, int) or isinstance(raw_restarts, bool):
                raise JournalError(
                    f"interrupted event for {attempt_id!r} has a non-integer restarts_used "
                    f"payload: {raw_restarts!r}"
                )
            if raw_restarts < previous_restarts:
                raise JournalError(
                    f"interrupted event for {attempt_id!r} restarts_used {raw_restarts} is "
                    f"less than the previously recorded {previous_restarts} (non-monotonic)"
                )
            max_restarts = self._manifest.budgets.max_restarts_per_attempt
            if raw_restarts > max_restarts:
                raise JournalError(
                    f"interrupted event for {attempt_id!r} restarts_used {raw_restarts} "
                    f"exceeds the frozen restart budget {max_restarts}"
                )
            self._attempt_states[attempt_id] = AttemptState.INTERRUPTED
            self._restarts_used[attempt_id] = raw_restarts
            self._pending_intent.pop(attempt_id, None)
            return
        if et == DiscoveryEventType.ATTEMPT_TERMINAL:
            resource_payload = event.payload.get("resource_receipt")
            if not resource_payload:
                raise JournalError(f"terminal event for {attempt_id!r} is missing its resource_receipt payload")
            try:
                resource_receipt = DiscoveryResourceReceipt.model_validate(resource_payload)
            except ValidationError as exc:
                raise JournalError(
                    f"terminal event for {attempt_id!r} has a malformed resource_receipt payload: {exc}"
                ) from exc

            receipt_payload = event.payload.get("terminal_receipt")
            if not receipt_payload:
                raise JournalError(f"terminal event for {attempt_id!r} is missing its terminal_receipt payload")
            try:
                receipt = DiscoveryTerminalReceipt.model_validate(receipt_payload)
            except ValidationError as exc:
                raise JournalError(
                    f"terminal event for {attempt_id!r} has a malformed terminal_receipt payload: {exc}"
                ) from exc

            if resource_receipt.campaign_id != self._manifest.campaign_id or resource_receipt.attempt_id != attempt_id:
                raise JournalError(
                    f"terminal event for {attempt_id!r} embeds a resource_receipt for a "
                    f"foreign campaign/attempt ({resource_receipt.campaign_id!r}/"
                    f"{resource_receipt.attempt_id!r})"
                )
            if receipt.campaign_id != self._manifest.campaign_id or receipt.attempt_id != attempt_id:
                raise JournalError(
                    f"terminal event for {attempt_id!r} embeds a terminal_receipt for a "
                    f"foreign campaign/attempt ({receipt.campaign_id!r}/{receipt.attempt_id!r})"
                )
            if receipt.resource_receipt_id != resource_receipt.receipt_id:
                raise JournalError(
                    f"terminal event for {attempt_id!r} terminal_receipt.resource_receipt_id "
                    f"{receipt.resource_receipt_id!r} does not match the embedded "
                    f"resource_receipt {resource_receipt.receipt_id!r}"
                )
            assignment = self._assignments.get(attempt_id)
            if assignment is None or receipt.lineage_id != assignment.lineage_id:
                raise JournalError(
                    f"terminal event for {attempt_id!r} terminal_receipt.lineage_id "
                    f"{receipt.lineage_id!r} does not match the submitted assignment's lineage"
                )

            # Duplicate embedded receipt IDs/sequences across different
            # terminal events would mean two attempts sharing one receipt's
            # identity — reject.
            for embedded_id in (resource_receipt.receipt_id, receipt.receipt_id):
                if embedded_id in self._seen_embedded_receipt_ids:
                    raise JournalError(f"duplicate embedded receipt_id {embedded_id!r} across terminal events")
                self._seen_embedded_receipt_ids.add(embedded_id)
            for embedded_sequence in (resource_receipt.sequence, receipt.sequence):
                if embedded_sequence in self._seen_embedded_receipt_sequences:
                    raise JournalError(
                        f"duplicate embedded receipt sequence {embedded_sequence} across terminal events"
                    )
                self._seen_embedded_receipt_sequences.add(embedded_sequence)

            # Per-outcome durable evidence, built directly FROM the replay
            # evidence table above this method (META-7 pre-commit fix brief
            # #11) — one branch per table row, so the code and the
            # enumeration cannot drift. Checked AFTER the field-consistency
            # checks above so a forged foreign-campaign/wrong-attempt/
            # lineage-mismatch/duplicate-receipt terminal is still rejected
            # for THAT specific reason first.
            event_wall = event.payload.get("_wall_clock_seconds")
            max_restarts = self._manifest.budgets.max_restarts_per_attempt
            restarts_used = self._restarts_used.get(attempt_id, 0)
            has_pending_intent = self._pending_intent.get(attempt_id) is not None
            restart_budget_exhausted = restarts_used >= max_restarts
            campaign_deadline = self._campaign_deadline_wall
            attempt_deadline = self._attempt_deadline_wall.get(attempt_id)
            deadline_exhausted = (campaign_deadline is not None and event_wall >= campaign_deadline) or (
                attempt_deadline is not None and event_wall >= attempt_deadline
            )

            precursor_ok = False
            if receipt.outcome == DiscoveryTerminalOutcome.OMITTED:
                # ROW 1: the sole legal precursor to a terminal from state
                # None is an unresolved submit intent — never legitimate
                # from any in-flight state, active or not.
                precursor_ok = current is None and attempt_id in self._assignments
            elif receipt.outcome == DiscoveryTerminalOutcome.CANCELLED:
                # ROW 2: campaign-stop-state evidence, uniform across every
                # in-flight precursor state (kimi repro'd a forged CANCELLED
                # from RUNNING when this was unconditionally allowed for any
                # ACTIVE state). ROW 3: OR, from ACTIVE (RUNNING) only, an
                # honest `_maybe_retry()` restart-exhaustion resolution of an
                # executor-reported "cancelled" status while the campaign
                # was still RUNNING the whole time (brief #10, F1r-a).
                precursor_ok = current is not None and (
                    self._campaign_state != CampaignState.RUNNING
                    or (
                        current in _ACTIVE_STATES
                        and self._last_launch_status.get(attempt_id) == "cancelled"
                        and restart_budget_exhausted
                    )
                )
            elif receipt.outcome == DiscoveryTerminalOutcome.TIMED_OUT:
                # ROW 4: deadline-exhaustion evidence, uniform across every
                # in-flight precursor state. ROW 5: OR, an honest EARLY
                # executor/evaluator-reported "timed_out" status resolved by
                # `_maybe_retry()`'s restart exhaustion — the deadline need
                # NOT actually be exhausted (parity-10 NEW-1). Evaluator-
                # class evidence (rule 2, brief #12) requires precursor
                # CHECKPOINTED specifically — an evaluate outcome cannot
                # exist for an attempt that never checkpointed THIS round;
                # launch-class evidence stays at the broader ACTIVE
                # (RUNNING) precursor where a timed-out launch actually
                # leaves the attempt.
                precursor_ok = current is not None and (
                    deadline_exhausted
                    or (
                        current in _ACTIVE_STATES
                        and self._last_launch_status.get(attempt_id) == "timed_out"
                        and restart_budget_exhausted
                    )
                    or (
                        current == AttemptState.CHECKPOINTED
                        and self._last_evaluate_status.get(attempt_id) == "timed_out"
                        and restart_budget_exhausted
                    )
                )
            elif receipt.outcome == DiscoveryTerminalOutcome.COMPLETED:
                # ROW 6: requires a durably journaled, successful
                # ATTEMPT_EVALUATE_OUTCOME for THIS attempt in the CURRENT
                # round — never merely "current happens to be an active
                # state" (which a crashed launch outcome could otherwise
                # reach and then ride to a forged COMPLETED via a fake
                # evaluate chain; closed at the CHECKPOINT_INTENT/
                # EVALUATE_INTENT gates too) and never a STALE evaluate
                # status surviving from an earlier round (rule 1: cleared at
                # every ATTEMPT_LAUNCH_INTENT). Evaluator-class evidence
                # requires precursor CHECKPOINTED specifically (rule 2).
                precursor_ok = (
                    current == AttemptState.CHECKPOINTED
                    and self._last_evaluate_status.get(attempt_id) == "completed"
                )
            elif receipt.outcome == DiscoveryTerminalOutcome.FAILED:
                if current == AttemptState.PREPARED:
                    # ROW 7: never launched at all — legitimate whenever the
                    # restart budget is already exhausted (max_restarts==0
                    # reduces this to the very first crash); no pending
                    # intent is required since there is no in-flight
                    # operation to have crashed DURING.
                    precursor_ok = restart_budget_exhausted
                elif current == AttemptState.INTERRUPTED or current in _ACTIVE_STATES:
                    # ROW 8: legitimate only as the restart-exhausted
                    # resolution of a crash DURING an in-flight operation —
                    # a pending, unresolved intent is the evidence that
                    # something was actually interrupted mid-flight, not
                    # merely that the journal happens to end after a clean
                    # resolve (recover() itself now never charges/finalizes
                    # an ACTIVE, no-pending-intent attempt — see the
                    # recovery loop above — so this is symmetric with what
                    # recover() actually produces, closing NEW-2).
                    precursor_ok = has_pending_intent and restart_budget_exhausted
                    # ROW 9 (rule 4, brief #12): FAILED-via-evaluation-budget
                    # is produced ONLY by `_evaluate()`'s OWN pre-check,
                    # which fires BEFORE any evaluate intent/outcome is ever
                    # journaled for this attempt in the CURRENT round —
                    # precursor CHECKPOINTED, no pending intent, and no
                    # evaluate status recorded this round (a stale prior-
                    # round evaluate status can never satisfy this: rule 1
                    # already cleared it). Requiring CHECKPOINTED (not just
                    # any ACTIVE state) closes a forged FAILED from RUNNING
                    # citing the global evaluation budget alone.
                    if (
                        current == AttemptState.CHECKPOINTED
                        and not has_pending_intent
                        and self._last_evaluate_status.get(attempt_id) is None
                        and self._evaluations_used >= self._manifest.budgets.max_evaluations
                    ):
                        precursor_ok = True
            elif receipt.outcome in _TERMINAL_OUTCOME_TO_EXPECTED_STATUS:
                # ROWS 10-13: CRASHED/WORKER_ERROR/EVALUATOR_ERROR/
                # LATE_RESULT_DISCARDED — consistent with the journaled
                # launch/evaluate outcome for THIS attempt in the CURRENT
                # round (never a bare assertion untethered from what was
                # actually journaled, and never a stale status from an
                # earlier round: rule 1) AND the restart budget already
                # exhausted (the sole producer, `_maybe_retry()`, never
                # finalizes unless it is). Evaluator-class evidence (rule 2)
                # requires precursor CHECKPOINTED specifically; launch-class
                # evidence stays at the broader ACTIVE precursor.
                expected_status = _TERMINAL_OUTCOME_TO_EXPECTED_STATUS[receipt.outcome]
                precursor_ok = restart_budget_exhausted and (
                    (current in _ACTIVE_STATES and self._last_launch_status.get(attempt_id) == expected_status)
                    or (
                        current == AttemptState.CHECKPOINTED
                        and self._last_evaluate_status.get(attempt_id) == expected_status
                    )
                )

            # CONTRADICTION (rule 3, brief #12): if the CURRENT round already
            # journaled a successful completed evaluate outcome (non-blank
            # score evidence), the ONLY acceptable terminal is COMPLETED — no
            # live producer emits TIMED_OUT/FAILED/CRASHED/etc. AFTER a
            # completed evaluation (verified against the full producer
            # enumeration above: every non-COMPLETED `_finalize_terminal`
            # call site fires strictly BEFORE or INSTEAD OF a completed
            # evaluate outcome, never after one). This overrides even
            # deadline-exhaustion evidence (row 4), which a forger could
            # otherwise piggyback on to claim TIMED_OUT despite the honest
            # journal already proving the attempt finished cleanly.
            if (
                self._last_evaluate_status.get(attempt_id) == "completed"
                and receipt.outcome != DiscoveryTerminalOutcome.COMPLETED
            ):
                precursor_ok = False

            if not precursor_ok:
                raise JournalError(f"illegal transition: terminal from state {current} for {attempt_id!r}")

            # Cross-check the embedded resource receipt's counts against what
            # replay has independently tracked up to this point — the
            # durable claim must match the state derived from the rest of
            # the journal, not merely be internally self-consistent.
            if resource_receipt.evaluations_used != self._evaluations_used:
                raise JournalError(
                    f"terminal event for {attempt_id!r} resource_receipt.evaluations_used "
                    f"{resource_receipt.evaluations_used} does not match durable replay state "
                    f"{self._evaluations_used}"
                )
            if resource_receipt.restarts_used != self._restarts_used.get(attempt_id, 0):
                raise JournalError(
                    f"terminal event for {attempt_id!r} resource_receipt.restarts_used "
                    f"{resource_receipt.restarts_used} does not match durable replay state "
                    f"{self._restarts_used.get(attempt_id, 0)}"
                )
            # Cross-check the claimed elapsed wall time against wall-clock
            # evidence this replay has independently reconstructed — the
            # SAME formula `_finalize_terminal` used live: the terminal
            # event's own validated `_wall_clock_seconds` sample minus this
            # attempt's immutable first-launch anchor. This closes a forged
            # in-budget terminal whose event carries a truthful (late) wall-
            # clock sample but an untruthfully low resource_receipt.wall_seconds
            # — exactly the independent-reproduction case (a rehashed
            # COMPLETED terminal at wall 20, deadline 10, resource wall 0).
            # Deliberately NOT a `<= max_wall_seconds` clamp: truthful wall
            # time may legitimately exceed the frozen budget after process
            # downtime (an honest TIMED_OUT overrun must never be rejected
            # merely for being large), so only DISHONEST timing — a value
            # that doesn't match the independently reconstructed evidence —
            # is rejected here.
            attempt_deadline_for_wall_check = self._attempt_deadline_wall.get(attempt_id)
            if attempt_deadline_for_wall_check is None:
                expected_wall_seconds = 0.0
            else:
                first_launch_wall = attempt_deadline_for_wall_check - float(
                    self._manifest.budgets.attempt_timeout_seconds
                )
                event_wall_for_check = event.payload.get("_wall_clock_seconds")
                expected_wall_seconds = max(0.0, float(event_wall_for_check) - first_launch_wall)
            if abs(resource_receipt.wall_seconds - expected_wall_seconds) > 1e-6:
                raise JournalError(
                    f"terminal event for {attempt_id!r} resource_receipt.wall_seconds "
                    f"{resource_receipt.wall_seconds} does not match the wall-clock evidence "
                    f"independently reconstructed from the durable launch anchor and this "
                    f"event's own timestamp ({expected_wall_seconds}) (tampered resource timing)"
                )
            # RECEIPT BINDING (rule 5, brief #12): a COMPLETED terminal's
            # claimed `closest_protected_result` must equal exactly
            # `proxy-only:<score_ref>` from the CURRENT round's journaled
            # successful evaluate outcome — never a different (better or
            # differently-shaped) score/gap forged into the receipt after an
            # honest completed evaluation.
            if receipt.outcome == DiscoveryTerminalOutcome.COMPLETED:
                expected_result = f"proxy-only:{self._last_evaluate_score_ref.get(attempt_id)}"
                if receipt.closest_protected_result != expected_result:
                    raise JournalError(
                        f"terminal event for {attempt_id!r} terminal_receipt.closest_protected_result "
                        f"{receipt.closest_protected_result!r} does not match the current round's "
                        f"journaled evaluate outcome (expected {expected_result!r}) (forged score/gap)"
                    )

            self._terminal_receipts[attempt_id] = receipt
            self._terminal_attempt_ids.add(attempt_id)
            self._attempt_states[attempt_id] = _TERMINAL_ATTEMPT_STATE_MAP[receipt.outcome]
            self._pending_intent.pop(attempt_id, None)
            return

        raise JournalError(f"unknown event_type {et!r} encountered during replay")

    # -- FIFO submission -------------------------------------------------------

    def submit(self, assignment: DiscoveryAssignment) -> None:
        self._check_not_poisoned()
        if not self._prepared:
            raise SupervisorError("campaign not prepared")
        if self._campaign_state != CampaignState.RUNNING:
            raise SupervisorError(f"cannot submit while campaign is {self._campaign_state.value}")
        # Revalidate from its own JSON dump before trusting it — a caller
        # could hand us an object built via `model_copy(update=...)`, which
        # bypasses pydantic validation and leaves a STALE `assignment_hash`
        # relative to the (post-mutation) field values. Journaling it as-is
        # would durably embed the stale-hash object; a later `recover()`
        # (which always revalidates) would reject that exact journal even
        # though THIS live call accepted it — a live/replay divergence
        # (META-7 pre-commit fix brief #9, F6).
        try:
            assignment = DiscoveryAssignment.model_validate(assignment.model_dump(mode="json"))
        except ValidationError as exc:
            raise SupervisorError(f"assignment failed self-hash revalidation: {exc}") from exc
        if assignment.campaign_id != self._manifest.campaign_id:
            raise SupervisorError("assignment belongs to a different campaign")
        if assignment.attempt_id in self._attempt_states or assignment.attempt_id in self._assignments:
            raise SupervisorError(f"attempt_id {assignment.attempt_id!r} was already submitted")
        if self._attempts_submitted >= self._manifest.budgets.max_attempts:
            raise SupervisorError("campaign attempt budget is exhausted; submission denied before launch")

        self._emit(
            attempt_id=assignment.attempt_id,
            event_type=DiscoveryEventType.ATTEMPT_SUBMIT_INTENT,
            payload={"assignment": assignment.model_dump(mode="json")},
        )
        self._assignments[assignment.attempt_id] = assignment
        self._attempts_submitted += 1
        self._restarts_used[assignment.attempt_id] = 0
        self._attempt_states[assignment.attempt_id] = AttemptState.PREPARED
        self._emit(
            attempt_id=assignment.attempt_id,
            event_type=DiscoveryEventType.ATTEMPT_SUBMIT_OUTCOME,
            payload={"accepted": True},
        )
        # Issuance (META-7 pre-commit fix brief #10, F3r) is durable ONLY
        # once this accepted outcome's own append has actually succeeded —
        # `_emit` above raises/poisons before this line if it didn't.
        self._issued_assignments[assignment.attempt_id] = assignment
        self._queue.append(assignment)

    # -- run -------------------------------------------------------------------

    async def run_available(self) -> None:
        """Launch every currently queued assignment (bounded by max_concurrency)
        and wait for each to reach a terminal or interrupted-for-retry state.

        A no-op when the campaign is not RUNNING (STOPPING/STOPPED): nothing
        may be queued or launched outside RUNNING. `recover()` and
        `request_stop()` are responsible for never leaving a retry-eligible
        assignment in the queue in that case — this is the second, cheap gate
        against a bug in either of those callers ever reaching a launch.
        """

        self._check_not_poisoned()
        if self._campaign_state != CampaignState.RUNNING:
            return
        tasks = [asyncio.create_task(self._run_attempt(self._queue.popleft())) for _ in range(len(self._queue))]
        if tasks:
            await asyncio.gather(*tasks)

    async def _run_attempt(self, assignment: DiscoveryAssignment) -> None:
        attempt_id = assignment.attempt_id
        while True:
            async with self._semaphore:
                # run_available() pops the whole queue into tasks up front;
                # a task can still be WAITING here on the semaphore when
                # request_stop() fires. Recheck campaign state immediately
                # after acquiring capacity — and again on every retry
                # iteration, since a stop can also land between retries —
                # so a stop can never be raced by a semaphore-waiting
                # attempt that only just got its turn.
                if self._campaign_state != CampaignState.RUNNING:
                    if attempt_id not in self._terminal_attempt_ids:
                        self._finalize_terminal(
                            assignment,
                            outcome=DiscoveryTerminalOutcome.CANCELLED,
                            closest_protected_result="proxy-only:none",
                            unresolved_gap=(
                                f"campaign was {self._campaign_state.value} when this attempt "
                                "reached the front of the concurrency queue; never launched"
                            ),
                        )
                    return
                self._in_flight.add(attempt_id)
                try:
                    resume_from_checkpoint = attempt_id in self._checkpointed
                    exec_outcome = await self._launch(assignment, resume_from_checkpoint=resume_from_checkpoint)
                    if exec_outcome is None:
                        return
                    if exec_outcome.status != "completed":
                        if self._maybe_retry(assignment, exec_outcome.status, exec_outcome.detail):
                            continue
                        return

                    eval_outcome = await self._evaluate(assignment)
                    if eval_outcome is None:
                        return
                    if eval_outcome.status != "completed":
                        if self._maybe_retry(assignment, eval_outcome.status, eval_outcome.detail):
                            continue
                        return

                    self._finalize_terminal(
                        assignment,
                        outcome=DiscoveryTerminalOutcome.COMPLETED,
                        closest_protected_result=f"proxy-only:{eval_outcome.score_ref}",
                        unresolved_gap=(
                            "protected evaluation is not run in this MVP; only proxy "
                            f"evidence (score_ref={eval_outcome.score_ref!r}) is available"
                        ),
                    )
                    return
                finally:
                    self._in_flight.discard(attempt_id)

    def _remaining_wall_seconds(self) -> float:
        """Two constraints, the tighter wins — mirroring the attempt-level
        hybrid below. (1) The IMMUTABLE campaign deadline minus a VALIDATED
        wall-clock "now": because wall-clock time advances continuously —
        through a crash, not just between durable events — this is correct
        whether or not any event was emitted during downtime, unlike an
        elapsed-duration snapshot. The validated read fails closed on a
        rollback BEFORE this method can return a bogus, budget-renewing
        number. (2) A monotonic, jump-immune per-instance cap: even if a
        rollback somehow evaded the check above, real operational time
        elapsed in THIS process still bounds what can be reported. The
        deadline itself is set exactly once, from CAMPAIGN_PREPARED (live)
        or its replayed equivalent (recovered), and never moves."""

        if self._campaign_deadline_wall is None:
            return float(self._manifest.budgets.max_wall_seconds)
        remaining_wall_clock = self._campaign_deadline_wall - self._read_validated_wall_clock()

        if self._campaign_started_operational is None:
            remaining_operational = float(self._manifest.budgets.max_wall_seconds)
        else:
            remaining_operational = float(self._manifest.budgets.max_wall_seconds) - (
                self._read_validated_operational_clock() - self._campaign_started_operational
            )
        return min(remaining_wall_clock, remaining_operational)

    def _remaining_attempt_seconds(self, attempt_id: str) -> float:
        """Two constraints, the tighter wins. (1) The IMMUTABLE wall-clock
        deadline anchored to this attempt's very FIRST launch — the
        crash-surviving source of truth, unaffected by how many retries or
        recoveries happened since — compared against a VALIDATED wall-clock
        "now" (fails closed on a rollback before this can return a bogus,
        budget-renewing number). (2) A monotonic, jump-immune bound on how
        much of the envelope THIS process's own retries may consume,
        reusing operational-clock precision for in-process timeout
        arithmetic. Neither constraint is ever renewed by a retry/recovery:
        the wall-clock deadline is set once at first launch (never moved by
        a later ATTEMPT_LAUNCH_INTENT); the operational anchor is set once
        per process via `setdefault`."""

        deadline = self._attempt_deadline_wall.get(attempt_id)
        if deadline is None:
            return float(self._manifest.budgets.attempt_timeout_seconds)
        remaining_wall_clock = deadline - self._read_validated_wall_clock()

        started = self._attempt_started_operational.get(attempt_id)
        if started is None:
            remaining_operational = float(self._manifest.budgets.attempt_timeout_seconds)
        else:
            remaining_operational = float(self._manifest.budgets.attempt_timeout_seconds) - (
                self._read_validated_operational_clock() - started
            )
        return min(remaining_wall_clock, remaining_operational)

    async def _launch(
        self, assignment: DiscoveryAssignment, *, resume_from_checkpoint: bool
    ) -> ExecutionOutcome | None:
        self._check_not_poisoned()
        attempt_id = assignment.attempt_id
        if attempt_id in self._terminal_attempt_ids:
            return None  # late call after another path already terminalized this attempt

        remaining_wall = self._remaining_wall_seconds()
        if remaining_wall <= 0:
            self._finalize_terminal(
                assignment,
                outcome=DiscoveryTerminalOutcome.TIMED_OUT,
                closest_protected_result="proxy-only:none",
                unresolved_gap=(
                    f"campaign wall budget ({self._manifest.budgets.max_wall_seconds}s) "
                    "was exhausted before this attempt could launch"
                ),
            )
            return None

        # First-ever launch of this attempt: capture ITS anchor exactly ONCE
        # (validated + fail-closed on rollback) and reuse that identical
        # value for both the live deadline and the emitted event's payload
        # below — never a second, separately-timed read. A later retry (in
        # this process or a recovered one) finds this dict entry already
        # present and never overwrites it — "first-launch deadline remains
        # unchanged across restart/retry".
        launch_wall_now: float | None = None
        if attempt_id not in self._attempt_deadline_wall:
            launch_wall_now = self._read_validated_wall_clock()
            self._attempt_deadline_wall[attempt_id] = launch_wall_now + float(
                self._manifest.budgets.attempt_timeout_seconds
            )
        self._attempt_started_operational.setdefault(attempt_id, self._read_validated_operational_clock())
        remaining_attempt = self._remaining_attempt_seconds(attempt_id)
        if remaining_attempt <= 0:
            self._finalize_terminal(
                assignment,
                outcome=DiscoveryTerminalOutcome.TIMED_OUT,
                closest_protected_result="proxy-only:none",
                unresolved_gap=(
                    f"attempt timeout envelope ({self._manifest.budgets.attempt_timeout_seconds}s) "
                    "was already exhausted by a prior phase/retry before this attempt could launch"
                ),
            )
            return None

        self._emit(
            attempt_id=attempt_id,
            event_type=DiscoveryEventType.ATTEMPT_LAUNCH_INTENT,
            payload={"resume_from_checkpoint": resume_from_checkpoint},
            wall_clock_seconds=launch_wall_now,
        )
        # Cap this phase's window by whichever is smaller: the remaining
        # single attempt-timeout envelope (shared across execution,
        # evaluation, and retries) or the remaining campaign wall budget.
        execution_timeout = min(remaining_attempt, remaining_wall)
        try:
            result = await asyncio.wait_for(
                self._executor(assignment=assignment, resume_from_checkpoint=resume_from_checkpoint),
                timeout=execution_timeout,
            )
        except asyncio.TimeoutError:
            result = ExecutionOutcome(status="timed_out", detail="attempt_timeout_seconds exceeded")
        except Exception as exc:  # pragma: no cover - defensive
            result = ExecutionOutcome(status="crashed", detail=str(exc))

        # Never trust the executor port's return value as-is: reject an
        # unknown/foreign outcome object instead of treating its shape as
        # authoritative (META-7 pre-commit fix brief #8, P2).
        if not isinstance(result, ExecutionOutcome):
            result = ExecutionOutcome(
                status="worker_error",
                detail=f"executor returned a foreign object instead of ExecutionOutcome: {type(result).__name__}",
            )
        elif result.status not in _VALID_EXECUTION_STATUSES:
            result = ExecutionOutcome(
                status="worker_error",
                detail=f"executor returned an unrecognized status {result.status!r}",
            )

        if attempt_id in self._terminal_attempt_ids:
            return None  # discard: a concurrent path already terminalized this attempt

        self._attempt_states[attempt_id] = AttemptState.RUNNING
        self._emit(
            attempt_id=attempt_id,
            event_type=DiscoveryEventType.ATTEMPT_LAUNCH_OUTCOME,
            payload={"status": result.status, "detail": result.detail},
        )
        if result.status == "completed":
            self._emit(attempt_id=attempt_id, event_type=DiscoveryEventType.ATTEMPT_CHECKPOINT_INTENT, payload={})
            self._attempt_states[attempt_id] = AttemptState.CHECKPOINTED
            self._checkpointed.add(attempt_id)
            self._emit(attempt_id=attempt_id, event_type=DiscoveryEventType.ATTEMPT_CHECKPOINT_OUTCOME, payload={})
        return result

    async def _evaluate(self, assignment: DiscoveryAssignment) -> EvaluationOutcome | None:
        self._check_not_poisoned()
        attempt_id = assignment.attempt_id
        if attempt_id in self._terminal_attempt_ids:
            return None
        if self._evaluations_used >= self._manifest.budgets.max_evaluations:
            self._finalize_terminal(
                assignment,
                outcome=DiscoveryTerminalOutcome.FAILED,
                closest_protected_result="proxy-only:none",
                unresolved_gap="campaign evaluation budget was exhausted before this attempt could be scored",
            )
            return None
        remaining_wall = self._remaining_wall_seconds()
        if remaining_wall <= 0:
            self._finalize_terminal(
                assignment,
                outcome=DiscoveryTerminalOutcome.TIMED_OUT,
                closest_protected_result="proxy-only:none",
                unresolved_gap=(
                    f"campaign wall budget ({self._manifest.budgets.max_wall_seconds}s) "
                    "was exhausted before this attempt could be scored"
                ),
            )
            return None
        remaining_attempt = self._remaining_attempt_seconds(attempt_id)
        if remaining_attempt <= 0:
            self._finalize_terminal(
                assignment,
                outcome=DiscoveryTerminalOutcome.TIMED_OUT,
                closest_protected_result="proxy-only:none",
                unresolved_gap=(
                    f"attempt timeout envelope ({self._manifest.budgets.attempt_timeout_seconds}s) "
                    "was already consumed by execution before this attempt could be scored"
                ),
            )
            return None

        # Reserve the evaluation slot BEFORE the only potentially-suspending
        # call in this method: a synchronous check-then-increment with no
        # `await` in between cannot interleave with another concurrent
        # attempt's `_evaluate` call, so the evaluator port is never invoked
        # over cap. No-refund policy: once reserved, the slot is spent
        # regardless of what happens next — completion, evaluator_error, or
        # timeout all represent real evaluator resource consumption, so none
        # of them give the slot back.
        self._evaluations_used += 1

        self._emit(attempt_id=attempt_id, event_type=DiscoveryEventType.ATTEMPT_EVALUATE_INTENT, payload={})
        # Proxy evaluation lives inside the SAME single attempt-timeout
        # envelope as execution (whatever execution already consumed is
        # subtracted here), also capped by remaining campaign wall budget —
        # a blocked/hanging evaluator must not hold run_available() forever.
        evaluation_timeout = min(remaining_attempt, remaining_wall)
        try:
            result = await asyncio.wait_for(self._evaluator(assignment=assignment), timeout=evaluation_timeout)
        except asyncio.TimeoutError:
            result = EvaluationOutcome(status="timed_out", detail="evaluator timed out within the attempt timeout envelope")
        except Exception as exc:  # pragma: no cover - defensive
            result = EvaluationOutcome(status="evaluator_error", detail=str(exc))

        # Never trust the evaluator port's return value as-is: reject an
        # unknown/foreign outcome object instead of treating its shape as
        # authoritative (META-7 pre-commit fix brief #8, P2).
        if not isinstance(result, EvaluationOutcome):
            result = EvaluationOutcome(
                status="evaluator_error",
                detail=f"evaluator returned a foreign object instead of EvaluationOutcome: {type(result).__name__}",
            )
        elif result.status not in _VALID_EVALUATION_STATUSES:
            result = EvaluationOutcome(
                status="evaluator_error",
                detail=f"evaluator returned an unrecognized status {result.status!r}",
            )
        elif result.status == "completed" and not (isinstance(result.score_ref, str) and result.score_ref.strip()):
            # A "completed" evaluation with an empty/blank score_ref would
            # otherwise flow into a COMPLETED terminal claiming
            # `closest_protected_result="proxy-only:"` — evidence that does
            # not exist (META-7 pre-commit fix brief #9, F7). `EvaluationOutcome`
            # is a plain dataclass, so a non-string score_ref from an
            # untrusted evaluator (e.g. an int) is NOT enforced at
            # construction time — calling `.strip()` on it directly would
            # raise AttributeError and strand the attempt with an
            # unresolved evaluate intent instead of normalizing cleanly
            # (META-7 pre-commit fix brief #10, F7r-b). The isinstance check
            # short-circuits before `.strip()` ever runs.
            result = EvaluationOutcome(
                status="evaluator_error",
                detail="evaluator reported completed with an empty/blank score_ref (no evidence)",
            )

        if attempt_id in self._terminal_attempt_ids:
            return None  # discard: a concurrent path already terminalized this attempt

        self._emit(
            attempt_id=attempt_id,
            event_type=DiscoveryEventType.ATTEMPT_EVALUATE_OUTCOME,
            payload={"status": result.status, "score_ref": result.score_ref},
        )
        return result

    def _maybe_retry(self, assignment: DiscoveryAssignment, failure_status: str, detail: str) -> bool:
        self._check_not_poisoned()
        attempt_id = assignment.attempt_id
        max_restarts = self._manifest.budgets.max_restarts_per_attempt
        if self._restarts_used[attempt_id] >= max_restarts:
            outcome = _EXECUTION_STATUS_TO_TERMINAL_OUTCOME.get(failure_status, DiscoveryTerminalOutcome.FAILED)
            self._finalize_terminal(
                assignment,
                outcome=outcome,
                closest_protected_result="proxy-only:none",
                unresolved_gap=f"restart budget ({max_restarts}) exhausted after {failure_status}: {detail}",
            )
            return False
        self._restarts_used[attempt_id] += 1
        self._attempt_states[attempt_id] = AttemptState.INTERRUPTED
        self._emit(
            attempt_id=attempt_id,
            event_type=DiscoveryEventType.ATTEMPT_INTERRUPTED,
            payload={"reason": failure_status, "restarts_used": self._restarts_used[attempt_id]},
        )
        return True

    def _finalize_terminal(
        self,
        assignment: DiscoveryAssignment,
        *,
        outcome: DiscoveryTerminalOutcome,
        closest_protected_result: str | None = None,
        unresolved_gap: str | None = None,
        omission_reason: str | None = None,
    ) -> None:
        self._check_not_poisoned()
        attempt_id = assignment.attempt_id
        if attempt_id in self._terminal_attempt_ids:
            return  # exactly-one-terminal: a second finalize call is a silent no-op

        self._terminal_attempt_ids.add(attempt_id)
        # Truthful elapsed wall time = a VALIDATED wall-clock "now" (fails
        # closed on a rollback, same as every other budget/anchor read)
        # minus the IMMUTABLE first-launch anchor (deadline minus the
        # frozen budget recovers it exactly) — correct across any number of
        # crashes, since wall-clock time advances continuously regardless
        # of process state. An attempt that never launched at all
        # (evaluation/wall-budget pre-checks, or a stop-requested
        # cancellation while still queued) has no anchor and reports 0.0
        # via the same "now" snapshot reused for both sides of the
        # subtraction (avoids a nonzero epsilon from two separate reads).
        # This SAME reading is also reused, unmodified, for the emitted
        # ATTEMPT_TERMINAL event's own payload below — one sample, two
        # uses, never a second independently-timed read.
        now_wall = self._read_validated_wall_clock()
        deadline = self._attempt_deadline_wall.get(attempt_id)
        if deadline is None:
            wall_seconds = 0.0
        else:
            first_launch_wall = deadline - float(self._manifest.budgets.attempt_timeout_seconds)
            wall_seconds = max(0.0, now_wall - first_launch_wall)
        resource_receipt = DiscoveryResourceReceipt(
            receipt_id=self._id_source(),
            campaign_id=self._manifest.campaign_id,
            attempt_id=attempt_id,
            sequence=self._next_receipt_sequence(),
            wall_seconds=wall_seconds,
            evaluations_used=self._evaluations_used,
            restarts_used=self._restarts_used.get(attempt_id, 0),
        )
        terminal_receipt = DiscoveryTerminalReceipt(
            receipt_id=self._id_source(),
            campaign_id=self._manifest.campaign_id,
            lineage_id=assignment.lineage_id,
            attempt_id=attempt_id,
            sequence=self._next_receipt_sequence(),
            outcome=outcome,
            resource_receipt_id=resource_receipt.receipt_id,
            closest_protected_result=closest_protected_result,
            unresolved_gap=unresolved_gap,
            omission_reason=omission_reason,
        )
        self._terminal_receipts[attempt_id] = terminal_receipt
        self._attempt_states[attempt_id] = _TERMINAL_ATTEMPT_STATE_MAP[outcome]
        self._emit(
            attempt_id=attempt_id,
            event_type=DiscoveryEventType.ATTEMPT_TERMINAL,
            payload={
                "resource_receipt": resource_receipt.model_dump(mode="json"),
                "terminal_receipt": terminal_receipt.model_dump(mode="json"),
            },
            wall_clock_seconds=now_wall,
        )

    # -- stop --------------------------------------------------------------

    def request_stop(self) -> None:
        self._check_not_poisoned()
        if not self._prepared:
            raise SupervisorError("cannot request stop: campaign not prepared")
        if self._campaign_state != CampaignState.RUNNING:
            raise SupervisorError(f"cannot request stop while campaign is {self._campaign_state.value}")
        self._campaign_state = CampaignState.STOPPING
        self._emit(attempt_id=None, event_type=DiscoveryEventType.CAMPAIGN_STOP_REQUESTED, payload={})
        # Graceful stop: no new launches. In-flight attempts (already popped
        # by a prior run_available()) still finish via their own task. Every
        # attempt still sitting in the queue was submitted and therefore owes
        # exactly one terminal receipt (charter invariant 9) — silently
        # dropping it here would leave it permanently PREPARED with none.
        while self._queue:
            queued_assignment = self._queue.popleft()
            self._finalize_terminal(
                queued_assignment,
                outcome=DiscoveryTerminalOutcome.CANCELLED,
                closest_protected_result="proxy-only:none",
                unresolved_gap="campaign stop was requested before this attempt was launched",
            )

    def finish_stop(self) -> None:
        self._check_not_poisoned()
        if self._campaign_state != CampaignState.STOPPING:
            raise SupervisorError("finish_stop called without a prior request_stop")
        self._campaign_state = CampaignState.STOPPED
        self._emit(attempt_id=None, event_type=DiscoveryEventType.CAMPAIGN_STOPPED, payload={})
