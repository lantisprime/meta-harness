"""Attempt-scoped event hook for tool execution telemetry."""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Callable, Optional

RunEventSink = Callable[[str, dict[str, Any]], None]

_sink: ContextVar[Optional[RunEventSink]] = ContextVar("run_event_sink", default=None)


def bind_run_event_sink(sink: Optional[RunEventSink]) -> Token:
    return _sink.set(sink)


def reset_run_event_sink(token: Token) -> None:
    _sink.reset(token)


def emit_run_event(kind: str, payload: dict[str, Any]) -> None:
    sink = _sink.get()
    if sink is not None:
        sink(kind, payload)
