"""Built-in domain suites for harness optimization.

The optimizer is domain-general — it takes ANY list of scoreable Tasks — and
these presets mirror the paper's non-coding domains (text classification,
math) plus extraction, so the loop is exercised well beyond the SDLC use case.
Search and holdout sets are disjoint instances of the same distribution: the
search set drives the proposer, the holdout set feeds the promotion gate only
(the paper holds the test set out until final frontier evaluation).

Suites are extensible: extra questions live in `extra_tasks.json` under the
suite's ledger dir and are merged in (alternating search/holdout) by
`search_and_holdout`. A generated item with a mislabeled answer biases both
sides of the paired comparison equally — the gate's sign test cancels task
difficulty — but arithmetic answers are always recomputed exactly.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Iterator, Optional

from metaharness.core.types import Task, TaskType
from metaharness.evals.verifiers import scoreable_number, scoreable_tol
from metaharness.harness.sandbox import eval_arithmetic

_REVIEWS = [
    ("the checkout flow is fast and the support team actually answers", "positive"),
    ("crashed twice during onboarding and lost my draft", "negative"),
    ("exactly what the docs promised, set up in ten minutes", "positive"),
    ("billing charged me twice and refunds take weeks", "negative"),
    ("the new dashboard makes our weekly report effortless", "positive"),
    ("latency doubled after the update and nobody responds", "negative"),
    ("importing our old data worked on the first try", "positive"),
    ("the mobile app logs me out every single day", "negative"),
]

_SENTENCES = [
    ("The observatory opened in 1897 after a decade of construction.", "1897"),
    ("Production of the sedan finally ceased in 1983.", "1983"),
    ("She defended her thesis in 2011 at the age of 24.", "2011"),
    ("The bridge, finished in 1932, still carries rail traffic.", "1932"),
    ("Their first album was recorded in 1969 over one weekend.", "1969"),
    ("The statute was repealed in 2004 after a long campaign.", "2004"),
    ("He joined the expedition in 1911 as its youngest member.", "1911"),
    ("The reactor was decommissioned in 1998.", "1998"),
]

_EXPRESSIONS = [
    "17*23+9", "384/16-7", "31*31-100", "2**10-24",
    "45*12+45", "1000-13*37", "88*11+2", "7*8*9-100",
]


def classification_tasks(items: list[tuple[str, str]]) -> list[Task]:
    return [
        Task(
            task_type=TaskType.CLASSIFY,
            objective="Classify the sentiment of the review as positive or negative. "
                      "Answer with the single word only.",
            inputs={"review": text, "labels": ["positive", "negative"]},
            success_check={"equals": label},
        )
        for text, label in items
    ]


def extraction_tasks(items: list[tuple[str, str]]) -> list[Task]:
    return [
        Task(
            task_type=TaskType.EXTRACT,
            objective="Extract the four-digit year mentioned in the sentence. "
                      "Answer with the year only.",
            inputs={"sentence": text},
            success_check={"equals": year},
        )
        for text, year in items
    ]


def math_tasks(expressions: list[str]) -> list[Task]:
    return [
        Task(
            task_type=TaskType.ARITHMETIC,
            objective=f"Compute {expr}. Answer with the number only.",
            inputs={"expression": expr},
            success_check={"equals": eval_arithmetic(expr)},
        )
        for expr in expressions
    ]


_BUILDERS = {
    "classify": (classification_tasks, _REVIEWS),
    "extract": (extraction_tasks, _SENTENCES),
    "math": (math_tasks, _EXPRESSIONS),
}

SUITE_NAMES = ["mixed", *sorted(_BUILDERS)]


def check_value_ok(check: dict[str, Any]) -> bool:
    """The key shape is right; would the VALUES crash or degrade a consumer?
    This is the source-side gate: it reuses the verifier's shared numeric-scoreability
    policy (scoreable_tol / scoreable_number) so a harvested or generated check cannot
    smuggle in a tol/equals/one_of value that turns a later tuning run into a crash or a
    silent ground-truth corruption."""
    # Issue #9 (GLM: no upper bound): scoreable_tol is a superset of the panel F1
    # finite/≥0 check (math.isclose raises on a negative tol; an inf/junk/overflowing
    # tol crashes or silently corrupts) — it ADDS the ≤MAX_TOL cap that stops a huge
    # finite tol from making any numeric output PASS.
    if "tol" in check and scoreable_tol(check["tol"]) is None:
        return False
    # Issue #9 (kimi): a recomputed/large-int equals overflows float() at tuning time.
    if "equals" in check and not scoreable_number(check["equals"]):
        return False
    if "one_of" in check:
        allowed = check["one_of"]
        if not isinstance(allowed, list) or not allowed:
            return False
        # codex P1: a huge-int one_of member overflows float() in the verifier.
        if not all(isinstance(v, (str, int, float)) and scoreable_number(v) for v in allowed):
            return False
    if "contains" in check:
        if not isinstance(check["contains"], str) or not check["contains"]:
            return False
    return True


def extras_path(suite_dir: Path | str) -> Path:
    return Path(suite_dir) / "extra_tasks.json"


def dedupe_key(objective: str, inputs: dict[str, Any]) -> tuple[str, str]:
    """Content-based identity — task_ids/run_ids are single-use, so dedupe must
    be on (objective, inputs). Canonical key shared by every extras writer
    (harvest, the coverage endpoint) so they agree on what counts as a dupe."""
    return (objective, json.dumps(inputs, sort_keys=True, default=str))


def load_extras(suite_dir: Path | str) -> list[Task]:
    path = extras_path(suite_dir)
    if not path.is_file():
        return []
    return [Task.model_validate(t) for t in json.loads(path.read_text(encoding="utf-8"))]


def save_extras(suite_dir: Path | str, tasks: list[Task]) -> None:
    """Atomic write: same-dir temp file + os.replace. `os.replace` is only an
    atomic rename within one filesystem, so the temp file MUST live in
    `suite_dir` — a reader (`load_extras`, `search_and_holdout`) can then never
    observe a torn/partial JSON body."""
    suite_dir = Path(suite_dir)
    suite_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([t.model_dump() for t in tasks], indent=1, default=str)
    fd, tmp_name = tempfile.mkstemp(dir=suite_dir, prefix=".extra_tasks.", suffix=".tmp")
    try:
        # mkstemp creates 0600 and os.replace keeps the temp file's mode — restore
        # the 0644 the old write_text gave the file, or it silently turns
        # owner-only on the first save through this path.
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, extras_path(suite_dir))
    except BaseException:
        with suppress(OSError):  # cleanup must never mask the original error
            os.unlink(tmp_name)
        raise


@contextmanager
def _extras_lock(suite_dir: Path | str) -> Iterator[None]:
    """Cross-process advisory lock over one suite's extras. `fcntl.flock` is
    POSIX-only (acceptable: CI is ubuntu-only, no Windows target) and BLOCKING
    with no timeout — hold times are a load→merge→save of a small JSON file,
    milliseconds at most. Acquisition/OSError propagates uncaught: a lock
    failure must be loud, never a silent last-writer-wins degradation."""
    suite_dir = Path(suite_dir)
    suite_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(suite_dir / "extra_tasks.json.lock", os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def append_extras(suite_dir: Path | str, candidates: list[Task]) -> tuple[list[Task], int]:
    """THE single mutation choke point for extra_tasks.json — every writer
    (harvest, the coverage endpoint) must call this instead of `save_extras`
    directly, so a cross-process race can never clobber a concurrent writer's
    additions. Holds `_extras_lock` across the fresh read AND the save: the
    lock must wrap the read, not just the write, or a second writer could read
    stale content between another writer's unlock and its own lock.
    Dedupes only against the extras file and within the batch — callers are
    expected to have already filtered candidates against the builtin
    search/holdout tasks (both current writers do)."""
    with _extras_lock(suite_dir):
        fresh = load_extras(suite_dir)
        seen = {dedupe_key(t.objective, t.inputs) for t in fresh}
        survivors: list[Task] = []
        for task in candidates:
            key = dedupe_key(task.objective, task.inputs)
            if key in seen:  # also dedupes WITHIN the batch — first wins
                continue
            seen.add(key)
            survivors.append(task)
        if survivors:
            save_extras(suite_dir, [*fresh, *survivors])
        return survivors, len(fresh) + len(survivors)


def search_and_holdout(
    suite: str, extras_dir: Optional[Path | str] = None
) -> tuple[list[Task], list[Task]]:
    """Disjoint (search, holdout) task lists for a named suite. 'mixed' spans
    every domain — the default, so optimization never overfits to one shape.
    Extras from `extras_dir` are merged in, alternating search/holdout so both
    sides grow together."""
    if suite == "mixed":
        searches, holdouts = [], []
        for name in sorted(_BUILDERS):
            s, h = search_and_holdout(name)
            searches.extend(s)
            holdouts.extend(h)
    elif suite in _BUILDERS:
        build, data = _BUILDERS[suite]
        split = len(data) - len(data) // 3  # ~2/3 search, ~1/3 holdout, disjoint
        searches, holdouts = build(list(data[:split])), build(list(data[split:]))
    else:
        raise ValueError(f"unknown suite {suite!r}; expected one of {SUITE_NAMES}")
    if extras_dir is not None:
        for i, task in enumerate(load_extras(extras_dir)):
            (searches if i % 2 == 0 else holdouts).append(task)
    return searches, holdouts
