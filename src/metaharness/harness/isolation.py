"""Workspace isolation and honest execution-boundary contracts for coding CLIs.

Two related concerns live here because they share the same trust surface
(what the harness can and cannot promise about a coding worker running in
a real workspace):

- **Workspace lease**: a synchronous, cross-process exclusive claim on a
  *resolved* workspace path. Concurrent coding workers (including subscription
  read-only inspection) cannot share one active checkout; the lease fails
  closed immediately rather than queueing. Distinct worktrees — explicit
  different paths supplied by the caller — remain independent and may run
  concurrently. The harness does NOT create, switch, or delete branches or
  worktrees.

- **Execution boundary**: a frozen, machine-readable description of what the
  adapter actually constrains. CLI-native sandboxes (codex `--sandbox
  workspace-write` / `read-only`, claude `--safe-mode --permission-mode plan`)
  are honestly labeled as CLI-native; Pi, OpenCode, and edit-capable Claude
  are labeled operator-trusted — the harness does NOT describe them as
  sandboxed, jailed, credential-stripped, network-denied, or containerized.

The post-hoc OS-sandboxed verifier path is unchanged: those receipts are
produced AFTER worker generation, in a separate harness boundary, and remain
the strongest verification evidence.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Workspace lease
# ---------------------------------------------------------------------------


class WorkspaceClaimError(RuntimeError):
    """A concurrent coding worker already holds the resolved workspace path.

    The error message is what surfaces as the worker's `error` text — keep it
    honest: "the resolved workspace already has an active coding worker" tells
    the operator what actually happened, no more."""


# process-local guard: even if a thread in this process forgets to release the
# OS lock, the in-process holder set keeps a reentrant / concurrent acquire in
# the same process from bypassing it. Both must be cleared on release. The
# dictionary OBJECT IDENTITY of the holder-meta dict identifies which context
# manager installed the reservation — the outermost `finally` of `claim_workspace`
# compares by `is` so it cannot accidentally remove a different holder's
# reservation.
_LOCAL_HOLDER_LOCK = threading.Lock()
_LOCAL_HOLDERS: dict[str, dict[str, object]] = {}

# lockfiles live OUTSIDE the workspace they protect — never auto-delete them
# (an unlink+reacquire creates an inode-races window where two processes can
# hold the same lock). Operators clean the lock dir explicitly if they want.
_LOCKFILE_DIR = Path.home() / ".metaharness" / "workspace_leases"

# holder-metadata payload cap so a single acquirer never wedges the diagnostic
# read with megabytes of bytes, and so the lockfile's tail cannot leak
# metadata from a prior holder under a different process's flock.
_HOLDER_META_MAX_BYTES = 1024


def _lease_key(resolved: Path) -> str:
    """Stable lease key: SHA-256 of the resolved absolute path. Symlink aliases
    that resolve to the same path produce the same key — bypassing the lease
    via a path alias is not possible."""
    return hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()


def _lockfile_path(key: str) -> Path:
    _LOCKFILE_DIR.mkdir(parents=True, exist_ok=True)
    return _LOCKFILE_DIR / f"{key}.lock"


@contextmanager
def claim_workspace(
    workspace: Path,
    *,
    worker_id: str,
    task_id: str,
) -> Iterator[Path]:
    """Hold an exclusive cross-process claim on the resolved workspace for the
    duration of the `with` block. On success yields the resolved Path; on a
    concurrent claim raises ``WorkspaceClaimError`` immediately.

    The acquisition order is fixed:

    1. **Process-local reservation FIRST** — same-process collisions are caught
       here with a truthful message and never need to touch the OS lock.
    2. **Cross-process `fcntl.flock`** is the authoritative lock.
    3. **Holder metadata** is written as truncated JSON at offset zero of the
       lockfile, so an audit reader sees only the current holder's bytes
       (no stale tails from a prior acquirer).

    Release is handled by ONE outermost ``finally`` that:

    - always closes the fd if it was opened, and
    - always removes the local reservation by the dict-object IDENTITY of the
      ``holder_meta`` we installed — covering ANY failure path (open raising,
      flock denied, metadata-write raising, any exception between flock
      success and entering the yield, the yield body raising, or normal
      completion). A different holder's reservation cannot be removed because
      the identity check ``current is holder_meta`` is false for it."""
    resolved = workspace.resolve()
    key = _lease_key(resolved)
    lockfile = _lockfile_path(key)

    # `holder_meta` is set only after the same-process reservation succeeds;
    # the outermost `finally` uses `holder_meta is not None` to know whether
    # this context manager installed the reservation it must now release.
    holder_meta: Optional[dict[str, object]] = None
    fd: Optional[int] = None
    try:
        # ---- 1. process-local reservation (fast, truthful same-process denial)
        with _LOCAL_HOLDER_LOCK:
            if key in _LOCAL_HOLDERS:
                existing = _LOCAL_HOLDERS[key]
                raise WorkspaceClaimError(
                    f"workspace {str(resolved)!r} already has an active coding "
                    f"worker in this process (held by {existing.get('worker_id')!r} "
                    f"on task {existing.get('task_id')!r}, lease {key[:16]}…)"
                )
            holder_meta = {
                "resolved_workspace": str(resolved),
                "worker_id": worker_id,
                "task_id": task_id,
                "pid": os.getpid(),
                "lease": key[:16],
            }
            _LOCAL_HOLDERS[key] = holder_meta

        # ---- 2. open lockfile ----
        try:
            fd = os.open(str(lockfile), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise WorkspaceClaimError(
                f"workspace {str(resolved)!r} could not open lease lockfile "
                f"({lockfile}): {type(exc).__name__}: {exc}"
            ) from exc

        # ---- 3. cross-process flock (authoritative) ----
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            try:
                holder_meta_text = os.read(fd, _HOLDER_META_MAX_BYTES).decode(
                    "utf-8", "replace",
                )
            except OSError:
                holder_meta_text = ""
            raise WorkspaceClaimError(
                f"workspace {str(resolved)!r} already has an active coding "
                f"worker in another process (lease {key[:16]}…); holder info: "
                f"{holder_meta_text.strip() or 'unavailable'} "
                f"(fcntl: {type(exc).__name__})"
            ) from exc

        # ---- 4. write holder metadata as truncated JSON at offset 0 ---------
        # Truncate first so a prior holder's tail cannot leak into the
        # diagnostic read. JSON (not repr) so the bytes are well-formed.
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            payload = json.dumps(holder_meta, sort_keys=True).encode("utf-8")
            os.write(fd, payload[:_HOLDER_META_MAX_BYTES])
        except OSError:
            # Diagnostic write is best-effort; the flock is still authoritative.
            pass

        # ---- 5. yield the resolved path; release flock on every yield exit ---
        try:
            yield resolved
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        # ALWAYS remove the local reservation by object identity, regardless
        # of which step raised (open, flock, metadata write, or anything
        # between flock success and entering the yield's try). The identity
        # check prevents removing a different holder's reservation.
        if holder_meta is not None:
            with _LOCAL_HOLDER_LOCK:
                current = _LOCAL_HOLDERS.get(key)
                if current is holder_meta:
                    _LOCAL_HOLDERS.pop(key, None)
        # fd close stays in the same outermost finally.
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Execution boundary contract
# ---------------------------------------------------------------------------


class ExecutionBoundary(BaseModel):
    """Frozen, machine-readable description of what an adapter actually constrains.

    The harness never silently upgrades or invents guarantees: this model is the
    canonical answer to "what protection does running this CLI give us?". An
    empty / made-up answer is worse than the honest one — the verifier OS
    sandbox is the only sandbox, and it runs after generation, not on it."""

    model_config = {"frozen": True, "extra": "forbid"}

    adapter: str = Field(min_length=1)
    sandbox: Optional[str] = None
    kind: str = Field(pattern=r"^(cli_native|operator_trusted)$")
    # what file writes the adapter admits:
    #   "none" — adapter denies writes outright (subscription codex read-only)
    #   "workspace" — adapter restricts writes to a declared cwd
    #   "unbounded_by_harness" — harness has no filesystem authority; the
    #     operator's CLI is trusted as-is
    write_scope: str = Field(
        pattern=r"^(none|workspace|unbounded_by_harness)$"
    )
    # the CLI's own auth (login / OAuth) is REQUIRED for these adapters —
    # the harness never proxies credentials, so authenticated network access
    # to provider APIs is always available whether or not the harness wants it.
    network_access: bool = True
    # the harness's post-hoc OS sandbox is NEVER on the generation boundary;
    # it runs only on verifier subprocesses after the worker has returned.
    harness_os_sandbox: bool = False


# Canonical adapter/sandbox -> boundary classification. Unknown combos raise
# ValueError; we deliberately do not invent a permissive default that would
# quietly down-grade a real restriction.
_BOUNDARY_TABLE: dict[tuple[str, Optional[str]], dict[str, str]] = {
    # codex workspace-write (the default for code-edit work) is its own native
    # workspace jail — writes are confined to --cd by codex itself.
    ("codex", "workspace-write"): {
        "kind": "cli_native",
        "write_scope": "workspace",
    },
    # subscription codex (read-only) writes nothing.
    ("codex", "read-only"): {
        "kind": "cli_native",
        "write_scope": "none",
    },
    # subscription claude runs in plan mode with Read/Glob/Grep only — same
    # posture as subscription codex.
    ("claude", "read-only"): {
        "kind": "cli_native",
        "write_scope": "none",
    },
    # claude WITHOUT the read-only restriction is edit-capable; the harness
    # applies no filesystem authority on top of claude's own modes.
    ("claude", None): {
        "kind": "operator_trusted",
        "write_scope": "unbounded_by_harness",
    },
    ("pi", None): {
        "kind": "operator_trusted",
        "write_scope": "unbounded_by_harness",
    },
    ("opencode", None): {
        "kind": "operator_trusted",
        "write_scope": "unbounded_by_harness",
    },
}


def execution_boundary_for(cli: str, *, sandbox: Optional[str] = None) -> ExecutionBoundary:
    """The honest boundary classification for one CLI invocation. Rejects
    unknown CLI/sandbox combinations rather than inventing guarantees."""
    key = (cli, sandbox)
    if key not in _BOUNDARY_TABLE:
        known = sorted(
            f"{cli!r}/{sandbox!r}" for cli, sandbox in _BOUNDARY_TABLE
        )
        raise ValueError(
            f"no execution boundary defined for cli={cli!r} sandbox={sandbox!r}; "
            f"known: {known}"
        )
    spec = _BOUNDARY_TABLE[key]
    return ExecutionBoundary(
        adapter=cli,
        sandbox=sandbox,
        kind=spec["kind"],
        write_scope=spec["write_scope"],
        network_access=True,
        harness_os_sandbox=False,
    )
