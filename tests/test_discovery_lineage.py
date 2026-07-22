"""META-7: LineageWorkspaceManager happy path + adversarial coverage.

Exercises real `git` (argv-only, no shell) against a throwaway repo per test
so worktree/branch/commit/status/HEAD assertions reflect actual git
semantics, not a mocked approximation. Covers the META-7 pre-commit fix
brief #8 findings: stale-hash replay, branch/path forgery, skipped-checkpoint
ancestry, moved-HEAD-before-mutation, and post-recovery append/re-replay.
"""
from __future__ import annotations

import os
import subprocess

import pytest
from pydantic import ValidationError

from metaharness.discovery.lineage import LineageError, LineageWorkspaceManager
from metaharness.discovery.models import DiscoveryLineageEventType, DiscoveryLineageReceipt


def _git(argv: list[str], cwd: str) -> str:
    result = subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(["init", "-q"], str(repo_root))
    _git(["config", "user.email", "test@example.com"], str(repo_root))
    _git(["config", "user.name", "Test"], str(repo_root))
    (repo_root / "README.md").write_text("baseline\n")
    _git(["add", "-A"], str(repo_root))
    _git(["commit", "-q", "-m", "baseline"], str(repo_root))
    baseline_commit = _git(["rev-parse", "HEAD"], str(repo_root))
    baseline_tree = _git(["rev-parse", f"{baseline_commit}^{{tree}}"], str(repo_root))
    return str(repo_root), baseline_commit, baseline_tree


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return str(ws)


@pytest.fixture()
def manager(repo, workspace):
    repo_root, _, _ = repo
    return LineageWorkspaceManager(repo_root=repo_root, workspace_root=workspace)


def make_created_receipt(*, campaign_id, lineage_id, worktree_path, baseline_commit, baseline_tree, branch_name=None, sequence=0, **overrides):
    """Build a valid, self-hashed CREATED receipt. Always goes through
    `model_validate` (never `model_copy`) so the hash is freshly computed
    from whatever fields are actually set — a stale-hash object never leaks
    out of this helper by accident."""

    payload = {
        "campaign_id": campaign_id,
        "lineage_id": lineage_id,
        "attempt_id": None,
        "event_type": "created",
        "parent_lineage_id": None,
        "parent_commit": baseline_commit,
        "tree_hash": baseline_tree,
        "commit_hash": None,
        "branch_name": branch_name or f"discovery/{campaign_id}/{lineage_id}",
        "worktree_path": worktree_path,
        "sequence": sequence,
        "receipt_id": f"r{sequence}",
    }
    payload.update(overrides)
    return DiscoveryLineageReceipt.model_validate(payload)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_create_baseline_lineage(manager, repo):
    _, baseline_commit, _ = repo
    receipt = manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    assert receipt.event_type is DiscoveryLineageEventType.CREATED
    assert receipt.parent_lineage_id is None
    assert receipt.parent_commit == baseline_commit
    assert os.path.isdir(manager.worktree_path("lin1"))
    assert manager.head_commit("lin1") == baseline_commit


