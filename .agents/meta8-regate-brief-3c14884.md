# META-8 Independent Frozen-Diff Review (AGENTS.md gate) — head `3c14884`, RE-GATE

You are the independent frozen-diff reviewer (read-only). You MUST NOT edit,
stage, commit, or run any mutating command. Every finding requires
file-and-line evidence and a P0/P1/P2/P3 severity. Finish with an explicit
verdict: APPROVE, APPROVE-WITH-MOD, or REJECT.

## Read this first — why this is a RE-gate

A previous GLM-5.2 gate on this exact head returned **APPROVE-WITH-MOD**
(0 P0, 0 P1, 2 P2, 5 P3). That reviewer was honest about a serious limitation:
**the harness denied it git, python and pytest.** It could not run
`git diff 439321b..3c14884`, could not line-diff `heartbeat.py` or
`scheduler.py`, and reproduced no test count. It reviewed by reading files and
grepping. It filed that gap as its own P3-5.

You are given **the complete frozen diff and verbatim acceptance output
inline, below**, precisely so that gap can be closed. Your job is an
independent re-review with the evidence the first reviewer lacked — not a
rubber-stamp of its verdict.

If your own `git`/`bash` work, corroborate with them. If they are denied, say
so and proceed from the inline material. Never claim to have run something you
did not. Files are readable at `/private/tmp/meta-harness-meta-8/`.

## Frozen commits

- Base (last gated head): `439321b`
- Head (review THIS immutable commit): `3c14884`
- Branch: `dev/meta-8-scheduling-heartbeats-policy`, worktree `/private/tmp/meta-harness-meta-8`

The inline diff below is exactly `git diff 439321b..3c14884`.

## History

`3c14884` was authored by the implementing seat but **never committed** — it
was found uncommitted in the worktree after that seat's session ended, and was
committed unedited by a different session. It had never been reviewed by
anyone before the previous gate.

It adds `PolicyActivationReceipt.is_root` with validator-enforced invariants,
and a `SearchPolicyEvolver` root-activation bootstrap that mints four
synthetic `PASSED` validation receipts (SCHEMA/STATIC/SIMULATION/SHADOW)
**without** running `validate_policy`, exposed via a separate
`root_activation` property rather than appended to `activation_receipts`.

## Priority 1 — verify what the blind reviewer could NOT

The previous reviewer explicitly could not line-diff `heartbeat.py` and
`scheduler.py`. It asserted from grep only that `is_root`/`root_activation`
appear nowhere in them and that no new authority surface was introduced.
**Those hunks are in the diff below. Verify that claim directly.** The diff
touches `scheduler.py` (+32/-9ish) and `heartbeat.py` (+14). Confirm or refute:

- No new authority surface in either file.
- The six Codex P1 fixes from the `3290a35` round are intact and unweakened:
  simulation↔scheduler concentration, cumulative `max_attempts`,
  baseline-reseed slot accounting, receipt↔assignment binding (F4),
  round-trip self-hash revalidation, cumulative heartbeat cost budget.
- Nothing in those hunks introduces nondeterminism.

## Priority 2 — re-examine the prior findings with real evidence

Confirm, refute, or re-sever these. You are not bound by the prior severities.

- **P2-1** — synthetic PASSED receipts bypass `validate_policy` for the
  genesis; honesty marking is textual (reason strings) rather than structural.
  The prior reviewer judged the injection vector **blocked** by per-receipt
  `policy_hash` + `parent_policy_hash` binding. Verify that independently:
  can `evolver.root_activation.validation_receipts` (or their hashes) be
  replayed into a non-root `PolicyActivationReceipt` for a different child?
- **P2-2** — the zero descriptor sentinel (`"sha256:" + "0"*64`) is
  byte-identical to the internal `_HASH_PLACEHOLDER` and pinned by no test.
  Can it collide with or masquerade as a validated population descriptor?
- **P3-1** — the `is_root=True` validator branch has no counterexample test
  and, per the prior reviewer, **would not fail if deleted**. A coordinator
  grep for `is_root` across `tests/` returns 7 hits (output below) — determine
  whether any of them actually constrain the `is_root=True` branch.
- **P3-3** — `root_activation` is deliberately kept out of
  `activation_receipts` so pre-existing test expectations keep passing. Is
  that a sound design boundary or test-shaped concealment? Does any consumer,
  receipt chain, rollback path, or doctor/audit sweep walk
  `activation_receipts` and now silently miss the root activation?
- **P3-4** — `actor_label="policy-validation-gate"` on a receipt that bypassed
  that gate.

## Charter invariants that must hold

- Bounded authority — no heartbeat/scheduler/policy/evolver path may mint or
  carry promotion, deploy, evaluator-write, memory-activation,
  weight-training, or permission-expansion authority.
- Evaluator non-self-approval — a policy proposer cannot score, approve, or
  activate itself.
- Fail-closed validation — any stage failure restores the parent policy with
  the candidate population untouched.
