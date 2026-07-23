# META-17 Independent Frozen-Diff Review (AGENTS.md gate) — head `28c4b6b`

You are the independent frozen-diff reviewer (read-only). You MUST NOT edit,
stage, commit, or run any mutating command. Every finding requires
file-and-line evidence and a P0/P1/P2/P3 severity. Finish with an explicit
verdict: APPROVE, APPROVE-WITH-MOD, or REJECT.

## Tooling

Every previous reviewer of this card was denied `git` and `bash` by the
harness. **The complete frozen diff and verbatim acceptance output are
therefore inline below.** Review from those; use `read`/`grep`/`find`/`ls` on
`/private/tmp/meta-harness-meta-17/` for surrounding code. Never claim to have
run something you did not. State at the end which tools you had.

## Frozen commits

- Base (last gated head): `bd9b79d`
- Head (review THIS immutable commit): `28c4b6b`
- Branch: `dev/meta-17-workflow-compilation`

The inline diff is exactly `git diff bd9b79d..28c4b6b`, spanning two commits
(`e312f68`, `28c4b6b`).

## History — this is the fifth gate on this card's tail

1. `89572ff` — APPROVE.
2. `4a8069b` — unreviewed "P2 tidy" adding a path-containment guard.
   Gated: **REJECT**, P1 — `.resolve()` calls above the `try` made
   `except OSError` unreachable; raw OSError escaped `run()` unjournalled.
3. `cc7e7c5` — consolidating fix. Gated: **APPROVE-WITH-MOD**. P1 fixed.
   Raised P2-1 (one journal kind for both escape and read failure is
   dishonest), P2-2 (`UnicodeDecodeError` escapes), P3-1 (dead alias),
   P3-2 (test predicate used `str.startswith`).
4. `bd9b79d` — applied P2-1/P2-2/P3-2. Gated: **APPROVE-WITH-MOD**, but found
   a **P1 regression**: splitting the unified `try` left
   `exec_path_resolved.exists()` outside both guards, reintroducing the
   raw-OSError-escape class. That gate offered a downgrade if the runner's
   `Path.exists()` ignored-set could be shown to swallow EACCES.
5. `28c4b6b` — **this head.** The downgrade was investigated and **rejected**
   (see below), so the guard was applied, plus the three remaining deferred
   findings.

## Why the offered P1 downgrade was refused

Evidence gathered (also inline in the acceptance block):

- **Python 3.14.4, this runner:** `Path.exists()` delegates to
  `os.path.exists()`, which catches all `OSError`/`ValueError`. Verified:
  `f.exists() -> False` while `os.stat()` on the same path raises
  `PermissionError 13`. The defect does **not** reproduce here.
- **Pre-3.13 implementation:** `Path.exists()` calls `self.stat()` and
  re-raises anything outside `_ignore_error` (ENOENT/ENOTDIR/EBADF/ELOOP).
  Verified on that implementation: `f.exists()` **RAISED** `PermissionError`
  errno 13.
- `selflearn/pyproject.toml` declares `requires-python = ">=3.10"`.

So the defect is live on 3.10, 3.11 and 3.12 — three of the four supported
minor versions — and the downgrade would have held only for the interpreter
that happens to run the tests. **Scrutinise this reasoning; if you think the
downgrade was actually correct, say so.**

## What 28c4b6b contains

1. **P1 guard** (`e312f68`) — the existence check moved into its own
   `try/except OSError`, journalling `executor.path-unreadable` and
   normalizing to `RuntimeCompError`.
2. **P2 — missing-source now journalled.** The "Executor source missing" raise
   emitted no provenance event. Now journals `executor.missing-source`, the
   same string `doctor.py` already uses for this condition.
3. **P3 — utf-8 pinned on executor source I/O.** `content_hash()` hashes
   `text.encode("utf-8")` explicitly, but the source was written and read with
   locale-dependent encoding. Both sides pinned: `registry.write_candidate`'s
   write and read-back, and the runtime read.
