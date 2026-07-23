# META-17 Independent Frozen-Diff Review (AGENTS.md gate) — head `5d2fb52`

You are the independent frozen-diff reviewer (read-only). You MUST NOT edit,
stage, commit, or run any mutating command. Every finding requires
file-and-line evidence and a P0/P1/P2/P3 severity. Finish with an explicit
verdict: APPROVE, APPROVE-WITH-MOD, or REJECT.

## Tooling

Every previous reviewer of this card was denied `git` and `bash`. **The
complete frozen diff and verbatim acceptance output are inline below**,
including an empirical probe the previous reviewer explicitly asked for but
could not run. Review from those; use `read`/`grep`/`find`/`ls` on
`/private/tmp/meta-harness-meta-17/`. Never claim to have run something you did
not. State at the end which tools you had.

## Frozen commits

- Base (last gated head): `28c4b6b`
- Head (review THIS immutable commit): `5d2fb52`
- Branch: `dev/meta-17-workflow-compilation`

The inline diff is exactly `git diff 28c4b6b..5d2fb52`.

## History — sixth gate on this card's tail

`89572ff` APPROVE → `4a8069b` **REJECT** (P1: dead `except OSError`) →
`cc7e7c5` APPROVE-WITH-MOD (P1 fixed; raised kind-honesty + `UnicodeDecodeError`)
→ `bd9b79d` APPROVE-WITH-MOD (**P1 regression**: `exists()` left unguarded by
the kind-split) → `28c4b6b` APPROVE-WITH-MOD (P1 resolved; closed the deferred
P2/P3s; surfaced a **new** pre-existing ValueError escape) → `5d2fb52`, this
head.

Two separate gates each missed a site in this block. Assume nothing is
exhaustive.

## What 5d2fb52 does

Closes the gate-5 findings.

**P2-1 — control characters in a tampered `registry.path`.** The previous
reviewer reasoned by type analysis that `resolve()`/`read_text()` raise
`ValueError` (not `OSError`) on an embedded NUL, and asked for empirical
confirmation on 3.10–3.12 which it could not perform. The probe was run (output
inline below) and **confirms the finding on the runner**, with a detail worth
noting: `resolve()` throws *before* `is_relative_to()` runs, so the containment
check was skipped entirely, not merely unjournalled.

Fixed in two layers:
1. `active.path` is validated for control characters **before** `resolve()`,
   refused under a **new kind** `executor.malformed-path` (deliberately not
   reusing `executor.path-escape`, since a malformed path is not an escape
   attempt — preserving the taxonomy discipline gate 3 established).
2. The three guards now catch `(OSError, ValueError)` so nothing in that family
   escapes unjournalled by another route.

**P2-2 — positive non-ASCII coverage.** The utf-8 pin previously had only
negative coverage. A positive citing test now proves a valid non-ASCII executor
round-trips write → read → hash → run through the real
`registry.write_candidate` path.

**P3-1 — `registry.json` I/O pinned** to utf-8 for consistency.

## Focus areas — be adversarial about these specifically

1. **Is the two-layer fix coherent, or is one layer redundant?** Argue it
   honestly. If the broadened `(OSError, ValueError)` guards alone would
   suffice, say the up-front check is redundant. If the up-front check alone
   would suffice, say the broadening is over-catching. (The author's claim is
   that both carry weight: without the up-front check the null-byte path is
   journalled but mislabelled `path-unreadable` and containment is still
   skipped. Verify or refute.)
2. **Does `except (OSError, ValueError)` now over-catch?** `ValueError` is a
   broad family. Could it swallow a genuine programming error — a bad argument,
   a malformed hash, a contract violation — and relabel it as a path problem,
   hiding a real bug behind an honest-looking refusal? This is the main risk
   the author accepted; scrutinise it.
3. **Is `ord(ch) < 32` the right predicate?** Consider DEL (0x7f), Unicode
   line separators, surrogates, and whether rejecting all C0 controls could
   refuse a legitimate path on any supported platform.