def test_checkpoint_then_child_commit(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    with open(os.path.join(worktree, "candidate.txt"), "w") as f:
        f.write("attempt work\n")

    checkpoint = manager.checkpoint("lin1", attempt_id="att1")
    assert checkpoint.event_type is DiscoveryLineageEventType.CHECKPOINTED
    assert checkpoint.commit_hash is not None
    assert checkpoint.commit_hash != baseline_commit
    assert manager.head_commit("lin1") == checkpoint.commit_hash

    with open(os.path.join(worktree, "candidate.txt"), "a") as f:
        f.write("more work\n")
    child = manager.commit_child("lin1", attempt_id="att1")
    assert child.event_type is DiscoveryLineageEventType.CHILD_COMMITTED
    assert child.commit_hash != checkpoint.commit_hash
    parent_of_child = _git(["rev-parse", "HEAD^"], worktree)
    assert parent_of_child == checkpoint.commit_hash


def test_checkpoint_with_no_changes_is_allowed(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    receipt = manager.checkpoint("lin1", attempt_id="att1")
    assert receipt.tree_hash is not None


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #8, P1-1: changed-path boundary enforcement
# ---------------------------------------------------------------------------


def test_checkpoint_rejects_out_of_boundary_changed_path_before_any_mutation(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(
        campaign_id="camp1",
        lineage_id="lin1",
        baseline_commit=baseline_commit,
        allowed_changed_paths=("src/",),
    )
    worktree = manager.worktree_path("lin1")
    os.makedirs(os.path.join(worktree, "src"))
    with open(os.path.join(worktree, "src", "ok.txt"), "w") as f:
        f.write("in boundary\n")
    with open(os.path.join(worktree, "outside.txt"), "w") as f:
        f.write("out of boundary\n")

    head_before = manager.head_commit("lin1")
    with pytest.raises(LineageError, match="changed-path boundary"):
        manager.checkpoint("lin1", attempt_id="att1")
    # Fail closed BEFORE any mutation: HEAD unchanged and `git add -A` never
    # ran (the out-of-boundary file is still untracked).
    assert manager.head_commit("lin1") == head_before
    status = _git(["status", "--porcelain"], worktree)
    assert "outside.txt" in status
    assert "?? outside.txt" in status or "A  outside.txt" not in status


def test_checkpoint_allows_in_boundary_changed_paths(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(
        campaign_id="camp1",
        lineage_id="lin1",
        baseline_commit=baseline_commit,
        allowed_changed_paths=("src/",),
    )
    worktree = manager.worktree_path("lin1")
    os.makedirs(os.path.join(worktree, "src"))
    with open(os.path.join(worktree, "src", "ok.txt"), "w") as f:
        f.write("in boundary\n")

    receipt = manager.checkpoint("lin1", attempt_id="att1")
    assert receipt.commit_hash is not None
    assert manager.head_commit("lin1") == receipt.commit_hash


def test_create_lineage_rejects_absolute_or_traversal_boundary_prefix(manager, repo):
    _, baseline_commit, _ = repo
    with pytest.raises(LineageError):
        manager.create_lineage(
            campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit,
            allowed_changed_paths=("/etc",),
        )
    with pytest.raises(LineageError):
        manager.create_lineage(
            campaign_id="camp1", lineage_id="lin2", baseline_commit=baseline_commit,
            allowed_changed_paths=("../escape",),
        )


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #9, F2: changed-path boundary durable/
# inherited/race-free
# ---------------------------------------------------------------------------


def test_recovered_lineage_boundary_is_restored_from_created_receipt(manager, repo):
    """The boundary must be positively reconstructed from the durable
    CREATED receipt on recovery -- never silently defaulted to
    unrestricted."""

    _, baseline_commit, _ = repo
    created = manager.create_lineage(
        campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit,
        allowed_changed_paths=("src/",),
    )
    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    fresh_manager.recover([created])

    worktree = fresh_manager.worktree_path("lin1")
    with open(os.path.join(worktree, "outside.txt"), "w") as f:
        f.write("out of boundary\n")
    with pytest.raises(LineageError, match="changed-path boundary"):
        fresh_manager.checkpoint("lin1", attempt_id="att1")


def test_child_lineage_inherits_parent_restricted_boundary(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(
        campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit,
        allowed_changed_paths=("src/",),
    )
    child = manager.create_lineage(campaign_id="camp1", lineage_id="lin2", parent_lineage_id="lin1")
    assert child.allowed_changed_paths == ("src/",)

    worktree = manager.worktree_path("lin2")
    with open(os.path.join(worktree, "outside.txt"), "w") as f:
        f.write("nope\n")
    with pytest.raises(LineageError, match="changed-path boundary"):
        manager.checkpoint("lin2", attempt_id="att2")


def test_child_lineage_cannot_widen_restricted_parent_boundary(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(
        campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit,
        allowed_changed_paths=("src/",),
    )
    with pytest.raises(LineageError):
        manager.create_lineage(
            campaign_id="camp1", lineage_id="lin2", parent_lineage_id="lin1",
            allowed_changed_paths=(),  # explicit request for unrestricted
        )
    with pytest.raises(LineageError):
        manager.create_lineage(
            campaign_id="camp1", lineage_id="lin3", parent_lineage_id="lin1",
            allowed_changed_paths=("docs/",),  # not contained within src/
        )
    # A genuine SUBSET of the parent's boundary is allowed.
    child = manager.create_lineage(
        campaign_id="camp1", lineage_id="lin4", parent_lineage_id="lin1",
        allowed_changed_paths=("src/sub/",),
    )
    assert child.allowed_changed_paths == ("src/sub/",)


def test_recover_rejects_child_receipt_widening_restricted_parent_boundary(manager, repo):
    """The SAME inheritance/subset rule must hold during replay, not just
    at live create_lineage() call time -- a forged/tampered CREATED receipt
    claiming a wider-than-parent boundary must fail closed."""

    _, baseline_commit, baseline_tree = repo
    worktree_a = os.path.join(manager._workspace_root, "camp1", "lin-a")
    worktree_b = os.path.join(manager._workspace_root, "camp1", "lin-b")
    parent_created = make_created_receipt(
        campaign_id="camp1", lineage_id="lin-a", worktree_path=worktree_a,
        baseline_commit=baseline_commit, baseline_tree=baseline_tree,
        sequence=0, allowed_changed_paths=("src/",),
    )
    child_created = make_created_receipt(
        campaign_id="camp1", lineage_id="lin-b", worktree_path=worktree_b,
        baseline_commit=baseline_commit, baseline_tree=baseline_tree,
        branch_name="discovery/camp1/lin-b", sequence=1,
        parent_lineage_id="lin-a", allowed_changed_paths=("docs/",),
    )
    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    with pytest.raises(LineageError):
        fresh_manager.recover([parent_created, child_created])


def test_staged_index_recheck_rejects_out_of_boundary_path_after_status_check(repo, workspace):
    """Close the TOCTOU: even if something ELSE stages an out-of-boundary
    path between the pre-stage `git status` check and the commit, the
    staged-index re-check must catch it and reset before committing."""

    repo_root, baseline_commit, _ = repo

    def racy_git_runner(argv: list[str], cwd: str) -> str:
        result = subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True, check=False)
        if argv[:1] == ["add"] and "--" in argv:
            # Simulate a race: something else stages an out-of-boundary file
            # in between this scoped `git add` and the commit that follows.
            with open(os.path.join(cwd, "danger.txt"), "w") as f:
                f.write("raced in\n")
            subprocess.run(["git", "add", "danger.txt"], cwd=cwd, check=True)
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(argv)!r} failed in {cwd!r}: {result.stderr.strip()}")
        return result.stdout.strip()

    manager = LineageWorkspaceManager(repo_root=repo_root, workspace_root=workspace, git_runner=racy_git_runner)
    manager.create_lineage(
        campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit,
        allowed_changed_paths=("src/",),
    )
    worktree = manager.worktree_path("lin1")
    os.makedirs(os.path.join(worktree, "src"))
    with open(os.path.join(worktree, "src", "ok.txt"), "w") as f:
        f.write("in boundary\n")

    head_before = manager.head_commit("lin1")
    with pytest.raises(LineageError, match="race detected"):
        manager.checkpoint("lin1", attempt_id="att1")
    assert manager.head_commit("lin1") == head_before
    staged = _git(["diff", "--cached", "--name-only"], worktree)
    assert staged == ""  # index was reset -- nothing left staged


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #9, F4: no mutation before receipt
# construction can succeed
# ---------------------------------------------------------------------------


def test_create_lineage_rejects_invalid_id_source_before_any_mutation(repo, workspace):
    repo_root, baseline_commit, _ = repo
    calls = {"n": 0}

    def flaky_id_source() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return ""  # invalid: min_length=1 violation
        return f"lin-rcpt-{calls['n']:08x}"

    manager = LineageWorkspaceManager(repo_root=repo_root, workspace_root=workspace, id_source=flaky_id_source)
    # META-7 pre-commit fix brief #10, F4r: an explicit pre-check now catches
    # this (LineageError) BEFORE the sequence counter is ever consumed,
    # rather than pydantic's own ValidationError firing after a wasted
    # sequence number.
    with pytest.raises(LineageError):
        manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    # No git worktree was created by the failed attempt -- the lineage_id is
    # still free to retry.
    assert not os.path.isdir(os.path.join(workspace, "camp1", "lin1"))
    receipt = manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    assert receipt.receipt_id
    assert receipt.sequence == 0  # the failed first attempt consumed NO sequence number


def test_failed_then_retried_create_lineage_recovers_with_no_sequence_gap(repo, workspace):
    """META-7 pre-commit fix brief #10, F4r: an id_source failing once, then
    succeeding, must not leave a sequence GAP -- the failed first attempt
    must consume no sequence number at all, so the retried receipt lands at
    sequence 0 and `recover()` replays the resulting single-receipt log
    cleanly (not "sequence has a gap")."""

    repo_root, baseline_commit, _ = repo
    calls = {"n": 0}

    def flaky_id_source() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return ""  # invalid -- fails before the sequence counter advances
        return f"lin-rcpt-{calls['n']:08x}"

    manager = LineageWorkspaceManager(repo_root=repo_root, workspace_root=workspace, id_source=flaky_id_source)
    with pytest.raises(LineageError):
        manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    receipt = manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    assert receipt.sequence == 0

    fresh_manager = LineageWorkspaceManager(repo_root=repo_root, workspace_root=workspace)
    fresh_manager.recover([receipt])  # must not raise "sequence has a gap"
    assert fresh_manager.worktree_path("lin1") == receipt.worktree_path


def test_checkpoint_rejects_invalid_id_source_before_any_mutation(repo, workspace):
    repo_root, baseline_commit, _ = repo
    calls = {"n": 0}

    def flaky_id_source() -> str:
        calls["n"] += 1
        if calls["n"] == 2:  # call #1 is create_lineage's own receipt_id
            return ""
        return f"lin-rcpt-{calls['n']:08x}"

    manager = LineageWorkspaceManager(repo_root=repo_root, workspace_root=workspace, id_source=flaky_id_source)
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    with open(os.path.join(worktree, "candidate.txt"), "w") as f:
        f.write("work\n")

    head_before = manager.head_commit("lin1")
    with pytest.raises(LineageError):
        manager.checkpoint("lin1", attempt_id="att1")
    assert manager.head_commit("lin1") == head_before
    status = _git(["status", "--porcelain"], worktree)
    assert "candidate.txt" in status  # still untracked -- `git add` never ran


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #11, F4c: the injected git runner's tree_hash
# (and, for _commit_worktree, commit_hash) return is ALSO a fallible receipt
# input -- validated as a proper 40-hex sha BEFORE the sequence counter is
# consumed, same as id_source's output (F4r).
# ---------------------------------------------------------------------------


def _flaky_tree_hash_git_runner(*, fail_on_pattern="^{tree}"):
    """Wraps the real `git` runner, returning a malformed value for the
    FIRST `rev-parse ...^{tree}` call only; every other call (including
    every subsequent tree-hash call) is passed straight through."""

    calls = {"n": 0}

    def runner(argv, cwd):
        if argv and argv[0] == "rev-parse" and any(fail_on_pattern in a for a in argv):
            calls["n"] += 1
            if calls["n"] == 1:
                return "not-a-real-tree-hash"
        return _git(argv, cwd)

    return runner


def test_create_lineage_rejects_malformed_tree_hash_before_any_mutation(repo, workspace):
    """META-7 pre-commit fix brief #11, F4c (NEW-3): a malformed tree_hash
    returned by the injected git runner must be rejected BEFORE
    `_next_sequence()` is consumed and BEFORE `git worktree add` ever runs --
    otherwise a failed-then-retried create_lineage reproduces the exact
    retry-gap bug F4r fixed for id_source, just for a different fallible
    runner-derived input."""

    repo_root, baseline_commit, _ = repo
    manager = LineageWorkspaceManager(
        repo_root=repo_root, workspace_root=workspace, git_runner=_flaky_tree_hash_git_runner()
    )
    with pytest.raises(LineageError, match="tree_hash"):
        manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    # No git worktree was created by the failed attempt -- the lineage_id is
    # still free to retry, and no branch/worktree ownership was published.
    assert not os.path.isdir(os.path.join(workspace, "camp1", "lin1"))

    receipt = manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    assert receipt.sequence == 0  # the failed first attempt consumed NO sequence number

    fresh_manager = LineageWorkspaceManager(repo_root=repo_root, workspace_root=workspace)
    fresh_manager.recover([receipt])  # must not raise "sequence has a gap"
    assert fresh_manager.worktree_path("lin1") == receipt.worktree_path


def test_commit_worktree_rejects_malformed_tree_hash_before_sequence_consumed(repo, workspace):
    """META-7 pre-commit fix brief #11, F4c audit of `_commit_worktree`: the
    commit itself is unavoidable once staged (unlike create_lineage, where
    the mutation happens AFTER receipt construction), but the durable
    sequence counter must still never be consumed, and `state`'s in-memory
    head_commit/tree_hash must never be updated, for a receipt that could
    not actually be constructed."""

    repo_root, baseline_commit, _ = repo
    real_repo_root = os.path.realpath(repo_root)
    calls = {"n": 0}

    def flaky_git_runner(argv, cwd):
        # Only interfere with `_commit_worktree`'s own tree-hash resolution
        # (run against the WORKTREE, not repo_root) -- create_lineage's own
        # tree_hash call (against repo_root) must pass through untouched so
        # this test isolates the `_commit_worktree` code path specifically.
        if os.path.realpath(cwd) != real_repo_root and argv and argv[0] == "rev-parse" and any(
            "^{tree}" in a for a in argv
        ):
            calls["n"] += 1
            if calls["n"] == 1:
                return "not-a-real-tree-hash"
        return _git(argv, cwd)

    manager = LineageWorkspaceManager(repo_root=repo_root, workspace_root=workspace, git_runner=flaky_git_runner)
    created_receipt = manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    with open(os.path.join(worktree, "candidate.txt"), "w") as f:
        f.write("work\n")

    head_before = manager.head_commit("lin1")
    with pytest.raises(LineageError, match="tree_hash"):
        manager.checkpoint("lin1", attempt_id="att1")
    # `state` was NOT advanced: head_commit/sequence unchanged despite the
    # commit itself having already landed in the worktree (unlike
    # create_lineage, `_commit_worktree` cannot avoid the git mutation before
    # tree_hash is knowable) -- so no sequence GAP is introduced by the
    # failed attempt, and the manager's own bookkeeping never reflects the
    # unconfirmed commit. A real caller must reconcile the worktree (e.g.
    # reset to the last known-good head) before retrying, exactly as the
    # pre-existing "moved HEAD" guard below already requires for ANY
    # unexpected worktree drift -- this is not a new limitation.
    assert manager.head_commit("lin1") == head_before
    _git(["reset", "--hard", head_before], worktree)

    checkpoint_receipt = manager.checkpoint("lin1", attempt_id="att1")
    assert checkpoint_receipt.sequence == 1  # immediately after CREATED's sequence 0 -- no gap

    fresh_manager = LineageWorkspaceManager(repo_root=repo_root, workspace_root=workspace)
    # Recover the actual durable receipts (CREATED @0, CHECKPOINTED @1) --
    # must not raise "sequence has a gap".
    fresh_manager.recover([created_receipt, checkpoint_receipt])


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #8, P1-4: mutation-before-validation
# ---------------------------------------------------------------------------


def test_checkpoint_rejects_empty_attempt_id_before_any_mutation(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    with open(os.path.join(worktree, "candidate.txt"), "w") as f:
        f.write("work\n")

    head_before = manager.head_commit("lin1")
    with pytest.raises(LineageError):
        manager.checkpoint("lin1", attempt_id="")
    assert manager.head_commit("lin1") == head_before
    status = _git(["status", "--porcelain"], worktree)
    assert "candidate.txt" in status  # still untracked -- `git add -A` never ran


def test_child_lineage_branches_from_exact_parent_head(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    with open(os.path.join(worktree, "candidate.txt"), "w") as f:
        f.write("parent progress\n")
    checkpoint = manager.checkpoint("lin1", attempt_id="att1")

    child_receipt = manager.create_lineage(campaign_id="camp1", lineage_id="lin2", parent_lineage_id="lin1")
    assert child_receipt.parent_commit == checkpoint.commit_hash
    assert child_receipt.parent_lineage_id == "lin1"
    child_worktree = manager.worktree_path("lin2")
    assert os.path.isfile(os.path.join(child_worktree, "candidate.txt"))


# ---------------------------------------------------------------------------
# Identifier / injection safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    ["../escape", "/abs/path", "a/b", "a\\b", "-oProxyCommand=x", "with space", "..", ""],
)
def test_rejects_unsafe_lineage_identifiers(manager, repo, bad_id):
    _, baseline_commit, _ = repo
    with pytest.raises(LineageError):
        manager.create_lineage(campaign_id="camp1", lineage_id=bad_id, baseline_commit=baseline_commit)


@pytest.mark.parametrize(
    "bad_id",
    ["../escape", "/abs/path", "a/b", "a\\b", "-oProxyCommand=x"],
)
def test_rejects_unsafe_campaign_identifiers(manager, repo, bad_id):
    _, baseline_commit, _ = repo
    with pytest.raises(LineageError):
        manager.create_lineage(campaign_id=bad_id, lineage_id="lin1", baseline_commit=baseline_commit)


def test_rejects_non_hex_baseline_ref(manager):
    with pytest.raises(LineageError):
        manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit="HEAD")


def test_rejects_unknown_commit_sha(manager):
    with pytest.raises(LineageError):
        manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit="f" * 40)


# ---------------------------------------------------------------------------
# Parent validation
# ---------------------------------------------------------------------------


def test_rejects_self_parent(manager, repo):
    with pytest.raises(LineageError):
        manager.create_lineage(campaign_id="camp1", lineage_id="lin1", parent_lineage_id="lin1")


def test_rejects_unknown_parent(manager):
    with pytest.raises(LineageError):
        manager.create_lineage(campaign_id="camp1", lineage_id="lin2", parent_lineage_id="ghost")


def test_rejects_baseline_commit_alongside_parent(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    with pytest.raises(LineageError):
        manager.create_lineage(
            campaign_id="camp1", lineage_id="lin2", parent_lineage_id="lin1", baseline_commit=baseline_commit
        )


def test_rejects_duplicate_lineage_id(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    with pytest.raises(LineageError):
        manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)


def test_recover_rejects_branch_name_collision_across_distinct_lineages(manager, repo):
    """A corrupt/tampered journal claiming the same branch for two different
    lineage_ids must fail closed during replay, not silently pick a winner."""

    _, baseline_commit, baseline_tree = repo
    worktree_a = os.path.join(manager._workspace_root, "camp1", "lin-a")
    worktree_b = os.path.join(manager._workspace_root, "camp1", "lin-b")
    created_a = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin-a",
        worktree_path=worktree_a,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        branch_name="discovery/camp1/shared",
        sequence=0,
    )
    created_b = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin-b",
        worktree_path=worktree_b,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        branch_name="discovery/camp1/shared",
        sequence=1,
    )
    with pytest.raises(LineageError):
        manager.recover([created_a, created_b])


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #9, F5: transactional recovery (lineage)
# ---------------------------------------------------------------------------


def test_failed_mid_replay_recovery_poisons_lineage_manager(manager, repo):
    """receipt A (lin-a) replays cleanly -- already publishing lin-a into
    self._lineages/_branch_owners/_worktree_owners -- BEFORE receipt B raises
    on the branch collision. The manager must come out poisoned/unusable,
    not a partially-recovered instance that still answers worktree_path/
    head_commit for lin-a."""

    _, baseline_commit, baseline_tree = repo
    worktree_a = os.path.join(manager._workspace_root, "camp1", "lin-a")
    worktree_b = os.path.join(manager._workspace_root, "camp1", "lin-b")
    created_a = make_created_receipt(
        campaign_id="camp1", lineage_id="lin-a", worktree_path=worktree_a,
        baseline_commit=baseline_commit, baseline_tree=baseline_tree,
        branch_name="discovery/camp1/shared", sequence=0,
    )
    created_b = make_created_receipt(
        campaign_id="camp1", lineage_id="lin-b", worktree_path=worktree_b,
        baseline_commit=baseline_commit, baseline_tree=baseline_tree,
        branch_name="discovery/camp1/shared", sequence=1,
    )
    with pytest.raises(LineageError):
        manager.recover([created_a, created_b])

    from metaharness.discovery.lineage import LineagePoisonedError

    with pytest.raises(LineagePoisonedError):
        manager.worktree_path("lin-a")
    with pytest.raises(LineagePoisonedError):
        manager.head_commit("lin-a")
    with pytest.raises(LineagePoisonedError):
        manager.is_quarantined("lin-a")
    with pytest.raises(LineagePoisonedError):
        manager.create_lineage(campaign_id="camp1", lineage_id="lin-c", baseline_commit=baseline_commit)


# ---------------------------------------------------------------------------
# Operations on missing/quarantined lineages
# ---------------------------------------------------------------------------


def test_checkpoint_unknown_lineage_raises(manager):
    with pytest.raises(LineageError):
        manager.checkpoint("ghost", attempt_id="att1")


def test_create_child_of_quarantined_parent_raises(manager, repo):
    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    # Force quarantine via the recovery path by leaving uncommitted (dirty) state.
    worktree = manager.worktree_path("lin1")
    with open(os.path.join(worktree, "rogue.txt"), "w") as f:
        f.write("uncommitted\n")
    created = [
        make_created_receipt(
            campaign_id="camp1",
            lineage_id="lin1",
            worktree_path=worktree,
            baseline_commit=baseline_commit,
            baseline_tree=baseline_tree,
        )
    ]
    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    quarantines = fresh_manager.recover(created)
    assert len(quarantines) == 1
    assert quarantines[0].event_type is DiscoveryLineageEventType.QUARANTINED
    assert fresh_manager.is_quarantined("lin1")
    with pytest.raises(LineageError):
        fresh_manager.create_lineage(campaign_id="camp1", lineage_id="lin2", parent_lineage_id="lin1")


# ---------------------------------------------------------------------------
# Recovery / replay
# ---------------------------------------------------------------------------


def test_recover_rebuilds_clean_state(manager, repo):
    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    with open(os.path.join(worktree, "candidate.txt"), "w") as f:
        f.write("work\n")
    checkpoint = manager.checkpoint("lin1", attempt_id="att1")

    receipts = [
        make_created_receipt(
            campaign_id="camp1",
            lineage_id="lin1",
            worktree_path=worktree,
            baseline_commit=baseline_commit,
            baseline_tree=baseline_tree,
        ),
        checkpoint,
    ]

    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    quarantines = fresh_manager.recover(receipts)
    assert quarantines == ()
    assert not fresh_manager.is_quarantined("lin1")
    assert fresh_manager.head_commit("lin1") == checkpoint.commit_hash


def test_recover_rejects_sequence_gap(manager, repo):
    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")

    created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        sequence=0,
    )
    gapped = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        sequence=5,
    )

    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    with pytest.raises(LineageError):
        fresh_manager.recover([created, gapped])


