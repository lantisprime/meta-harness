"""Workspace evidence for the rubric judge and run packaging.

Observed live (2026-07-08, runs ec3559b/afd3ce2): workers doing real code_edit
work through file tools returned narration as their final text, and the judge —
seeing only that text — failed the step three times while router.js sat
correctly edited on disk. The judge's blind spot is the workspace; this module
is the corrective lens.

Root discipline: evidence is collected ONLY from the workspace root the runner
recorded on its result (builtin tool jail or coding-CLI cwd) — never from cwd,
never inferred. Tool-call arguments contribute path hints; disk is the
authority for content.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Optional

MAX_FILES = 8               # files shown to the judge / recorded per attempt
MAX_FILE_BYTES = 2_000      # head of each file
MAX_TOTAL_BYTES = 6_000     # whole evidence block
_SCAN_CAP = 500             # walk guard for huge workspaces
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
_MTIME_SLACK_S = 2.0        # clock slack when deriving the attempt window

_WRITE_TOOLS = {"write_file", "edit_file"}


def _hint_paths(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Relative paths named by builtin write/edit tool calls, order-preserved."""
    hints: list[str] = []
    for call in tool_calls or []:
        if call.get("tool") in _WRITE_TOOLS:
            path = (call.get("arguments") or {}).get("path")
            if isinstance(path, str) and path and path not in hints:
                hints.append(path)
    return hints


def changed_files(root: str | Path, since: float,
                  tool_calls: Optional[list[dict[str, Any]]] = None) -> list[Path]:
    """Files under `root` modified at/after `since`, plus files named by
    write/edit tool-call hints (whatever their mtime — an edit that landed
    before the window's clock slack still counts). Relative paths, sorted,
    hint paths first."""
    base = Path(root)
    if not base.is_dir():
        return []
    base = base.resolve()
    found: list[Path] = []
    seen: set[Path] = set()
    for hint in _hint_paths(tool_calls or []):
        candidate = (base / hint).resolve()
        if candidate.is_file() and base in candidate.parents and candidate not in seen:
            found.append(candidate.relative_to(base))
            seen.add(candidate)
    scanned = 0
    for p in sorted(base.rglob("*")):
        if scanned >= _SCAN_CAP:
            break
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        scanned += 1
        if not p.is_file() or p in seen:
            continue
        try:
            if p.stat().st_mtime >= since - _MTIME_SLACK_S:
                found.append(p.relative_to(base))
                seen.add(p)
        except OSError:
            continue
    return found


def collect_evidence(root: str, since: float,
                     tool_calls: Optional[list[dict[str, Any]]] = None,
                     max_files: int = MAX_FILES,
                     max_file_bytes: int = MAX_FILE_BYTES,
                     max_total_bytes: int = MAX_TOTAL_BYTES) -> Optional[dict[str, Any]]:
    """Evidence bundle for one attempt: which files changed in the recorded
    workspace root during the attempt window, with capped content heads.
    Returns None when nothing changed (pure text-work) — the judge prompt then
    stays exactly as before."""
    if not root:
        return None
    paths = changed_files(root, since, tool_calls)
    if not paths:
        return None
    files: list[dict[str, Any]] = []
    omitted: list[str] = []
    budget = max_total_bytes
    for rel in paths:
        if len(files) >= max_files or budget <= 0:
            omitted.append(str(rel))
            continue
        target = Path(root) / rel
        try:
            raw = target.read_text(errors="replace")
        except OSError:
            omitted.append(str(rel))
            continue
        head = raw[: min(max_file_bytes, budget)]
        budget -= len(head)
        files.append({
            "path": str(rel),
            "size": len(raw),
            "sha256": hashlib.sha256(raw.encode()).hexdigest()[:16],
            "content": head,
            "truncated": len(head) < len(raw),
        })
    if not files:
        return None
    return {"root": str(root), "files": files, "omitted": omitted}


def attempt_window_start(latency_s: float) -> float:
    """When the attempt roughly began, derived from the result's latency —
    callers that know the exact start should pass it instead."""
    return time.time() - max(latency_s, 0.0) - _MTIME_SLACK_S


def render_evidence(evidence: dict[str, Any]) -> str:
    """The evidence block as the judge sees it."""
    lines = [f"Files the worker actually changed in its workspace ({evidence['root']}):"]
    for f in evidence["files"]:
        suffix = " …[truncated]" if f["truncated"] else ""
        lines.append(f"--- {f['path']} ({f['size']} bytes, sha256:{f['sha256']})")
        lines.append(f["content"] + suffix)
    if evidence["omitted"]:
        lines.append(f"(also changed, not shown: {', '.join(evidence['omitted'])})")
    return "\n".join(lines)