4. **Is `executor.malformed-path` the right call**, or does adding a fourth
   kind fragment the taxonomy? Check no consumer breaks.
5. **The positive round-trip test.** Does it actually prove what it claims?
   It asserts `status == "completed"` and no `executor.tampered` event. Would
   it fail if the utf-8 pin were reverted on either side? If not, it is
   decorative — say so.
6. **Read the whole block as a unit.** Six commits have now layered onto this
   ~70-line region. Is it coherent, or are there dead branches, redundant
   checks, or contradictory comments left behind?

## Charter invariants that must hold

- Bounded authority — the runtime must never widen its own read surface.
- Evaluator non-self-approval — untouched by this diff; confirm.
- Full-fidelity evidence — refusals journalled, not silently swallowed, and
  labelled honestly.
- Honest termination — fail closed on any ambiguity.
- Reversible lineage — executors stay content-hash-bound to their procedure.

## Required output

1. Verdict: APPROVE / APPROVE-WITH-MOD / REJECT.
2. Findings with severity, `file:line` evidence, concrete failure scenario.
3. Per-invariant HOLDS/BREACHED statement.
4. Explicit statement whether `5d2fb52` regresses anything, and whether the
   unjournalled-escape class is now closed for both `OSError` and `ValueError`.
5. Which tools you had, and what you could not verify.

---

# ACCEPTANCE COMMAND OUTPUT (run by the coordinator, verbatim)

Root suite, run separately in `/private/tmp/meta-harness-meta-17`:
`1697 passed, 2 xfailed, 733 warnings in 256.97s (0:04:16)`

selflearn suite: 338 at `28c4b6b` → 340 at `5d2fb52`, exactly the two new
citing tests. Both were verified failing before the fix: the control-character
test previously reported `Cannot validate executor path` via the broadened
guard instead of the honest `control characters` refusal.

$ cd /private/tmp/meta-harness-meta-17/selflearn && python -m pytest -q
........................................................................ [ 84%]
....................................................                     [100%]
340 passed in 0.66s

$ git diff --check 28c4b6b..5d2fb52
(clean, no output)

$ python  # EMPIRICAL confirmation of the gate-5 P2-1 null-byte finding
resolve()      -> ValueError: lstat: embedded null character in path  | isinstance OSError: False
exists()       -> returned False  (no raise)
read_text()    -> ValueError: embedded null byte  | isinstance OSError: False

---

# FROZEN DIFF — `git diff 28c4b6b..5d2fb52` (complete, verbatim)

