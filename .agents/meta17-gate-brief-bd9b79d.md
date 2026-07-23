# META-17 Independent Frozen-Diff Review (AGENTS.md gate) — head `bd9b79d`

You are the independent frozen-diff reviewer (read-only). You MUST NOT edit,
stage, commit, or run any mutating command. Every finding requires
file-and-line evidence and a P0/P1/P2/P3 severity. Finish with an explicit
verdict: APPROVE, APPROVE-WITH-MOD, or REJECT.

## Tooling

Previous reviewers of this card were denied `git` and `bash` by the harness.
**The complete frozen diff and verbatim acceptance output are therefore inline
below.** Review from those; use `read`/`grep`/`find`/`ls` on
`/private/tmp/meta-harness-meta-17/` to inspect surrounding code. If `git`/`bash`
happen to work, corroborate — but never claim to have run something you did not.
State plainly at the end which tools you had.

## Frozen commits

- Base (last gated head, APPROVE-WITH-MOD): `cc7e7c5`
- Head (review THIS immutable commit): `bd9b79d`
- Branch: `dev/meta-17-workflow-compilation`

The inline diff is exactly `git diff cc7e7c5..bd9b79d`.

## History

This is the third gate on this card's tail:

1. `89572ff` — **APPROVE**.
2. `4a8069b` — an unreviewed "P2 tidy" adding a path-containment guard.
   Gated later: **REJECT**, one P1 — both `.resolve()` calls sat above the
   `try`, so `except OSError` was unreachable dead code and a raw OSError
   escaped `run()` unjournalled.
3. `cc7e7c5` — the consolidating fix. Gated: **APPROVE-WITH-MOD**, P1
   confirmed fixed, integration unblocked. That gate raised two P2s and two
   P3s.
4. `bd9b79d` — **this commit**, applying three of those four.

## What bd9b79d applies

- **P2-1 (journal-kind honesty).** The prior gate held that routing every
  `OSError` to `kind="executor.path-escape"` was *not defensible*, because a
  contained-but-unreadable path is not an escape attempt, and noted that
  `cc7e7c5`'s own test pinned that imprecision. Failures are now split:
  `executor.path-escape` for a containment breach only,
  `executor.path-unreadable` for a path that cannot be resolved or read.
- **P2-2 (`UnicodeDecodeError` escape).** It is a `ValueError`, not an
  `OSError`, so `read_text()` could raise it straight out of `run()`
  unjournalled. Now caught alongside `OSError`.
- **P3-2 (test predicate).** The oserror citing test gated its injected
  failure on `str(self).startswith(...)`; switched to `Path.is_relative_to`.

**Deliberately NOT applied** (out of the approved scope, tracked as
follow-up): P3-1, that the `TestAuthorError` alias is not a working
deprecation shim — the formerly public import paths now raise `ImportError`
and it has zero in-repo consumers.

## Focus areas — be adversarial about these specifically

1. **Did the restructure reintroduce the original P1?** The single `try` was
   split into two guards with unguarded code between them. Verify that
   *every* operation that can raise now sits inside a guard, and that nothing
   between them can raise unjournalled. Pay attention to
   `exec_path_resolved.exists()` — can it raise, or does it suppress errors?
2. **Is the new kind taxonomy actually honest and complete?** Is every journal
   site labelled with the kind that describes it? Is there a failure mode that
   fits neither `path-escape` nor `path-unreadable`? Is a resolve() failure
   correctly *not* an escape?
3. **Does the containment raise still work?** It now sits outside any `try`.
   Confirm the "escapes store root" path still journals and raises correctly,
   and that `RuntimeCompError` cannot be caught by either handler.
4. **Is `UnicodeDecodeError` the only non-OSError family that can escape?**
   Consider anything else `read_text()` or the surrounding code can raise —
   `LookupError` on a bad encoding name, `MemoryError`, `ValueError`
   subclasses other than the decode error.
