# META-17 Independent Frozen-Diff Review (AGENTS.md gate) — head `cc7e7c5`

You are the independent frozen-diff reviewer (read-only). You MUST NOT edit,
stage, commit, or run any mutating command. Every finding requires
file-and-line evidence and a P0/P1/P2/P3 severity. Finish with an explicit
verdict: APPROVE, APPROVE-WITH-MOD, or REJECT.

## Read this first — tooling

A previous reviewer of this card was denied git and all bash execution by the
harness and could not diff or run anything. **You are therefore given the
complete frozen diff and the verbatim acceptance-command output inline, below.**
Review from those. If your own `git`/`bash` happen to work, use them to
corroborate — but nothing in this brief depends on that, and you must not
claim you ran something you did not. If a tool is denied, say so and proceed
from the inline material.

Files are readable at `/private/tmp/meta-harness-meta-17/`; use `read`/`grep`
freely to inspect surrounding code the diff does not show.

## Frozen commits

- Base (last APPROVED head): `89572ff`
- Head (review THIS immutable commit): `cc7e7c5`
- Branch: `dev/meta-17-workflow-compilation`, worktree `/private/tmp/meta-harness-meta-17`

The inline diff below is exactly `git diff 89572ff..cc7e7c5`.

## History — what happened and why this is the frozen range

1. `89572ff` was **APPROVED** by a GLM-5.2 gate (0 P0, 0 P1, 4 P2, 5 P3).
2. `4a8069b` was then committed as a "P2 tidy" and was never separately
   reviewed. It added a path-containment guard plus a `TestAuthorError` →
   `WorkflowTestAuthorError` rename.
3. A gate on `4a8069b` returned **REJECT** on one P1: both `.resolve()` calls
   sat above the `try`, so the `except OSError` handler was unreachable dead
   code and a raw OSError could escape `run()` unjournalled. It also raised a
   P2 (TOCTOU: containment checked on `exec_path_resolved`, read performed on
   the unresolved `exec_path`) and a P3 (contained-but-unreadable path, e.g.
   `active.path == "."`, surfacing a raw `IsADirectoryError`).
4. `cc7e7c5` is the fix, applying the reviewer's recommended consolidating
   change covering all three (A + B + F).

The review range is `89572ff..cc7e7c5` — the last APPROVED head to now — so
the previously unreviewed tidy **and** its fix are both in scope. Do not
re-litigate anything settled at or before `89572ff`.

## What cc7e7c5 claims

Resolution, containment, existence and the read now sit inside ONE
`try/except OSError`, and the read comes from `exec_path_resolved` — the same
path object that was validated — rather than from the unresolved `exec_path`.
Two citing tests were added, both verified failing before the fix.

## Focus areas — be adversarial about these specifically

1. **Is the P1 actually fixed, and completely?** Can any operation in the new
   block still raise an OSError that escapes unjournalled or un-normalized?
   Consider `Path(self.store.root).resolve()`, `exec_path.resolve()`,
   `is_relative_to`, `exists()`, `read_text()` — and anything *after* the
   block that touches the filesystem.
2. **Does the `except OSError` now over-catch?** `RuntimeCompError` extends
   `RuntimeError`, so the guard's own raises should pass through. Verify that
   — and verify the "escapes store root" and "Executor source missing" raises
   still surface with their original messages and are not swallowed or
   relabelled as "Cannot validate executor path".
3. **Journal-kind honesty.** Every OSError in the block is journalled as
   `executor.path-escape`. For a containment failure that is accurate; for
   `IsADirectoryError` on a *contained* path it arguably is not. Is one reason
   code defensible here, or does it corrupt the evidence record by labelling a
   read failure as an escape attempt? State a clear position.
4. **Is the TOCTOU actually closed?** The read is now from
   `exec_path_resolved`. Does any residual check/use split remain between the
   containment check and the hash comparison?