def test_recover_quarantines_dirty_worktree(manager, repo):
    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    with open(os.path.join(worktree, "dirty.txt"), "w") as f:
        f.write("uncommitted\n")

    created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
    )

    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    quarantines = fresh_manager.recover([created])
    assert len(quarantines) == 1
    assert "dirty" in quarantines[0].detail


def test_recover_quarantines_missing_worktree(manager, repo):
    _, baseline_commit, baseline_tree = repo
    worktree = os.path.join(manager._workspace_root, "camp1", "lin1")
    created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
    )
    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    quarantines = fresh_manager.recover([created])
    assert len(quarantines) == 1
    assert "missing" in quarantines[0].detail


def test_recover_quarantines_symlinked_worktree(manager, repo, tmp_path):
    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    real_worktree = manager.worktree_path("lin1")
    alias_path = os.path.join(manager._workspace_root, "camp1", "lin1-alias")
    os.symlink(real_worktree, alias_path)

    created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1-alias",
        worktree_path=alias_path,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        branch_name="discovery/camp1/lin1-alias",
    )
    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    quarantines = fresh_manager.recover([created])
    assert len(quarantines) == 1
    assert "symlink" in quarantines[0].detail


def test_recover_realpath_alias_has_a_single_winner(manager, repo):
    """Two receipts whose worktree_path resolves to the SAME real directory
    (one direct, one via a symlink alias) must not both be treated as valid:
    the real (non-symlink) owner survives, the symlink alias is quarantined."""

    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    real_worktree = manager.worktree_path("lin1")
    alias_path = os.path.join(manager._workspace_root, "camp1", "lin1-alias")
    os.symlink(real_worktree, alias_path)

    created_real = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=real_worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        sequence=0,
    )
    created_alias = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1-alias",
        worktree_path=alias_path,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        branch_name="discovery/camp1/lin1-alias",
        sequence=1,
    )

    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    quarantines = fresh_manager.recover([created_real, created_alias])
    quarantined_ids = {q.lineage_id for q in quarantines}
    assert quarantined_ids == {"lin1-alias"}
    assert not fresh_manager.is_quarantined("lin1")
    assert fresh_manager.is_quarantined("lin1-alias")


