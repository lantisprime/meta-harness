"""LineageWorkspaceManager: one isolated git worktree per candidate lineage.

A worktree is ephemeral candidate state, never memory — the durable
`DiscoveryLineageReceipt` log is the source of truth (charter invariant 8,
reversible operation). All git access goes through argv lists (no shell
interpolation, no symbolic refs — only 40-hex commit shas cross the
boundary), so branch/path names derived from validated identifiers cannot be
used for ref or option injection.

Recovery replays the durable receipt log and cross-checks it against actual
on-disk/git state; anything it cannot positively confirm (dirty, foreign,
symlinked, ambiguous, missing, or tampered) is quarantined rather than
guessed about or deleted.
"""
from __future__ import annotations

import itertools
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Sequence

from pydantic import ValidationError

from metaharness.discovery.models import (
    GIT_SHA_PATTERN,
    DiscoveryLineageEventType,
    DiscoveryLineageReceipt,
)

_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_COMMIT_PATTERN = re.compile(GIT_SHA_PATTERN)
_DEFAULT_RECEIPT_ID_PATTERN = re.compile(r"^lin-rcpt-([0-9a-f]{8})$")

GitRunner = Callable[[list[str], str], str]


def _default_git_runner(argv: list[str], cwd: str) -> str:
    result = subprocess.run(
        ["git", *argv], cwd=cwd, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(argv)!r} failed in {cwd!r}: {result.stderr.strip()}")
    return result.stdout.strip()


class LineageError(ValueError):
    """A lineage operation was rejected — fail-closed, never guessed around."""


class LineagePoisonedError(LineageError):
    """A prior `recover()` failed mid-replay, so this instance's local state
    may be a partially-applied mix of validated and never-fully-validated
    receipts. Every public command and introspection refuses from here on —
    construct a fresh instance and recover() from the actual receipt log
    instead of trusting this instance's local state (META-7 pre-commit fix
    brief #9, F5)."""


@dataclass
class _LineageState:
    lineage_id: str
    campaign_id: str
    parent_lineage_id: str | None
    parent_commit: str
    branch_name: str
    worktree_path: str
    head_commit: str
    tree_hash: str
    sequence: int
    quarantined: bool = False
    quarantine_reason: str | None = None
    # The campaign's declared changed-path boundary (relative prefixes under
    # the worktree root). Empty means no restriction was declared for this
    # lineage. Durable: carried on every `DiscoveryLineageReceipt` for this
    # lineage and reconstructed by `recover()` from the CREATED receipt
    # (META-7 pre-commit fix brief #9, F2).
    allowed_changed_paths: tuple[str, ...] = ()