5. **The two new tests.** `test_runtime_path_validation_oserror_is_journalled`
   monkeypatches `Path.resolve` to raise `PermissionError` for paths under the
   store root. Is that a faithful simulation or does it also intercept
   resolution the production path would not perform — i.e. does it prove the
   real defect, or a mock artifact? Would each test fail if the fix were
   reverted? (The coordinator reports both failed before the fix, the second
   with a raw `IsADirectoryError`; assess whether that is the right failure.)
6. **The rename and its alias.** `TestAuthorError = WorkflowTestAuthorError`
   is kept outside `__all__`. Confirm the rename is complete in shipped code
   and assess whether the alias is coherent deprecation or dead code.
7. **Anything in the previously-unreviewed `4a8069b` portion** that the
   `4a8069b` gate missed — it reviewed statically without running anything.

## Charter invariants that must hold

- Bounded authority — the runtime must never widen its own read surface;
  executors stay quarantined by default.
- Evaluator non-self-approval — the test author stays identity-distinct from
  the compiler and never sees executor bytes.
- Full-fidelity evidence — refusals are journalled, not silently swallowed.
  **This is the invariant the P1 breached; confirm it now holds.**
- Honest termination — fail closed on any ambiguity.
- Reversible lineage — executors stay content-hash-bound to their procedure.

## Required output

1. Verdict: APPROVE / APPROVE-WITH-MOD / REJECT.
2. Findings with severity, `file:line` evidence, and a concrete failure
   scenario. No finding without evidence.
3. Per-invariant HOLDS/BREACHED statement.
4. Explicit statement whether `cc7e7c5` fixes the `4a8069b` P1, and whether it
   regresses anything approved at `89572ff`.
5. State plainly which tools were available to you and what you could not
   verify.

---

# ACCEPTANCE COMMAND OUTPUT (run by the coordinator, verbatim)

Root suite, run separately in `/private/tmp/meta-harness-meta-17`:
`1697 passed, 2 xfailed, 733 warnings in 258.18s (0:04:18)`

Baseline comparison, same interpreter: the selflearn suite at `4a8069b`
measured **333 passed**; at `cc7e7c5` it measures **335 passed** — a delta of
exactly the two new citing tests.

$ cd /private/tmp/meta-harness-meta-17/selflearn && python -m pytest tests/test_compilation.py tests/test_compilation_gate.py tests/test_compilation_runtime.py -q
........................................................................ [ 66%]
.....................................                                    [100%]
109 passed in 0.17s

$ cd /private/tmp/meta-harness-meta-17/selflearn && python -m pytest -q
........................................................................ [ 85%]
...............................................                          [100%]
335 passed in 0.68s

$ git -C /private/tmp/meta-harness-meta-17 diff --check 89572ff..cc7e7c5
(clean, no output)

---

# FROZEN DIFF — `git diff 89572ff..cc7e7c5` (complete, verbatim)