4. **P3 — dead `TestAuthorError` alias removed.**

## Focus areas — be adversarial about these specifically

1. **Is the raw-OSError-escape class now fully closed?** This is the third
   attempt. Walk every statement from `exec_path` construction to the hash
   comparison and identify anything that can raise outside a guard. Do not
   assume prior gates were exhaustive — two of them missed a site.
2. **Is `executor.missing-source` the right kind?** `doctor.py` uses that
   string as a Finding code. Does emitting it as a *journal kind* create a
   collision, a double-count, or a confusing signal for anything that consumes
   both? Check whether doctor reads journal events or only reports findings.
3. **Is the utf-8 pin complete and safe?** `registry.py` also does
   `read_text()`/`write_text()` on `registry.json` (`:70,:72,:80,:92`) which
   were left unpinned. Is that inconsistency defensible (json.dumps defaults to
   `ensure_ascii=True`) or a latent gap? Could the pin break reading executors
   written by an earlier version under a different locale?
4. **Does removing the alias break anything?** Confirm no in-repo consumer and
   assess out-of-tree breakage risk.
5. **Test quality.** Do `test_runtime_missing_source_is_journalled` and
   `test_runtime_stat_failure_is_journalled` genuinely constrain the new code?
   The stat test injects the error via monkeypatch rather than relying on
   interpreter behaviour — is that the right call given 3.13+ swallows it, or
   does it make the test a mock artifact that would pass even if the guard were
   wrong?
6. **Has the accumulated churn introduced anything?** Five commits have now
   touched this ~50-line block. Read it as a whole and say whether it is
   coherent or whether the layered fixes have left dead branches, redundant
   checks, or contradictory comments.

## Charter invariants that must hold

- Bounded authority — the runtime must never widen its own read surface.
- Evaluator non-self-approval — test author identity-distinct from compiler,
  never sees executor bytes.
- Full-fidelity evidence — refusals journalled, not silently swallowed, and
  labelled honestly.
- Honest termination — fail closed on any ambiguity.
- Reversible lineage — executors stay content-hash-bound to their procedure.

## Required output

1. Verdict: APPROVE / APPROVE-WITH-MOD / REJECT.
2. Findings with severity, `file:line` evidence, concrete failure scenario.
3. Per-invariant HOLDS/BREACHED statement.
4. Explicit statement whether `28c4b6b` regresses anything, and whether the
   raw-OSError-escape class is now closed.
5. Which tools you had, and what you could not verify.

---

# ACCEPTANCE COMMAND OUTPUT (run by the coordinator, verbatim)

Root suite, run separately in `/private/tmp/meta-harness-meta-17`:
`1697 passed, 2 xfailed, 733 warnings in 255.27s (0:04:15)`

selflearn suite deltas, same interpreter: 336 at `bd9b79d` → 337 at `e312f68`
→ 338 at `28c4b6b`. Each delta is exactly one new citing test.

$ cd /private/tmp/meta-harness-meta-17/selflearn && python -m pytest -q
........................................................................ [ 85%]
..................................................                       [100%]
338 passed in 0.95s

$ git -C /private/tmp/meta-harness-meta-17 diff --check bd9b79d..28c4b6b
(clean, no output)

$ grep -rn 'TestAuthorError' src/ tests/ | grep -v WorkflowTestAuthorError   # alias removal sweep
(no matches - alias fully removed)

$ python  # Path.exists() behaviour, the P1 evidence
Python 3.14.4 (this runner): f.exists() -> False (no exception); os.stat() raises PermissionError 13
Pre-3.13 implementation:     f.exists() RAISED PermissionError errno 13
selflearn/pyproject.toml:    requires-python = ">=3.10"  -> defect live on 3.10/3.11/3.12

---

# FROZEN DIFF — `git diff bd9b79d..28c4b6b` (complete, verbatim)

