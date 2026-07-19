"""TaskExecutor tests: escalation on verified failure, authenticity rejection,
budget ceilings, plateau stop, capability matrix learning, provenance trail."""
from __future__ import annotations

import asyncio
import time

import pytest

from metaharness.core import (
    Budget,
    MASTMode,
    Task,
    TaskExecutor,
    TaskType,
    Tier,
    VerificationResult,
    Verdict,
    WorkerResult,
)
from metaharness.harness import (
    MockLLMWorker,
    ScriptedWorker,
    result_signing_bytes,
    sign_result,
)
from metaharness.harness.runner import Runner, WorkerTimeout
from metaharness.identity import (
    KeyPair,
    ProvenanceLog,
    TokenIssuer,
    WorkerRegistry,
    registration_payload,
)
from metaharness.routing import CapabilityMatrix, Router


def register(registry: WorkerRegistry, worker_id: str, kp: KeyPair):
    challenge = registry.begin_registration(worker_id)
    payload = registration_payload(worker_id, kp.public_b64(), challenge.nonce)
    registry.complete_registration(worker_id, kp.public_b64(), kp.sign(payload))


def classify_task(answer: str = "positive", **kw) -> Task:
    return Task(
        task_type=TaskType.CLASSIFY,
        objective="Classify sentiment",
        inputs={"text": "great", "labels": ["positive", "negative"]},
        success_check={"equals": answer},
        **kw,
    )


async def test_pass_on_first_attempt():
    router = Router({Tier.SMALL: MockLLMWorker("w", Tier.SMALL, seed=1,
                                               skills={TaskType.CLASSIFY: 1.0})})
    executor = TaskExecutor(router)
    outcome = await executor.execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS
    assert len(outcome.attempts) == 1 and outcome.escalations == 0
    assert outcome.final_output == "positive"


@pytest.mark.parametrize(
    "defect,reason_substring",
    [
        ("expired", "expired"),
        ("wrong_subject", "token subject is"),
        ("wrong_task", "token bound to task"),
        ("missing_scope", "no scope covers"),
        ("wrong_scope", "no scope covers"),
        ("revoked", "token revoked"),
    ],
)
async def test_pre_dispatch_authorization_denial_paths(defect, reason_substring):
    """META-18: every pre-dispatch denial path must fail closed — the runner
    is never called, no `attempt.*` event is emitted, no `Attempt` is appended,
    no capability-matrix sample is banked, the outcome is FAIL, and the
    signed provenance records the reason. Each defect is produced by a token
    that is validly signed by the configured issuer (the issuer mints tokens
    that trip exactly one validation rule)."""
    calls = 0
    events: list[tuple[str, dict]] = []
    matrix = CapabilityMatrix()
    provenance = ProvenanceLog()

    def handler(task: Task):
        nonlocal calls
        calls += 1
        return "positive"

    class DefectIssuer(TokenIssuer):
        """Issuer that mints a validly-signed token, but with exactly one
        defect of the requested kind so the executor's re-check returns a
        different reasoned `TokenCheck` per parameter."""

        def issue(self, subject, scopes, *, ttl_s=600.0, task_id=None, now=None):
            from metaharness.identity.tokens import TokenPayload, CapabilityToken
            at = now if now is not None else time.time()
            if defect == "expired":
                # Issue with TTL=0 in a past clock; the signature is valid
                # for the bytes, but `at > expires_at` fails.
                effective_now = at - 10.0
                payload = TokenPayload(
                    subject=subject, scopes=sorted(scopes), task_id=task_id,
                    issued_at=effective_now, expires_at=effective_now,
                )
                return CapabilityToken(
                    payload=payload,
                    issuer_public_b64=self.public_b64(),
                    signature_b64=self._keypair.sign(payload.signing_bytes()),
                )
            if defect == "wrong_subject":
                # Valid signature, but bound to a different subject.
                payload = TokenPayload(
                    subject="someone-else", scopes=sorted(scopes), task_id=task_id,
                    issued_at=at, expires_at=at + ttl_s,
                )
                return CapabilityToken(
                    payload=payload,
                    issuer_public_b64=self.public_b64(),
                    signature_b64=self._keypair.sign(payload.signing_bytes()),
                )
            if defect == "wrong_task":
                payload = TokenPayload(
                    subject=subject, scopes=sorted(scopes), task_id="some-other-task",
                    issued_at=at, expires_at=at + ttl_s,
                )
                return CapabilityToken(
                    payload=payload,
                    issuer_public_b64=self.public_b64(),
                    signature_b64=self._keypair.sign(payload.signing_bytes()),
                )
            if defect == "missing_scope":
                # Valid signature, but no scopes at all — every required scope
                # is missing.
                payload = TokenPayload(
                    subject=subject, scopes=[], task_id=task_id,
                    issued_at=at, expires_at=at + ttl_s,
                )
                return CapabilityToken(
                    payload=payload,
                    issuer_public_b64=self.public_b64(),
                    signature_b64=self._keypair.sign(payload.signing_bytes()),
                )
            if defect == "wrong_scope":
                # Valid signature, but the only scope is unrelated to what the
                # dispatch gate requires (so required_scopes are uncovered).
                payload = TokenPayload(
                    subject=subject, scopes=["totally:unrelated"],
                    task_id=task_id, issued_at=at, expires_at=at + ttl_s,
                )
                return CapabilityToken(
                    payload=payload,
                    issuer_public_b64=self.public_b64(),
                    signature_b64=self._keypair.sign(payload.signing_bytes()),
                )
            if defect == "revoked":
                token = super().issue(subject, scopes, ttl_s=ttl_s, task_id=task_id, now=now)
                self.revoke(token.payload.token_id)
                return token
            return super().issue(subject, scopes, ttl_s=ttl_s, task_id=task_id, now=now)

    worker = ScriptedWorker(
        "w", handler, tier=Tier.SMALL, model="small-model",
    )
    outcome = await TaskExecutor(
        Router({Tier.SMALL: worker}, matrix=matrix),
        token_issuer=DefectIssuer(),
        provenance=provenance,
        orchestrator_keypair=KeyPair.generate(),
    ).execute(
        classify_task(max_attempts=1),
        event_sink=lambda k, p: events.append((k, p)),
    )

    assert calls == 0
    assert outcome.final_verdict is Verdict.FAIL
    assert outcome.attempts == []
    assert events == []
    assert matrix.samples("small-model", TaskType.CLASSIFY) == 0
    actions = [e.action for e in provenance.entries()]
    assert "task.authorization_denied" in actions
    denied = [e for e in provenance.entries() if e.action == "task.authorization_denied"]
    assert len(denied) == 1
    detail = denied[0].detail
    assert detail["task_id"] == outcome.task.id
    assert detail["attempt"] == 1
    assert detail["worker_id"] == "w"
    assert detail["tier"] == Tier.SMALL.value
    assert detail["task_type"] == TaskType.CLASSIFY.value
    assert reason_substring in detail["reason"]
    # no raw token bytes leak into the redacted provenance detail
    assert "signature" not in detail
    assert "signature_b64" not in detail


