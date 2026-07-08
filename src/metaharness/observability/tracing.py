"""OpenTelemetry tracing for the meta-harness.

Every layer (orchestrator, router, worker, verifier, correction loop) emits real
OTel spans. We keep an in-memory span collector so the WebUI can render a live
timeline without needing an external OTLP collector, but the same TracerProvider can
also fan out to a real OTLP endpoint when one is configured.

Span attributes carry the things you actually want to see per step: model, tier,
tokens, cost, verdict, and (on failure) the MAST label as a span event.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

_LOCK = threading.Lock()
_INITIALIZED = False


@dataclass
class CapturedSpan:
    """A flattened, JSON-friendly view of a finished span for the WebUI/API."""

    name: str
    span_id: str
    parent_id: Optional[str]
    trace_id: str
    start_ns: int
    end_ns: int
    status: str
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        return (self.end_ns - self.start_ns) / 1e6


class InMemorySpanStore(SpanExporter):
    """Collects finished spans in memory, newest-last, grouped by trace id."""

    def __init__(self, capacity: int = 10_000) -> None:
        self._spans: list[CapturedSpan] = []
        self._capacity = capacity
        self._lock = threading.Lock()

    def export(self, spans) -> SpanExportResult:  # type: ignore[override]
        with self._lock:
            for span in spans:
                self._spans.append(_flatten(span))
            if len(self._spans) > self._capacity:
                self._spans = self._spans[-self._capacity:]
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover - lifecycle
        return None

    def all(self) -> list[CapturedSpan]:
        with self._lock:
            return list(self._spans)

    def by_trace(self, trace_id: str) -> list[CapturedSpan]:
        with self._lock:
            return [s for s in self._spans if s.trace_id == trace_id]

    def traces(self) -> list[str]:
        with self._lock:
            seen: dict[str, None] = {}
            for s in self._spans:
                seen.setdefault(s.trace_id, None)
            return list(seen.keys())

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()


def _flatten(span: ReadableSpan) -> CapturedSpan:
    ctx = span.get_span_context()
    parent = span.parent
    return CapturedSpan(
        name=span.name,
        span_id=format(ctx.span_id, "016x"),
        parent_id=format(parent.span_id, "016x") if parent else None,
        trace_id=format(ctx.trace_id, "032x"),
        start_ns=span.start_time or 0,
        end_ns=span.end_time or 0,
        status=span.status.status_code.name if span.status else "UNSET",
        attributes=dict(span.attributes or {}),
        events=[
            {"name": e.name, "attributes": dict(e.attributes or {}), "time_ns": e.timestamp}
            for e in span.events
        ],
    )


_STORE = InMemorySpanStore()


def store() -> InMemorySpanStore:
    return _STORE


def setup_tracing(service_name: str = "metaharness", otlp_endpoint: Optional[str] = None):
    """Idempotently install a TracerProvider with the in-memory store (and OTLP if
    an endpoint is given). Safe to call multiple times."""
    global _INITIALIZED
    with _LOCK:
        if _INITIALIZED:
            return trace.get_tracer(service_name)
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(SimpleSpanProcessor(_STORE))
        if otlp_endpoint:
            try:  # optional dependency; only used when explicitly configured
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                provider.add_span_processor(
                    SimpleSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
                )
            except Exception:  # pragma: no cover - optional
                pass
        trace.set_tracer_provider(provider)
        _INITIALIZED = True
        return trace.get_tracer(service_name)


def tracer(name: str = "metaharness"):
    setup_tracing()
    return trace.get_tracer(name)