```diff
diff --git a/selflearn/src/selflearn/compilation/registry.py b/selflearn/src/selflearn/compilation/registry.py
index 251f3a2..d6f4d23 100644
--- a/selflearn/src/selflearn/compilation/registry.py
+++ b/selflearn/src/selflearn/compilation/registry.py
@@ -208,7 +208,7 @@ class ExecutorRegistry:
         path = entry_dir / f"{candidate.spec.spec_hash}.py"

         if path.exists():
-            existing = path.read_text()
+            existing = path.read_text(encoding="utf-8")
             if existing != candidate.source:
                 raise RegistryError(
                     f"Refusing to overwrite {path} with different content")
@@ -216,7 +216,10 @@ class ExecutorRegistry:
         else:
             # FIX-9: write source via tmp + os.replace (atomic)
             tmp = path.with_suffix(".py.tmp")
-            tmp.write_text(candidate.source)
+            # utf-8 pinned: content_hash() hashes text.encode("utf-8"),
+            # so locale-dependent file I/O would break the hash contract
+            # for any non-ASCII source under a non-utf-8 locale.
+            tmp.write_text(candidate.source, encoding="utf-8")
             os.replace(tmp, path)

         # FIX-3: add quarantined record (idempotent — skip if already present)
diff --git a/selflearn/src/selflearn/compilation/runtime.py b/selflearn/src/selflearn/compilation/runtime.py
index 2dfd524..8fb35aa 100644
--- a/selflearn/src/selflearn/compilation/runtime.py
+++ b/selflearn/src/selflearn/compilation/runtime.py
@@ -212,7 +212,28 @@ class ExecutorRuntime:
             raise RuntimeCompError(
                 f"Executor path {active.path!r} escapes store root")

-        if not exec_path_resolved.exists():
+        # exists() must be guarded too. On CPython < 3.13 Path.exists() only
+        # swallows ENOENT/ENOTDIR/EBADF/ELOOP and re-raises everything else --
+        # notably EACCES/EPERM/EIO, reachable on a contained path whose final
+        # component is stat-denied, since resolve(strict=False) need not stat
+        # the tail. This package supports >=3.10, so an unguarded call would
+        # let a raw OSError escape run() unjournalled on 3.10-3.12 even though
+        # 3.13+ (where exists() delegates to os.path.exists) swallows it.
+        try:
+            missing = not exec_path_resolved.exists()
+        except OSError as exc:
+            self._journal_refusal(entry_id, "executor.path-unreadable",
+                                 f"cannot stat executor path {active.path!r}: {exc}",
+                                 now=now)
+            raise RuntimeCompError(
+                f"Cannot read executor path {active.path!r}") from exc
+
+        if missing:
+            # Journalled like every other refusal: an aborted run must never
+            # leave a silent gap in the evidence record.
+            self._journal_refusal(entry_id, "executor.missing-source",
+                                 f"executor source missing at {active.path!r}",
+                                 now=now)
             raise RuntimeCompError(f"Executor source missing: {active.path}")

         # UnicodeDecodeError is a ValueError, not an OSError, so it needs
@@ -220,7 +241,7 @@ class ExecutorRuntime:
         # would otherwise escape run() raw and unjournalled -- the same class
         # of breach as the resolve() gap, in a different exception family.
         try:
-            source = exec_path_resolved.read_text()
+            source = exec_path_resolved.read_text(encoding="utf-8")
         except (OSError, UnicodeDecodeError) as exc:
             self._journal_refusal(entry_id, "executor.path-unreadable",
                                  f"cannot read executor path {active.path!r}: {exc}",
diff --git a/selflearn/src/selflearn/compilation/testgen.py b/selflearn/src/selflearn/compilation/testgen.py
index 2e31380..1c8cbc6 100644
--- a/selflearn/src/selflearn/compilation/testgen.py
+++ b/selflearn/src/selflearn/compilation/testgen.py
@@ -21,10 +21,6 @@ class WorkflowTestAuthorError(RuntimeError):
     pass


-# Backward-compatible alias preserved outside the public __all__.
-TestAuthorError = WorkflowTestAuthorError
-
-
 # Marker to represent the compiler identity for distinctness check
 class _CompilerMarker:
     """Marker class representing the deterministic workflow compiler."""
diff --git a/selflearn/tests/test_compilation_runtime.py b/selflearn/tests/test_compilation_runtime.py
index 109ab32..1efc5ae 100644
--- a/selflearn/tests/test_compilation_runtime.py
+++ b/selflearn/tests/test_compilation_runtime.py
@@ -1278,13 +1278,129 @@ def test_runtime_unreadable_contained_path_is_journalled():
         assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)


+def test_runtime_missing_source_is_journalled():
+    """A genuinely absent executor file must journal its refusal.
+
+    Every other refusal path emits a provenance event; this one did not, so an
+    aborted run left a silent gap in the evidence record.
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
+            # Contained and resolvable, but no such file exists.
+            path=f"{pack}/executors/wf-001/absent.py",
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
+        with pytest.raises(RuntimeCompError, match="Executor source missing"):
+            runtime.run("wf-001", task_id="t1", topic="test",
+                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
+                       now="2024-01-01T00:00:00Z")
+
+        assert any(e["kind"] == "executor.missing-source" for e in provenance.events)
+        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)
+
+
+def test_runtime_stat_failure_is_journalled(monkeypatch):
+    """An OSError from the existence check must be journalled and normalized.
+
+    Citing test for the gate P1 on bd9b79d. On CPython < 3.13 ``Path.exists()``
+    swallows only ENOENT/ENOTDIR/EBADF/ELOOP and re-raises the rest, so an
+    EACCES on a contained path escaped ``run()`` raw and unjournalled. This
+    package supports >=3.10, so the guard is required even though 3.13+
+    delegates to ``os.path.exists`` and swallows it -- which is exactly why
+    this test injects the error rather than relying on interpreter behaviour.
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
+            path=f"{pack}/executors/wf-001/deadbeef.py",
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
+        real_exists = Path.exists
+
+        def _refuse_exists(self, *args, **kwargs):
+            # Only the executor's own stat is denied; the store and registry
+            # keep working, so the failure lands on the guarded call and not
+            # somewhere earlier in run(). Matched by filename because the
+            # guarded call uses the *resolved* path, which on macOS differs
+            # from the constructed one (/var -> /private/var).
+            if self.name == "deadbeef.py":
+                raise PermissionError(13, "Permission denied")
+            return real_exists(self, *args, **kwargs)
+
+        monkeypatch.setattr(Path, "exists", _refuse_exists)
+
+        with pytest.raises(RuntimeCompError, match="Cannot read executor path"):
+            runtime.run("wf-001", task_id="t1", topic="test",
+                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
+                       now="2024-01-01T00:00:00Z")
+
+        assert any(e["kind"] == "executor.path-unreadable" for e in provenance.events)
+        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)
+
+
 def test_runtime_undecodable_source_is_journalled():
     """An executor file that cannot be decoded fails closed, not raw.

     ``read_text()`` raises UnicodeDecodeError, which is a ValueError and NOT an
-    OSError, so it needs catching explicitly. Reachable through locale drift
-    (executor written under UTF-8, run under a C/POSIX-locale runtime) or a
-    corrupted file on disk.
+    OSError, so it needs catching explicitly. Reachable through a corrupted
+    file on disk.
+
+    The read is now pinned to utf-8 (matching content_hash's explicit
+    text.encode("utf-8")), so these bytes fail to decode on every runner. Under
+    the previous locale-dependent read this test would have passed for the
+    wrong reason on a latin-1/cp1252 locale, where b"\\xff\\xfe" decodes cleanly
+    and the run would instead have hit the hash-mismatch path.
     """
     with tempfile.TemporaryDirectory() as tmpdir:
         store_root = Path(tmpdir)
```