async def test_valid_dispatch_token_is_bound_and_precedes_assignment():
    """META-18: capability evidence rides INSIDE the existing
    `attempt.assigned` payload — no new canonical event kind. The authorization
    object is redacted (no signature/private material), exactly bound to the
    chosen worker and task, and precedes any runner call. The issuer must be
    explicitly supplied for the redacted payload to land on the event (the
    private default preserves the legacy payload byte-for-byte for out-of-scope
    canonical event-equality tests)."""
    events: list[tuple[str, dict]] = []
    outcome = await TaskExecutor(
        Router({Tier.SMALL: MockLLMWorker(
            "w", Tier.SMALL, seed=1, skills={TaskType.CLASSIFY: 1.0}
        )}),
        token_issuer=TokenIssuer(),
    ).execute(classify_task(), event_sink=lambda k, p: events.append((k, p)))

    assert outcome.final_verdict is Verdict.PASS
    kinds = [kind for kind, _ in events]
    assert "attempt.authorized" not in kinds  # no new canonical event kind
    assert kinds.index("attempt.assigned") < kinds.index("attempt.started")
    assigned = next(payload for kind, payload in events if kind == "attempt.assigned")
    authorization = assigned["authorization"]
    assert authorization["subject"] == "w"
    assert authorization["task_id"] == outcome.task.id
    assert authorization["scopes"] == [
        "task:execute", "task_type:classify", "tier:small"
    ]
    assert "token_id" in authorization and authorization["token_id"]
    assert authorization["expires_at"] > 0
    # no signature/private material in the redacted object
    assert "signature" not in authorization
    assert "signature_b64" not in authorization
    assert "issuer_private_b64" not in authorization


async def test_required_execution_evidence_is_attached_without_worker_shell(tmp_path):
    seen = []

    def handler(task: Task):
        seen.append(task)
        return {"all_met": True, "criteria": []}

    async def workspace_verifier(root):
        assert root == str(tmp_path)
        return VerificationResult(
            verdict=Verdict.PASS,
            score=1.0,
            scorer="execution",
            detail="command: python -m pytest -q\nstatus: passed\noutput:\n1 passed",
        )

    task = Task(
        objective="verify it",
        output_schema={"type": "object"},
        requires_execution_evidence=True,
        tools=["read_file"],
        max_attempts=1,
    )
    outcome = await TaskExecutor(
        Router({Tier.SMALL: ScriptedWorker("w", handler)}),
        workspace_root=str(tmp_path),
        workspace_verifier=workspace_verifier,
    ).execute(task)

    assert outcome.final_verdict is Verdict.UNVERIFIED
    assert seen[0].tools == ["read_file"]
    assert "1 passed" in seen[0].inputs["harness_execution_evidence"]
    assert "harness_execution_evidence" not in task.inputs


