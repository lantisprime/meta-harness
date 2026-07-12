"""Run packaging: one zip with everything a finished run produced.

Contents: manifest.json (statuses, verdicts, costs, caps), workflow.json (the
spec), journal.jsonl (the full event trail), steps/<id>.md|.json (each step's
output, markdown for prose / JSON for structured), and workspace/ — ONLY files
changed during the run window under each step's RECORDED workspace root
(never the whole shared workspace, never a guessed path). Every cap or skip is
listed in the manifest; nothing is silently dropped.
"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from metaharness.evals.evidence import changed_files

MAX_WORKSPACE_FILES = 50
MAX_WORKSPACE_FILE_BYTES = 200_000
MAX_WORKSPACE_TOTAL_BYTES = 1_000_000

# POSIX-portable, traversal-safe names: no path separators, no '..', no control chars.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_.\-]*$")


def _safe_step_member(step_id: str, output: Any) -> tuple[str, bytes]:
    """Return a traversal-safe ZIP member name for a step output."""
    if not _SAFE_NAME_RE.fullmatch(step_id):
        raise ValueError(f"unsafe step id for packaging: {step_id!r}")
    if isinstance(output, str):
        return f"steps/{step_id}.md", output.encode()
    return (f"steps/{step_id}.json",
            json.dumps(output, indent=2, ensure_ascii=False, default=str).encode())


def _safe_workspace_path(rel: str | Path) -> str:
    """Reject workspace relative paths that try to escape or contain unsafe chars."""
    text = str(rel)
    if not text or text.startswith(("/", "\\")):
        raise ValueError(f"workspace path is absolute or empty: {text!r}")
    parts = text.replace("\\", "/").split("/")
    if any(p == ".." or p.startswith("..") for p in parts):
        raise ValueError(f"workspace path escapes root: {text!r}")
    if any(not p or re.search(r"[\x00-\x1f\x7f]", p) for p in parts):
        raise ValueError(f"workspace path contains empty or control segments: {text!r}")
    return text


def _workspace_target(base: Path, rel: str) -> Path:
    """Resolve the target and verify it stays inside the recorded root.

    Symlinks are rejected outright; paths resolving outside the root raise.
    """
    target = (base / rel).resolve()
    root = base.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"workspace path escapes root: {rel!r}") from exc
    return target


def build_package_bytes(spec, state, journal_entries, *, canonical_events=None) -> bytes:
    """The zip for one run. `spec` is the WorkflowSpec, `state` the RunState,
    `journal_entries` the run's journal entries (model objects)."""
    from metaharness.workflows.engine import _snapshot_digest

    run_started = journal_entries[0].at if journal_entries else 0.0
    buf = io.BytesIO()
    workspace_manifest: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []

    # Structural provenance validation first: ref and snapshot must agree.
    if (state.blueprint_ref is None) != (state.blueprint_snapshot is None):
        raise ValueError(
            "package blueprint_ref and blueprint_snapshot must both be present or both absent"
        )
    if state.blueprint_snapshot is not None:
        from metaharness.blueprints.models import ArtifactRef, BlueprintVersion
        bp = BlueprintVersion.model_validate(state.blueprint_snapshot)
        ref = ArtifactRef.model_validate(state.blueprint_ref)
        if bp.id != ref.id or bp.version != ref.version:
            raise ValueError(
                f"package blueprint snapshot identity {bp.id!r} v{bp.version} "
                f"does not match ref {ref.id!r} v{ref.version}"
            )

    # Then verify present snapshot digests; tolerate old journals.
    expected_digest = _snapshot_digest(spec, state.blueprint_snapshot)
    if state.snapshot_digest is not None and state.snapshot_digest != expected_digest:
        raise ValueError(
            f"snapshot digest mismatch before packaging: "
            f"state {state.snapshot_digest}, computed {expected_digest}"
        )

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("workflow.json",
                    json.dumps(spec.model_dump(mode="json"), indent=2, ensure_ascii=False))
        if state.blueprint_ref is not None and state.blueprint_snapshot is not None:
            zf.writestr("blueprint.json",
                        json.dumps(state.blueprint_snapshot, indent=2, ensure_ascii=False))
        primary_events = canonical_events if canonical_events is not None else journal_entries
        zf.writestr("journal.jsonl", "\n".join(
            json.dumps(e.model_dump(mode="json"), ensure_ascii=False, default=str)
            for e in primary_events))
        if canonical_events is not None:
            zf.writestr("journal.legacy.jsonl", "\n".join(
                json.dumps(e.model_dump(mode="json"), ensure_ascii=False, default=str)
                for e in journal_entries))
        for step_id, record in state.completed.items():
            name, payload = _safe_step_member(step_id, record.output)
            zf.writestr(name, payload)

        # workspace files: changed during the run, under recorded roots only
        roots: list[str] = []
        for record in state.completed.values():
            if record.workspace_root and record.workspace_root not in roots:
                roots.append(record.workspace_root)
        seen_members: set[str] = set()
        total = 0
        count = 0
        for i, root in enumerate(roots):
            base = Path(root)
            prefix = f"workspace/{i}_{base.name or 'root'}"
            for rel in changed_files(root, run_started):
                member = f"{prefix}/{rel}"
                if member in seen_members:
                    continue
                try:
                    _safe_workspace_path(rel)
                    raw_target = base / rel
                    # Reject symlinks only in raw_target and ancestors strictly
                    # below the recorded base. Do not inspect base or ancestors
                    # above it (e.g. macOS /var -> /private/var), or every file
                    # would be rejected.
                    cursor = raw_target
                    symlink_found = False
                    while cursor != base:
                        if cursor.is_symlink():
                            symlink_found = True
                            break
                        cursor = cursor.parent
                    if symlink_found:
                        omitted.append({"path": member, "reason": "symlink refused"})
                        continue
                    target = _workspace_target(base, rel)
                    data = target.read_bytes()
                except (OSError, ValueError):
                    omitted.append({"path": member, "reason": "unreadable"})
                    continue
                if count >= MAX_WORKSPACE_FILES:
                    omitted.append({"path": member, "reason": "file-count cap"})
                    continue
                if len(data) > MAX_WORKSPACE_FILE_BYTES:
                    omitted.append({"path": member, "reason": "file too large"})
                    continue
                if total + len(data) > MAX_WORKSPACE_TOTAL_BYTES:
                    omitted.append({"path": member, "reason": "total-size cap"})
                    continue
                zf.writestr(member, data)
                seen_members.add(member)
                workspace_manifest.append({
                    "path": member, "bytes": len(data), "root": root})
                total += len(data)
                count += 1

        manifest = {
            "run_id": state.run_id,
            "workflow": state.workflow,
            "snapshot_digest": state.snapshot_digest,
            "blueprint_ref": (state.blueprint_ref
                              if state.blueprint_ref is not None else None),
            "status": state.status.value,
            "failed_step": state.failed_step,
            "generated_at": time.time(),
            "run_started_at": run_started,
            "steps": {
                sid: {
                    "verdict": r.verdict.value,
                    "attempts": r.attempts,
                    "cost_usd": r.cost_usd,
                    "workspace_root": r.workspace_root,
                } for sid, r in state.completed.items()
            },
            "skipped": state.skipped,
            "workspace_files": workspace_manifest,
            "workspace_omitted": omitted,
            "caps": {
                "max_files": MAX_WORKSPACE_FILES,
                "max_file_bytes": MAX_WORKSPACE_FILE_BYTES,
                "max_total_bytes": MAX_WORKSPACE_TOTAL_BYTES,
            },
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return buf.getvalue()
