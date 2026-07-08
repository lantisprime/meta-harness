"""Coding-agent CLI workers: delegate CODE_EDIT-class tasks to a full coding
harness (Pi, Codex CLI, OpenCode, Claude Code) running headless in a managed
workspace.

Motivation: these CLIs are complete agent harnesses — file editing, shell,
search, their own verification loops. Wrapping one as a Runner gives the
meta-harness hands: it can implement its own plans, generate experiment code,
and produce artifacts the eval layer can grade. Per design principle "worker
output is data": the CLI's stdout is parsed, never interpreted as instructions.

Invocation recipes follow memory/knowledge_base/coding-agent-clis-mcp.md —
one-shot headless, prompt over stdin where the CLI supports it (untrusted task
text never lands in argv), session persistence off, cwd pinned to the
workspace.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from metaharness.core.types import Task, Tier, WorkerResult
from metaharness.harness.runner import BaseRunner
from metaharness.identity.keys import KeyPair

WORKSPACES_DIR = Path.home() / ".metaharness" / "workspaces"


@dataclass(frozen=True)
class CLIAdapter:
    """How to drive one coding CLI headless: argv builder + output parser."""

    binary: str
    build: Callable[["CodingAgentWorker", str, Path], tuple[list[str], Optional[str]]]
    # build(worker, prompt, workspace) -> (argv, stdin_text or None)
    parse: Callable[[str], tuple[str, float]]
    # parse(stdout) -> (final_text, cost_usd)


def _parse_text(stdout: str) -> tuple[str, float]:
    return stdout.strip(), 0.0


def _parse_claude_json(stdout: str) -> tuple[str, float]:
    try:
        data = json.loads(stdout)
        return str(data.get("result", "")), float(data.get("total_cost_usd", 0.0))
    except (ValueError, TypeError):
        return stdout.strip(), 0.0


def _parse_pi_jsonl(stdout: str) -> tuple[str, float]:
    """Last assistant text across Pi's --mode json event stream."""
    text, cost = "", 0.0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message") or {}
        if message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, list):
                parts = [c.get("text", "") for c in content if isinstance(c, dict)]
                text = "\n".join(p for p in parts if p) or text
            elif isinstance(content, str) and content:
                text = content
        usage = event.get("usage") or message.get("usage") or {}
        if isinstance(usage, dict):
            cost = float(usage.get("cost", {}).get("total", cost)) if isinstance(usage.get("cost"), dict) else cost
    return text or stdout.strip(), cost


def _build_pi(worker: "CodingAgentWorker", prompt: str, ws: Path) -> tuple[list[str], Optional[str]]:
    argv = [worker.binary, "--mode", "json", "--no-session", "--no-extensions",
            "--no-skills", "--no-prompt-templates", "--no-themes"]
    if worker.model:
        argv += ["--model", worker.model]
    if worker.system_prompt:
        argv += ["--append-system-prompt", worker.system_prompt]
    argv += ["-p"]
    return argv, prompt


def _build_codex(worker: "CodingAgentWorker", prompt: str, ws: Path) -> tuple[list[str], Optional[str]]:
    argv = [worker.binary, "exec", "--skip-git-repo-check",
            "--sandbox", "workspace-write", "--cd", str(ws)]
    if worker.model:
        argv += ["-m", worker.model]
    argv += ["-"]  # prompt from stdin
    return argv, prompt


def _build_opencode(worker: "CodingAgentWorker", prompt: str, ws: Path) -> tuple[list[str], Optional[str]]:
    argv = [worker.binary, "run"]
    if worker.model:
        argv += ["-m", worker.model]
    argv += [prompt]
    return argv, None


def _build_claude(worker: "CodingAgentWorker", prompt: str, ws: Path) -> tuple[list[str], Optional[str]]:
    argv = [worker.binary, "-p", "--output-format", "json"]
    if worker.model:
        argv += ["--model", worker.model]
    if worker.system_prompt:
        argv += ["--append-system-prompt", worker.system_prompt]
    return argv, prompt