async def test_escalates_on_verified_failure():
    always_wrong = MockLLMWorker("w-small", Tier.SMALL, seed=1,
                                 skills={TaskType.CLASSIFY: 0.0})
    always_right = MockLLMWorker("w-front", Tier.FRONTIER, seed=2,
                                 skills={TaskType.CLASSIFY: 1.0})
    router = Router({Tier.SMALL: always_wrong, Tier.FRONTIER: always_right})
    executor = TaskExecutor(router)
    outcome = await executor.execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS
    assert outcome.escalations == 1
    tiers = [a.result.tier for a in outcome.attempts]
    assert tiers == [Tier.SMALL, Tier.FRONTIER]


async def test_timeout_retries_same_tier_and_records_only_later_pass():
    """Issue #11: a timeout is operationally neutral. Retry the same tier once,
    do not poison its capability cell with the timeout, and bank a later PASS."""
    calls = 0

    def timeout_then_pass(task: Task):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkerTimeout("small: timed out after 1s", timeout_s=1.0)
        return "positive"

    matrix = CapabilityMatrix()
    small = ScriptedWorker(
        "small", timeout_then_pass, tier=Tier.SMALL, model="small-model"
    )
    frontier = ScriptedWorker(
        "frontier", lambda task: "positive",
        tier=Tier.FRONTIER, model="frontier-model",
    )
    provenance = ProvenanceLog()
    outcome = await TaskExecutor(
        Router({Tier.SMALL: small, Tier.FRONTIER: frontier}, matrix=matrix,
               explore_rate=0.0),
        provenance=provenance,
        orchestrator_keypair=KeyPair.generate(),
    ).execute(classify_task(max_attempts=3))

    assert outcome.final_verdict == Verdict.PASS
    assert [a.result.tier for a in outcome.attempts] == [Tier.SMALL, Tier.SMALL]
    assert outcome.attempts[0].verification.failure_mode == MASTMode.TIMEOUT
    assert outcome.escalations == 0
    assert matrix.samples("small-model", TaskType.CLASSIFY) == 1  # PASS only
    assert matrix.samples("frontier-model", TaskType.CLASSIFY) == 0
    assert [e.action for e in provenance.entries()].count("task.timeout_retry") == 1


async def test_second_same_tier_timeout_escalates_without_negative_evidence():
    """Issue #11: one retry is a grace period, not an infinite loop. A second
    timeout escalates, while neither timeout becomes model-skill evidence."""
    def always_times_out(task: Task):
        raise WorkerTimeout("small: timed out after 1s", timeout_s=1.0)

    matrix = CapabilityMatrix()
    small = ScriptedWorker(
        "small", always_times_out, tier=Tier.SMALL, model="small-model"
    )
    frontier = ScriptedWorker(
        "frontier", lambda task: "positive",
        tier=Tier.FRONTIER, model="frontier-model",
    )
    outcome = await TaskExecutor(
        Router({Tier.SMALL: small, Tier.FRONTIER: frontier}, matrix=matrix,
               explore_rate=0.0)
    ).execute(classify_task(max_attempts=3))

    assert outcome.final_verdict == Verdict.PASS
    assert [a.result.tier for a in outcome.attempts] == [
        Tier.SMALL, Tier.SMALL, Tier.FRONTIER,
    ]
    assert outcome.escalations == 1
    assert all(
        a.verification.failure_mode == MASTMode.TIMEOUT
        for a in outcome.attempts[:2]
    )
    assert matrix.samples("small-model", TaskType.CLASSIFY) == 0
    assert matrix.samples("frontier-model", TaskType.CLASSIFY) == 1