```diff
diff --git a/selflearn/src/selflearn/compilation/registry.py b/selflearn/src/selflearn/compilation/registry.py
index d6f4d23..6f0b83c 100644
--- a/selflearn/src/selflearn/compilation/registry.py
+++ b/selflearn/src/selflearn/compilation/registry.py
@@ -67,9 +67,9 @@ class ExecutorRegistry:
         """
         if not self._registry_path.exists():
             self._executors_dir.mkdir(parents=True, exist_ok=True)
-            self._registry_path.write_text(json.dumps({"records": []}))
+            self._registry_path.write_text(json.dumps({"records": []}), encoding="utf-8")
         try:
-            return json.loads(self._registry_path.read_text())
+            return json.loads(self._registry_path.read_text(encoding="utf-8"))
         except (json.JSONDecodeError, IOError) as e:
             raise RegistryError(f"Registry corrupt: {e}")

@@ -77,7 +77,7 @@ class ExecutorRegistry:
         """Atomic write of registry using tmp + os.replace."""
         self._executors_dir.mkdir(parents=True, exist_ok=True)
         tmp = self._registry_path.with_suffix(".json.tmp")
-        tmp.write_text(json.dumps(data, indent=1))
+        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
         os.replace(tmp, self._registry_path)

     def record_for(self, entry_id: str, status: str | None = None) -> list[ExecutorRecord]:
@@ -89,7 +89,7 @@ class ExecutorRegistry:
         if not self._registry_path.exists():
             return []
         try:
-            data = json.loads(self._registry_path.read_text())
+            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
         except (json.JSONDecodeError, IOError) as e:
             # F2-9: raise on corrupt file, don't silently return []
             raise RegistryError(f"Registry corrupt: {e}")
diff --git a/selflearn/src/selflearn/compilation/runtime.py b/selflearn/src/selflearn/compilation/runtime.py
index 8fb35aa..c5edaf2 100644
--- a/selflearn/src/selflearn/compilation/runtime.py
+++ b/selflearn/src/selflearn/compilation/runtime.py
@@ -181,6 +181,20 @@ class ExecutorRuntime:

         # Load and verify executor source
         from pathlib import Path
+
+        # A tampered registry.path can carry control characters, notably NUL.
+        # Path.resolve() and open() raise ValueError -- NOT OSError -- on those,
+        # so such a path would slip past every OSError guard below and, worse,
+        # skip the containment check entirely: resolve() throws before
+        # is_relative_to() ever runs. Reject up front, under its own kind,
+        # since a malformed path is not an escape attempt.
+        if any(ord(ch) < 32 for ch in active.path):
+            self._journal_refusal(entry_id, "executor.malformed-path",
+                                 f"executor path {active.path!r} contains control characters",
+                                 now=now)
+            raise RuntimeCompError(
+                f"Executor path {active.path!r} contains control characters")
+
         exec_path = Path(self.store.root) / active.path

         # Bounded-authority check: a tampered registry.path cannot widen the
@@ -198,7 +212,7 @@ class ExecutorRuntime:
             store_root_resolved = Path(self.store.root).resolve()
             exec_path_resolved = exec_path.resolve()
             contained = exec_path_resolved.is_relative_to(store_root_resolved)
-        except OSError as exc:
+        except (OSError, ValueError) as exc:
             self._journal_refusal(entry_id, "executor.path-unreadable",
                                  f"cannot resolve executor path {active.path!r}: {exc}",
                                  now=now)
@@ -221,7 +235,7 @@ class ExecutorRuntime:
         # 3.13+ (where exists() delegates to os.path.exists) swallows it.
         try:
             missing = not exec_path_resolved.exists()
-        except OSError as exc:
+        except (OSError, ValueError) as exc:
             self._journal_refusal(entry_id, "executor.path-unreadable",
                                  f"cannot stat executor path {active.path!r}: {exc}",
                                  now=now)
@@ -236,13 +250,14 @@ class ExecutorRuntime:
                                  now=now)
             raise RuntimeCompError(f"Executor source missing: {active.path}")

-        # UnicodeDecodeError is a ValueError, not an OSError, so it needs
-        # catching explicitly: a locale-drifted or corrupted executor file
-        # would otherwise escape run() raw and unjournalled -- the same class
-        # of breach as the resolve() gap, in a different exception family.
+        # ValueError is caught alongside OSError throughout: UnicodeDecodeError
+        # (a corrupted executor file) and the embedded-null-byte error are both
+        # ValueError subclasses, and neither is an OSError -- so without this
+        # they escape run() raw and unjournalled, the same class of breach as
+        # the original resolve() gap in a different exception family.
         try:
             source = exec_path_resolved.read_text(encoding="utf-8")
-        except (OSError, UnicodeDecodeError) as exc:
+        except (OSError, ValueError) as exc:
             self._journal_refusal(entry_id, "executor.path-unreadable",
                                  f"cannot read executor path {active.path!r}: {exc}",
                                  now=now)
diff --git a/selflearn/tests/test_compilation_runtime.py b/selflearn/tests/test_compilation_runtime.py
index 1efc5ae..dd679ce 100644
--- a/selflearn/tests/test_compilation_runtime.py
+++ b/selflearn/tests/test_compilation_runtime.py
@@ -1278,6 +1278,126 @@ def test_runtime_unreadable_contained_path_is_journalled():
         assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)


+def test_runtime_non_ascii_executor_round_trips():
+    """A VALID non-ASCII executor must round-trip write -> read -> hash -> run.
+
+    content_hash() hashes text.encode("utf-8") explicitly, and executor source
+    I/O is now pinned to utf-8 on both sides. Non-ASCII source is reachable in
+    practice -- the compiler embeds the step objective and repr(entry_id)
+    directly -- so this proves the hash binding survives the round trip.
+    Without it the only non-ASCII coverage is the negative case (invalid bytes
+    fail closed), and a future refactor could silently break non-ASCII lineage
+    with CI still green.
+    """
+    with tempfile.TemporaryDirectory() as tmpdir:
+        store_root = Path(tmpdir)
+        pack = "test"
+        store = PackStore(store_root)
+
+        procedure = (
+            ProcedureStep(id="step1", objective="déployer le café ☕",
+                          task_type="code_edit"),
+        )
+        entry = _make_entry(pack=pack, procedure=procedure)
+        store.add_candidate(entry)
+
+        spec_hash = canonical_procedure_hash(procedure)
+
+        from selflearn.compilation.compiler import WorkflowCompiler
+        compiler = WorkflowCompiler()
+        candidate = compiler.compile(entry, pack=pack,
+                                     compiled_at="2024-01-01T00:00:00Z",
+                                     provenance=FakeProvenance())
+
+        # The fixture is only meaningful if the compiled source really carries
+        # non-ASCII bytes.
+        assert any(ord(ch) > 127 for ch in candidate.source)
+
+        registry = ExecutorRegistry(store_root, pack)
+        # Written through the real registry path, which pins utf-8.
+        registry.write_candidate(candidate)
+
+        relative_path = f"{pack}/executors/wf-001/{spec_hash}.py"
+        record = ExecutorRecord(
+            record_id="",
+            entry_id="wf-001",
+            pack=pack,
+            spec_hash=spec_hash,
+            executor_hash=candidate.executor_hash,
+            status="active",
+            path=relative_path,
+            receipt_id="rcpt1",
+            updated_at="2024-01-01T00:00:00Z",
+        )
+        registry.add_record(record)
+
+        provenance = FakeProvenance()
+
+        def clock():
+            return datetime(2024, 1, 1, tzinfo=timezone.utc)
+
+        runtime = ExecutorRuntime(registry, store, provenance, clock)
+
+        result = runtime.run("wf-001", task_id="t1", topic="test",
+                            task_type="code_edit",
+                            step_handler=lambda sid, sdata: {"status": "ok"},
+                            now="2024-01-01T00:00:00Z")
+
+        # The hash check passed, so the utf-8 write and read agreed with
+        # content_hash's utf-8 encoding.
+        assert result.status == "completed"
+        assert not any(e["kind"] == "executor.tampered" for e in provenance.events)
+
+
+def test_runtime_control_character_path_is_rejected():
+    """A registry.path carrying a control character is refused before resolve().
+
+    Citing test for the gate-5 P2-1. Path.resolve() and open() raise ValueError
+    -- not OSError -- on an embedded NUL, so such a path escaped every OSError
+    guard raw and unjournalled, and skipped the containment check entirely
+    because resolve() throws before is_relative_to() runs.
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
+            path=f"{pack}/exec\x00utors/wf-001/x.py",
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
+        with pytest.raises(RuntimeCompError, match="control characters"):
+            runtime.run("wf-001", task_id="t1", topic="test",
+                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
+                       now="2024-01-01T00:00:00Z")
+
+        # A malformed path is not an escape attempt; it gets its own kind.
+        assert any(e["kind"] == "executor.malformed-path" for e in provenance.events)
+        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)
+
+
 def test_runtime_missing_source_is_journalled():
     """A genuinely absent executor file must journal its refusal.

```