5. **The new citing test.** `test_runtime_undecodable_source_is_journalled`
   writes `b"\xff\xfe# not decodable\n"` under the pack's `executors/` dir.
   Is that a faithful reproduction, and would it fail if the
   `UnicodeDecodeError` catch were removed? (The coordinator reports it does
   fail, with a raw `UnicodeDecodeError`, when the catch is narrowed back to
   `OSError` alone.) Do the two amended tests genuinely constrain the new
   taxonomy, including their `assert not any(... path-escape ...)` clauses?
6. **Regression risk from renaming a journal kind.** `executor.path-escape` is
   an existing evidence kind. Renaming what it covers is an observable
   contract change for anything that consumes the journal. The coordinator's
   consumer sweep is inline below — verify it independently and say whether
   any consumer, doctor check, test, or fixture outside `runtime.py` depends
   on the old behaviour.

## Charter invariants that must hold

- Bounded authority — the runtime must never widen its own read surface.
- Evaluator non-self-approval — test author identity-distinct from the
  compiler, never sees executor bytes.
- Full-fidelity evidence — refusals journalled, not silently swallowed, and
  labelled honestly.
- Honest termination — fail closed on any ambiguity.
- Reversible lineage — executors stay content-hash-bound to their procedure.

## Required output

1. Verdict: APPROVE / APPROVE-WITH-MOD / REJECT.
2. Findings with severity, `file:line` evidence, concrete failure scenario.
3. Per-invariant HOLDS/BREACHED statement.
4. Explicit statement whether `bd9b79d` regresses anything approved at
   `cc7e7c5`, and whether the original `4a8069b` P1 remains fixed.
5. Which tools you had, and what you could not verify.

---

# ACCEPTANCE COMMAND OUTPUT (run by the coordinator, verbatim)

Root suite, run separately in `/private/tmp/meta-harness-meta-17`:
`1697 passed, 2 xfailed, 733 warnings in 277.34s (0:04:37)`

Baseline deltas, same interpreter: selflearn 335 at `cc7e7c5` → 336 at
`bd9b79d`; compilation 109 → 110. Delta is exactly the one new citing test.

$ cd /private/tmp/meta-harness-meta-17/selflearn && python -m pytest tests/test_compilation.py tests/test_compilation_gate.py tests/test_compilation_runtime.py -q
........................................................................ [ 65%]
......................................                                   [100%]
110 passed in 0.26s

$ cd /private/tmp/meta-harness-meta-17/selflearn && python -m pytest -q
........................................................................ [ 85%]
................................................                         [100%]
336 passed in 1.03s

$ git -C /private/tmp/meta-harness-meta-17 diff --check cc7e7c5..bd9b79d
(clean, no output)

