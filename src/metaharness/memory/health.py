"""Memory-skill health signal with circuit-breaker semantics (META5-MEM-013).

A ``MemorySkillCircuitBreaker`` counts consecutive failures against the
memory-skill subsystem; once the count reaches ``failure_threshold``,
the breaker opens and subsequent :meth:`require_healthy` calls raise
:class:`CircuitOpenError` until a manual :meth:`record_success` (or
:meth:`reset`) closes it again.

This signal exists so repeated assembly failures trip an explicit "unhealthy"
state instead of every call falling back to the legacy fitter with no
audit trail.
"""
from __future__ import annotations

import threading
from typing import Any


class CircuitOpenError(Exception):
    """Raised by :meth:`MemorySkillCircuitBreaker.require_healthy` after the
    breaker has opened. Callers must surface this as an explicit fallback
    rather than masking it with the legacy fitter.
    """


class MemorySkillCircuitBreaker:
    """Thread-safe circuit breaker with a pure-Python failure counter.

    State machine:
        CLOSED (healthy, counting failures)
            record_failure()              -> increment count; open on threshold
        OPEN (unhealthy, all require_healthy() calls raise)
            record_success() (manual reset) -> back to CLOSED with count = 0
        ``reset()`` unconditionally returns to CLOSED.

    The default policy: once the threshold is reached, the breaker stays
    open until explicitly reset. There's no auto half-open probe; the
    spec leaves that to the orchestrator's review flow.
    """

    def __init__(self, failure_threshold: int = 3):
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be a positive integer")
        self._threshold = failure_threshold
        self._lock = threading.RLock()
        self._failures = 0
        self._opened_at: int | None = None

    @property
    def failure_threshold(self) -> int:
        return self._threshold

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failures

    def record_failure(self) -> int:
        """Increment the failure counter; open the breaker if the threshold
        is reached. Returns the new failure count."""

        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold and self._opened_at is None:
                self._opened_at = self._failures
            return self._failures

    def record_success(self) -> int:
        """Reset the breaker to CLOSED. Returns the previous failure count
        (0 if the breaker was already closed)."""

        with self._lock:
            previous = self._failures
            self._failures = 0
            self._opened_at = None
            return previous

    def reset(self) -> None:
        """Alias for :meth:`record_success` — explicit hard reset."""

        self.record_success()

    def is_healthy(self) -> bool:
        """``False`` once the breaker has opened (failure_count >=
        failure_threshold). Callers consult this when deciding whether to
        fall back to the legacy fitter vs. attempt a memory-skill
        assembly."""

        with self._lock:
            return self._failures < self._threshold

    def require_healthy(self) -> None:
        """Raise :class:`CircuitOpenError` if the breaker has opened. Use
        this as the guard at the top of a memory-skill call path so the
        fallback is deliberate, not silent."""

        with self._lock:
            if self._failures >= self._threshold:
                raise CircuitOpenError(
                    f"memory-skill circuit breaker is open "
                    f"(failures={self._failures}, threshold={self._threshold}); "
                    "fallback to legacy fitter is a deliberate decision, "
                    "not a silent one"
                )

    def snapshot(self) -> dict[str, Any]:
        """Inspectable summary; safe to serialise (no locks held, all
        primitives)."""

        with self._lock:
            return {
                "failure_threshold": self._threshold,
                "failure_count": self._failures,
                "open": self._failures >= self._threshold,
            }
