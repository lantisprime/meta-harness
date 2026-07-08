"""Builtin tools: workspace-scoped file operations, web fetch, calculator.

Every file tool is jailed to a workspace root — a path that resolves outside
it is refused (ToolError), so a worker can never be talked into reading or
writing the wider filesystem. Workflow steps share the run's workspace, which
is also where coding-agent workers leave their artifacts.
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from metaharness.harness.sandbox import SandboxError, eval_arithmetic
from metaharness.tools.registry import ToolError, ToolRegistry, ToolSpec

DEFAULT_WORKSPACE = Path.home() / ".metaharness" / "workspaces" / "shared"
_MAX_READ = 50_000
_MAX_MATCHES = 100
_MAX_FILES = 200


def _jail(root: Path, rel: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)  # lazily, on first actual use
    root = root.resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolError(f"path {rel!r} escapes the workspace")
    return candidate


def build_file_tools(root: Path) -> list[ToolSpec]:

    def read_file(path: str) -> str:
        target = _jail(root, path)
        if not target.is_file():
            raise ToolError(f"no such file: {path}")
        return target.read_text(errors="replace")[:_MAX_READ]

    def write_file(path: str, content: str) -> str:
        target = _jail(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"wrote {len(content)} chars to {path}"

    def edit_file(path: str, old: str, new: str) -> str:
        target = _jail(root, path)
        if not target.is_file():
            raise ToolError(f"no such file: {path}")
        text = target.read_text(errors="replace")
        count = text.count(old)
        if count == 0:
            raise ToolError("old string not found in file")
        if count > 1:
            raise ToolError(f"old string is ambiguous ({count} occurrences)")
        target.write_text(text.replace(old, new, 1))
        return f"edited {path}"

    def list_files(pattern: str = "**/*") -> str:
        entries = []
        for p in sorted(root.glob(pattern)):
            if p.is_file():
                entries.append(str(p.relative_to(root)))
            if len(entries) >= _MAX_FILES:
                entries.append(f"[…capped at {_MAX_FILES} files…]")
                break
        return "\n".join(entries) or "(no files)"

    def grep(pattern: str, glob: str = "**/*") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"bad regex: {exc}")
        matches = []
        for p in sorted(root.glob(glob)):
            if not p.is_file() or p.stat().st_size > 1_000_000:
                continue
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    matches.append(f"{p.relative_to(root)}:{i}: {line.strip()[:200]}")
                    if len(matches) >= _MAX_MATCHES:
                        matches.append(f"[…capped at {_MAX_MATCHES} matches…]")
                        return "\n".join(matches)
        return "\n".join(matches) or "(no matches)"

    _str = {"type": "string"}
    return [
        ToolSpec("read_file", "Read a text file from the workspace.",
                 {"type": "object", "properties": {"path": _str}, "required": ["path"]},
                 read_file, keywords=("read", "open", "file", "cat", "view")),
        ToolSpec("write_file", "Create or overwrite a text file in the workspace.",
                 {"type": "object", "properties": {"path": _str, "content": _str},
                  "required": ["path", "content"]},
                 write_file, keywords=("write", "create", "save", "file")),
        ToolSpec("edit_file", "Replace one exact string in a workspace file.",
                 {"type": "object", "properties": {"path": _str, "old": _str, "new": _str},
                  "required": ["path", "old", "new"]},
                 edit_file, keywords=("edit", "modify", "replace", "change", "patch", "fix")),
        ToolSpec("list_files", "List workspace files matching a glob pattern.",
                 {"type": "object", "properties": {"pattern": _str}},
                 list_files, keywords=("list", "ls", "find", "files", "directory")),
        ToolSpec("grep", "Search workspace files with a regex; returns file:line matches.",
                 {"type": "object", "properties": {"pattern": _str, "glob": _str},
                  "required": ["pattern"]},
                 grep, keywords=("grep", "search", "find", "locate", "code")),
    ]


async def _web_fetch(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ToolError("only http(s) URLs")
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolError(f"fetch failed: {exc}")
    return resp.text


def _calculator(expression: str) -> str:
    try:
        return str(eval_arithmetic(expression))
    except SandboxError as exc:
        raise ToolError(str(exc))


def default_registry(workspace: Path = DEFAULT_WORKSPACE) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in build_file_tools(workspace):
        registry.register(spec)
    registry.register(ToolSpec(
        "web_fetch", "Fetch a URL and return its text content.",
        {"type": "object", "properties": {"url": {"type": "string"}},
         "required": ["url"]},
        _web_fetch, keywords=("web", "url", "http", "fetch", "download", "website", "page")))
    registry.register(ToolSpec(
        "calculator", "Evaluate an arithmetic expression exactly.",
        {"type": "object", "properties": {"expression": {"type": "string"}},
         "required": ["expression"]},
        _calculator, keywords=("calculate", "arithmetic", "math", "compute", "sum",
                               "multiply", "divide", "percentage")))
    return registry