```diff
diff --git a/selflearn/src/selflearn/__init__.py b/selflearn/src/selflearn/__init__.py
index 81dfdac..3a932a1 100644
--- a/selflearn/src/selflearn/__init__.py
+++ b/selflearn/src/selflearn/__init__.py
@@ -95,7 +95,7 @@ from selflearn.compilation import (
     GateError,
     RegistryError,
     RuntimeCompError,
-    TestAuthorError,
+    WorkflowTestAuthorError,
     canonical_procedure_hash,
     content_hash,
 )
@@ -141,7 +141,7 @@ __all__ = [
     "GateError",
     "RegistryError",
     "RuntimeCompError",
-    "TestAuthorError",
+    "WorkflowTestAuthorError",
     "canonical_procedure_hash",
     "content_hash",
     "__version__",
diff --git a/selflearn/src/selflearn/compilation/__init__.py b/selflearn/src/selflearn/compilation/__init__.py
index fb9fb3d..3c97938 100644
--- a/selflearn/src/selflearn/compilation/__init__.py
+++ b/selflearn/src/selflearn/compilation/__init__.py
@@ -33,7 +33,7 @@ from selflearn.compilation.runtime import (
     _make_restricted_globals,
 )
 from selflearn.compilation.testgen import (
-    TestAuthorError,
+    WorkflowTestAuthorError,
     WorkflowTestAuthor,
     AUTHOR_ROLE,
 )
@@ -65,7 +65,7 @@ __all__ = [
     "_make_restricted_globals",
     "ExecutorRuntime",
     "RunResult",
-    "TestAuthorError",
+    "WorkflowTestAuthorError",
     "WorkflowTestAuthor",
     "AUTHOR_ROLE",
     "CrossValidationGate",
diff --git a/selflearn/src/selflearn/compilation/runtime.py b/selflearn/src/selflearn/compilation/runtime.py
index 60eb9fc..1fa7ce9 100644
--- a/selflearn/src/selflearn/compilation/runtime.py
+++ b/selflearn/src/selflearn/compilation/runtime.py
@@ -182,10 +182,37 @@ class ExecutorRuntime:
         # Load and verify executor source
         from pathlib import Path
         exec_path = Path(self.store.root) / active.path
-        if not exec_path.exists():
-            raise RuntimeCompError(f"Executor source missing: {active.path}")
 
-        source = exec_path.read_text()
+        # Bounded-authority check: a tampered registry.path cannot widen the
+        # read surface outside the store root.
+        #
+        # Resolution, containment, and the read all sit inside ONE OSError
+        # guard, for two reasons. First, .resolve() is the operation that
+        # actually raises (ELOOP, or EACCES on a /proc/<pid>/root component);
+        # leaving it above the try made the handler unreachable and let a raw
+        # OSError escape run() unjournalled. Second, checking one path object
+        # and then reading a different one is a check/use split -- the read
+        # must come from the same resolved path that was validated.
+        try:
+            store_root_resolved = Path(self.store.root).resolve()
+            exec_path_resolved = exec_path.resolve()
+            if not exec_path_resolved.is_relative_to(store_root_resolved):
+                self._journal_refusal(entry_id, "executor.path-escape",
+                                     f"executor path {active.path!r} escapes store root",
+                                     now=now)
+                raise RuntimeCompError(
+                    f"Executor path {active.path!r} escapes store root")
+            if not exec_path_resolved.exists():
+                raise RuntimeCompError(
+                    f"Executor source missing: {active.path}")
+            source = exec_path_resolved.read_text()
+        except OSError as exc:
+            self._journal_refusal(entry_id, "executor.path-escape",
+                                 f"cannot validate executor path {active.path!r}: {exc}",
+                                 now=now)
+            raise RuntimeCompError(
+                f"Cannot validate executor path {active.path!r}") from exc
+
         import hashlib
         actual_hash = hashlib.sha256(source.encode()).hexdigest()
         if actual_hash != active.executor_hash:
diff --git a/selflearn/src/selflearn/compilation/testgen.py b/selflearn/src/selflearn/compilation/testgen.py
index 4623368..2e31380 100644
--- a/selflearn/src/selflearn/compilation/testgen.py
+++ b/selflearn/src/selflearn/compilation/testgen.py
@@ -16,11 +16,15 @@ from selflearn.ports import IdentityPort, ModelPort, ProvenancePort
 AUTHOR_ROLE = "workflow-test-author"
 
 
-class TestAuthorError(RuntimeError):
+class WorkflowTestAuthorError(RuntimeError):
     """Error during test generation."""
     pass
 
 
+# Backward-compatible alias preserved outside the public __all__.
+TestAuthorError = WorkflowTestAuthorError
+
+
 # Marker to represent the compiler identity for distinctness check
 class _CompilerMarker:
     """Marker class representing the deterministic workflow compiler."""
@@ -40,15 +44,15 @@ class WorkflowTestAuthor:
         # Enforce identity separation - compiler model_id must be distinct
         try:
             if not identity.distinct(model, _CompilerMarker()):
-                raise TestAuthorError(
+                raise WorkflowTestAuthorError(
                     f"identity violation: test author must be distinct from "
                     f"compiler (basis: {identity.basis})")
         except Exception as exc:
-            # F2-17: convert identity-port failures into TestAuthorError,
+            # F2-17: convert identity-port failures into WorkflowTestAuthorError,
             # preserving the underlying cause.
-            if isinstance(exc, TestAuthorError):
+            if isinstance(exc, WorkflowTestAuthorError):
                 raise
-            raise TestAuthorError(
+            raise WorkflowTestAuthorError(
                 f"identity verification failed: {exc}"
             ) from exc
 
@@ -71,7 +75,7 @@ class WorkflowTestAuthor:
             IndependentTestSuite with generated test source
 
         Raises:
-            TestAuthorError: If the model returns invalid output
+            WorkflowTestAuthorError: If the model returns invalid output
         """
         # Build context - NEVER includes executor source (asserted in tests)
         context = {
@@ -96,7 +100,7 @@ class WorkflowTestAuthor:
 
         # Schema validation
         if not isinstance(plan, list) or not plan:
-            raise TestAuthorError("Test author returned no tests")
+            raise WorkflowTestAuthorError("Test author returned no tests")
 
         # FIX-5: validate test plan
         valid_kinds = {"order", "check", "approval", "failure-path"}
@@ -106,67 +110,67 @@ class WorkflowTestAuthor:
 
         for i, test in enumerate(plan):
             if not isinstance(test, dict):
-                raise TestAuthorError(f"Test #{i} is not a dict")
+                raise WorkflowTestAuthorError(f"Test #{i} is not a dict")
             kind = test.get("kind", "")
             if kind not in valid_kinds:
-                raise TestAuthorError(f"Test #{i} has invalid kind {kind!r}")
+                raise WorkflowTestAuthorError(f"Test #{i} has invalid kind {kind!r}")
 
             # FIX-5: name/step_id/expect must be strings
             name = test.get("name", "")
             step_id = test.get("step_id", "")
             expect = test.get("expect", "")
             if not isinstance(name, str):
-                raise TestAuthorError(f"Test #{i} name must be a string")
+                raise WorkflowTestAuthorError(f"Test #{i} name must be a string")
             if not isinstance(step_id, str):
-                raise TestAuthorError(f"Test #{i} step_id must be a string")
+                raise WorkflowTestAuthorError(f"Test #{i} step_id must be a string")
             if not isinstance(expect, str):
-                raise TestAuthorError(f"Test #{i} expect must be a string")
+                raise WorkflowTestAuthorError(f"Test #{i} expect must be a string")
 
             if kind == "order":
                 has_order = True
                 # F2-10: order expect may be a JSON array or comma-separated ids.
                 if not expect:
-                    raise TestAuthorError(
+                    raise WorkflowTestAuthorError(
                         f"Test #{i} (order) requires non-empty expect")
                 # Parse order; validate every id belongs to the spec.
                 try:
                     parsed = json.loads(expect)
                     if not isinstance(parsed, list):
-                        raise TestAuthorError(
+                        raise WorkflowTestAuthorError(
                             f"Test #{i} (order) expect must be a list")
                 except json.JSONDecodeError:
                     parsed = [part.strip() for part in expect.split(",")]
                 for ordered_id in parsed:
                     if ordered_id not in spec_step_ids:
-                        raise TestAuthorError(
+                        raise WorkflowTestAuthorError(
                             f"Test #{i} (order) expect step {ordered_id!r} "
                             f"not in spec")
             if kind == "approval":
                 has_approval = True
                 # F3-4: approval step_id must be non-empty and a real spec step
                 if not step_id:
-                    raise TestAuthorError(
+                    raise WorkflowTestAuthorError(
                         f"Test #{i} (approval) requires a step_id")
                 if step_id not in spec_step_ids:
-                    raise TestAuthorError(
+                    raise WorkflowTestAuthorError(
                         f"Test #{i} (approval) step_id {step_id!r} not in spec")
             if kind == "check":
                 # FIX-5: check/failure-path step_id must be in spec
                 if step_id and step_id not in spec_step_ids:
-                    raise TestAuthorError(
+                    raise WorkflowTestAuthorError(
                         f"Test #{i} step_id {step_id!r} not in spec")
             if kind == "failure-path":
                 if step_id and step_id not in spec_step_ids:
-                    raise TestAuthorError(
+                    raise WorkflowTestAuthorError(
                         f"Test #{i} step_id {step_id!r} not in spec")
 
         # Coverage floor: need order test
         if not has_order:
-            raise TestAuthorError("Test plan must include at least one 'order' test")
+            raise WorkflowTestAuthorError("Test plan must include at least one 'order' test")
 
         # Coverage floor: need approval test if spec has approval steps
         if self._has_approval_step(spec.procedure) and not has_approval:
-            raise TestAuthorError(
+            raise WorkflowTestAuthorError(
                 "Test plan must include at least one 'approval' test "
                 "since the spec has approval steps")
 
@@ -240,6 +244,7 @@ class WorkflowTestAuthor:
         # F4-2: do not emit `import json`.  The sandbox harness injects `json`
         # directly into the restricted execution namespace; keeping imports out
         # of model-rendered code closes __import__ injection paths.
+
         lines.append("def run_tests(load_executor):")
         lines.append('    """Run the test suite against an executor."""')
         lines.append("    results = []")
diff --git a/selflearn/tests/test_compilation_gate.py b/selflearn/tests/test_compilation_gate.py
index 536901a..a65a71c 100644
--- a/selflearn/tests/test_compilation_gate.py
+++ b/selflearn/tests/test_compilation_gate.py
@@ -42,7 +42,7 @@ from selflearn.compilation.gate import COMPILER_ID
 from selflearn.compilation.models import CrossValidationReceipt, ExecutorRecord
 from selflearn.compilation.registry import RegistryError
 from selflearn.compilation.runtime import _make_restricted_globals
-from selflearn.compilation.testgen import TestAuthorError, WorkflowTestAuthor
+from selflearn.compilation.testgen import WorkflowTestAuthorError, WorkflowTestAuthor
 from selflearn.contracts import EntrySource, ProcedureStep
 from selflearn.ports import ExecutionPort, ExecutionResult, IdentityPort, ProvenancePort
 from selflearn.store.packstore import PackStore
@@ -215,7 +215,7 @@ def test_test_author_rejects_compiler_identity():
 
     identity = FakeIdentity()
 
-    with pytest.raises(TestAuthorError, match="distinct from compiler"):
+    with pytest.raises(WorkflowTestAuthorError, match="distinct from compiler"):
         WorkflowTestAuthor(CompilerModel(), identity)
 
 
@@ -229,14 +229,14 @@ def test_test_author_accepts_distinct_identity():
 
 
 def test_test_author_wraps_identity_error():
-    """F2-17: identity.distinct failure becomes TestAuthorError."""
+    """F2-17: identity.distinct failure becomes WorkflowTestAuthorError."""
     class ExplodingIdentity:
         basis = "explodes"
 
         def distinct(self, a, b):
             raise RuntimeError("identity backend unavailable")
 
-    with pytest.raises(TestAuthorError, match="identity verification failed"):
+    with pytest.raises(WorkflowTestAuthorError, match="identity verification failed"):
         WorkflowTestAuthor(FakeModel(), ExplodingIdentity())
 
 
@@ -1125,7 +1125,7 @@ def test_testgen_order_expect_comma_split_renders():
 
 
 def test_testgen_nonexistent_step_id_raises():
-    """Plan referencing nonexistent step_id -> TestAuthorError."""
+    """Plan referencing nonexistent step_id -> WorkflowTestAuthorError."""
     from selflearn.compilation.testgen import WorkflowTestAuthor
 
     class BadModel:
@@ -1155,12 +1155,12 @@ def test_testgen_nonexistent_step_id_raises():
     )
 
     author = WorkflowTestAuthor(BadModel(), FakeIdentity())
-    with pytest.raises(TestAuthorError, match="nonexistent_step"):
+    with pytest.raises(WorkflowTestAuthorError, match="nonexistent_step"):
         author.author_suite(spec, authored_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())
 
 
 def test_testgen_order_without_expect_raises():
-    """Order test without expect -> TestAuthorError."""
+    """Order test without expect -> WorkflowTestAuthorError."""
     from selflearn.compilation.testgen import WorkflowTestAuthor
 
     class BadModel:
@@ -1188,7 +1188,7 @@ def test_testgen_order_without_expect_raises():
     )
 
     author = WorkflowTestAuthor(BadModel(), FakeIdentity())
-    with pytest.raises(TestAuthorError, match="non-empty expect"):
+    with pytest.raises(WorkflowTestAuthorError, match="non-empty expect"):
         author.author_suite(spec, authored_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())
 
 
@@ -1458,7 +1458,7 @@ def test_gate_activation_binds_matching_registry_record():
 # =============================================================================
 
 def test_testgen_approval_nonexistent_step_id_raises():
-    """F3-4: approval step_id not in spec -> TestAuthorError."""
+    """F3-4: approval step_id not in spec -> WorkflowTestAuthorError."""
     from selflearn.compilation.testgen import WorkflowTestAuthor
 
     class BadModel:
@@ -1488,14 +1488,14 @@ def test_testgen_approval_nonexistent_step_id_raises():
         procedure=steps,
     )
 
-    with pytest.raises(TestAuthorError, match="nonexistent"):
+    with pytest.raises(WorkflowTestAuthorError, match="nonexistent"):
         WorkflowTestAuthor(BadModel(), FakeIdentity()).author_suite(
             spec, authored_at="t", provenance=FakeProvenance()
         )
 
 
 def test_testgen_approval_empty_step_id_raises():
-    """F3-4: approval step_id empty -> TestAuthorError."""
+    """F3-4: approval step_id empty -> WorkflowTestAuthorError."""
     from selflearn.compilation.testgen import WorkflowTestAuthor
 
     class BadModel:
@@ -1525,7 +1525,7 @@ def test_testgen_approval_empty_step_id_raises():
         procedure=steps,
     )
 
-    with pytest.raises(TestAuthorError, match="requires a step_id"):
+    with pytest.raises(WorkflowTestAuthorError, match="requires a step_id"):
         WorkflowTestAuthor(BadModel(), FakeIdentity()).author_suite(
             spec, authored_at="t", provenance=FakeProvenance()
         )
diff --git a/selflearn/tests/test_compilation_runtime.py b/selflearn/tests/test_compilation_runtime.py
index 1ac5182..862d3fd 100644
--- a/selflearn/tests/test_compilation_runtime.py
+++ b/selflearn/tests/test_compilation_runtime.py
@@ -1120,6 +1120,156 @@ def test_runtime_missing_entry_journals_refusal():
         assert any(e["kind"] == "executor.unverifiable" for e in provenance.events)
 
 
+# =============================================================================
+# Final tidy: executor path-escape blocked
+# =============================================================================
+
+def test_runtime_path_escape_blocked():
+    """A tampered registry.path outside the store root is refused before read."""
+    with tempfile.TemporaryDirectory() as tmpdir:
+        store_root = Path(tmpdir)
+        pack = "test"
+        store = PackStore(store_root)
+
+        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
+        entry = _make_entry(pack=pack, procedure=procedure)
+        store.add_candidate(entry)
+
+        spec_hash = canonical_procedure_hash(procedure)
+        record = ExecutorRecord(
+            record_id="",
+            entry_id="wf-001",
+            pack=pack,
+            spec_hash=spec_hash,
+            executor_hash="b" * 64,
+            status="active",
+            path="../../etc/hostile.py",
+            receipt_id="rcpt1",
+            updated_at="2024-01-01T00:00:00Z",
+        )
+        registry = ExecutorRegistry(store_root, pack)
+        registry.add_record(record)
+
+        provenance = FakeProvenance()
+
+        def clock():
+            return datetime(2024, 1, 1, tzinfo=timezone.utc)
+
+        runtime = ExecutorRuntime(registry, store, provenance, clock)
+
+        with pytest.raises(RuntimeCompError, match="escapes store root"):
+            runtime.run("wf-001", task_id="t1", topic="test",
+                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
+                       now="2024-01-01T00:00:00Z")
+
+        assert any(e["kind"] == "executor.path-escape" for e in provenance.events)
+
+
+def test_runtime_path_validation_oserror_is_journalled(monkeypatch):
+    """A filesystem error while validating the executor path must be journalled
+    and normalized to RuntimeCompError, never surfaced as a raw OSError.
+
+    Citing test for the gate P1 on 4a8069b: the ``.resolve()`` calls sat above
+    the ``try``, so its ``except OSError`` handler was unreachable and an OSError
+    (e.g. ELOOP, or EACCES on a ``/proc/<pid>/root`` component) escaped ``run()``
+    raw and unjournalled.
+    """
+    with tempfile.TemporaryDirectory() as tmpdir:
+        store_root = Path(tmpdir)
+        pack = "test"
+        store = PackStore(store_root)
+
+        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
+        entry = _make_entry(pack=pack, procedure=procedure)
+        store.add_candidate(entry)
+
+        spec_hash = canonical_procedure_hash(procedure)
+        record = ExecutorRecord(
+            record_id="",
+            entry_id="wf-001",
+            pack=pack,
+            spec_hash=spec_hash,
+            executor_hash="b" * 64,
+            status="active",
+            path="executors/wf-001.py",
+            receipt_id="rcpt1",
+            updated_at="2024-01-01T00:00:00Z",
+        )
+        registry = ExecutorRegistry(store_root, pack)
+        registry.add_record(record)
+
+        provenance = FakeProvenance()
+
+        def clock():
+            return datetime(2024, 1, 1, tzinfo=timezone.utc)
+
+        runtime = ExecutorRuntime(registry, store, provenance, clock)
+
+        real_resolve = Path.resolve
+
+        def _refuse_resolve(self, *args, **kwargs):
+            # Only executor-path validation is made to fail; unrelated
+            # resolution elsewhere in the call keeps working.
+            if str(self).startswith(str(store_root)):
+                raise PermissionError(13, "Permission denied")
+            return real_resolve(self, *args, **kwargs)
+
+        monkeypatch.setattr(Path, "resolve", _refuse_resolve)
+
+        with pytest.raises(RuntimeCompError, match="Cannot validate executor path"):
+            runtime.run("wf-001", task_id="t1", topic="test",
+                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
+                       now="2024-01-01T00:00:00Z")
+
+        assert any(e["kind"] == "executor.path-escape" for e in provenance.events)
+
+
+def test_runtime_unreadable_contained_path_is_journalled():
+    """A contained-but-unreadable executor path fails closed as RuntimeCompError.
+
+    ``active.path == "."`` resolves to the store root itself, which is contained
+    and exists, so it passes containment and then raises IsADirectoryError on
+    read. That OSError must be journalled and normalized, not surfaced raw.
+    """
+    with tempfile.TemporaryDirectory() as tmpdir:
+        store_root = Path(tmpdir)
+        pack = "test"
+        store = PackStore(store_root)
+
+        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
+        entry = _make_entry(pack=pack, procedure=procedure)
+        store.add_candidate(entry)
+
+        spec_hash = canonical_procedure_hash(procedure)
+        record = ExecutorRecord(
+            record_id="",
+            entry_id="wf-001",
+            pack=pack,
+            spec_hash=spec_hash,
+            executor_hash="b" * 64,
+            status="active",
+            path=".",
+            receipt_id="rcpt1",
+            updated_at="2024-01-01T00:00:00Z",
+        )
+        registry = ExecutorRegistry(store_root, pack)
+        registry.add_record(record)
+
+        provenance = FakeProvenance()
+
+        def clock():
+            return datetime(2024, 1, 1, tzinfo=timezone.utc)
+
+        runtime = ExecutorRuntime(registry, store, provenance, clock)
+
+        with pytest.raises(RuntimeCompError, match="Cannot validate executor path"):
+            runtime.run("wf-001", task_id="t1", topic="test",
+                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
+                       now="2024-01-01T00:00:00Z")
+
+        assert any(e["kind"] == "executor.path-escape" for e in provenance.events)
+
+
 # =============================================================================
 # F4-12 citing test: transition refuses to operate on unknown record
 # =============================================================================
```