class LineageWorkspaceManager:
    def __init__(
        self,
        *,
        repo_root: str,
        workspace_root: str,
        git_runner: GitRunner | None = None,
        id_source: Callable[[], str] | None = None,
    ) -> None:
        if not os.path.isabs(repo_root) or not os.path.isabs(workspace_root):
            raise LineageError("repo_root and workspace_root must be absolute paths")
        self._repo_root = os.path.realpath(repo_root)
        self._workspace_root = os.path.realpath(workspace_root)
        self._git = git_runner or _default_git_runner
        self._receipt_seq = itertools.count(0)
        self._id_source = id_source or (lambda: f"lin-rcpt-{next(self._receipt_seq):08x}")
        self._sequence = itertools.count(0)
        self._lineages: dict[str, _LineageState] = {}
        self._branch_owners: dict[str, str] = {}
        self._worktree_owners: dict[str, str] = {}
        self._poisoned = False
        self._poison_reason: str | None = None

    # -- poison ---------------------------------------------------------------

    def _check_not_poisoned(self) -> None:
        if self._poisoned:
            raise LineagePoisonedError(
                f"lineage manager is poisoned ({self._poison_reason}); construct a "
                "fresh instance and recover() from the actual receipt log instead "
                "of trusting this instance's local state"
            )

    def _poison(self, reason: str) -> None:
        self._poisoned = True
        self._poison_reason = reason

    # -- identifiers / containment -----------------------------------------

    @staticmethod
    def _validate_identifier(value: str, label: str) -> None:
        if not _IDENTIFIER_PATTERN.match(value):
            raise LineageError(
                f"{label}={value!r} is not a safe identifier "
                f"(must match {_IDENTIFIER_PATTERN.pattern})"
            )

    @staticmethod
    def _validate_changed_path_prefix(value: str) -> None:
        normalized = value.replace("\\", "/") if isinstance(value, str) else ""
        if (
            not isinstance(value, str)
            or not value
            or normalized.startswith("/")
            or ".." in normalized.split("/")
        ):
            raise LineageError(
                f"allowed changed-path prefix {value!r} must be a non-empty, "
                "relative, traversal-free path prefix"
            )

    @staticmethod
    def _path_within_changed_path_boundary(path: str, allowed_prefixes: tuple[str, ...]) -> bool:
        normalized = path.replace("\\", "/")
        for prefix in allowed_prefixes:
            normalized_prefix = prefix.replace("\\", "/").rstrip("/")
            if normalized == normalized_prefix or normalized.startswith(normalized_prefix + "/"):
                return True
        return False

    def _check_changed_path_boundary(self, state: "_LineageState") -> None:
        """Validate the worktree's ACTUAL changed paths against the
        campaign's declared boundary BEFORE any staging/commit mutation.
        Fails closed (raises) on the first out-of-boundary path found; the
        caller must not have run `git add`/`git commit` yet."""

        if not state.allowed_changed_paths:
            return
        status = self._git(["status", "--porcelain"], state.worktree_path)
        for line in status.splitlines():
            if not line:
                continue
            path_part = line[3:]
            candidates = path_part.split(" -> ", 1) if " -> " in path_part else [path_part]
            for raw_candidate in candidates:
                candidate = raw_candidate.strip().strip('"')
                if not self._path_within_changed_path_boundary(candidate, state.allowed_changed_paths):
                    raise LineageError(
                        f"lineage {state.lineage_id!r} changed path {candidate!r} is outside "
                        f"the campaign's declared changed-path boundary {state.allowed_changed_paths!r} "
                        "(refusing to stage/commit)"
                    )

    @staticmethod
    def _prefix_contained_within(child_prefix: str, parent_prefixes: tuple[str, ...]) -> bool:
        normalized_child = child_prefix.replace("\\", "/").rstrip("/")
        for parent_prefix in parent_prefixes:
            normalized_parent = parent_prefix.replace("\\", "/").rstrip("/")
            if normalized_child == normalized_parent or normalized_child.startswith(normalized_parent + "/"):
                return True
        return False

    def _resolve_child_boundary(
        self, parent_boundary: tuple[str, ...], requested: Sequence[str] | None
    ) -> tuple[str, ...]:
        """A child lineage's changed-path boundary either INHERITS the
        parent's verbatim (`requested is None`) or must be an explicit,
        prefix-contained SUBSET of it — a restricted parent can never
        produce an unrestricted (or wider) child (META-7 pre-commit fix
        brief #9, F2b)."""

        if requested is None:
            return parent_boundary
        candidate = tuple(requested)
        for prefix in candidate:
            self._validate_changed_path_prefix(prefix)
        if parent_boundary:
            if not candidate:
                raise LineageError(
                    "a restricted parent's changed-path boundary can never be "
                    "widened to unrestricted for a child lineage"
                )
            for prefix in candidate:
                if not self._prefix_contained_within(prefix, parent_boundary):
                    raise LineageError(
                        f"child allowed_changed_paths prefix {prefix!r} is not contained "
                        f"within the parent's boundary {parent_boundary!r} (a restricted "
                        "parent can never produce a wider child)"
                    )
        return candidate

    def _derive_paths(self, campaign_id: str, lineage_id: str) -> tuple[str, str]:
        branch_name = f"discovery/{campaign_id}/{lineage_id}"
        worktree_path = os.path.join(self._workspace_root, campaign_id, lineage_id)
        resolved = os.path.realpath(worktree_path)
        root_with_sep = self._workspace_root + os.sep
        if resolved != self._workspace_root and not resolved.startswith(root_with_sep):
            raise LineageError("derived worktree_path escapes workspace_root containment")
        return branch_name, worktree_path

    def _verify_commit(self, commit: str) -> None:
        if not _COMMIT_PATTERN.match(commit):
            raise LineageError(f"{commit!r} is not a full 40-hex commit sha")
        try:
            self._git(["cat-file", "-e", f"{commit}^{{commit}}"], self._repo_root)
        except RuntimeError as exc:
            raise LineageError(f"{commit!r} does not resolve to a commit") from exc

    def _next_sequence(self) -> int:
        return next(self._sequence)

    # -- creation -------------------------------------------------------

    def create_lineage(
        self,
        *,
        campaign_id: str,
        lineage_id: str,
        parent_lineage_id: str | None = None,
        baseline_commit: str | None = None,
        allowed_changed_paths: Sequence[str] | None = None,
    ) -> DiscoveryLineageReceipt:
        self._check_not_poisoned()
        self._validate_identifier(campaign_id, "campaign_id")
        self._validate_identifier(lineage_id, "lineage_id")
        if lineage_id in self._lineages:
            raise LineageError(f"lineage_id {lineage_id!r} already exists")
        if parent_lineage_id == lineage_id:
            raise LineageError("a lineage cannot be its own parent")

        if parent_lineage_id is None:
            if baseline_commit is None:
                raise LineageError("baseline_commit is required for a baseline-rooted lineage")
            base_commit = baseline_commit
            effective_allowed_changed_paths = tuple(allowed_changed_paths or ())
            for prefix in effective_allowed_changed_paths:
                self._validate_changed_path_prefix(prefix)
        else:
            if baseline_commit is not None:
                raise LineageError("baseline_commit must not be set alongside parent_lineage_id")
            parent = self._lineages.get(parent_lineage_id)
            if parent is None:
                raise LineageError(f"unknown parent_lineage_id {parent_lineage_id!r}")
            if parent.campaign_id != campaign_id:
                raise LineageError("parent lineage belongs to a different campaign")
            if parent.quarantined:
                raise LineageError(
                    f"parent lineage {parent_lineage_id!r} is quarantined: "
                    f"{parent.quarantine_reason}"
                )
            base_commit = parent.head_commit
            effective_allowed_changed_paths = self._resolve_child_boundary(
                parent.allowed_changed_paths, allowed_changed_paths
            )

        self._verify_commit(base_commit)
        branch_name, worktree_path = self._derive_paths(campaign_id, lineage_id)
        if branch_name in self._branch_owners:
            raise LineageError(f"branch {branch_name!r} is already in use")
        if worktree_path in self._worktree_owners:
            raise LineageError(f"worktree_path {worktree_path!r} is already in use")

        # `tree_hash` is a property of the COMMIT, not the worktree — resolve
        # it against repo_root, BEFORE any worktree mutation, so every
        # receipt field is already known and the full receipt can be built
        # (and validated) BEFORE `git worktree add` ever runs (META-7
        # pre-commit fix brief #9, F4): an invalid/raising id_source, or any
        # other receipt-construction failure, now fails closed before any
        # external mutation instead of leaving a durable-looking, receiptless
        # worktree/state behind.
        tree_hash = self._git(["rev-parse", f"{base_commit}^{{tree}}"], self._repo_root)
        # Validate the injected git runner's `tree_hash` return BEFORE
        # consuming the sequence counter (META-7 pre-commit fix brief #11,
        # F4c): the receipt's `tree_hash` field enforces this same 40-hex
        # pattern, but relying on THAT to fail would do so only after
        # `_next_sequence()` already ran below — reproducing the exact
        # retry-gap bug F4r fixed for id_source, just for a different
        # fallible runner-derived input.
        if not _COMMIT_PATTERN.match(tree_hash):
            raise LineageError(f"git runner returned a malformed tree_hash: {tree_hash!r}")

        # Validate the id_source's output BEFORE consuming the sequence
        # counter (META-7 pre-commit fix brief #10, F4r): consuming the
        # counter first left a GAP on a failed-then-retried attempt (the
        # counter never rewinds), which a later `recover()` correctly
        # rejects as "sequence has a gap" even though nothing was ever
        # durably published under the skipped number.
        receipt_id = self._id_source()
        if not isinstance(receipt_id, str) or len(receipt_id) < 1:
            raise LineageError(f"id_source produced an invalid receipt_id: {receipt_id!r}")
        sequence = self._next_sequence()

        receipt = DiscoveryLineageReceipt(
            receipt_id=receipt_id,
            campaign_id=campaign_id,
            lineage_id=lineage_id,
            attempt_id=None,
            event_type=DiscoveryLineageEventType.CREATED,
            parent_lineage_id=parent_lineage_id,
            parent_commit=base_commit,
            tree_hash=tree_hash,
            commit_hash=None,
            branch_name=branch_name,
            worktree_path=worktree_path,
            sequence=sequence,
            allowed_changed_paths=effective_allowed_changed_paths,
        )

        self._git(["worktree", "add", "-b", branch_name, worktree_path, base_commit], self._repo_root)

        state = _LineageState(
            lineage_id=lineage_id,
            campaign_id=campaign_id,
            parent_lineage_id=parent_lineage_id,
            parent_commit=base_commit,
            branch_name=branch_name,
            worktree_path=worktree_path,
            head_commit=base_commit,
            tree_hash=tree_hash,
            sequence=sequence,
            allowed_changed_paths=effective_allowed_changed_paths,
        )
        self._lineages[lineage_id] = state
        self._branch_owners[branch_name] = lineage_id
        self._worktree_owners[worktree_path] = lineage_id

        return receipt

    # -- checkpoint / child commit ---------------------------------------

    def _require_state(self, lineage_id: str) -> _LineageState:
        state = self._lineages.get(lineage_id)
        if state is None:
            raise LineageError(f"unknown lineage_id {lineage_id!r}")
        if state.quarantined:
            raise LineageError(f"lineage {lineage_id!r} is quarantined: {state.quarantine_reason}")
        if not os.path.isdir(state.worktree_path):
            raise LineageError(f"lineage {lineage_id!r} worktree is missing at {state.worktree_path!r}")
        return state

    def _commit_worktree(
        self,
        lineage_id: str,
        *,
        attempt_id: str,
        event_type: DiscoveryLineageEventType,
        message: str,
    ) -> DiscoveryLineageReceipt:
        self._check_not_poisoned()
        # Validate every caller-supplied input that will end up in the
        # returned receipt BEFORE any staging/commit mutation — a bad
        # attempt_id/message must fail closed here, not only surface when
        # the receipt is finally constructed after git has already staged
        # or committed the worktree.
        if not isinstance(attempt_id, str) or len(attempt_id) < 1:
            raise LineageError(f"attempt_id={attempt_id!r} must be a non-empty string")
        if not isinstance(message, str) or len(message) < 1:
            raise LineageError(f"message={message!r} must be a non-empty string")

        # Precompute + validate the id_source's output BEFORE any staging/
        # commit mutation (META-7 pre-commit fix brief #9, F4). Every OTHER
        # receipt field is either already validated (attempt_id/message
        # above) or copied verbatim from already-validated durable state
        # (campaign_id/parent_lineage_id/parent_commit/branch_name/
        # worktree_path/allowed_changed_paths) or is git's own guaranteed-
        # valid-format commit/tree sha — so once receipt_id is confirmed
        # valid, the POST-commit receipt construction below is infallible:
        # nothing left can raise, so no rollback-after-mutation is needed.
        receipt_id = self._id_source()
        if not isinstance(receipt_id, str) or len(receipt_id) < 1:
            raise LineageError(f"id_source produced an invalid receipt_id: {receipt_id!r}")

        state = self._require_state(lineage_id)
        prior_head = state.head_commit

        # Verify the worktree's actual HEAD still matches our recorded
        # expectation BEFORE staging/committing anything. If something moved
        # HEAD out from under us (a stray checkout/reset by another process),
        # this rejects the operation before any mutation — not after.
        actual_head_before = self._git(["rev-parse", "HEAD"], state.worktree_path)
        if actual_head_before != prior_head:
            raise LineageError(
                f"lineage {lineage_id!r} worktree HEAD {actual_head_before} does not "
                f"match the recorded head {prior_head} (moved HEAD; refusing to "
                "stage/commit)"
            )

        # Validate the campaign's declared changed-path boundary against the
        # worktree's ACTUAL changed paths before staging/committing anything
        # (fast-path courtesy check; the authoritative, race-free gate is the
        # staged-index re-check performed by `_stage_changes` below).
        self._check_changed_path_boundary(state)

        self._stage_changes(state)
        self._git(["commit", "--allow-empty", "-m", message], state.worktree_path)
        status = self._git(["status", "--porcelain"], state.worktree_path)
        if status:
            raise LineageError(f"lineage {lineage_id!r} worktree is not clean after commit")

        new_commit = self._git(["rev-parse", "HEAD"], state.worktree_path)
        actual_parent = self._git(["rev-parse", "HEAD^"], state.worktree_path)
        if actual_parent != prior_head:
            raise LineageError(
                f"lineage {lineage_id!r} checkpoint parent {actual_parent} does not "
                f"exactly match the recorded prior head {prior_head} "
                "(exact-parent-ancestry violation)"
            )
        tree_hash = self._git(["rev-parse", "HEAD^{tree}"], state.worktree_path)

        # Validate the injected git runner's `new_commit`/`tree_hash`
        # returns BEFORE consuming the sequence counter (META-7 pre-commit
        # fix brief #11, F4c) — same retry-gap hazard as `create_lineage`
        # above: the commit already happened (git mutation is unavoidable
        # here, unlike `create_lineage`), but the DURABLE sequence must
        # still never be consumed for a receipt that cannot be constructed.
        if not _COMMIT_PATTERN.match(new_commit):
            raise LineageError(f"git runner returned a malformed commit hash: {new_commit!r}")
        if not _COMMIT_PATTERN.match(tree_hash):
            raise LineageError(f"git runner returned a malformed tree_hash: {tree_hash!r}")

        state.head_commit = new_commit
        state.tree_hash = tree_hash
        state.sequence = self._next_sequence()

        return DiscoveryLineageReceipt(
            receipt_id=receipt_id,
            campaign_id=state.campaign_id,
            lineage_id=lineage_id,
            attempt_id=attempt_id,
            event_type=event_type,
            parent_lineage_id=state.parent_lineage_id,
            parent_commit=state.parent_commit,
            tree_hash=tree_hash,
            commit_hash=new_commit,
            branch_name=state.branch_name,
            worktree_path=state.worktree_path,
            sequence=state.sequence,
            allowed_changed_paths=state.allowed_changed_paths,
        )

    def _stage_changes(self, state: "_LineageState") -> None:
        """Stage the worktree's changes for the upcoming commit. When a
        changed-path boundary is declared, NEVER a bare `git add -A`: stage
        with an explicit pathspec derived from the boundary (so nothing
        outside it can ever be staged by this call), then verify what
        actually landed in the index (`git diff --cached --name-only`)
        BEFORE the caller commits — closing the TOCTOU between the earlier
        `git status` boundary check and the commit, where some OTHER actor
        could have staged an out-of-boundary path in between. Fails closed
        and resets the index (never commits a partial/tainted stage) if
        anything staged is outside the boundary (META-7 pre-commit fix
        brief #9, F2c)."""

        if not state.allowed_changed_paths:
            self._git(["add", "-A"], state.worktree_path)
            return

        self._git(["add", "-A", "--", *state.allowed_changed_paths], state.worktree_path)
        staged = self._git(["diff", "--cached", "--name-only"], state.worktree_path)
        for raw_line in staged.splitlines():
            path = raw_line.strip()
            if not path:
                continue
            if not self._path_within_changed_path_boundary(path, state.allowed_changed_paths):
                self._git(["reset"], state.worktree_path)
                raise LineageError(
                    f"lineage {state.lineage_id!r} staged path {path!r} is outside the "
                    f"campaign's declared changed-path boundary {state.allowed_changed_paths!r} "
                    "(race detected after staging; index reset, refusing to commit)"
                )

    def checkpoint(self, lineage_id: str, *, attempt_id: str, message: str = "checkpoint") -> DiscoveryLineageReceipt:
        return self._commit_worktree(
            lineage_id, attempt_id=attempt_id, event_type=DiscoveryLineageEventType.CHECKPOINTED, message=message
        )

    def commit_child(self, lineage_id: str, *, attempt_id: str, message: str = "candidate") -> DiscoveryLineageReceipt:
        return self._commit_worktree(
            lineage_id, attempt_id=attempt_id, event_type=DiscoveryLineageEventType.CHILD_COMMITTED, message=message
        )

    # -- introspection ----------------------------------------------------

    def worktree_path(self, lineage_id: str) -> str:
        self._check_not_poisoned()
        return self._require_state(lineage_id).worktree_path

    def head_commit(self, lineage_id: str) -> str:
        self._check_not_poisoned()
        return self._require_state(lineage_id).head_commit

    def is_quarantined(self, lineage_id: str) -> bool:
        self._check_not_poisoned()
        state = self._lineages.get(lineage_id)
        return state is not None and state.quarantined

    # -- recovery -----------------------------------------------------------

    def _quarantine(
        self, state: _LineageState, *, attempt_id: str | None, reason: str
    ) -> DiscoveryLineageReceipt:
        state.quarantined = True
        state.quarantine_reason = reason
        state.sequence = self._next_sequence()
        return DiscoveryLineageReceipt(
            receipt_id=self._id_source(),
            campaign_id=state.campaign_id,
            lineage_id=state.lineage_id,
            attempt_id=attempt_id,
            event_type=DiscoveryLineageEventType.QUARANTINED,
            parent_lineage_id=state.parent_lineage_id,
            parent_commit=state.parent_commit,
            tree_hash=state.tree_hash,
            commit_hash=None,
            branch_name=state.branch_name,
            worktree_path=state.worktree_path,
            sequence=state.sequence,
            detail=reason,
            allowed_changed_paths=state.allowed_changed_paths,
        )

    def recover(self, receipts: Sequence[DiscoveryLineageReceipt]) -> tuple[DiscoveryLineageReceipt, ...]:
        """Replay a durable lineage-receipt log and rebuild in-memory state.

        Fails closed (raises) on structurally corrupt input — sequence
        gaps/duplicates, an unknown/self/quarantined parent, or a branch/path
        claimed by two different lineages. Once replay succeeds, cross-checks
        each lineage's actual on-disk worktree and quarantines (does not
        delete or guess about) anything dirty, foreign, symlinked, ambiguous,
        missing, or tampered. Returns the newly minted quarantine receipts so
        the caller can persist them to the durable journal.
        """

        self._check_not_poisoned()
        # Revalidate every receipt from its own JSON dump before trusting it.
        # A `model_copy(update=...)` bypasses pydantic validation entirely, so
        # a caller could hand us an object whose fields were mutated after
        # construction while its `receipt_hash` still reflects the ORIGINAL
        # (pre-mutation) content. Round-tripping through JSON re-runs the
        # self-hash wrap-validator, which rejects that stale-hash object.
        revalidated: list[DiscoveryLineageReceipt] = []
        for receipt in receipts:
            try:
                revalidated.append(DiscoveryLineageReceipt.model_validate(receipt.model_dump(mode="json")))
            except ValidationError as exc:
                raise LineageError(f"lineage receipt failed self-hash revalidation: {exc}") from exc

        ordered = sorted(revalidated, key=lambda r: r.sequence)
        seen_sequences: set[int] = set()
        seen_receipt_ids: set[str] = set()
        for receipt in ordered:
            if receipt.sequence in seen_sequences:
                raise LineageError(f"duplicate lineage receipt sequence {receipt.sequence}")
            seen_sequences.add(receipt.sequence)
            if receipt.receipt_id in seen_receipt_ids:
                raise LineageError(f"duplicate lineage receipt_id {receipt.receipt_id!r}")
            seen_receipt_ids.add(receipt.receipt_id)
        if ordered and sorted(seen_sequences) != list(range(len(ordered))):
            raise LineageError("lineage receipt sequence has a gap")

        try:
            # Advance the sequence counter past this durable high-water mark
            # (sequences are gap-checked contiguous above, so len(ordered) IS
            # that mark) BEFORE replay can mint any new (quarantine) receipts.
            self._sequence = itertools.count(len(ordered))

            # The default receipt-ID generator is a SEPARATE identity space from
            # sequence numbers — IDs need not be contiguous or aligned with
            # len(ordered) (a caller may have used a different id_source for
            # some receipts, or simply skipped values). Advance past the MAXIMUM
            # default-pattern suffix actually seen, not merely len(ordered),
            # so a sparse/high existing ID is never undercut and collided with.
            max_default_receipt_id_suffix = -1
            for receipt in ordered:
                match = _DEFAULT_RECEIPT_ID_PATTERN.match(receipt.receipt_id)
                if match:
                    max_default_receipt_id_suffix = max(max_default_receipt_id_suffix, int(match.group(1), 16))
            self._receipt_seq = itertools.count(max_default_receipt_id_suffix + 1)

            for receipt in ordered:
                self._apply_receipt(receipt)

            new_receipts: list[DiscoveryLineageReceipt] = []
            realpaths_claimed: dict[str, str] = {}
            for lineage_id, state in sorted(self._lineages.items()):
                if state.quarantined:
                    continue
                reason = self._detect_worktree_anomaly(state, realpaths_claimed)
                if reason is not None:
                    new_receipts.append(self._quarantine(state, attempt_id=None, reason=reason))
            return tuple(new_receipts)
        except Exception as exc:
            # ANY failure once replay has started mutating this instance's
            # state must never leave a partially-recovered, seemingly-usable
            # instance behind — an earlier receipt in `ordered` may already
            # have published a lineage into `self._lineages`/`_branch_owners`/
            # `_worktree_owners` before a LATER receipt raised. Poison
            # (META-7 pre-commit fix brief #9, F5).
            if not self._poisoned:
                self._poison(f"recover() failed mid-replay: {exc}")
            raise

    def _apply_receipt(self, receipt: DiscoveryLineageReceipt) -> None:
        if receipt.event_type == DiscoveryLineageEventType.CREATED:
            if receipt.lineage_id in self._lineages:
                raise LineageError(f"duplicate CREATED receipt for lineage {receipt.lineage_id!r}")
            self._validate_identifier(receipt.campaign_id, "campaign_id")
            self._validate_identifier(receipt.lineage_id, "lineage_id")

            expected_branch, expected_worktree_path = self._derive_paths(receipt.campaign_id, receipt.lineage_id)
            if receipt.branch_name != expected_branch:
                raise LineageError(
                    f"lineage {receipt.lineage_id!r} branch_name {receipt.branch_name!r} "
                    f"does not match the derived branch {expected_branch!r} (branch forgery)"
                )
            if receipt.worktree_path != expected_worktree_path:
                raise LineageError(
                    f"lineage {receipt.lineage_id!r} worktree_path {receipt.worktree_path!r} "
                    f"does not match the derived path {expected_worktree_path!r} (path forgery)"
                )

            if receipt.parent_lineage_id is not None:
                parent = self._lineages.get(receipt.parent_lineage_id)
                if parent is None:
                    raise LineageError(
                        f"lineage {receipt.lineage_id!r} claims unknown parent "
                        f"{receipt.parent_lineage_id!r}"
                    )
                if parent.quarantined:
                    raise LineageError(
                        f"lineage {receipt.lineage_id!r} claims quarantined parent "
                        f"{receipt.parent_lineage_id!r}"
                    )
                if parent.campaign_id != receipt.campaign_id:
                    raise LineageError(
                        f"lineage {receipt.lineage_id!r} campaign_id {receipt.campaign_id!r} "
                        f"does not match parent {receipt.parent_lineage_id!r}'s campaign "
                        f"{parent.campaign_id!r} (child/parent campaign binding violation)"
                    )
                if parent.head_commit != receipt.parent_commit:
                    raise LineageError(
                        f"lineage {receipt.lineage_id!r} parent_commit does not match "
                        f"parent {receipt.parent_lineage_id!r}'s recorded head at the "
                        "time of branching (exact-parent-ancestry violation)"
                    )
                # A restricted parent can never produce an unrestricted or
                # wider child boundary — a lineage whose boundary cannot be
                # positively reconstructed as a valid inheritance/subset of
                # its parent's fails closed here (quarantined only applies
                # to on-disk anomalies AFTER replay succeeds; a structurally
                # invalid boundary claim is rejected outright, same as any
                # other forged CREATED field) (META-7 pre-commit fix brief
                # #9, F2).
                if parent.allowed_changed_paths and not receipt.allowed_changed_paths:
                    raise LineageError(
                        f"lineage {receipt.lineage_id!r} claims an unrestricted changed-path "
                        f"boundary, but its parent {receipt.parent_lineage_id!r} is restricted "
                        f"to {parent.allowed_changed_paths!r} (a restricted parent can never "
                        "produce a wider child)"
                    )
                if parent.allowed_changed_paths:
                    for child_prefix in receipt.allowed_changed_paths:
                        if not self._prefix_contained_within(child_prefix, parent.allowed_changed_paths):
                            raise LineageError(
                                f"lineage {receipt.lineage_id!r} allowed_changed_paths prefix "
                                f"{child_prefix!r} is not contained within its parent "
                                f"{receipt.parent_lineage_id!r}'s boundary "
                                f"{parent.allowed_changed_paths!r}"
                            )
            if receipt.branch_name in self._branch_owners:
                raise LineageError(f"branch {receipt.branch_name!r} claimed by two lineages")
            if receipt.worktree_path in self._worktree_owners:
                raise LineageError(f"worktree_path {receipt.worktree_path!r} claimed by two lineages")

            self._verify_commit(receipt.parent_commit)
            actual_tree = self._git(["rev-parse", f"{receipt.parent_commit}^{{tree}}"], self._repo_root)
            if actual_tree != receipt.tree_hash:
                raise LineageError(
                    f"lineage {receipt.lineage_id!r} tree_hash {receipt.tree_hash!r} does "
                    f"not match the actual tree {actual_tree!r} of commit "
                    f"{receipt.parent_commit!r} (exact root tree violation)"
                )

            state = _LineageState(
                lineage_id=receipt.lineage_id,
                campaign_id=receipt.campaign_id,
                parent_lineage_id=receipt.parent_lineage_id,
                parent_commit=receipt.parent_commit,
                branch_name=receipt.branch_name,
                worktree_path=receipt.worktree_path,
                head_commit=receipt.parent_commit,
                tree_hash=receipt.tree_hash,
                sequence=receipt.sequence,
                allowed_changed_paths=receipt.allowed_changed_paths,
            )
            self._lineages[receipt.lineage_id] = state
            self._branch_owners[receipt.branch_name] = receipt.lineage_id
            self._worktree_owners[receipt.worktree_path] = receipt.lineage_id
            return

        state = self._lineages.get(receipt.lineage_id)
        if state is None:
            raise LineageError(
                f"receipt for unknown lineage {receipt.lineage_id!r} "
                f"(event_type={receipt.event_type.value})"
            )

        # Immutable per-lineage identity fields: every later receipt for a
        # known lineage must keep restating the SAME campaign/parent/branch/
        # path recorded at CREATED time. None of these can legitimately
        # change across a lineage's life.
        if receipt.campaign_id != state.campaign_id:
            raise LineageError(
                f"lineage {receipt.lineage_id!r} receipt campaign_id {receipt.campaign_id!r} "
                f"does not match its recorded campaign {state.campaign_id!r} (immutable field changed)"
            )
        if receipt.parent_lineage_id != state.parent_lineage_id:
            raise LineageError(
                f"lineage {receipt.lineage_id!r} receipt parent_lineage_id "
                f"{receipt.parent_lineage_id!r} does not match its recorded parent "
                f"{state.parent_lineage_id!r} (immutable field changed)"
            )
        if receipt.parent_commit != state.parent_commit:
            raise LineageError(
                f"lineage {receipt.lineage_id!r} receipt parent_commit {receipt.parent_commit!r} "
                f"does not match its recorded parent_commit {state.parent_commit!r} "
                "(immutable field changed)"
            )
        if receipt.branch_name != state.branch_name:
            raise LineageError(
                f"lineage {receipt.lineage_id!r} receipt branch_name {receipt.branch_name!r} "
                f"does not match its recorded branch {state.branch_name!r} (immutable field changed)"
            )
        if receipt.worktree_path != state.worktree_path:
            raise LineageError(
                f"lineage {receipt.lineage_id!r} receipt worktree_path {receipt.worktree_path!r} "
                f"does not match its recorded path {state.worktree_path!r} (immutable field changed)"
            )
        if receipt.allowed_changed_paths != state.allowed_changed_paths:
            raise LineageError(
                f"lineage {receipt.lineage_id!r} receipt allowed_changed_paths "
                f"{receipt.allowed_changed_paths!r} does not match its recorded boundary "
                f"{state.allowed_changed_paths!r} (immutable field changed)"
            )

        if receipt.event_type in (
            DiscoveryLineageEventType.CHECKPOINTED,
            DiscoveryLineageEventType.CHILD_COMMITTED,
        ):
            commit_hash = receipt.commit_hash
            if commit_hash is None:
                raise LineageError(f"{receipt.event_type.value} receipt for {receipt.lineage_id!r} has no commit_hash")
            self._verify_commit(commit_hash)
            actual_parent = self._git(["rev-parse", f"{commit_hash}^"], self._repo_root)
            if actual_parent != state.head_commit:
                raise LineageError(
                    f"lineage {receipt.lineage_id!r} commit {commit_hash!r} parent "
                    f"{actual_parent!r} does not exactly match the recorded prior head "
                    f"{state.head_commit!r} (skipped-checkpoint / exact-parent-ancestry violation)"
                )
            actual_tree = self._git(["rev-parse", f"{commit_hash}^{{tree}}"], self._repo_root)
            if actual_tree != receipt.tree_hash:
                raise LineageError(
                    f"lineage {receipt.lineage_id!r} tree_hash {receipt.tree_hash!r} does "
                    f"not match the actual tree {actual_tree!r} of commit {commit_hash!r} "
                    "(exact root tree violation)"
                )
            state.head_commit = commit_hash
            state.tree_hash = receipt.tree_hash
            state.sequence = receipt.sequence
        elif receipt.event_type == DiscoveryLineageEventType.QUARANTINED:
            state.quarantined = True
            state.quarantine_reason = receipt.detail
            state.sequence = receipt.sequence
        elif receipt.event_type == DiscoveryLineageEventType.RECOVERED:
            state.quarantined = False
            state.quarantine_reason = None
            state.sequence = receipt.sequence

    def _detect_worktree_anomaly(
        self, state: _LineageState, realpaths_claimed: dict[str, str]
    ) -> str | None:
        path = state.worktree_path
        if os.path.islink(path):
            return f"worktree_path {path!r} is a symlink, not a real directory"
        if not os.path.isdir(path):
            return f"worktree_path {path!r} is missing"

        resolved = os.path.realpath(path)
        root_with_sep = self._workspace_root + os.sep
        if resolved != self._workspace_root and not resolved.startswith(root_with_sep):
            return f"worktree_path {path!r} resolves outside workspace_root containment"

        prior_owner = realpaths_claimed.get(resolved)
        if prior_owner is not None:
            return f"worktree real path {resolved!r} is already claimed by lineage {prior_owner!r} (symlink-alias ambiguity)"
        realpaths_claimed[resolved] = state.lineage_id

        try:
            registered = self._git(["worktree", "list", "--porcelain"], self._repo_root)
        except RuntimeError as exc:
            return f"could not enumerate git worktrees: {exc}"
        if resolved not in {os.path.realpath(line.split(" ", 1)[1]) for line in registered.splitlines() if line.startswith("worktree ")}:
            return f"worktree_path {path!r} is not a registered git worktree (foreign directory)"

        try:
            status = self._git(["status", "--porcelain"], path)
        except RuntimeError as exc:
            return f"could not read git status for {path!r}: {exc}"
        if status:
            return f"worktree {path!r} has uncommitted changes (dirty recovery state)"

        try:
            actual_head = self._git(["rev-parse", "HEAD"], path)
        except RuntimeError as exc:
            return f"could not resolve HEAD for {path!r}: {exc}"
        if actual_head != state.head_commit:
            return (
                f"worktree {path!r} HEAD {actual_head} does not match the durable "
                f"receipt's recorded head {state.head_commit} (tampered state)"
            )

        # HEAD commit matching is not enough: two different branches (or a
        # detached HEAD) can point at the same commit. The worktree must
        # actually have the derived durable branch checked out.
        expected_ref = f"refs/heads/{state.branch_name}"
        try:
            checked_out_ref = self._git(["symbolic-ref", "HEAD"], path)
        except RuntimeError as exc:
            return f"worktree {path!r} HEAD is not on any branch (detached): {exc}"
        if checked_out_ref != expected_ref:
            return (
                f"worktree {path!r} has {checked_out_ref!r} checked out, expected "
                f"{expected_ref!r} (branch binding violation)"
            )
        return None
