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
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from metaharness.core.types import Task, TaskType, Tier, WorkerResult
from metaharness.harness.runner import BaseRunner, WorkerTimeout
from metaharness.identity.keys import KeyPair

WORKSPACES_DIR = Path.home() / ".metaharness" / "workspaces"

# a coding CLI implementing a plan needs far more wall-clock than a one-shot
# text task; scale the base timeout by task type (issue #2). Precedent for
# table+override shape: context.TIER_CONTEXT_BUDGET / budget_for().
TASK_TYPE_TIMEOUT_FACTOR: dict[TaskType, float] = {TaskType.CODE_EDIT: 3.0}


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
    if worker.cli_model:
        argv += ["--model", worker.cli_model]
    if worker.system_prompt:
        argv += ["--append-system-prompt", worker.system_prompt]
    argv += ["-p"]
    return argv, prompt


def _build_codex(worker: "CodingAgentWorker", prompt: str, ws: Path) -> tuple[list[str], Optional[str]]:
    sandbox = getattr(worker, "sandbox", "workspace-write")
    argv = [worker.binary, "exec", "--skip-git-repo-check", "--sandbox", sandbox,
            "--cd", str(ws)]
    if sandbox == "read-only":
        argv += ["--ephemeral"]
    if worker.cli_model:
        argv += ["-m", worker.cli_model]
    argv += ["-"]  # prompt from stdin
    return argv, prompt


def _build_opencode(worker: "CodingAgentWorker", prompt: str, ws: Path) -> tuple[list[str], Optional[str]]:
    argv = [worker.binary, "run"]
    if worker.cli_model:
        argv += ["-m", worker.cli_model]
    argv += [prompt]
    return argv, None


def _build_claude(worker: "CodingAgentWorker", prompt: str, ws: Path) -> tuple[list[str], Optional[str]]:
    argv = [worker.binary, "-p", "--output-format", "json"]
    if getattr(worker, "sandbox", None) == "read-only":
        argv += ["--safe-mode", "--no-session-persistence",
                 "--permission-mode", "plan", "--tools", "Read,Glob,Grep"]
    if worker.cli_model:
        argv += ["--model", worker.cli_model]
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


# each coding harness authenticates ITSELF — the meta-harness never stores or
# proxies these keys, it only tells the user where they live
CLI_KEY_HINTS: dict[str, str] = {
    "pi": "keys: standard env vars (ANTHROPIC_API_KEY, …) or ~/.pi/agent/auth.json",
    "codex": "auth: `codex login` (ChatGPT OAuth) or CODEX_API_KEY; config in ~/.codex/config.toml",
    "opencode": "keys: `opencode auth login` or provider.options.apiKey in ~/.config/opencode/opencode.json",
    "claude": "auth: `claude login` (subscription) or ANTHROPIC_API_KEY",
}

# CLIs that can enumerate their own models; others fall back to known aliases
_CLI_MODEL_LISTERS: dict[str, list[str]] = {
    "pi": ["--list-models"],
    "opencode": ["models"],
}
_CLI_STATIC_MODELS: dict[str, list[str]] = {
    "claude": ["sonnet", "opus", "haiku", "claude-fable-5", "claude-opus-4-8"],
    "codex": ["gpt-5.2-codex", "gpt-5.2", "o5"],
}

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][\w.\-]*(/[\w.\-:]+)+$")

PI_MODELS_PATH = Path.home() / ".pi" / "agent" / "models.json"
OPENCODE_CONFIG_PATHS = [
    Path.home() / ".config" / "opencode" / "opencode.json",
    Path.home() / ".config" / "opencode" / "opencode.jsonc",
]


def _load_jsonish(path: Path) -> dict:
    """JSON, tolerating the JSONC subset opencode uses (full-line comments,
    trailing commas). Anything unparseable yields {} — suggestions only."""
    try:
        text = path.read_text()
    except OSError:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        stripped = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("//")
        )
        stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
        try:
            return json.loads(stripped)
        except ValueError:
            return {}


def pi_config_models(path: Path = PI_MODELS_PATH) -> list[str]:
    """provider/model ids from Pi's custom model registry (~/.pi/agent/
    models.json) — the ids Pi itself accepts via --model."""
    data = _load_jsonish(path)
    models = []
    for pid, provider in (data.get("providers") or {}).items():
        for entry in provider.get("models") or []:
            if isinstance(entry, dict) and entry.get("id"):
                models.append(f"{pid}/{entry['id']}")
    return models