async def test_timeout_retry_stays_on_exact_tier_after_budget_charge():
    """Issue #11 review: leaving MID unexcluded is not enough. Once the first
    attempt is charged, normal affordability filtering would reroute to SMALL;
    the timeout grace attempt must still run on the exact MID tier."""
    class CostlyMid(Runner):
        worker_id, tier, model = "mid", Tier.MID, "mid-model"

        def __init__(self) -> None:
            self.calls = 0

        async def run(self, task: Task) -> WorkerResult:
            self.calls += 1
            if self.calls == 1:
                return WorkerResult(
                    task_id=task.id, worker_id=self.worker_id,
                    tier=self.tier, model=self.model,
                    error="WorkerTimeout: mid timed out after 1s",
                    timed_out=True, cost_usd=0.02,
                )
            return WorkerResult(
                task_id=task.id, worker_id=self.worker_id,
                tier=self.tier, model=self.model,
                output="positive", raw_text="positive",
            )

    matrix = CapabilityMatrix()
    mid = CostlyMid()
    router = Router(
        {
            Tier.SMALL: ScriptedWorker(
                "small", lambda task: "positive",
                tier=Tier.SMALL, model="small-model",
            ),
            Tier.MID: mid,
        },
        matrix=matrix,
        explore_rate=0.0,
    )
    # CODE_EDIT starts at MID under the cold-start priors. After the first
    # $0.02 charge only SMALL clears the normal affordability filter.
    task = Task(
        task_type=TaskType.CODE_EDIT,
        objective="make the change",
        success_check={"equals": "positive"},
        max_attempts=2,
    )
    outcome = await TaskExecutor(
        router, budget=Budget(max_cost_usd=0.021)
    ).execute(task)

    assert outcome.final_verdict == Verdict.PASS
    assert [a.result.tier for a in outcome.attempts] == [Tier.MID, Tier.MID]
    assert mid.calls == 2
    assert router.route_evidence() == {"mid": {"mid": 2}}
    assert matrix.samples("mid-model", TaskType.CODE_EDIT) == 1  # PASS only
    assert matrix.samples("small-model", TaskType.CODE_EDIT) == 0


async def test_matrix_learns_from_outcomes():
    matrix = CapabilityMatrix()
    router = Router(
        {Tier.SMALL: MockLLMWorker("w", Tier.SMALL, seed=3,
                                   skills={TaskType.CLASSIFY: 0.0}),
         Tier.MID: MockLLMWorker("w2", Tier.MID, seed=4,
                                 skills={TaskType.CLASSIFY: 1.0})},
        matrix=matrix,
    )
    executor = TaskExecutor(router)
    await executor.execute(classify_task())
    assert matrix.samples("mock-small", TaskType.CLASSIFY) >= 1
    assert matrix.samples("mock-mid", TaskType.CLASSIFY) >= 1


async def test_context_contract_violation_aborts_retries_and_is_no_capability_evidence():
    """META-19 (F6): a deterministic LiveContextViolation surfaces as
    error_kind="context_contract". Retries are pure waste (same inputs would
    reproduce it), so the task aborts after one attempt, and the failure never
    banks capability/routing evidence (it is not a model-skill signal)."""
    from metaharness.context.live import LiveContextViolation
    from metaharness.harness.runner import BaseRunner

    class ViolatingWorker(BaseRunner):
        def __init__(self):
            super().__init__(worker_id="w", tier=Tier.SMALL, model="model-a")
            self.calls = 0

        async def _execute(self, task: Task) -> WorkerResult:
            self.calls += 1
            raise LiveContextViolation("untrusted content in instruction slot")

    matrix = CapabilityMatrix()
    worker = ViolatingWorker()
    executor = TaskExecutor(Router({Tier.SMALL: worker}, matrix=matrix))
    outcome = await executor.execute(classify_task(max_attempts=3))

    assert worker.calls == 1  # aborted, not retried three times
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].result.error_kind == "context_contract"
    assert outcome.final_verdict == Verdict.FAIL
    assert matrix.samples("model-a", TaskType.CLASSIFY) == 0  # excluded from evidence


async def test_pool_member_runs_and_matrix_records_under_its_model():
    """With two members on a tier, the executor runs the member decide() picked
    and the capability matrix learns under THAT member's model, not the pool's."""
    matrix = CapabilityMatrix()
    router = Router(
        {Tier.MID: [
            MockLLMWorker("mid-a", Tier.MID, model="model-a", seed=1,
                          skills={TaskType.CLASSIFY: 1.0}),
            MockLLMWorker("mid-b", Tier.MID, model="model-b", seed=2,
                          skills={TaskType.CLASSIFY: 0.0}),
        ]},
        matrix=matrix, explore_rate=0.0,  # deterministic: no ε-exploration
    )
    executor = TaskExecutor(router)
    outcome = await executor.execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS
    assert outcome.attempts[0].result.model == "model-a"  # cold start -> first member
    assert matrix.samples("model-a", TaskType.CLASSIFY) >= 1
    assert matrix.samples("model-b", TaskType.CLASSIFY) == 0


