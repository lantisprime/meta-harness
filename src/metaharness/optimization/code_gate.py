"""Deterministic, LLM-free validation of a proposed CODE artifact — the
code-space counterpart to the pydantic interface gate in params.py.

The Meta-Harness paper (arXiv 2603.28052) guards code-space search with a trio
of deterministic checks that never invoke the model:

- **interface validation** — does the artifact present the required contract
  (a `.py` module exposing a callable `build(base) -> Runner`)? This mirrors the
  pydantic bounds that gate the KNOB surface: a proposal that doesn't parse into
  the interface is rejected loudly, never silently evaluated.
- **edit-scope containment** — does the artifact stay inside the sandboxed edit
  surface (the ledger root), or does it try to escape via an absolute path,
  `..`, or a symlink? The paper constrains the proposer's edits to a bounded
  scope; we enforce that with realpath containment.
- **decontamination** — does the source smuggle a held-out task's expected
  answer? The paper holds the test set out until final frontier evaluation; a
  module that hard-codes a holdout answer would poison that gate, so any such
  overlap is rejected.

This is parallel to — not a replacement for — the HarnessParams pydantic gate:
that one validates the knob surface, this one validates the code surface. A
candidate that fails here is recorded as a rejected candidate with a precise
reason (the string becomes `Candidate.rejected_reason`), exactly like a pydantic
ValidationError on a bad delta.

The interface check runs in a TIMEOUT-BOUND SUBPROCESS: a candidate module that
hangs or segfaults on import must not take the optimizer down with it, so import
happens in a child process we can kill. We do NOT sandbox execution beyond that
timeout — a code artifact runs at the same trust level as the built-in
enrichment stack (documented decision); the gate is about interface, scope, and
decontamination, not about defending against a hostile payload.
"""
from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from pydantic import BaseModel, ValidationError

from metaharness.core.types import Task
from metaharness.optimization.params import HarnessParams

# A code artifact is a small wrapper module, not a program — cap its size so a
# runaway or binary blob can't be read/hashed/imported as "code".
MAX_CODE_BYTES = 64 * 1024
# Holdout answers shorter than this are too common to be evidence of leakage
# (e.g. the digit "1" appears in nearly any source), so decontamination skips them.
MIN_DECON_LEN = 3
# Default import-probe timeout; overridable for tests that assert the hang path.
DEFAULT_IMPORT_TIMEOUT = 10.0

# Import probe: exit 0 iff the module at argv[1] imports AND exposes a callable
# `build`. Exit 3 distinguishes "imported but no build" from a nonzero import
# crash, so the two produce distinct rejection reasons.
_PROBE_SRC = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('_probe', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "sys.exit(0 if callable(getattr(module, 'build', None)) else 3)\n"
)


class CodeGateResult(BaseModel):
    """Outcome of the code gate. `reason` is empty on success and a precise,
    ledger-ready rejection string on failure; `code_hash` is the sha256 of the
    validated file bytes, set only on success (the loop stamps it onto params)."""

    ok: bool
    reason: str = ""
    code_hash: Optional[str] = None


def _holdout_answers(holdout_tasks: Sequence[Task]) -> list[str]:
    """Expected `{"equals": ...}` answers, as strings, long enough to be
    evidence of leakage. Only `equals` checks carry a literal answer to leak."""
    answers: list[str] = []
    for task in holdout_tasks:
        check = task.success_check or {}
        if "equals" not in check:
            continue
        want = str(check["equals"])
        if len(want) >= MIN_DECON_LEN:
            answers.append(want)
    return answers


def validate_code(
    ledger_root: Path | str,
    code_ref: str,
    holdout_tasks: Sequence[Task],
    *,
    timeout: float = DEFAULT_IMPORT_TIMEOUT,
) -> CodeGateResult:
    """Validate a proposed code artifact. Checks run in order, first failure
    wins, and every failure returns a distinct, precise reason.

    Order: path shape → realpath containment → file/size → ast.parse →
    subprocess interface probe → decontamination → hash."""
    root = Path(ledger_root)

    # 1a. path shape — reuse the exact rules the HarnessParams field validator
    # enforces, so the two gates never drift.
    try:
        HarnessParams(code_ref=code_ref)
    except ValidationError as exc:
        detail = exc.errors()[0].get("msg", "invalid path")
        return CodeGateResult(ok=False, reason=f"code_ref path invalid: {detail}")

    # 1b. edit-scope containment — resolve follows symlinks, so a link that
    # points outside the root is caught here.
    target = (root / code_ref).resolve()
    if not target.is_relative_to(root.resolve()):
        return CodeGateResult(ok=False, reason=f"code_ref {code_ref!r} escapes the ledger root")

    # 1c. file exists, is a regular file, within the size cap.
    if not target.is_file():
        return CodeGateResult(ok=False, reason=f"code_ref {code_ref!r} is not a regular file")
    size = target.stat().st_size
    if size > MAX_CODE_BYTES:
        return CodeGateResult(
            ok=False,
            reason=f"code artifact is {size} bytes, over the {MAX_CODE_BYTES}-byte cap",
        )

    source = target.read_text(encoding="utf-8", errors="replace")

    # 2. it must parse as Python.
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return CodeGateResult(ok=False, reason=f"code artifact has a syntax error: {exc}")

    # 3. interface — import in a timeout-bound subprocess; a hang/segfault on
    # import is contained to the child, never the optimizer.
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE_SRC, str(target)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CodeGateResult(
            ok=False, reason=f"code artifact import exceeded the {timeout:g}s timeout"
        )
    if proc.returncode == 3:
        return CodeGateResult(
            ok=False, reason="code artifact defines no callable build(base) -> Runner"
        )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        why = tail[-1] if tail else f"exit code {proc.returncode}"
        return CodeGateResult(ok=False, reason=f"code artifact failed to import: {why}")

    # 4. decontamination — the source must not contain any holdout answer.
    for answer in _holdout_answers(holdout_tasks):
        if answer in source:
            return CodeGateResult(
                ok=False,
                reason=f"code artifact embeds a held-out answer ({answer!r}) — decontamination failed",
            )

    # 5. success — freeze the validated bytes' hash for the loop to stamp on params.
    code_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    return CodeGateResult(ok=True, code_hash=code_hash)
