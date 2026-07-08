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

import json
from pathlib import Path
from typing import Optional

from metaharness.core.types import Task, TaskType
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


def extras_path(suite_dir: Path | str) -> Path:
    return Path(suite_dir) / "extra_tasks.json"


def load_extras(suite_dir: Path | str) -> list[Task]:
    path = extras_path(suite_dir)
    if not path.is_file():
        return []
    return [Task.model_validate(t) for t in json.loads(path.read_text(encoding="utf-8"))]


def save_extras(suite_dir: Path | str, tasks: list[Task]) -> None:
    Path(suite_dir).mkdir(parents=True, exist_ok=True)
    extras_path(suite_dir).write_text(
        json.dumps([t.model_dump() for t in tasks], indent=1, default=str),
        encoding="utf-8",
    )


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
