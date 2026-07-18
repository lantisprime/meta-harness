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
#
# FIX-3: ``_DOMAIN_AUTHORITY_FORBIDDEN`` is the canonical set. ``authorize``
# normalises the candidate action (lowercase, whitespace-collapsed,
# underscores -> hyphens) and rejects any form that is one of the canonical
# stems or a morphological extension (deploy + suffix: -s/-ed/-ing/-ment,
# promote + suffix: -s/-ed/-ing, evaluate + suffix, self_approve + suffix,
# widen_visibility + suffix, commit_domain + suffix). The set of
# ``_FORBIDDEN_STEMS`` is the morpheme the rejection logic walks.
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
_FORBIDDEN_STEMS: frozenset[str] = frozenset(
    {
        "deploy",
        "promote",
        "self_approve",
        "self-approve",
        "widen_visibility",
        "widen-visibility",
        "evaluate",
        "commit_domain",
        "commit-domain",
    }
)
_FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    "",
    "s",
    "ed",
    "ing",
    "ment",
    "tion",
    "ation",
    "ions",
    "ations",
    "action",
    "actions",
    "inator",
    "inators",
    "or",
    "ers",
    "er",
    "ive",
    "ive_action",
    "ive-action",
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

        FIX-3: forbidden check is morphological, not literal. The candidate
        action is normalised (lowercase, stripped, separators collapsed)
        and any of the following is rejected as forbidden:

        - the normalised action matches a forbidden canonical action
        - the normalised action matches a forbidden stem + a known
          morphological suffix (``s``, ``ed``, ``ing``, ``ment``, ``tion``,
          ``ation``, ``action``, ``inator`` and their plurals)

        ``allowed_actions`` should be the closed set the parent orchestrator
        has granted for this specialist; ``authorize`` is the gate.
        """

        if not isinstance(allowed_actions, set):
            raise TypeError("allowed_actions must be a set")
        normalized = self._normalize_action(self.action)
        if self._is_forbidden(normalized):
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

    @staticmethod
    def _normalize_action(value: str) -> str:
        lowered = value.strip().lower()
        return lowered.replace(" ", "").replace("__", "_")

    @classmethod
    def _is_forbidden(cls, normalized: str) -> bool:
        """FIX-3: reject a normalized action if it equals a forbidden stem,
        starts with a forbidden stem (or its silent-``e`` form) followed by
        a bounded morphological suffix, or is a hyphen/underscore-joined
        compound whose head is forbidden. The suffix bound of six characters
        covers the standard English derivations (s, ed, ing, ment, tion,
        ation, action, inator) without false-matching unrelated long words.
        """

        if not normalized:
            return False
        for stem in _FORBIDDEN_STEMS:
            stem_forms = {stem}
            if stem.endswith("e") and len(stem) > 1:
                stem_forms.add(stem[:-1])
            for variant in stem_forms:
                if normalized == variant:
                    return True
                if normalized.startswith(variant):
                    remainder = normalized[len(variant):]
                    if remainder and remainder[0] in {"_", "-"} and len(remainder) <= 8:
                        return True
                    if remainder and remainder[0].isalpha() and len(remainder) <= 6:
                        return True
        return False
