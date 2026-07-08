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
import time
import zipfile
from pathlib import Path
from typing import Any

from metaharness.evals.evidence import changed_files

MAX_WORKSPACE_FILES = 50
MAX_WORKSPACE_FILE_BYTES = 200_000
MAX_WORKSPACE_TOTAL_BYTES = 1_000_000


def _step_member(step_id: str, output: Any) -> tuple[str, bytes]:
    if isinstance(output, str):
        return f"steps/{step_id}.md", output.encode()
    return (f"steps/{step_id}.json",
            json.dumps(output, indent=2, ensure_ascii=False, default=str).encode())


def build_package_bytes(spec, state, journal_entries) -> bytes:
    """The zip for one run. `spec` is the WorkflowSpec, `state` the RunState,
    `journal_entries` the run's journal entries (model objects)."""
    run_started = journal_entries[0].at if journal_entries else 0.0
    buf = io.BytesIO()
    workspace_manifest: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("workflow.json",
                    json.dumps(spec.model_dump(mode="json"), indent=2, ensure_ascii=False))
        zf.writestr("journal.jsonl", "\n".join(
            json.dumps(e.model_dump(mode="json"), ensure_ascii=False, default=str)
            for e in journal_entries))
        for step_id, record in state.completed.items():
            name, payload = _step_member(step_id, record.output)
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
                target = base / rel
                try:
                    data = target.read_bytes()
                except OSError:
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