async def test_exploration_serves_benched_member_and_earns_evidence():
    """End to end: forced ε-exploration routes a verifiable task to the benched
    pool member, runner_for serves THAT member, and the matrix accrues evidence
    under the benched member's model — the whole point of exploring."""
    class ForceExplore:
        def random(self) -> float:
            return 0.0

    matrix = CapabilityMatrix()
    for _ in range(10):  # mid-a is the well-evidenced incumbent
        matrix.record("model-a", TaskType.CLASSIFY, passed=True)
    router = Router(
        {Tier.MID: [
            MockLLMWorker("mid-a", Tier.MID, model="model-a", seed=1,
                          skills={TaskType.CLASSIFY: 1.0}),
            MockLLMWorker("mid-b", Tier.MID, model="model-b", seed=2,
                          skills={TaskType.CLASSIFY: 1.0}),
        ]},
        matrix=matrix, explore_rate=1.0, rng=ForceExplore(),
    )
    outcome = await TaskExecutor(router).execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS
    assert outcome.attempts[0].result.model == "model-b"  # benched member ran
    assert matrix.samples("model-b", TaskType.CLASSIFY) == 1  # evidence earned
    assert matrix.samples("model-a", TaskType.CLASSIFY) == 10  # incumbent untouched


async def test_explored_failure_retries_tier_instead_of_escalating():
    """An ε-exploration flub is the benched member's failure, not the tier's:
    the loop retries the SAME tier, decide() picks the best member on merit,
    and no frontier call is paid for evidence we chose to buy cheap."""
    class ExploreOnce:
        def __init__(self) -> None:
            self.values = [0.0]  # first decide explores; later ones never

        def random(self) -> float:
            return self.values.pop(0) if self.values else 1.0

    matrix = CapabilityMatrix()
    router = Router(
        {Tier.MID: [
            MockLLMWorker("mid-a", Tier.MID, model="model-a", seed=1,
                          skills={TaskType.CLASSIFY: 1.0}),
            MockLLMWorker("mid-b", Tier.MID, model="model-b", seed=2,
                          skills={TaskType.CLASSIFY: 0.0}),
         ],
         Tier.FRONTIER: MockLLMWorker("w-front", Tier.FRONTIER, model="model-f",
                                      seed=3, skills={TaskType.CLASSIFY: 1.0})},
        matrix=matrix, explore_rate=0.5, rng=ExploreOnce(),
    )
    outcome = await TaskExecutor(router).execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS
    assert outcome.escalations == 0  # explored FAIL never escalates the tier
    assert [a.result.tier for a in outcome.attempts] == [Tier.MID, Tier.MID]
    assert [a.result.model for a in outcome.attempts] == ["model-b", "model-a"]
    assert matrix.samples("model-b", TaskType.CLASSIFY) == 1  # FAIL still banked
    assert matrix.samples("model-f", TaskType.CLASSIFY) == 0  # frontier untouched


async def test_route_counts_tally_routes_not_tasks():
    """route_counts are 'times routed': an escalating task increments the count
    of every tier it visited."""
    router = Router({
        Tier.SMALL: MockLLMWorker("w-small", Tier.SMALL, seed=1,
                                  skills={TaskType.CLASSIFY: 0.0}),
        Tier.MID: MockLLMWorker("w-mid", Tier.MID, seed=2,
                                skills={TaskType.CLASSIFY: 1.0}),
    })
    outcome = await TaskExecutor(router).execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS and outcome.escalations >= 1
    assert router.route_evidence() == {"small": {"w-small": 1}, "mid": {"w-mid": 1}}


async def test_authenticity_unsigned_result_rejected():
    """A worker whose results aren't signed under a registered key never passes,
    even when its answers are correct."""
    registry = WorkerRegistry()
    kp_registered = KeyPair.generate()
    register(registry, "w-signed", kp_registered)
    # runner presents worker_id "w-signed" but has no key: results are unsigned
    unsigned = ScriptedWorker("w-signed", lambda t: "positive")
    router = Router({Tier.SMALL: unsigned})
    executor = TaskExecutor(router, registry=registry)
    outcome = await executor.execute(classify_task(max_attempts=2))
    assert outcome.final_verdict == Verdict.FAIL
    assert all(a.verification.scorer == "authenticity" for a in outcome.attempts)


async def test_authenticity_wrong_key_rejected():
    registry = WorkerRegistry()
    register(registry, "w1", KeyPair.generate())      # registered key
    impostor_key = KeyPair.generate()                  # different key signs results
    runner = ScriptedWorker("w1", lambda t: "positive", keypair=impostor_key)
    executor = TaskExecutor(Router({Tier.SMALL: runner}), registry=registry)
    outcome = await executor.execute(classify_task(max_attempts=1))
    assert outcome.final_verdict == Verdict.FAIL
    assert outcome.attempts[0].verification.scorer == "authenticity"