$ grep -rn 'path-escape|path-unreadable' across src and tests  # consumer sweep
/private/tmp/meta-harness-meta-17/selflearn/src/selflearn/compilation/runtime.py:191:        # containment breach is `executor.path-escape`, while a path that
/private/tmp/meta-harness-meta-17/selflearn/src/selflearn/compilation/runtime.py:192:        # cannot be resolved or read is `executor.path-unreadable`. Collapsing
/private/tmp/meta-harness-meta-17/selflearn/src/selflearn/compilation/runtime.py:202:            self._journal_refusal(entry_id, "executor.path-unreadable",
/private/tmp/meta-harness-meta-17/selflearn/src/selflearn/compilation/runtime.py:209:            self._journal_refusal(entry_id, "executor.path-escape",
/private/tmp/meta-harness-meta-17/selflearn/src/selflearn/compilation/runtime.py:225:            self._journal_refusal(entry_id, "executor.path-unreadable",

---

# FROZEN DIFF — `git diff cc7e7c5..bd9b79d` (complete, verbatim)

```diff
diff --git a/selflearn/src/selflearn/compilation/runtime.py b/selflearn/src/selflearn/compilation/runtime.py
index 1fa7ce9..2dfd524 100644
--- a/selflearn/src/selflearn/compilation/runtime.py
+++ b/selflearn/src/selflearn/compilation/runtime.py
@@ -186,33 +186,48 @@ class ExecutorRuntime:
         # Bounded-authority check: a tampered registry.path cannot widen the
         # read surface outside the store root.
         #
-        # Resolution, containment, and the read all sit inside ONE OSError
-        # guard, for two reasons. First, .resolve() is the operation that
-        # actually raises (ELOOP, or EACCES on a /proc/<pid>/root component);
-        # leaving it above the try made the handler unreachable and let a raw
-        # OSError escape run() unjournalled. Second, checking one path object
-        # and then reading a different one is a check/use split -- the read
-        # must come from the same resolved path that was validated.
+        # Every operation that can fail sits inside a guard, and each failure
+        # is journalled under the kind that honestly describes it: a
+        # containment breach is `executor.path-escape`, while a path that
+        # cannot be resolved or read is `executor.path-unreadable`. Collapsing
+        # both into one kind would record a mere read failure as an escape
+        # attempt, corrupting the evidence taxonomy for any consumer that
+        # buckets by kind. The read comes from the same resolved path that was
+        # validated, so there is no check/use split.
         try:
             store_root_resolved = Path(self.store.root).resolve()
             exec_path_resolved = exec_path.resolve()
-            if not exec_path_resolved.is_relative_to(store_root_resolved):
-                self._journal_refusal(entry_id, "executor.path-escape",
-                                     f"executor path {active.path!r} escapes store root",
-                                     now=now)
-                raise RuntimeCompError(
-                    f"Executor path {active.path!r} escapes store root")
-            if not exec_path_resolved.exists():
-                raise RuntimeCompError(
-                    f"Executor source missing: {active.path}")
-            source = exec_path_resolved.read_text()
+            contained = exec_path_resolved.is_relative_to(store_root_resolved)
         except OSError as exc:
-            self._journal_refusal(entry_id, "executor.path-escape",
-                                 f"cannot validate executor path {active.path!r}: {exc}",
+            self._journal_refusal(entry_id, "executor.path-unreadable",
+                                 f"cannot resolve executor path {active.path!r}: {exc}",
                                  now=now)
             raise RuntimeCompError(
                 f"Cannot validate executor path {active.path!r}") from exc

+        if not contained:
+            self._journal_refusal(entry_id, "executor.path-escape",
+                                 f"executor path {active.path!r} escapes store root",
+                                 now=now)
+            raise RuntimeCompError(
+                f"Executor path {active.path!r} escapes store root")
+
+        if not exec_path_resolved.exists():
+            raise RuntimeCompError(f"Executor source missing: {active.path}")
+
+        # UnicodeDecodeError is a ValueError, not an OSError, so it needs
+        # catching explicitly: a locale-drifted or corrupted executor file
+        # would otherwise escape run() raw and unjournalled -- the same class
+        # of breach as the resolve() gap, in a different exception family.
+        try:
+            source = exec_path_resolved.read_text()
+        except (OSError, UnicodeDecodeError) as exc:
+            self._journal_refusal(entry_id, "executor.path-unreadable",
+                                 f"cannot read executor path {active.path!r}: {exc}",
+                                 now=now)
+            raise RuntimeCompError(
+                f"Cannot read executor path {active.path!r}") from exc
+
         import hashlib
         actual_hash = hashlib.sha256(source.encode()).hexdigest()
         if actual_hash != active.executor_hash:
diff --git a/selflearn/tests/test_compilation_runtime.py b/selflearn/tests/test_compilation_runtime.py
index 862d3fd..109ab32 100644
--- a/selflearn/tests/test_compilation_runtime.py
+++ b/selflearn/tests/test_compilation_runtime.py
@@ -1209,8 +1209,10 @@ def test_runtime_path_validation_oserror_is_journalled(monkeypatch):

         def _refuse_resolve(self, *args, **kwargs):
             # Only executor-path validation is made to fail; unrelated
-            # resolution elsewhere in the call keeps working.
-            if str(self).startswith(str(store_root)):
+            # resolution elsewhere in the call keeps working. Uses
+            # is_relative_to, not str.startswith -- a prefix check would also
+            # match a sibling directory sharing the tempdir name prefix.
+            if self.is_relative_to(store_root):
                 raise PermissionError(13, "Permission denied")
             return real_resolve(self, *args, **kwargs)

@@ -1221,7 +1223,10 @@ def test_runtime_path_validation_oserror_is_journalled(monkeypatch):
                        task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                        now="2024-01-01T00:00:00Z")

-        assert any(e["kind"] == "executor.path-escape" for e in provenance.events)
+        # A resolution failure is NOT an escape attempt: it must be journalled
+        # as unreadable, and must not pollute the path-escape bucket.
+        assert any(e["kind"] == "executor.path-unreadable" for e in provenance.events)
+        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)


 def test_runtime_unreadable_contained_path_is_journalled():
@@ -1229,7 +1234,9 @@ def test_runtime_unreadable_contained_path_is_journalled():

     ``active.path == "."`` resolves to the store root itself, which is contained
     and exists, so it passes containment and then raises IsADirectoryError on
-    read. That OSError must be journalled and normalized, not surfaced raw.
+    read. That OSError must be journalled and normalized, not surfaced raw --
+    and journalled as *unreadable*, not as an escape attempt, since the path
+    never left the store root.
     """
     with tempfile.TemporaryDirectory() as tmpdir:
         store_root = Path(tmpdir)
@@ -1262,12 +1269,69 @@ def test_runtime_unreadable_contained_path_is_journalled():

         runtime = ExecutorRuntime(registry, store, provenance, clock)

-        with pytest.raises(RuntimeCompError, match="Cannot validate executor path"):
+        with pytest.raises(RuntimeCompError, match="Cannot read executor path"):
             runtime.run("wf-001", task_id="t1", topic="test",
                        task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                        now="2024-01-01T00:00:00Z")

-        assert any(e["kind"] == "executor.path-escape" for e in provenance.events)
+        assert any(e["kind"] == "executor.path-unreadable" for e in provenance.events)
+        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)
+
+
+def test_runtime_undecodable_source_is_journalled():
+    """An executor file that cannot be decoded fails closed, not raw.
+
+    ``read_text()`` raises UnicodeDecodeError, which is a ValueError and NOT an
+    OSError, so it needs catching explicitly. Reachable through locale drift
+    (executor written under UTF-8, run under a C/POSIX-locale runtime) or a
+    corrupted file on disk.
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
+        # A contained, existing executor file holding bytes that are not valid
+        # UTF-8. Placed under the pack's own executors/ dir, matching the
+        # layout ExecutorRegistry.write_candidate produces, so PackStore does
+        # not mistake a bare top-level directory for a pack.
+        exec_rel = f"{pack}/executors/wf-001/deadbeef.py"
+        exec_abs = store_root / exec_rel
+        exec_abs.parent.mkdir(parents=True, exist_ok=True)
+        exec_abs.write_bytes(b"\xff\xfe# not decodable\n")
+
+        spec_hash = canonical_procedure_hash(procedure)
+        record = ExecutorRecord(
+            record_id="",
+            entry_id="wf-001",
+            pack=pack,
+            spec_hash=spec_hash,
+            executor_hash="b" * 64,
+            status="active",
+            path=exec_rel,
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
+        with pytest.raises(RuntimeCompError, match="Cannot read executor path"):
+            runtime.run("wf-001", task_id="t1", topic="test",
+                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
+                       now="2024-01-01T00:00:00Z")
+
+        assert any(e["kind"] == "executor.path-unreadable" for e in provenance.events)


 # =============================================================================
```
