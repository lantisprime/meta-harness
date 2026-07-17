"""Bounded, scope-checked specialist task-action contract (META5-MEM-008).

A ``SpecialistTaskAction`` is a typed, frozen action description: it
records WHAT a specialist wants to do, in WHAT scope, with WHAT payload.
Execution authority is not granted here — the type only validates and
records. The building's domain-action commit, visibility widening,
evaluator, promotion, deployment, and self-approval authorities are
explicitly NOT routed through this type.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from metaharness.context import ContextScope
from metaharness.context.models import FrozenModel


class UnauthorizedTaskActionError(Exception):
    """Raised when :meth:`SpecialistTaskAction.authorize` is given an
    allowlist that does not contain the action's action vocabulary slot.

    META5-MEM-008: specialists must not bypass the allowlist; they have no
    self-grant authority.
    """


# Domain-action authorities that specialists MUST NEVER carry. The check is
# belt-and-suspenders against a future allowlist widening: even if some
# caller passes ``allowed_actions={"deploy"}``, the constructor rejects the
# action. This is the static side; :meth:`authorize` is the dynamic side.
_DOMAIN_AUTHORITY_FORBIDDEN: frozenset[str] = frozenset(
    {
        "deploy",
        "promote",
        "self_approve",
        "widen_visibility",
        "evaluate",
        "commit_domain",
    }
)


class SpecialistTaskAction(FrozenModel):
    """Typed, frozen task action for a specialist sub-agent.

    Fields:
        specialist_id — the identity of the proposing specialist.
        action — vocabulary slot inside the allowlist (e.g. ``read_only``).
        scope — the requested scope; :meth:`authorize` confirms it against
            the supplied allowlist.
        payload — opaque task-action detail (must be JSON-serialisable dict).
        created_seq — deterministic in-process counter (injectable clock is
            owned by the broader memory substrate; this is a counter only).
    """

    schema_version: int = 1
    specialist_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    scope: ContextScope
    payload: tuple[tuple[str, Any], ...] = ()
    created_seq: int = Field(default=0, ge=0)
    reason: str | None = None

    FORBIDDEN_ACTIONS: ClassVar[frozenset[str]] = _DOMAIN_AUTHORITY_FORBIDDEN

    def authorize(self, *, allowed_actions: set[str]) -> None:
        """Raise :class:`UnauthorizedTaskActionError` if this action's
        vocabulary slot is not in ``allowed_actions``, or if the action
        belongs to the static forbidden-authority set.

        ``allowed_actions`` should be the closed set the parent orchestrator
        has granted for this specialist; ``authorize`` is the gate.
        """

        if not isinstance(allowed_actions, set):
            raise TypeError("allowed_actions must be a set")
        if self.action in self.FORBIDDEN_ACTIONS:
            raise UnauthorizedTaskActionError(
                f"specialist {self.specialist_id!r} requested forbidden "
                f"domain action {self.action!r}; this authority is never "
                "delegable to a specialist"
            )
        if self.action not in allowed_actions:
            raise UnauthorizedTaskActionError(
                f"specialist {self.specialist_id!r} is not authorized for "
                f"action {self.action!r}; allowed_actions="
                f"{sorted(allowed_actions)}"
            )