async def test_authenticity_failures_never_reach_the_capability_matrix():
    """Regression (issue-#5 panel P1): the executor reorder flattened the
    else-branch that kept matrix.record unreachable for authenticity failures,
    so badly-signed/replayed results recorded as skill-FAILs. An authenticity
    FAIL says nothing about the model's skill (verifiers.authenticity_failure
    invariant: 'Not recorded in the capability matrix') and must record
    NOTHING — while a normal verified attempt still records."""
    registry = WorkerRegistry()
    register(registry, "w-signed", KeyPair.generate())
    # runner presents worker_id "w-signed" but has no key: results are unsigned
    unsigned = ScriptedWorker("w-signed", lambda t: "positive", model="unsigned-model")
    matrix = CapabilityMatrix()
    executor = TaskExecutor(Router({Tier.SMALL: unsigned}, matrix=matrix),
                            registry=registry)
    outcome = await executor.execute(classify_task(max_attempts=2))
    assert outcome.final_verdict == Verdict.FAIL
    assert all(a.verification.scorer == "authenticity" for a in outcome.attempts)
    assert matrix.samples("unsigned-model", TaskType.CLASSIFY) == 0  # nothing banked

    # sanity: a properly signed worker's verified outcome still records
    kp = KeyPair.generate()
    register(registry, "w-ok", kp)
    signed = MockLLMWorker("w-ok", Tier.SMALL, keypair=kp, seed=1,
                           skills={TaskType.CLASSIFY: 1.0})
    executor = TaskExecutor(Router({Tier.SMALL: signed}, matrix=matrix),
                            registry=registry)
    outcome = await executor.execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS
    assert matrix.samples("mock-small", TaskType.CLASSIFY) >= 1


async def test_signed_registered_worker_passes_authenticity():
    registry = WorkerRegistry()
    kp = KeyPair.generate()
    register(registry, "w1", kp)
    runner = MockLLMWorker("w1", Tier.SMALL, keypair=kp, seed=1,
                           skills={TaskType.CLASSIFY: 1.0})
    executor = TaskExecutor(Router({Tier.SMALL: runner}), registry=registry)
    outcome = await executor.execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS


class _WorkspaceRunner(Runner):
    worker_id, tier, model = "workspace-w", Tier.SMALL, "workspace-model"

    def __init__(self, keypair: KeyPair, root: str, *, legacy: bool = False) -> None:
        self.keypair = keypair
        self.root = root
        self.legacy = legacy

    async def run(self, task: Task) -> WorkerResult:
        result = WorkerResult(
            task_id=task.id,
            worker_id=self.worker_id,
            tier=self.tier,
            model=self.model,
            output="narrated success",
            raw_text="narrated success",
            workspace_root=self.root,
        )
        if self.legacy:
            result.signature_b64 = self.keypair.sign(
                result_signing_bytes(result, version=1)
            )
            return result
        return sign_result(result, self.keypair)


async def test_attested_code_workspace_execution_overrides_text_scorer(tmp_path):
    """Issue #1 hierarchy: a real test verdict beats narration/success_check."""
    kp = KeyPair.generate()
    registry = WorkerRegistry()
    register(registry, "workspace-w", kp)
    runner = _WorkspaceRunner(kp, str(tmp_path))
    calls = []

    async def execution(task, result):
        calls.append(result.workspace_root)
        return VerificationResult(
            verdict=Verdict.PASS, score=1.0, scorer="execution",
            detail="pytest passed",
        )

    task = Task(
        task_type=TaskType.CODE_EDIT,
        objective="fix it",
        success_check={"equals": "different text"},
        max_attempts=1,
    )
    matrix = CapabilityMatrix()
    outcome = await TaskExecutor(
        Router({Tier.SMALL: runner}, matrix=matrix),
        registry=registry,
        execution_verifier=execution,
    ).execute(task)

    assert outcome.final_verdict is Verdict.PASS
    assert outcome.attempts[0].verification.scorer == "execution"
    assert calls == [str(tmp_path)]
    assert matrix.samples("workspace-model", TaskType.CODE_EDIT) == 1


async def test_execution_failure_overrides_passing_text_check(tmp_path):
    kp = KeyPair.generate()
    registry = WorkerRegistry()
    register(registry, "workspace-w", kp)
    runner = _WorkspaceRunner(kp, str(tmp_path))

    async def execution(task, result):
        return VerificationResult(
            verdict=Verdict.FAIL, score=0.0, scorer="execution",
            detail="pytest failed", failure_mode=MASTMode.DISOBEY_TASK_SPEC,
        )

    task = Task(
        task_type=TaskType.CODE_EDIT,
        objective="fix it",
        success_check={"equals": "narrated success"},
        max_attempts=1,
    )
    outcome = await TaskExecutor(
        Router({Tier.SMALL: runner}),
        registry=registry,
        execution_verifier=execution,
    ).execute(task)

    assert outcome.final_verdict is Verdict.FAIL
    assert outcome.attempts[0].verification.detail == "pytest failed"


