"""Workspace evidence: the judge's corrective lens for file-based work.

Regression for the live 2026-07-08 false negatives (runs ec3559b/afd3ce2):
a worker edited router.js correctly via tools, returned narration as its final
text, and the judge — grading text only — failed the step three times.
"""
from __future__ import annotations

import time

from metaharness.core.types import Task, Tier, Verdict, WorkerResult
from metaharness.evals.evidence import (
    changed_files,
    collect_evidence,
    render_evidence,
)
from metaharness.evals.judge import make_judge
from metaharness.harness import ScriptedWorker
from metaharness.identity import KeyPair


def test_changed_files_finds_recent_and_hinted(tmp_path):
    old = tmp_path / "untouched.js"
    old.write_text("old")
    import os
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    fresh = tmp_path / "edited.js"
    fresh.write_text("fresh content")
    hinted = tmp_path / "sub" / "hinted.js"
    hinted.parent.mkdir()
    hinted.write_text("hinted")
    os.utime(hinted, (time.time() - 3600, time.time() - 3600))  # old mtime, but hinted

    since = time.time() - 60
    hits = changed_files(tmp_path, since, tool_calls=[
        {"tool": "edit_file", "arguments": {"path": "sub/hinted.js"}}])
    names = [str(p) for p in hits]
    assert "edited.js" in names
    assert "sub/hinted.js" in names, "tool-call hints count regardless of mtime"
    assert "untouched.js" not in names


def test_changed_files_never_escapes_root(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    hits = changed_files(root, 0, tool_calls=[
        {"tool": "write_file", "arguments": {"path": "../secret.txt"}}])
    assert all("secret" not in str(p) for p in hits)


def test_collect_evidence_caps_and_manifests_omissions(tmp_path):
    for i in range(12):
        (tmp_path / f"f{i:02d}.txt").write_text("x" * 500)
    ev = collect_evidence(str(tmp_path), time.time() - 60)
    assert ev is not None
    assert len(ev["files"]) <= 8
    assert ev["omitted"], "capped files are listed, never silently dropped"
    total = sum(len(f["content"]) for f in ev["files"])
    assert total <= 6_000


def test_no_changes_means_no_evidence(tmp_path):
    old = tmp_path / "old.txt"
    old.write_text("old")
    import os
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert collect_evidence(str(tmp_path), time.time() - 60) is None
    assert collect_evidence("", time.time()) is None  # no recorded root → no claim


async def test_judge_sees_workspace_evidence_for_narration_output(tmp_path):
    """THE live bug: correct file on disk + narration text → judge must be shown
    the file and told disk is ground truth."""
    (tmp_path / "router.js").write_text("// GET /health route implemented\n")

    prompts = []

    def grading_handler(task):
        prompts.append(task.objective)
        return {"pass": True, "reason": "the changed file satisfies the contract"}

    judge = make_judge(ScriptedWorker("j", grading_handler, tier=Tier.FRONTIER,
                                      keypair=KeyPair.generate()))
    task = Task(objective="Implement the /health route in router.js")
    result = WorkerResult(
        task_id=task.id, worker_id="w", tier=Tier.FRONTIER, model="m",
        output="The edit is confirmed. Let me read router.js…",  # narration
        workspace_root=str(tmp_path), latency_s=5.0,
    )
    verification = await judge(task, result)

    assert verification.verdict is Verdict.PASS
    prompt = prompts[0]
    assert "router.js" in prompt and "GET /health route implemented" in prompt
    assert "ground truth" in prompt, "judge must be told files outrank narration"


async def test_judge_prompt_unchanged_without_workspace(tmp_path):
    prompts = []

    def grading_handler(task):
        prompts.append(task.objective)
        return {"pass": False, "reason": "no"}

    judge = make_judge(ScriptedWorker("j", grading_handler, tier=Tier.FRONTIER,
                                      keypair=KeyPair.generate()))
    task = Task(objective="Summarize the incident")
    result = WorkerResult(task_id=task.id, worker_id="w", tier=Tier.SMALL,
                          model="m", output="a summary")
    await judge(task, result)
    assert "ground truth" not in prompts[0]
    assert "workspace" not in prompts[0].lower()


def test_render_evidence_shows_omissions(tmp_path):
    (tmp_path / "a.txt").write_text("aaa")
    ev = collect_evidence(str(tmp_path), time.time() - 60)
    ev["omitted"] = ["big.bin"]
    text = render_evidence(ev)
    assert "a.txt" in text and "big.bin" in text and "not shown" in text