CLI_ADAPTERS: dict[str, CLIAdapter] = {
    "pi": CLIAdapter(binary="pi", build=_build_pi, parse=_parse_pi_jsonl),
    "codex": CLIAdapter(binary="codex", build=_build_codex, parse=_parse_text),
    "opencode": CLIAdapter(binary="opencode", build=_build_opencode, parse=_parse_text),
    "claude": CLIAdapter(binary="claude", build=_build_claude, parse=_parse_claude_json),
}


def available_clis() -> dict[str, str]:
    """Which coding CLIs are installed on this machine (name -> path)."""
    found = {}
    for name, adapter in CLI_ADAPTERS.items():
        path = shutil.which(adapter.binary)
        if path:
            found[name] = path
    return found


def _render_prompt(task: Task) -> str:
    """The Task delegation contract as one prompt for a coding agent."""
    parts = [task.objective]
    if task.boundaries:
        parts.append("Constraints:\n" + "\n".join(f"- {b}" for b in task.boundaries))
    visible = {k: v for k, v in (task.inputs or {}).items() if not k.startswith("_")}
    if visible:
        parts.append("Inputs:\n" + json.dumps(visible, ensure_ascii=False, default=str))
    if task.output_schema:
        parts.append(
            "When done, print a final summary as a single JSON object matching:\n"
            + json.dumps(task.output_schema)
        )
    return "\n\n".join(parts)


class CodingAgentWorker(BaseRunner):
    """A worker that runs a coding-agent CLI one-shot per task in a workspace.

    Every run is headless and ephemeral (no CLI-side session state); the
    workspace directory is the durable artifact. A non-zero exit or a spawn
    failure becomes WorkerResult.error — loud, never a silent pass.
    """

    def __init__(
        self,
        worker_id: str,
        cli: str,
        model: str = "",
        tier: Tier = Tier.FRONTIER,
        keypair: Optional[KeyPair] = None,
        system_prompt: str = "",
        workspace: Optional[Path] = None,   # default: per-task dir under ~/.metaharness/workspaces
        binary: Optional[str] = None,       # override path (tests use a stub script)
        extra_env: Optional[dict[str, str]] = None,
        timeout_s: float = 600.0,
    ) -> None:
        if cli not in CLI_ADAPTERS:
            raise ValueError(f"unknown coding CLI '{cli}' (known: {sorted(CLI_ADAPTERS)})")
        super().__init__(worker_id=worker_id, tier=tier,
                         model=model or f"{cli}-cli", keypair=keypair)
        self.cli = cli
        self.adapter = CLI_ADAPTERS[cli]
        self.system_prompt = system_prompt
        self.workspace = Path(workspace) if workspace else None
        self.binary = binary or self.adapter.binary
        self.extra_env = extra_env or {}
        self.timeout_s = timeout_s

    def _workspace_for(self, task: Task) -> Path:
        explicit = (task.inputs or {}).get("_workspace")
        ws = Path(explicit) if explicit else (
            self.workspace or WORKSPACES_DIR / task.id
        )
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    async def _execute(self, task: Task) -> WorkerResult:
        workspace = self._workspace_for(task)
        prompt = _render_prompt(task)
        argv, stdin_text = self.adapter.build(self, prompt, workspace)
        env = {**os.environ, **self.extra_env}

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=workspace,
                env=env,
                stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RuntimeError(f"{self.cli}: cannot launch '{self.binary}': {exc}") from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(stdin_text.encode() if stdin_text is not None else None),
                timeout=self.timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"{self.cli}: timed out after {self.timeout_s:.0f}s")

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        if proc.returncode != 0:
            detail = (stderr or stdout).strip()[-2000:]
            raise RuntimeError(f"{self.cli}: exit {proc.returncode}: {detail}")

        text, cost = self.adapter.parse(stdout)
        from metaharness.harness.local import parse_output
        return WorkerResult(
            task_id=task.id,
            worker_id=self.worker_id,
            tier=self.tier,
            model=self.model,
            output=parse_output(text, expect_json=bool(task.output_schema)),
            raw_text=text,
            cost_usd=cost,
        )
