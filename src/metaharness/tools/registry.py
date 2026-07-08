"""Tool registry: one catalog of everything a worker may call — builtin tools
and MCP-server tools — plus the selection logic that keeps per-step tool
subsets SMALL.

Design per memory/knowledge_base/context-engineering-agent-harnesses.md:
- workers never see the full catalog: accuracy degrades with tool count, and
  small local models degrade worst (RAG-MCP, MCPVerse). `select_for` returns
  an adaptive shortlist (score cliff, cap 7).
- schemas render deterministically (sorted keys, stable order) so a worker's
  prompt prefix stays KV-cache-friendly across rounds.
- tool RESULTS are pruned before they re-enter context: task-conditioned
  digest with head/tail preserved (Squeez-style), never a silent truncation.
"""
from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

MAX_RESULT_CHARS = 4_000
DEFAULT_SUBSET_CAP = 7


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]                  # JSON schema for arguments
    handler: Callable[..., Any]                   # sync or async; kwargs from args
    source: str = "builtin"                       # builtin | mcp:<server>
    keywords: tuple[str, ...] = ()                # extra terms for selection


class ToolError(Exception):
    """A tool failed; the message is worker-visible data, never instructions."""


_WORD_RE = re.compile(r"[a-z0-9_]+")


def _terms(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def digest_text(text: str, max_chars: int = MAX_RESULT_CHARS,
                focus_terms: Optional[set[str]] = None) -> str:
    """Shrink a blob to budget, keeping focus-relevant lines plus head/tail.
    The pruning is always LOUD — the reader sees exactly what was dropped."""
    if len(text) <= max_chars:
        return text
    focus_terms = focus_terms or set()
    kept: list[str] = []
    used = 0
    focus_budget = max_chars // 2
    if focus_terms:
        for line in text.splitlines():
            if _terms(line) & focus_terms and used + len(line) < focus_budget:
                kept.append(line)
                used += len(line) + 1
    remainder = max_chars - used
    head = text[: int(remainder * 0.7)]
    tail = text[-int(remainder * 0.3):] if remainder > 10 else ""
    dropped = len(text) - used - len(head) - len(tail)
    parts = [head, f"\n[…pruned {dropped} chars…]\n", tail]
    if kept:
        parts += ["\n[objective-relevant lines kept:]\n", "\n".join(kept)]
    return "".join(parts)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} already registered")
        self._tools[spec.name] = spec

    def unregister_source(self, source: str) -> int:
        """Drop every tool from one source (e.g. an MCP server being reloaded)."""
        names = [n for n, t in self._tools.items() if t.source == source]
        for name in names:
            del self._tools[name]
        return len(names)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return [self._tools[n] for n in sorted(self._tools)]

    def names(self) -> list[str]:
        return sorted(self._tools)

    # ------------------------------------------------------------- selection

    def select_for(self, objective: str, boundaries: Optional[list[str]] = None,
                   cap: int = DEFAULT_SUBSET_CAP) -> list[str]:
        """Adaptive tool shortlist for one step: score by term overlap
        (name 3x, keywords 2x, description 1x), keep tools above a cliff
        relative to the best score, cap the subset. No signal -> NO tools;
        most steps are pure text-work and tools would only confuse."""
        text_terms = _terms(objective + " " + " ".join(boundaries or []))
        scored: list[tuple[float, str]] = []
        for name, tool in self._tools.items():
            score = (
                3.0 * len(text_terms & _terms(tool.name.replace(".", " ")))
                + 2.0 * len(text_terms & _terms(" ".join(tool.keywords)))
                + 1.0 * len(text_terms & _terms(tool.description))
            )
            if score > 0:
                scored.append((score, name))
        if not scored:
            return []
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        top = scored[0][0]
        cliff = max(2.0, 0.4 * top)
        return [name for score, name in scored[:cap] if score >= cliff]

    # -------------------------------------------------------------- schemas

    def openai_schemas(self, names: list[str]) -> list[dict[str, Any]]:
        """OpenAI function-calling schemas, deterministic order & serialization
        (sorted by name; sorted keys downstream keeps the prefix byte-stable)."""
        schemas = []
        for name in sorted(set(names)):
            tool = self._tools.get(name)
            if tool is None:
                continue  # a plan may reference a tool that has since unloaded
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name.replace(".", "__"),  # dialect-safe name
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            })
        return schemas

    def resolve_call_name(self, wire_name: str) -> Optional[str]:
        """Map a dialect-safe wire name (dots escaped) back to the registry."""
        if wire_name in self._tools:
            return wire_name
        dotted = wire_name.replace("__", ".")
        return dotted if dotted in self._tools else None

    # ------------------------------------------------------------ execution

    async def call(self, name: str, arguments: dict[str, Any],
                   focus: str = "") -> str:
        """Run a tool; the return value is a pruned STRING (worker-facing).
        Errors come back as data ('tool error: …') so the worker can adapt,
        while a missing tool is a hard error — that's a harness bug."""
        resolved = self.resolve_call_name(name)
        if resolved is None:
            raise ToolError(f"unknown tool {name!r}")
        tool = self._tools[resolved]
        try:
            result = tool.handler(**arguments)
            if inspect.isawaitable(result):
                result = await result
        except ToolError as exc:
            return f"tool error: {exc}"
        except TypeError as exc:  # bad/missing arguments — worker can retry
            return f"tool error: bad arguments for {resolved}: {exc}"
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str, sort_keys=True)
        return digest_text(result, MAX_RESULT_CHARS, _terms(focus))