# ---------------------------------------------------------------------------
# Pre-commit fix brief #8: stale-hash replay
# ---------------------------------------------------------------------------


def test_recover_rejects_stale_hash_model_copy(manager, repo):
    """`model_copy(update=...)` bypasses validation entirely, so its
    `receipt_hash` still reflects the ORIGINAL pre-mutation content. recover()
    must revalidate from the JSON dump and reject this, not trust the object."""

    _, baseline_commit, baseline_tree = repo
    worktree = os.path.join(manager._workspace_root, "camp1", "lin1")
    original = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
    )
    stale = original.model_copy(update={"detail": "tampered after construction, hash never recomputed"})
    assert stale.receipt_hash == original.receipt_hash  # the stale ghost hash

    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    with pytest.raises(LineageError, match="revalidation"):
        fresh_manager.recover([stale])


def test_recover_rejects_stale_hash_after_field_mutation_attempt(manager, repo):
    """Even a receipt whose declared fields look internally consistent is
    rejected if its hash was never recomputed for the ACTUAL final values."""

    _, baseline_commit, baseline_tree = repo
    worktree = os.path.join(manager._workspace_root, "camp1", "lin1")
    original = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
    )
    tampered_worktree = os.path.join(manager._workspace_root, "camp1", "lin1-moved")
    stale = original.model_copy(update={"worktree_path": tampered_worktree})

    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    with pytest.raises(LineageError, match="revalidation"):
        fresh_manager.recover([stale])


# ---------------------------------------------------------------------------
# Pre-commit fix brief #8: branch/path forgery
# ---------------------------------------------------------------------------


def test_recover_rejects_branch_name_not_matching_derivation(manager, repo):
    _, baseline_commit, baseline_tree = repo
    worktree = os.path.join(manager._workspace_root, "camp1", "lin1")
    forged = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        branch_name="discovery/camp1/some-other-name",
    )
    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    with pytest.raises(LineageError, match="branch forgery"):
        fresh_manager.recover([forged])


def test_recover_rejects_worktree_path_not_matching_derivation(manager, repo):
    _, baseline_commit, baseline_tree = repo
    forged_path = os.path.join(manager._workspace_root, "camp1", "not-the-derived-path")
    forged = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=forged_path,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
    )
    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    with pytest.raises(LineageError, match="path forgery"):
        fresh_manager.recover([forged])


def test_recover_rejects_immutable_field_change_on_checkpoint(manager, repo):
    """A CHECKPOINTED receipt claiming a different campaign_id than the
    lineage's own CREATED receipt must be rejected."""

    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    checkpoint = manager.checkpoint("lin1", attempt_id="att1")

    created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
    )
    forged_checkpoint = DiscoveryLineageReceipt.model_validate(
        {**checkpoint.model_dump(mode="json"), "campaign_id": "camp-FORGED", "receipt_hash": ""}
    )

    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    with pytest.raises(LineageError, match="immutable field changed"):
        fresh_manager.recover([created, forged_checkpoint])


# ---------------------------------------------------------------------------
# Pre-commit fix brief #8: skipped-checkpoint ancestry
# ---------------------------------------------------------------------------


def test_recover_rejects_skipped_checkpoint_ancestry(manager, repo):
    """A CHECKPOINTED receipt whose commit's actual git-parent is NOT the
    lineage's recorded prior head (e.g. it skips over an intermediate real
    checkpoint) must be rejected, even though the commit itself is real."""

    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")

    with open(os.path.join(worktree, "step1.txt"), "w") as f:
        f.write("first\n")
    first_checkpoint = manager.checkpoint("lin1", attempt_id="att1")

    with open(os.path.join(worktree, "step2.txt"), "w") as f:
        f.write("second\n")
    second_checkpoint = manager.checkpoint("lin1", attempt_id="att1")

    created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
    )
    # Replay CREATED then jump straight to the SECOND checkpoint, skipping
    # the first — second_checkpoint's real git-parent is first_checkpoint's
    # commit, not the baseline, so this must fail the ancestry check.
    skipped = DiscoveryLineageReceipt.model_validate(
        {**second_checkpoint.model_dump(mode="json"), "sequence": 1, "receipt_hash": ""}
    )

    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    with pytest.raises(LineageError, match="skipped-checkpoint"):
        fresh_manager.recover([created, skipped])
    del first_checkpoint


# ---------------------------------------------------------------------------
# Pre-commit fix brief #8: moved-HEAD before mutation (live path)
# ---------------------------------------------------------------------------


def test_checkpoint_rejects_moved_head_before_any_mutation(manager, repo):
    _, baseline_commit, _ = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")

    # Simulate external tampering: commit directly in the worktree, bypassing
    # the manager, so the manager's recorded head_commit is now stale.
    with open(os.path.join(worktree, "external.txt"), "w") as f:
        f.write("someone else committed this\n")
    _git(["add", "-A"], worktree)
    _git(["commit", "-q", "-m", "external commit"], worktree)
    moved_head = _git(["rev-parse", "HEAD"], worktree)
    assert moved_head != baseline_commit

    with open(os.path.join(worktree, "candidate.txt"), "w") as f:
        f.write("attempt work\n")

    with pytest.raises(LineageError, match="moved HEAD"):
        manager.checkpoint("lin1", attempt_id="att1")

    # No mutation happened: HEAD is exactly where the external commit left
    # it, and candidate.txt was never staged/committed by the failed call.
    assert _git(["rev-parse", "HEAD"], worktree) == moved_head
    status = _git(["status", "--porcelain"], worktree)
    assert "candidate.txt" in status  # still untracked/unstaged, not committed


# ---------------------------------------------------------------------------
# Pre-commit fix brief #8: post-recovery append/re-replay
# ---------------------------------------------------------------------------


def test_post_recovery_live_operation_and_full_re_replay_round_trip(manager, repo):
    """After recovery, a new live checkpoint must not collide with any
    sequence/receipt_id already used in history, and replaying the FULL
    combined history (old + new) through a third fresh manager must succeed."""

    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    with open(os.path.join(worktree, "candidate.txt"), "w") as f:
        f.write("work\n")
    original_checkpoint = manager.checkpoint("lin1", attempt_id="att1")

    created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
    )
    history = [created, original_checkpoint]

    recovered_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    quarantines = recovered_manager.recover(history)
    assert quarantines == ()

    with open(os.path.join(worktree, "candidate.txt"), "a") as f:
        f.write("more work after recovery\n")
    new_checkpoint = recovered_manager.checkpoint("lin1", attempt_id="att2")

    used_sequences = {r.sequence for r in history} | {new_checkpoint.sequence}
    assert len(used_sequences) == 3  # no collision
    used_receipt_ids = {r.receipt_id for r in history} | {new_checkpoint.receipt_id}
    assert len(used_receipt_ids) == 3  # no collision

    full_history = history + [new_checkpoint]
    third_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    re_replay_quarantines = third_manager.recover(full_history)
    assert re_replay_quarantines == ()
    assert third_manager.head_commit("lin1") == new_checkpoint.commit_hash


# ---------------------------------------------------------------------------
# Pre-commit fix brief 2, item 6: full lineage ownership binding — campaign
# IDs must match parent/child, and the worktree's ACTUAL checked-out branch
# must match the derived durable branch (not merely its path/HEAD)
# ---------------------------------------------------------------------------


def test_recover_rejects_child_lineage_with_mismatched_parent_campaign(manager, repo):
    _, baseline_commit, baseline_tree = repo
    lin1_worktree = os.path.join(manager._workspace_root, "camp1", "lin1")
    lin1_created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=lin1_worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        sequence=0,
    )
    mismatched_child = make_created_receipt(
        campaign_id="camp2",  # differs from parent lin1's campaign "camp1"
        lineage_id="lin2",
        worktree_path=os.path.join(manager._workspace_root, "camp2", "lin2"),
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        branch_name="discovery/camp2/lin2",
        parent_lineage_id="lin1",
        sequence=1,
    )
    with pytest.raises(LineageError, match="campaign"):
        manager.recover([lin1_created, mismatched_child])


def test_recover_rejects_worktree_checked_out_to_wrong_branch(manager, repo):
    """HEAD commit matching is not enough: the worktree must actually have
    the derived durable branch checked out, not merely a different ref
    (or a different branch) that happens to point at the same commit."""

    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    head = manager.head_commit("lin1")

    _git(["branch", "rogue-branch", head], worktree)
    _git(["checkout", "rogue-branch"], worktree)
    assert _git(["rev-parse", "HEAD"], worktree) == head  # HEAD commit unchanged

    created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
    )
    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    quarantines = fresh_manager.recover([created])
    assert len(quarantines) == 1
    assert "branch" in quarantines[0].detail.lower()


def test_recover_advances_default_receipt_id_generator_past_sparse_high_water_mark(manager, repo):
    """Pre-commit fix brief 2, item 8 (P2): the default lin-rcpt-XXXXXXXX
    generator must advance past the MAXIMUM durable generated-ID suffix, not
    merely len(receipts) — a sparse/high existing ID must not be undercut."""

    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    with open(os.path.join(worktree, "f.txt"), "w") as f:
        f.write("x\n")
    real_checkpoint = manager.checkpoint("lin1", attempt_id="att1")

    created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        receipt_id="lin-rcpt-00000000",
    )
    # Only 2 receipts total, but one carries a SPARSE, HIGH default-pattern
    # id far beyond len(receipts)=2.
    high_checkpoint = DiscoveryLineageReceipt.model_validate(
        {**real_checkpoint.model_dump(mode="json"), "receipt_id": "lin-rcpt-000000ff", "receipt_hash": ""}
    )

    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    quarantines = fresh_manager.recover([created, high_checkpoint])
    assert quarantines == ()

    with open(os.path.join(worktree, "f2.txt"), "w") as f:
        f.write("y\n")
    new_checkpoint = fresh_manager.checkpoint("lin1", attempt_id="att2")
    suffix = new_checkpoint.receipt_id.rsplit("-", 1)[-1]
    assert int(suffix, 16) > 0xFF
    assert new_checkpoint.receipt_id not in {created.receipt_id, high_checkpoint.receipt_id}


def test_recover_rejects_worktree_with_detached_head(manager, repo):
    _, baseline_commit, baseline_tree = repo
    manager.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=baseline_commit)
    worktree = manager.worktree_path("lin1")
    head = manager.head_commit("lin1")
    _git(["checkout", "--detach", head], worktree)

    created = make_created_receipt(
        campaign_id="camp1",
        lineage_id="lin1",
        worktree_path=worktree,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
    )
    fresh_manager = LineageWorkspaceManager(repo_root=manager._repo_root, workspace_root=manager._workspace_root)
    quarantines = fresh_manager.recover([created])
    assert len(quarantines) == 1