async def test_legacy_workspace_is_not_executed_and_judge_sees_no_root(tmp_path):
    kp = KeyPair.generate()
    registry = WorkerRegistry()
    register(registry, "workspace-w", kp)
    runner = _WorkspaceRunner(kp, str(tmp_path), legacy=True)
    execution_calls = []
    judge_roots = []

    async def execution(task, result):
        execution_calls.append(result.workspace_root)
        return VerificationResult(verdict=Verdict.PASS, scorer="execution")

    async def judge(task, result):
        judge_roots.append(result.workspace_root)
        return VerificationResult(
            verdict=Verdict.PASS, score=1.0, scorer="judge", detail="looks good",
        )

    outcome = await TaskExecutor(
        Router({Tier.SMALL: runner}),
        registry=registry,
        execution_verifier=execution,
        judge=judge,
    ).execute(Task(task_type=TaskType.CODE_EDIT, objective="fix it", max_attempts=1))

    assert outcome.final_verdict is Verdict.PASS
    assert outcome.attempts[0].verification.scorer == "judge"
    assert execution_calls == []
    assert judge_roots == [""]


async def test_attested_workspace_without_check_falls_back_to_judge(tmp_path):
    kp = KeyPair.generate()
    registry = WorkerRegistry()
    register(registry, "workspace-w", kp)
    runner = _WorkspaceRunner(kp, str(tmp_path))  # empty: no pytest/package marker
    judge_roots = []

    async def judge(task, result):
        judge_roots.append(result.workspace_root)
        return VerificationResult(
            verdict=Verdict.PASS, score=1.0, scorer="judge", detail="files look good",
        )

    outcome = await TaskExecutor(
        Router({Tier.SMALL: runner}),
        registry=registry,
        judge=judge,
    ).execute(Task(task_type=TaskType.CODE_EDIT, objective="fix it", max_attempts=1))

    assert outcome.final_verdict is Verdict.PASS
    assert outcome.attempts[0].verification.scorer == "judge"
    assert judge_roots == [str(tmp_path)]


async def test_over_budget_worker_never_launches_execution_check(tmp_path):
    kp = KeyPair.generate()
    registry = WorkerRegistry()
    register(registry, "workspace-w", kp)
    runner = _WorkspaceRunner(kp, str(tmp_path))
    calls = []

    async def execution(task, result):
        calls.append(result)
        return VerificationResult(verdict=Verdict.PASS, scorer="execution")

    # The direct test runner reports zero worker latency/tokens, so start the
    # shared budget already over cap to prove the pre-execution check blocks it.
    budget = Budget(max_tokens=0, spent_tokens=1)
    outcome = await TaskExecutor(
        Router({Tier.SMALL: runner}),
        registry=registry,
        budget=budget,
        execution_verifier=execution,
    ).execute(Task(task_type=TaskType.CODE_EDIT, objective="fix it", max_attempts=1))

    assert outcome.attempts[0].verification.scorer == "budget"
    assert calls == []


async def test_execution_check_wall_time_is_charged_to_budget(tmp_path):
    kp = KeyPair.generate()
    registry = WorkerRegistry()
    register(registry, "workspace-w", kp)
    runner = _WorkspaceRunner(kp, str(tmp_path))

    async def execution(task, result):
        await asyncio.sleep(0.02)
        return VerificationResult(verdict=Verdict.PASS, scorer="execution")

    budget = Budget(max_wall_s=0.001)
    outcome = await TaskExecutor(
        Router({Tier.SMALL: runner}),
        registry=registry,
        budget=budget,
        execution_verifier=execution,
    ).execute(Task(task_type=TaskType.CODE_EDIT, objective="fix it", max_attempts=1))

    assert budget.spent_wall_s >= 0.02
    assert outcome.attempts[0].verification.scorer == "budget"


async def test_budget_hard_stop():
    runner = MockLLMWorker("w", Tier.SMALL, seed=1, skills={TaskType.CLASSIFY: 0.0})
    budget = Budget(max_tokens=50)  # one attempt blows through this
    executor = TaskExecutor(Router({Tier.SMALL: runner}), budget=budget)
    outcome = await executor.execute(classify_task(max_attempts=5))
    assert outcome.final_verdict != Verdict.PASS
    assert len(outcome.attempts) < 5
    assert outcome.attempts[-1].verification.failure_mode is not None
    assert outcome.attempts[-1].verification.scorer == "budget"