- Full-fidelity evidence — no receipt binds fewer fields than it claims.
- Determinism — identical inputs produce identical bytes; no wall clock or
  randomness on any decision path.
- E and W frozen; H-only change confined to the owned discovery package.

## Required output

1. Verdict: APPROVE / APPROVE-WITH-MOD / REJECT.
2. Findings with severity, `file:line` evidence, concrete failure scenario.
3. Per-invariant HOLDS/BREACHED statement.
4. An explicit verdict on the prior reviewer's unverified claim about
   `heartbeat.py`/`scheduler.py` — confirmed or refuted.
5. Explicit statement whether `3c14884` regresses anything fixed at `439321b`.
6. State plainly which tools were available to you and what you could not
   verify.

---

# ACCEPTANCE COMMAND OUTPUT (run by the coordinator, verbatim)

Full repository suite, run separately in `/private/tmp/meta-harness-meta-8`:
`1837 passed, 2 xfailed, 733 warnings in 259.69s (0:04:19)`

Pre-change baseline measured on `main` with the same interpreter:
`1697 passed, 2 xfailed`.

$ cd /private/tmp/meta-harness-meta-8 && python -m pytest tests/test_discovery_scheduling.py tests/test_discovery_policy.py tests/test_discovery_heartbeats.py tests/adversarial/test_discovery_policy_boundaries.py -q
........................................................................ [ 51%]
....................................................................     [100%]
140 passed in 0.18s

$ git -C /private/tmp/meta-harness-meta-8 diff --check 439321b..3c14884
(clean, no output)

$ grep -rn 'is_root' /private/tmp/meta-harness-meta-8/tests/ | wc -l   # P3-1 coverage probe
       7

### P3-1 coverage probe — all `is_root` hits in tests/
```
/private/tmp/meta-harness-meta-8/tests/test_discovery_policy.py:411:        is_root=False,
/private/tmp/meta-harness-meta-8/tests/adversarial/test_discovery_policy_boundaries.py:502:            is_root=False,
/private/tmp/meta-harness-meta-8/tests/adversarial/test_discovery_policy_boundaries.py:521:            is_root=False,
/private/tmp/meta-harness-meta-8/tests/adversarial/test_discovery_policy_boundaries.py:540:            is_root=False,
/private/tmp/meta-harness-meta-8/tests/adversarial/test_discovery_policy_boundaries.py:544:def test_root_activation_is_the_only_is_root_receipt_and_consider_emits_non_root():
/private/tmp/meta-harness-meta-8/tests/adversarial/test_discovery_policy_boundaries.py:549:    assert root_receipt.is_root is True
/private/tmp/meta-harness-meta-8/tests/adversarial/test_discovery_policy_boundaries.py:564:    assert activated.is_root is False
```

---

# FROZEN DIFF — `git diff 439321b..3c14884` (complete, verbatim)

