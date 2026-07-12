"""Shared worker eligibility rules used by readiness and live routing.

Keeping this predicate free of router/readiness imports makes it the single
source of truth for both the preview and execution paths.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Optional

if TYPE_CHECKING:
    from metaharness.core.types import Task


_HOST_ALIASES = {"claude-code": "claude"}
_TIER_RANK = {"small": 0, "mid": 1, "frontier": 2}


def _tier_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _canonical_host(value: str) -> str:
    normalized = value.strip().lower()
    return _HOST_ALIASES.get(normalized, normalized)


@dataclass(frozen=True)
class WorkerProfile:
    worker_id: str
    tier: Any
    active: bool = True
    roles: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    task_types: tuple[str, ...] = ()
    tiers: tuple[str, ...] = ()
    host: str = ""


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    code: str = "eligible"
    detail: str = ""


def host_chain(environ: Optional[dict[str, str]] = None) -> tuple[str, ...]:
    """Return the normalized outer-host chain carried across CLI spawns."""
    env = os.environ if environ is None else environ
    values = [
        _canonical_host(part)
        for part in env.get("METAHARNESS_HOST_CHAIN", "").split(",")
    ]
    current = _canonical_host(env.get("METAHARNESS_HOST", ""))
    if current:
        values.append(current)
    return tuple(dict.fromkeys(value for value in values if value))


def worker_eligibility(
    task: "Task",
    profile: WorkerProfile,
    *,
    active_host_chain: Optional[Iterable[str]] = None,
) -> EligibilityResult:
    """Decide whether one exact worker may execute ``task``.

    Empty profile role/capability/task-type lists mean unrestricted, preserving
    existing agent definitions. A hard worker pin is exact and is never treated
    as a preference.
    """
    if task.worker_id and task.worker_id != profile.worker_id:
        return EligibilityResult(False, "worker_mismatch", "a different worker is hard-pinned")
    if not profile.active:
        return EligibilityResult(False, "inactive", "worker is retired or inactive")
    profile_tier = _tier_value(profile.tier)
    if profile.tiers and profile_tier not in profile.tiers:
        return EligibilityResult(False, "tier_mismatch", "worker is not registered for this tier")
    if (
        task.tier_hint is not None
        and _TIER_RANK[profile_tier] < _TIER_RANK[_tier_value(task.tier_hint)]
    ):
        return EligibilityResult(False, "tier_mismatch", "worker is below the requested tier floor")
    if profile.task_types and task.task_type.value not in profile.task_types:
        return EligibilityResult(False, "task_type_mismatch", "worker does not support this task type")
    if task.role and task.role not in profile.roles:
        return EligibilityResult(False, "role_mismatch", "worker does not provide the requested role")
    missing = sorted(set(task.required_capabilities) - set(profile.capabilities))
    if missing:
        return EligibilityResult(
            False, "capability_mismatch",
            f"worker lacks required capabilities: {', '.join(missing)}",
        )
    chain = {
        _canonical_host(value)
        for value in (host_chain() if active_host_chain is None else active_host_chain)
    }
    profile_host = _canonical_host(profile.host)
    if profile_host and profile_host in chain:
        return EligibilityResult(
            False, "unsafe_recursion",
            f"host {profile_host!r} already appears in the active harness chain",
        )
    return EligibilityResult(True)


def child_host_environment(host: str, environ: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Return recursion metadata for a child CLI harness, or reject a cycle."""
    host = _canonical_host(host)
    chain = host_chain(environ)
    if host in chain:
        raise RuntimeError(f"unsafe recursive harness spawn: host {host!r} is already active")
    return {
        "METAHARNESS_HOST": host,
        "METAHARNESS_HOST_CHAIN": ",".join((*chain, host)),
    }