async def test_worker_error_over_budget_surfaces_tool_error_not_budget_exceeded():
    """Issue #5: charging before inspecting the result masked a genuine worker
    failure as budget exhaustion. A worker that errors AND blows the cap on the
    same attempt must record the real TOOL_ERROR — the run still stops (budget
    event recorded), it just doesn't lie about why."""
    from metaharness.core.types import MASTMode

    class Failing(Runner):
        worker_id, tier, model = "w", Tier.SMALL, "w"
        async def run(self, task):
            return WorkerResult(task_id=task.id, worker_id="w", tier=self.tier,
                                model="w", error="tool blew up",
                                tokens_in=30, tokens_out=10)

    budget = Budget(max_tokens=5)  # the 40-token charge blows the cap
    executor = TaskExecutor(Router({Tier.SMALL: Failing()}), budget=budget)
    outcome = await executor.execute(classify_task(max_attempts=3))
    assert outcome.final_verdict == Verdict.FAIL
    assert len(outcome.attempts) == 1  # the budget stop still halts the run
    assert outcome.attempts[-1].verification.failure_mode == MASTMode.TOOL_ERROR
    assert outcome.attempts[-1].verification.scorer == "execution"
    assert budget.spent_tokens == 40  # charged regardless


async def test_unverified_stops_iteration():
    """No checkable signal → one attempt, UNVERIFIED; iterating would be vibes."""
    runner = ScriptedWorker("w", lambda t: {"essay": "..."})
    executor = TaskExecutor(Router({Tier.SMALL: runner}))
    task = Task(task_type=TaskType.SUMMARIZE, objective="write", max_attempts=5)
    outcome = await executor.execute(task)
    assert outcome.final_verdict == Verdict.UNVERIFIED
    assert len(outcome.attempts) == 1


async def test_reflection_feeds_next_attempt():
    # META-19 (F2): reflections now accumulate in task.advice, not boundaries.
    # Advice quotes prior worker output verbatim; keeping it out of the
    # caller-authored boundaries contract prevents untrusted-data laundering.
    seen_advice: list[list[str]] = []
    seen_boundaries: list[list[str]] = []

    def handler(task: Task):
        seen_advice.append(list(task.advice))
        seen_boundaries.append(list(task.boundaries))
        return "negative"  # always wrong

    runner = ScriptedWorker("w", handler)
    executor = TaskExecutor(
        Router({Tier.SMALL: runner}),
        reflector=lambda task, attempt: f"attempt {attempt.n} returned {attempt.result.output!r}; that is wrong",
    )
    await executor.execute(classify_task(max_attempts=3))
    assert seen_advice[0] == []
    assert any("wrong" in b for b in seen_advice[1])
    # repetition notice also appears once the same wrong answer recurs
    assert any("different approach" in b.lower() for b in seen_advice[2])
    # boundaries stay the pure caller-authored contract across every attempt
    assert all(b == seen_boundaries[0] for b in seen_boundaries)


async def test_provenance_trail_written_and_verifiable():
    orch_kp = KeyPair.generate()
    registry = WorkerRegistry()
    register(registry, "orchestrator", orch_kp)
    provenance = ProvenanceLog()
    runner = MockLLMWorker("w", Tier.SMALL, seed=1, skills={TaskType.CLASSIFY: 1.0})
    executor = TaskExecutor(
        Router({Tier.SMALL: runner}),
        provenance=provenance,
        orchestrator_keypair=orch_kp,
    )
    await executor.execute(classify_task())
    kinds = [e.action for e in provenance.entries()]
    assert kinds[0] == "task.started" and kinds[-1] == "task.finished"
    assert "task.attempt" in kinds
    check = provenance.verify_chain(
        lambda wid: registry.get(wid).public_key_b64 if registry.get(wid) else None
    )
    assert check.ok, check.reason


async def test_observer_receives_every_outcome():
    """Regression (2026-07-08): LearningLoop.observe was never wired into the
    server path, so WebUI failure clusters stayed empty forever. The executor
    now notifies an observer for each finished task."""
    from metaharness.correction import LearningLoop, Playbook
    from metaharness.core.types import MASTMode

    loop = LearningLoop(Playbook())
    always_wrong = MockLLMWorker("w", Tier.SMALL, seed=1,
                                 skills={TaskType.CLASSIFY: 0.0})
    executor = TaskExecutor(Router({Tier.SMALL: always_wrong}),
                            playbook_hints=loop.hints_for, observer=loop.observe)
    await executor.execute(classify_task(max_attempts=2))
    assert loop.stats.count("classify", MASTMode.DISOBEY_TASK_SPEC) >= 1


async def test_broken_observer_never_fails_the_task():
    def bad_observer(outcome):
        raise RuntimeError("observer exploded")

    runner = MockLLMWorker("w", Tier.SMALL, seed=1, skills={TaskType.CLASSIFY: 1.0})
    executor = TaskExecutor(Router({Tier.SMALL: runner}), observer=bad_observer)
    outcome = await executor.execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS
