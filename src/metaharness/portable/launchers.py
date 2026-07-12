"""Generator-owned launch descriptors for supported coding agents.

The descriptors deliberately contain argv arrays rather than executable scripts.
An outer integration is responsible for spawning the neutral MetaHarness CLI and
passing context on stdin (or replacing ``-`` with a context file path).
"""
from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Literal


LauncherTarget = Literal["codex", "claude-code", "pi", "opencode"]
LAUNCHER_SCHEMA_VERSION = 1
_TARGETS: tuple[LauncherTarget, ...] = ("codex", "claude-code", "pi", "opencode")
_EXIT_CODES = {
    "completed": 0,
    "execution_failed": 1,
    "invalid_or_not_ready": 2,
    "approval_required": 20,
}
_AUTH_HOME_PARTS = frozenset({".claude", ".codex", ".opencode", ".pi", ".ssh"})


def _safe_path(
    label: str,
    value: str,
    *,
    relative: bool,
    allow_dash: bool = False,
    allow_dot: bool = False,
) -> str:
    if allow_dash and value == "-":
        return value
    if allow_dot and value == ".":
        return value
    if not value or "\x00" in value or "\n" in value or "\r" in value or "\\" in value:
        raise ValueError(f"{label} must be a normalized POSIX path")
    if value.startswith("-"):
        raise ValueError(f"{label} must not be parsed as a command option")
    path = PurePosixPath(value)
    if relative and path.is_absolute():
        raise ValueError(f"{label} must be relative to the package")
    raw_parts = value.split("/")
    if path.is_absolute():
        raw_parts = raw_parts[1:]
    parts = path.parts[1:] if path.is_absolute() else path.parts
    if any(part in {"", ".", ".."} for part in raw_parts) or any(part == ".." for part in parts):
        raise ValueError(f"{label} must be normalized without traversal")
    lowered = {part.casefold() for part in parts}
    vendor_parts = {"opencode", "claude", "codex", "pi"}
    if lowered & _AUTH_HOME_PARTS or (
        ".config" in lowered and (vendor_parts & lowered)
    ) or (
        ".local" in lowered and "share" in lowered and (vendor_parts & lowered)
    ):
        raise ValueError(f"{label} must not reference a vendor authentication home")
    return value


def launcher_descriptor(
    target: LauncherTarget,
    *,
    blueprint_path: str = "harness.json",
    workspace_path: str = ".",
    context_file: str = "-",
) -> dict:
    """Return the stable, vendor-neutral process contract for one launcher."""
    if target not in _TARGETS:
        raise ValueError(f"unsupported launcher target: {target!r}")
    blueprint_path = _safe_path("blueprint_path", blueprint_path, relative=True)
    workspace_path = _safe_path(
        "workspace_path", workspace_path, relative=False, allow_dot=True
    )
    context_file = _safe_path(
        "context_file", context_file, relative=True, allow_dash=True
    )

    return {
        "schema_version": LAUNCHER_SCHEMA_VERSION,
        "target": target,
        "process": {
            "argv": [
                "metaharness",
                "blueprint",
                "run",
                blueprint_path,
                "--context-file",
                context_file,
                "--workspace",
                workspace_path,
                "--format",
                "jsonl",
                "--approval",
                "stop",
            ],
            "cwd": ".",
            "environment": {"METAHARNESS_HOST": target},
            "stdin": "context-json" if context_file == "-" else "inherit",
            "stdout": "metaharness-jsonl-v1",
            "stderr": "human-diagnostics",
            "exit_codes": dict(_EXIT_CODES),
            "propagate_exit_code": True,
        },
        "security": {
            "copies_vendor_auth": False,
            "shell": False,
            "approval_policy": "stop",
        },
    }


def launcher_layout(
    target: LauncherTarget,
    *,
    blueprint_path: str = "harness.json",
    workspace_path: str = ".",
    context_file: str = "-",
) -> dict[str, bytes]:
    """Return the versioned, generator-owned files for a launcher directory."""
    descriptor = launcher_descriptor(
        target,
        blueprint_path=blueprint_path,
        workspace_path=workspace_path,
        context_file=context_file,
    )
    json_bytes = (
        json.dumps(descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()
    instructions = (
        f"# MetaHarness launcher for {target}\n\n"
        "Use `launcher.json` as a process-spawn contract. Pass context JSON on stdin "
        "when `--context-file` is `-`, stream stdout as JSONL, show stderr to the "
        "human, and return the child exit code unchanged. Never import or copy the "
        "coding agent's authentication directory.\n"
    ).encode()
    prefix = f"launchers/{target}"
    return {
        f"{prefix}/INSTRUCTIONS.md": instructions,
        f"{prefix}/launcher.json": json_bytes,
    }


def all_launcher_layouts() -> dict[str, bytes]:
    """Return canonical files for every supported outer launcher."""
    files: dict[str, bytes] = {}
    for target in _TARGETS:
        files.update(launcher_layout(target))
    return files