def opencode_config_models(paths: Optional[list[Path]] = None) -> list[str]:
    """provider/model ids from OpenCode's config (opencode.json[c])."""
    models = []
    for path in paths or OPENCODE_CONFIG_PATHS:
        data = _load_jsonish(path)
        for pid, provider in (data.get("provider") or {}).items():
            if isinstance(provider, dict):
                for mid in (provider.get("models") or {}):
                    models.append(f"{pid}/{mid}")
        if models:
            break
    return models


_CLI_CONFIG_MODELS = {
    "pi": pi_config_models,
    "opencode": opencode_config_models,
}


async def list_cli_models(cli: str, timeout_s: float = 15.0,
                          cap: int = 300) -> list[str]:
    """The models a coding CLI can use, asked from the CLI itself when it
    supports listing. Failures fall back to the static aliases — a model id
    is a suggestion, not a gate; the CLI validates it at run time."""
    if cli not in CLI_ADAPTERS:
        raise ValueError(f"unknown coding CLI {cli!r}")
    lister = _CLI_MODEL_LISTERS.get(cli)
    # the CLI's own config directory is the fastest, most personal source:
    # custom registries (pi models.json) and configured providers (opencode)
    config_reader = _CLI_CONFIG_MODELS.get(cli)
    configured = config_reader() if config_reader else []
    static = _dedupe(configured + _CLI_STATIC_MODELS.get(cli, []))
    binary = shutil.which(CLI_ADAPTERS[cli].binary)
    if lister is None or binary is None:
        return static
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *lister,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except (OSError, asyncio.TimeoutError):
        try:
            proc.kill()
        except Exception:
            pass
        return static
    models = []
    for line in stdout_b.decode(errors="replace").splitlines():
        token = line.strip().split()[0] if line.strip() else ""
        if _MODEL_ID_RE.match(token):
            models.append(token)
            if len(models) >= cap:
                break
    return _dedupe(configured + models)[:cap] or static


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]


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

    BASE_TIMEOUT_S: float = 600.0

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
        timeout_s: Optional[float] = None,  # None = task-type-aware default (issue #2)
    ) -> None:
        if cli not in CLI_ADAPTERS:
            raise ValueError(f"unknown coding CLI '{cli}' (known: {sorted(CLI_ADAPTERS)})")
        # model has two jobs kept strictly apart: `model` (display/matrix key,
        # falls back to '<cli>-cli') vs `cli_model` (the explicit override the
        # CLI receives; empty = the CLI's own configured default — a display
        # placeholder must NEVER reach the command line)
        super().__init__(worker_id=worker_id, tier=tier,
                         model=model or f"{cli}-cli", keypair=keypair)
        self.cli_model = model
        self.cli = cli
        self.adapter = CLI_ADAPTERS[cli]
        self.system_prompt = system_prompt
        self.workspace = Path(workspace) if workspace else None
        self.binary = binary or self.adapter.binary
        self.extra_env = extra_env or {}
        self.timeout_s = timeout_s  # configured override; None = unset

    def effective_timeout_s(self, task: Task) -> float:
        """The timeout actually applied for one task: an explicit config
        override wins flat across all task types; otherwise CODE_EDIT work
        (a coding CLI implementing a plan) gets more room than a quick
        one-shot text task (issue #2)."""
        if self.timeout_s is not None:
            return self.timeout_s
        return self.BASE_TIMEOUT_S * TASK_TYPE_TIMEOUT_FACTOR.get(task.task_type, 1.0)

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

        eff = self.effective_timeout_s(task)
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(stdin_text.encode() if stdin_text is not None else None),
                timeout=eff,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            # :g not :.0f — subsecond test timeouts (e.g. 0.5) must not render as "0s"
            raise WorkerTimeout(f"{self.cli}: timed out after {eff:g}s", timeout_s=eff)

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        if proc.returncode != 0:
            detail = (stderr or stdout).strip()[-2000:]
            raise RuntimeError(f"{self.cli}: exit {proc.returncode}: {detail}")

        text, cost = self.adapter.parse(stdout)
        from metaharness.harness.local import parse_output
        # F3 (panel 2026-07-09, GLM P2): coding CLIs (codex/opencode) report cost
        # 0.0 and no token usage, so CodeProposer charged ~nothing for the most
        # expensive calls in the harness. No adapter surfaces a token count today,
        # so ESTIMATE from character length (~4 chars/token) — a rough but non-zero
        # figure so budget accounting reflects that these calls are not free.
        tokens_in = len(prompt) // 4
        tokens_out = len(text) // 4
        return WorkerResult(
            task_id=task.id,
            worker_id=self.worker_id,
            tier=self.tier,
            model=self.model,
            output=parse_output(text, expect_json=bool(task.output_schema)),
            raw_text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            workspace_root=str(workspace),
        )