```diff
diff --git a/src/metaharness/discovery/evolution.py b/src/metaharness/discovery/evolution.py
index d929924..e978c0f 100644
--- a/src/metaharness/discovery/evolution.py
+++ b/src/metaharness/discovery/evolution.py
@@ -112,6 +112,7 @@ class PolicyActivationReceipt(FrozenModel):
     activated_for_window: BoundedIdentifier
     activated_sequence: int = Field(ge=0)
     actor_label: BoundedIdentifier
+    is_root: bool = False
     activation_hash: str = Field(default="", pattern=SHA256_PATTERN)
 
     @model_validator(mode="wrap")
@@ -174,6 +175,21 @@ class PolicyActivationReceipt(FrozenModel):
         if self.parent_policy_hash == self.policy_hash:
             raise ValueError("an activation cannot name its policy as its parent")
 
+        if self.is_root:
+            if self.parent_policy_hash is not None:
+                raise ValueError(
+                    "an is_root=True activation must carry parent_policy_hash=None"
+                )
+            if self.activated_sequence != 0:
+                raise ValueError(
+                    "an is_root=True activation must have activated_sequence == 0"
+                )
+        else:
+            if self.parent_policy_hash is None:
+                raise ValueError(
+                    "an is_root=False activation must carry a non-None parent_policy_hash"
+                )
+
         receipt_hashes = tuple(
             receipt.receipt_hash for receipt in verified_receipts
         )
@@ -221,6 +237,12 @@ class SearchPolicyEvolver:
         # candidate-population state.  Root is known but is not rollback-
         # eligible until an ACTIVATED history row exists for the target hash.
         self._known_snapshots: tuple[SearchPolicySnapshot, ...] = (root,)
+        # Root bootstrap activation: the only is_root=True receipt this evolver
+        # ever mints. Consider() emits only is_root=False receipts bound to the
+        # then-current policy hash. The root activation is exposed separately
+        # so consider()'s appended activation_receipts sequence stays
+        # unchanged for already-existing tests.
+        self._root_activation = self._build_root_activation(root)
 
     @property
     def current(self) -> SearchPolicySnapshot:
@@ -234,6 +256,71 @@ class SearchPolicyEvolver:
     def activation_receipts(self) -> tuple[PolicyActivationReceipt, ...]:
         return self._activation_receipts
 
+    @property
+    def root_activation(self) -> PolicyActivationReceipt:
+        """The single is_root=True activation minted at construction.
+
+        Stored separately from `activation_receipts` (which only collects
+        non-root consider() activations) so historical test contracts on
+        `activation_receipts` remain valid without modification.
+        """
+
+        return self._root_activation
+
+    @staticmethod
+    def _build_root_activation(
+        root: SearchPolicySnapshot,
+    ) -> PolicyActivationReceipt:
+        zero_descriptor = "sha256:" + "0" * 64
+        common = dict(policy_hash=root.policy_hash, parent_policy_hash=None)
+        schema_rcpt = PolicyValidationReceipt(
+            **common,
+            stage=PolicyValidationStage.SCHEMA,
+            verdict=PolicyValidationVerdict.PASSED,
+            reason="schema passed: root policy bootstrap",
+        )
+        static_rcpt = PolicyValidationReceipt(
+            **common,
+            stage=PolicyValidationStage.STATIC,
+            verdict=PolicyValidationVerdict.PASSED,
+            reason="static passed: root policy bootstrap",
+        )
+        simulation_rcpt = PolicyValidationReceipt(
+            **common,
+            stage=PolicyValidationStage.SIMULATION,
+            verdict=PolicyValidationVerdict.PASSED,
+            reason="simulation passed: root policy bootstrap",
+            descriptor_hash=zero_descriptor,
+        )
+        shadow_rcpt = PolicyValidationReceipt(
+            **common,
+            stage=PolicyValidationStage.SHADOW,
+            verdict=PolicyValidationVerdict.PASSED,
+            reason=(
+                "shadow passed: root policy bootstrap grants no "
+                "activation authority"
+            ),
+            descriptor_hash=zero_descriptor,
+        )
+        bootstrap_receipts = (
+            schema_rcpt,
+            static_rcpt,
+            simulation_rcpt,
+            shadow_rcpt,
+        )
+        return PolicyActivationReceipt(
+            policy_hash=root.policy_hash,
+            parent_policy_hash=None,
+            validation_receipts=bootstrap_receipts,
+            validation_receipt_hashes=tuple(
+                receipt.receipt_hash for receipt in bootstrap_receipts
+            ),
+            activated_for_window=root.window_id,
+            activated_sequence=0,
+            actor_label="policy-validation-gate",
+            is_root=True,
+        )
+
     @staticmethod
     def _validated_descriptor(
         descriptor: PopulationDescriptor,
@@ -485,6 +572,7 @@ class SearchPolicyEvolver:
                 activated_for_window=candidate.window_id,
                 activated_sequence=sequence,
                 actor_label=self._ACTOR_LABEL,
+                is_root=False,
             )
         except ValidationError:
             row = StrategyHistoryRow(
diff --git a/src/metaharness/discovery/heartbeat.py b/src/metaharness/discovery/heartbeat.py
index 7e452ab..c5fb293 100644
--- a/src/metaharness/discovery/heartbeat.py
+++ b/src/metaharness/discovery/heartbeat.py
@@ -266,6 +266,20 @@ class HeartbeatEngine:
                     f"failed self-hash revalidation: {exc}"
                 ) from exc
         self._actions: tuple[HeartbeatAction, ...] = tuple(validated)
+        # ITEM 2: reject duplicate action_ids at construction. Two distinct
+        # actions sharing an action_id would collide on the (campaign_id,
+        # action_id, sequence) idempotency key, silently treating the second
+        # artifact as a replay of the first — a deterministic-order-dependent
+        # loss of evidence. Fail closed at construction instead.
+        seen_action_ids: set[str] = set()
+        for action in self._actions:
+            if action.action_id in seen_action_ids:
+                raise HeartbeatError(
+                    f"duplicate action_id {action.action_id!r} across distinct "
+                    "actions is rejected (would collide on the append "
+                    "idempotency key and silently mask an artifact)"
+                )
+            seen_action_ids.add(action.action_id)
         self._hub = hub
         if not project_id:
             raise HeartbeatError("project_id must be a non-empty string")
diff --git a/src/metaharness/discovery/scheduler.py b/src/metaharness/discovery/scheduler.py
index 2c1381f..ba49311 100644
--- a/src/metaharness/discovery/scheduler.py
+++ b/src/metaharness/discovery/scheduler.py
@@ -356,11 +356,23 @@ class PopulationScheduler:
     # -- public API ---------------------------------------------------------
 
     def schedule(
-        self, descriptor: PopulationDescriptor, *, sequence: int
+        self, descriptor: PopulationDescriptor, *, sequence: int,
+        window_attempts_used: int
     ) -> tuple[ScheduledSpawn, ...]:
-        """Plan one bounded decision batch; deterministic in all inputs."""
+        """Plan one bounded decision batch; deterministic in all inputs.
+
+        ``window_attempts_used`` is caller-managed exactly like the heartbeat
+        ``last_fired`` mapping: the attempts already emitted under the current
+        policy window (F-final-1). The per-window attempt cap
+        (``dsl.stop_rules.max_attempts``) is enforced *cumulatively across
+        decisions* via this parameter, not per-batch, so two consecutive
+        schedule() calls cannot exceed the window cap together (e.g. 4+4 under
+        max_attempts=4).
+        """
         if sequence < 0:
             raise SchedulerError("sequence must be non-negative")
+        if window_attempts_used < 0:
+            raise SchedulerError("window_attempts_used must be non-negative")
 
         # F5: round-trip re-validate the descriptor from its own JSON dump so
         # a stale in-process hash (model_copy tampering) fails closed before
@@ -383,6 +395,17 @@ class PopulationScheduler:
             )
 
         dsl = policy.policy
+        # F-final-1: the policy's own stop-rule attempt cap is enforced
+        # cumulatively across the window. remaining_window is the budget left
+        # for THIS and later decisions; fail closed when it is exhausted.
+        remaining_window = dsl.stop_rules.max_attempts - window_attempts_used
+        if remaining_window <= 0:
+            raise SchedulerError(
+                "window attempt budget is exhausted (max_attempts="
+                f"{dsl.stop_rules.max_attempts}, window_attempts_used="
+                f"{window_attempts_used}); cannot schedule without over-"
+                "allocating the policy window"
+            )
         remaining = dict(descriptor.remaining_budget)
         if "attempts" not in remaining:
             raise SchedulerError(
@@ -396,14 +419,15 @@ class PopulationScheduler:
             )
 
         # width / concurrency respect DSL maxima, the campaign budget, the
-        # policy's own stop-rule attempt cap (F2), and the descriptor's
+        # policy's own per-window stop-rule attempt cap (F2 + F-final-1: capped
+        # by remaining_window, not max_attempts alone), and the descriptor's
         # remaining attempts budget — never over-allocate.
         width = min(
             dsl.max_width,
             dsl.max_concurrency,
             self._budgets.max_concurrency,
             remaining_attempts,
-            dsl.stop_rules.max_attempts,
+            remaining_window,
         )
         if width < 1:
             raise SchedulerError("scheduler width is non-positive after budget caps")
diff --git a/tests/adversarial/test_discovery_policy_boundaries.py b/tests/adversarial/test_discovery_policy_boundaries.py
index ea24e7b..6e5fca7 100644
--- a/tests/adversarial/test_discovery_policy_boundaries.py
+++ b/tests/adversarial/test_discovery_policy_boundaries.py
@@ -103,7 +103,7 @@ def _search_decision_receipt() -> SearchDecisionReceipt:
         make_sched_snapshot(),
         campaign_budgets=make_budgets(),
     )
-    return scheduler.schedule(descriptor, sequence=1)[0].receipt
+    return scheduler.schedule(descriptor, sequence=1, window_attempts_used=0)[0].receipt
 
 
 def _without_self_hash(model):
@@ -366,7 +366,7 @@ def test_scheduler_receipt_binds_policy_descriptor_and_rejects_tampering():
     receipt = PopulationScheduler(
         snapshot,
         campaign_budgets=make_budgets(),
-    ).schedule(descriptor, sequence=2)[0].receipt
+    ).schedule(descriptor, sequence=2, window_attempts_used=0)[0].receipt
 
     assert receipt.descriptor_hash == descriptor.descriptor_hash
     assert receipt.policy_hash == snapshot.policy_hash
@@ -396,7 +396,7 @@ def test_tamper_sweep_over_real_schedule_heartbeat_policy_pipeline():
     root = make_parent()
 
     scheduler = PopulationScheduler(root, campaign_budgets=make_budgets())
-    spawn = scheduler.schedule(descriptor, sequence=1)[0]
+    spawn = scheduler.schedule(descriptor, sequence=1, window_attempts_used=0)[0]
 
     hub = make_real_hub(project_id="meta-harness")
     action = make_heartbeat_action(
@@ -474,6 +474,99 @@ def test_forged_receipt_or_activation_with_mismatched_parent_hash_is_rejected():
         PolicyActivationReceipt(**activation_payload)
 
 
+def test_forged_non_root_activation_with_no_parent_is_rejected():
+    receipts = make_passed_validation_receipts()
+
+    receipts_with_no_parent = tuple(
+        receipt.model_copy(update={"parent_policy_hash": None})
+        if receipt is receipts[index]
+        else receipt
+        for index in range(len(receipts))
+        for receipt in [receipts[index]]
+    )
+    forged = tuple(
+        receipt.model_copy(update={"parent_policy_hash": None})
+        for receipt in receipts
+    )
+    with pytest.raises(ValidationError):
+        PolicyActivationReceipt(
+            policy_hash=receipts[0].policy_hash,
+            parent_policy_hash=None,
+            validation_receipts=forged,
+            validation_receipt_hashes=[
+                receipt.receipt_hash for receipt in forged
+            ],
+            activated_for_window="window-1",
+            activated_sequence=1,
+            actor_label="policy-validation-gate",
+            is_root=False,
+        )
+
+    non_root_no_parent = [
+        receipt.model_copy(update={"parent_policy_hash": None})
+        if receipt is receipts[1] else receipt
+        for receipt in receipts
+    ]
+    with pytest.raises(ValidationError):
+        PolicyActivationReceipt(
+            policy_hash=receipts[0].policy_hash,
+            parent_policy_hash=receipts[0].parent_policy_hash,
+            validation_receipts=non_root_no_parent,
+            validation_receipt_hashes=[
+                receipt.receipt_hash for receipt in non_root_no_parent
+            ],
+            activated_for_window="window-1",
+            activated_sequence=1,
+            actor_label="policy-validation-gate",
+            is_root=False,
+        )
+
+    non_root_mismatched = [
+        receipt.model_copy(update={"parent_policy_hash": receipts[0].policy_hash})
+        if receipt is receipts[1] else receipt
+        for receipt in receipts
+    ]
+    with pytest.raises(ValidationError):
+        PolicyActivationReceipt(
+            policy_hash=receipts[0].policy_hash,
+            parent_policy_hash=receipts[0].parent_policy_hash,
+            validation_receipts=non_root_mismatched,
+            validation_receipt_hashes=[
+                receipt.receipt_hash for receipt in non_root_mismatched
+            ],
+            activated_for_window="window-1",
+            activated_sequence=1,
+            actor_label="policy-validation-gate",
+            is_root=False,
+        )
+
+
+def test_root_activation_is_the_only_is_root_receipt_and_consider_emits_non_root():
+    parent = make_parent()
+    evolver = SearchPolicyEvolver(parent)
+    root_receipt = evolver.root_activation
+
+    assert root_receipt.is_root is True
+    assert root_receipt.parent_policy_hash is None
+    assert root_receipt.activated_sequence == 0
+    assert root_receipt.policy_hash == parent.policy_hash
+    assert evolver.activation_receipts == ()
+
+    child = evolver.propose_child(
+        make_policy(inspiration_selector=InspirationSelector.NONE),
+        window_id="window-1",
+        sequence=1,
+    )
+    row = evolver.consider(child, window=make_policy_descriptor(), sequence=1)
+    assert row.outcome is StrategyHistoryOutcome.ACTIVATED
+
+    activated = evolver.activation_receipts[0]
+    assert activated.is_root is False
+    assert activated.parent_policy_hash == parent.policy_hash
+    assert activated.policy_hash == child.policy_hash
+    assert activated.activated_sequence == 1
+
+
 def test_baseline_with_positive_diversity_floor_full_pipeline_activates():
     parent = make_parent()
     evolver = SearchPolicyEvolver(parent)
diff --git a/tests/test_discovery_heartbeats.py b/tests/test_discovery_heartbeats.py
index 25c0728..02a24b1 100644
--- a/tests/test_discovery_heartbeats.py
+++ b/tests/test_discovery_heartbeats.py
@@ -601,3 +601,56 @@ def test_heartbeat_f10_engine_uses_explicit_project_id_for_appends():
     descriptor = make_descriptor(steps_since_meaningful_improvement=4)
     with pytest.raises(HeartbeatError):
         engine.evaluate(descriptor, sequence=5, last_fired={})
+
+
+# ---------------------------------------------------------------------------
+# META-8 final fix-round regression (ITEM 2).
+# ---------------------------------------------------------------------------
+
+
+def test_heartbeat_final2_rejects_duplicate_action_ids_at_construction():
+    # Two distinct actions sharing an action_id would collide on the
+    # (campaign_id, action_id, sequence) idempotency key, silently treating
+    # the second artifact as a replay of the first (a deterministic-order-
+    # dependent loss of evidence). Construction must fail closed.
+    action_a = make_action(
+        action_id="hb-dupe",
+        kind=HeartbeatKind.REFLECTION,
+        trigger=HeartbeatTrigger.PLATEAU,
+        improvement_epsilon=0.5,
+        cooldown_sequences=1,
+    )
+    action_b = make_action(
+        action_id="hb-dupe",
+        kind=HeartbeatKind.CONSOLIDATION,
+        trigger=HeartbeatTrigger.EVALUATION,
+        cooldown_sequences=1,
+    )
+    with pytest.raises(HeartbeatError):
+        make_engine((action_a, action_b))
+
+    # Positive control: distinct action_ids are accepted.
+    action_b_distinct = make_action(
+        action_id="hb-other",
+        kind=HeartbeatKind.CONSOLIDATION,
+        trigger=HeartbeatTrigger.EVALUATION,
+        cooldown_sequences=1,
+    )
+    engine = make_engine((action_a, action_b_distinct))
+    assert {a.action_id for a in engine.actions} == {"hb-dupe", "hb-other"}
+
+
+def test_heartbeat_final2_duplicate_action_id_rejected_even_after_hash_revalidate():
+    # The duplicate check runs AFTER per-action hash revalidation (F9), so a
+    # tampered action is still caught as a HeartbeatError — but two valid,
+    # distinct actions with the same action_id are rejected by ITEM 2, not
+    # silently coalesced.
+    base = make_action(action_id="hb-shared", improvement_epsilon=0.5)
+    twin_fields = {
+        k: v for k, v in base.model_dump(mode="json").items() if k != "action_hash"
+    }
+    twin_fields["kind"] = HeartbeatKind.CONSOLIDATION
+    twin = HeartbeatAction(**twin_fields)
+    assert twin.action_hash != base.action_hash, "twin must be a distinct action"
+    with pytest.raises(HeartbeatError):
+        make_engine((base, twin))
diff --git a/tests/test_discovery_policy.py b/tests/test_discovery_policy.py
index accc1ae..cdd3a8e 100644
--- a/tests/test_discovery_policy.py
+++ b/tests/test_discovery_policy.py
@@ -408,6 +408,7 @@ def make_activation_receipt(**overrides) -> PolicyActivationReceipt:
         activated_for_window="window-1",
         activated_sequence=1,
         actor_label="policy-validation-gate",
+        is_root=False,
     )
     defaults.update(overrides)
     return PolicyActivationReceipt(**defaults)
diff --git a/tests/test_discovery_scheduling.py b/tests/test_discovery_scheduling.py
index 85df2df..b237c69 100644
--- a/tests/test_discovery_scheduling.py
+++ b/tests/test_discovery_scheduling.py
@@ -353,7 +353,7 @@ def _explorer_spawns(spawns: tuple[ScheduledSpawn, ...]) -> list[ScheduledSpawn]
 def test_scheduler_receipt_completeness_records_every_required_field():
     snapshot = make_sched_snapshot()
     scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
-    spawns = scheduler.schedule(make_descriptor(), sequence=1)
+    spawns = scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=0)
 
     assert spawns, "scheduler must emit at least one spawn"
     for spawn in spawns:
@@ -399,14 +399,14 @@ def test_scheduler_diversity_floor_forces_non_elite_parents_on_concentrated_desc
 
     # Not concentrated (0.3 <= 0.7): ELITE selects the frontier elite.
     relaxed = make_diverse_descriptor(concentration=0.3)
-    relaxed_spawns = scheduler.schedule(relaxed, sequence=1)
+    relaxed_spawns = scheduler.schedule(relaxed, sequence=1, window_attempts_used=0)
     relaxed_opt = _optimizer_spawns(relaxed_spawns)
     assert relaxed_opt, "expected at least one optimizer spawn"
     assert relaxed_opt[0].receipt.parent_candidate_id == "cand-elite"
 
     # Concentrated (0.95 > 0.7): forced UNDEREXPLORED picks the non-elite.
     concentrated = make_diverse_descriptor(concentration=0.95)
-    concentrated_spawns = scheduler.schedule(concentrated, sequence=1)
+    concentrated_spawns = scheduler.schedule(concentrated, sequence=1, window_attempts_used=0)
     concentrated_opt = _optimizer_spawns(concentrated_spawns)
     assert concentrated_opt, "expected at least one optimizer spawn"
     forced_parent = concentrated_opt[0].receipt.parent_candidate_id
@@ -422,17 +422,17 @@ def test_scheduler_baseline_reseed_fires_on_the_interval():
     )
     scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
 
-    reseed_at_zero = scheduler.schedule(make_descriptor(), sequence=0)
+    reseed_at_zero = scheduler.schedule(make_descriptor(), sequence=0, window_attempts_used=0)
     assert any(
         s.receipt.reason.startswith("baseline reseed") for s in reseed_at_zero
     ), "a baseline reseed must fire on a multiple of the interval"
 
-    none_at_one = scheduler.schedule(make_descriptor(), sequence=1)
+    none_at_one = scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=0)
     assert not any(
         s.receipt.reason.startswith("baseline reseed") for s in none_at_one
     ), "no baseline reseed should fire off the interval"
 
-    reseed_at_five = scheduler.schedule(make_descriptor(), sequence=5)
+    reseed_at_five = scheduler.schedule(make_descriptor(), sequence=5, window_attempts_used=0)
     assert any(
         s.receipt.reason.startswith("baseline reseed") for s in reseed_at_five
     ), "a baseline reseed must fire again on the next multiple of the interval"
@@ -443,8 +443,8 @@ def test_scheduler_is_deterministic_for_identical_inputs():
     scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
     descriptor = make_descriptor()
 
-    first = scheduler.schedule(descriptor, sequence=3)
-    second = scheduler.schedule(descriptor, sequence=3)
+    first = scheduler.schedule(descriptor, sequence=3, window_attempts_used=0)
+    second = scheduler.schedule(descriptor, sequence=3, window_attempts_used=0)
 
     assert len(first) == len(second)
     for a, b in zip(first, second):
@@ -460,7 +460,7 @@ def test_scheduler_over_budget_allocation_is_rejected_and_never_over_allocates()
     # Fully exhausted attempts budget -> scheduling is rejected outright.
     exhausted = make_descriptor(remaining_budget={"attempts": 0.0})
     with pytest.raises(SchedulerError):
-        scheduler.schedule(exhausted, sequence=1)
+        scheduler.schedule(exhausted, sequence=1, window_attempts_used=0)
 
     # Partial budget caps width to the remaining attempts (never over).
     cap_snapshot = make_sched_snapshot(
@@ -470,14 +470,14 @@ def test_scheduler_over_budget_allocation_is_rejected_and_never_over_allocates()
         cap_snapshot, campaign_budgets=make_budgets(max_concurrency=10)
     )
     partial = make_descriptor(remaining_budget={"attempts": 2.0})
-    spawns = cap_scheduler.schedule(partial, sequence=1)
+    spawns = cap_scheduler.schedule(partial, sequence=1, window_attempts_used=0)
     assert len(spawns) == 2, "width must be capped to remaining attempts, not exceeded"
 
 
 def test_scheduler_optimizer_needs_parent_explorer_must_not_have_parent():
     snapshot = make_sched_snapshot()
     scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
-    spawns = scheduler.schedule(make_descriptor(), sequence=1)
+    spawns = scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=0)
 
     optimizers = _optimizer_spawns(spawns)
     explorers = _explorer_spawns(spawns)
@@ -529,7 +529,7 @@ def test_scheduler_binds_policy_and_descriptor_hash_on_every_receipt():
     snapshot = make_sched_snapshot()
     scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
     descriptor = make_descriptor()
-    spawns = scheduler.schedule(descriptor, sequence=2)
+    spawns = scheduler.schedule(descriptor, sequence=2, window_attempts_used=0)
 
     assert spawns
     for spawn in spawns:
@@ -550,13 +550,13 @@ def test_scheduler_rejects_campaign_mismatch_and_tampered_policy():
     snapshot = make_sched_snapshot()
     scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
     with pytest.raises(SchedulerError):
-        scheduler.schedule(foreign, sequence=1)
+        scheduler.schedule(foreign, sequence=1, window_attempts_used=0)
 
 
 def test_scheduler_receipt_self_hash_rejects_tampering_and_authority_extras():
     snapshot = make_sched_snapshot()
     scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
-    spawn = scheduler.schedule(make_descriptor(), sequence=1)[0]
+    spawn = scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=0)[0]
 
     tampered = spawn.receipt.model_dump(mode="json")
     tampered["receipt_hash"] = "sha256:" + "9" * 64
@@ -605,7 +605,7 @@ def test_scheduler_f1_reseed_never_overruns_width_with_optimizer_only():
     scheduler = PopulationScheduler(
         snapshot, campaign_budgets=make_budgets(max_concurrency=10)
     )
-    spawns = scheduler.schedule(make_descriptor(), sequence=0)
+    spawns = scheduler.schedule(make_descriptor(), sequence=0, window_attempts_used=0)
     assert len(spawns) == 2, "reseed must occupy a slot inside width, not add width+1"
     assert any(s.receipt.reason.startswith("baseline reseed") for s in spawns)
 
@@ -626,7 +626,7 @@ def test_scheduler_f2_stop_rules_max_attempts_caps_batch_size():
         snapshot, campaign_budgets=make_budgets(max_concurrency=10)
     )
     descriptor = make_descriptor(remaining_budget={"attempts": 100.0})
-    spawns = scheduler.schedule(descriptor, sequence=1)
+    spawns = scheduler.schedule(descriptor, sequence=1, window_attempts_used=0)
     assert len(spawns) <= 4
     assert len(spawns) == 4
 
@@ -675,7 +675,7 @@ def test_scheduler_f3_emitted_batch_respects_diversity_floor():
         parent_selection_concentration=0.5,
         lineage_depth=1,
     )
-    spawns = scheduler.schedule(descriptor, sequence=1)
+    spawns = scheduler.schedule(descriptor, sequence=1, window_attempts_used=0)
     assert len(spawns) == 2
     assert _batch_parent_concentration(spawns) <= 0.5 + 1e-12
 
@@ -697,7 +697,7 @@ def test_scheduler_f3_degrades_single_optimizer_under_high_floor():
         snapshot, campaign_budgets=make_budgets(max_concurrency=10)
     )
     descriptor = make_descriptor(lineage_depth=1)
-    spawns = scheduler.schedule(descriptor, sequence=1)
+    spawns = scheduler.schedule(descriptor, sequence=1, window_attempts_used=0)
     assert len(spawns) == 1
     assert spawns[0].receipt.role is DiscoveryRole.EXPLORER
     assert spawns[0].receipt.parent_lineage_id is None
@@ -711,14 +711,14 @@ def test_scheduler_f4_scheduled_spawn_rejects_foreign_campaign_pairing():
     scheduler_one = PopulationScheduler(
         snapshot_one, campaign_budgets=make_budgets()
     )
-    spawn_one = scheduler_one.schedule(make_descriptor(), sequence=1)[0]
+    spawn_one = scheduler_one.schedule(make_descriptor(), sequence=1, window_attempts_used=0)[0]
 
     snapshot_two = make_sched_snapshot(campaign_id="campaign-2")
     scheduler_two = PopulationScheduler(
         snapshot_two, campaign_budgets=make_budgets()
     )
     desc_two = make_descriptor(campaign_id="campaign-2")
-    spawn_two = scheduler_two.schedule(desc_two, sequence=1)[0]
+    spawn_two = scheduler_two.schedule(desc_two, sequence=1, window_attempts_used=0)[0]
 
     with pytest.raises(ValidationError):
         ScheduledSpawn(assignment=spawn_two.assignment, receipt=spawn_one.receipt)
@@ -732,7 +732,7 @@ def test_scheduler_f4_scheduled_spawn_rejects_foreign_campaign_pairing():
 def test_scheduler_f4_receipt_carries_assignment_hash_binding():
     snapshot = make_sched_snapshot()
     scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
-    spawn = scheduler.schedule(make_descriptor(), sequence=1)[0]
+    spawn = scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=0)[0]
     # F4: every receipt binds the paired assignment hash.
     assert spawn.receipt.assignment_hash == spawn.assignment.assignment_hash
     assert spawn.receipt.assignment_hash.startswith("sha256:")
@@ -748,7 +748,7 @@ def test_scheduler_f5_rejects_stale_hash_descriptor_from_model_copy():
         update={"remaining_budget": (("attempts", 1.0),)}
     )
     with pytest.raises(SchedulerError):
-        scheduler.schedule(tampered, sequence=1)
+        scheduler.schedule(tampered, sequence=1, window_attempts_used=0)
 
 
 def test_scheduler_f6_optimizer_build_without_parent_raises_not_assert():
@@ -775,3 +775,67 @@ def test_scheduler_f6_optimizer_build_without_parent_raises_not_assert():
             expected_gain=0.5,
             reason="optimizer without parent",
         )
+
+
+# ---------------------------------------------------------------------------
+# META-8 final fix-round regressions.
+# ---------------------------------------------------------------------------
+
+
+def test_scheduler_final1_window_attempts_used_enforced_cumulatively():
+    # max_attempts=4: two consecutive decisions must not emit 4+4 spawns.
+    # The caller-managed window_attempts_used accumulator caps the second
+    # decision by what the first already emitted, then fails closed once the
+    # window is exhausted.
+    snapshot = make_sched_snapshot(
+        policy_overrides={
+            "max_width": 10,
+            "max_concurrency": 10,
+            "stop_rules": SearchPolicyStopRules(
+                max_attempts=4, max_cost=50.0, stagnation_window=3
+            ),
+        }
+    )
+    scheduler = PopulationScheduler(
+        snapshot, campaign_budgets=make_budgets(max_concurrency=10)
+    )
+    descriptor = make_descriptor(remaining_budget={"attempts": 100.0})
+
+    first = scheduler.schedule(
+        descriptor, sequence=1, window_attempts_used=0
+    )
+    assert len(first) <= 4, "first decision must respect max_attempts=4"
+    assert len(first) == 4
+
+    # Second decision with the first batch already counted: only 0 attempts
+    # remain in the window, so scheduling must fail closed (no over-allocation).
+    with pytest.raises(SchedulerError):
+        scheduler.schedule(
+            descriptor, sequence=2, window_attempts_used=4
+        )
+
+
+def test_scheduler_final1_window_attempts_used_caps_partial_remaining():
+    # max_attempts=4 with 3 already used -> only 1 spawn remains in the window.
+    snapshot = make_sched_snapshot(
+        policy_overrides={
+            "max_width": 10,
+            "max_concurrency": 10,
+            "stop_rules": SearchPolicyStopRules(
+                max_attempts=4, max_cost=50.0, stagnation_window=3
+            ),
+        }
+    )
+    scheduler = PopulationScheduler(
+        snapshot, campaign_budgets=make_budgets(max_concurrency=10)
+    )
+    descriptor = make_descriptor(remaining_budget={"attempts": 100.0})
+    spawns = scheduler.schedule(descriptor, sequence=2, window_attempts_used=3)
+    assert len(spawns) == 1
+
+
+def test_scheduler_final1_rejects_negative_window_attempts_used():
+    snapshot = make_sched_snapshot()
+    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
+    with pytest.raises(SchedulerError):
+        scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=-1)
```
