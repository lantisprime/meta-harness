"""`metaharness harvest` tests: journal → suite-extras extraction.

Everything runs under `tmp_path` — a harvest run must never touch the real
`~/.metaharness`. Synthetic journals are written as raw JSONL (the exact shape
`Journal.load` reads), so the corpus edge cases verified in the real data
(reason-only failures, duplicate `step.started`, structured dict outputs,
missing `run.started`) are reproducible here.
"""
from __future__ import annotations

import json

import pytest

from metaharness.cli import main
from metaharness.core.types import Task, TaskType, Tier, WorkerResult
from metaharness.evals.verifiers import verify_output
import metaharness.optimization.harvest as harvest_mod
from metaharness.optimization.harvest import HarvestReport, harvest_journals
from metaharness.optimization.suites import (
    check_value_ok,
    extras_path,
    load_extras,
    save_extras,
    search_and_holdout,
)
from metaharness.workflows.dsl import WorkflowSpec


# -- journal fixtures --------------------------------------------------------------


def _write_events(path, run_id, events):
    """Write (kind, step_id, payload) tuples as JournalEntry JSONL lines."""
    lines = []
    for seq, (kind, step_id, payload) in enumerate(events):
        lines.append(json.dumps(
            {"seq": seq, "at": 0.0, "kind": kind, "run_id": run_id,
             "step_id": step_id, "payload": payload},
            sort_keys=True,
        ))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _journal(dirpath, run_id, steps, completions, context=None):
    """Write a full run journal: a validated run.started header + step events."""
    spec = WorkflowSpec.model_validate({"name": "wf", "steps": steps})
    events = [("run.started", None,
               {"workflow": spec.model_dump(mode="json"), "context": context or {}})]
    events.extend(completions)
    dirpath.mkdir(parents=True, exist_ok=True)
    _write_events(dirpath / f"{run_id}.jsonl", run_id, events)


def _completed(sid, output, verdict="pass"):
    return ("step.completed", sid,
            {"step_id": sid, "verdict": verdict, "output": output,
             "attempts": 1, "cost_usd": 0.0})


def _failed(sid, output, verdict="fail"):
    # terminal StepRecord dump (verdict present) — a genuinely executed step
    return ("step.failed", sid,
            {"step_id": sid, "verdict": verdict, "output": output,
             "attempts": 3, "cost_usd": 0.0})


def _reason_failed(sid, reason):
    # pre-execution orchestration failure (engine.py:171-172) — no verdict
    return ("step.failed", sid, {"reason": reason})


def _step(sid, task_type, objective, inputs, success_check, **extra):
    d = {"id": sid, "task_type": task_type, "objective": objective, "inputs": inputs}
    if success_check is not None:
        d["success_check"] = success_check
    d.update(extra)
    return d


# -- tests -------------------------------------------------------------------------


def test_happy_path_writes_loadable_mergeable_extras(tmp_path):
    journals = tmp_path / "journals"
    root = tmp_path / "opt"
    steps = [
        _step("cls", "classify", "Classify the tone.", {"text": "lovely"},
              {"one_of": ["positive", "negative"]}),
        _step("calc", "arithmetic", "Compute 6*7.", {"expression": "6*7"},
              {"equals": 42}),
    ]
    _journal(journals, "run_aaa", steps,
             [_completed("cls", "positive"), _completed("calc", 42)])

    report = harvest_journals(journals, "mixed", root)
    assert report.added == 2
    assert report.steps_executed == 2

    extras = load_extras(root / "mixed")
    assert len(extras) == 2
    assert all(isinstance(t, Task) for t in extras)

    search, holdout = search_and_holdout("mixed", extras_dir=root / "mixed")
    base_search, base_holdout = search_and_holdout("mixed")
    assert len(search) + len(holdout) == len(base_search) + len(base_holdout) + 2


def test_context_and_step_references_resolved(tmp_path):
    journals = tmp_path / "journals"
    steps = [
        _step("cls", "classify", "Classify the review.", {"review": "$context.text"},
              {"one_of": ["positive", "negative"]}),
        _step("ext", "extract", "Echo the upstream label.",
              {"label": "$steps.cls.output"}, {"equals": "positive"},
              depends_on=["cls"]),
    ]
    _journal(journals, "run_ref", steps,
             [_completed("cls", "positive"), _completed("ext", "positive")],
             context={"text": "great product"})

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.added == 2
    extras = load_extras(tmp_path / "opt" / "mixed")
    resolved_inputs = [t.inputs for t in extras]
    assert {"review": "great product"} in resolved_inputs      # $context resolved
    assert {"label": "positive"} in resolved_inputs            # $steps.output resolved


def test_unresolvable_reference_skipped_and_counted(tmp_path):
    journals = tmp_path / "journals"
    steps = [
        _step("cls", "classify", "Fine one.", {"review": "ok"},
              {"one_of": ["positive", "negative"]}),
        _step("bad", "extract", "Bad ref.", {"x": "$steps.missing.output"},
              {"equals": "y"}),
    ]
    _journal(journals, "run_u", steps,
             [_completed("cls", "positive"), _completed("bad", "y")])

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.skipped["unresolvable_inputs"] == 1
    assert report.added == 1  # the good step still harvested; harvest continued


def test_arithmetic_recomputed_and_unevaluable_skipped(tmp_path):
    journals = tmp_path / "journals"
    steps = [
        _step("lie", "arithmetic", "Compute 6*7.", {"expression": "6*7"},
              {"equals": 999}),                       # journal lies about the answer
        _step("bad", "arithmetic", "Compute nonsense.", {"expression": "import os"},
              {"equals": 1}),
    ]
    _journal(journals, "run_m", steps,
             [_completed("lie", 999), _completed("bad", 1)])

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.skipped["arithmetic_unevaluable"] == 1
    assert report.added == 1
    (task,) = load_extras(tmp_path / "opt" / "mixed")
    assert task.success_check["equals"] == 42  # recomputed, not the journaled 999


def test_no_check_bad_vocab_and_type_not_allowed_counted(tmp_path):
    journals = tmp_path / "journals"
    steps = [
        _step("nocheck", "classify", "No success check.", {"review": "x"}, None),
        _step("vocab", "classify", "Unverifiable check.", {"review": "y"},
              {"regex": "^a"}),
        _step("summ", "summarize", "Summarize (type not in mixed).", {"text": "z"},
              {"equals": "s"}),
    ]
    _journal(journals, "run_s", steps,
             [_completed("nocheck", "positive"), _completed("vocab", "positive"),
              _completed("summ", "s")])

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.skipped["no_success_check"] == 1
    assert report.skipped["bad_check_vocab"] == 1
    assert report.skipped["type_not_allowed"] == 1
    assert report.added == 0


def test_reason_only_failure_not_executed(tmp_path):
    journals = tmp_path / "journals"
    steps = [_step("cls", "classify", "Only orchestration-failed.", {"review": "x"},
                   {"one_of": ["positive", "negative"]})]
    _journal(journals, "run_r", steps, [_reason_failed("cls", "bad input reference")])

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.steps_executed == 0      # a reason-only failure never counts as executed
    assert report.added == 0


def test_dedupe_against_builtin_existing_and_within_corpus(tmp_path):
    journals = tmp_path / "journals"
    root = tmp_path / "opt"

    # (a) a task identical to a builtin seed is skipped as a duplicate
    builtin_search, _ = search_and_holdout("mixed")
    seed = next(t for t in builtin_search if t.task_type == TaskType.CLASSIFY)
    dup_of_seed = _step("dup", "classify", seed.objective, seed.inputs,
                        {"one_of": ["positive", "negative"]})

    # (b) two journals share one objective/inputs → harvested once
    shared = _step("shared", "classify", "Shared objective.", {"review": "same"},
                   {"one_of": ["positive", "negative"]})
    fresh = _step("fresh", "extract", "Unique one.", {"text": "u"}, {"equals": "u"})

    _journal(journals, "run_1", [dup_of_seed, shared, fresh],
             [_completed("dup", "positive"), _completed("shared", "positive"),
              _completed("fresh", "u")])
    _journal(journals, "run_2", [shared],
             [_completed("shared", "positive")])

    report = harvest_journals(journals, "mixed", root)
    assert report.added == 2                     # shared (once) + fresh
    assert report.skipped["duplicate"] == 2      # builtin dup + second journal's shared

    # (c) re-harvest with the same objective already in extras → skipped again
    report2 = harvest_journals(journals, "mixed", root)
    assert report2.added == 0
    assert report2.skipped["duplicate"] >= 2


def test_failed_step_still_harvested_with_verdict_provenance(tmp_path):
    journals = tmp_path / "journals"
    steps = [_step("ext", "extract", "Extract the code.", {"text": "code is ABC"},
                   {"equals": "ABC"})]
    _journal(journals, "run_f", steps, [_failed("ext", "WRONG", verdict="fail")])

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.added == 1
    assert report.candidates[0]["verdict"] == "fail"     # provenance, not the run outcome
    (task,) = load_extras(tmp_path / "opt" / "mixed")
    assert task.success_check == {"equals": "ABC"}       # ground truth is the check


def test_corrupt_and_headerless_journals_skipped_others_processed(tmp_path):
    journals = tmp_path / "journals"
    journals.mkdir()
    # 1: garbage bytes
    (journals / "run_bad.jsonl").write_text("this is not json{\n", encoding="utf-8")
    # 2: valid JSONL but no run.started header
    _write_events(journals / "run_nohdr.jsonl", "run_nohdr",
                  [("step.completed", "cls",
                    {"step_id": "cls", "verdict": "pass", "output": "positive"})])
    # 3: a good journal
    _journal(journals, "run_ok",
             [_step("cls", "classify", "Good.", {"review": "x"},
                    {"one_of": ["positive", "negative"]})],
             [_completed("cls", "positive")])

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.files_scanned == 3
    assert report.files_unreadable == 2
    assert report.added == 1


def test_idempotent_second_run_adds_zero_and_bytes_unchanged(tmp_path):
    journals = tmp_path / "journals"
    root = tmp_path / "opt"
    _journal(journals, "run_i",
             [_step("cls", "classify", "Once.", {"review": "x"},
                    {"one_of": ["positive", "negative"]})],
             [_completed("cls", "positive")])

    r1 = harvest_journals(journals, "mixed", root)
    assert r1.added == 1
    first_bytes = extras_path(root / "mixed").read_bytes()

    r2 = harvest_journals(journals, "mixed", root)
    assert r2.added == 0
    assert extras_path(root / "mixed").read_bytes() == first_bytes


def test_oversized_task_skipped(tmp_path):
    journals = tmp_path / "journals"
    big = "x" * 4000
    _journal(journals, "run_big",
             [_step("ext", "extract", "Huge input.", {"text": big}, {"equals": "y"})],
             [_completed("ext", "y")])

    report = harvest_journals(journals, "mixed", tmp_path / "opt", max_task_chars=500)
    assert report.skipped["oversized"] == 1
    assert report.added == 0


def test_deterministic_report_and_candidate_order(tmp_path):
    journals = tmp_path / "journals"
    _journal(journals, "run_b",
             [_step("c2", "classify", "B.", {"review": "b"},
                    {"one_of": ["positive", "negative"]})],
             [_completed("c2", "positive")])
    _journal(journals, "run_a",
             [_step("c1", "classify", "A.", {"review": "a"},
                    {"one_of": ["positive", "negative"]})],
             [_completed("c1", "positive")])

    r1 = harvest_journals(journals, "mixed", tmp_path / "opt1", dry_run=True)
    r2 = harvest_journals(journals, "mixed", tmp_path / "opt2", dry_run=True)
    assert r1.model_dump() == r2.model_dump()
    # filename-sorted scan ⇒ run_a's candidate precedes run_b's
    assert [c["run_id"] for c in r1.candidates] == ["run_a", "run_b"]


def test_cli_dry_run_prints_report_and_writes_nothing(tmp_path, capsys):
    journals = tmp_path / "journals"
    root = tmp_path / "opt"
    _journal(journals, "run_cli",
             [_step("cls", "classify", "CLI one.", {"review": "x"},
                    {"one_of": ["positive", "negative"]})],
             [_completed("cls", "positive")])

    main(["harvest", "--dry-run", "--suite", "mixed",
          "--journals", str(journals), "--root", str(root)])
    out = json.loads(capsys.readouterr().out)
    assert out["added"] == 1
    assert not extras_path(root / "mixed").exists()   # dry-run wrote nothing


def test_mixed_vs_named_suite_type_filtering(tmp_path):
    journals = tmp_path / "journals"
    steps = [
        _step("cls", "classify", "Tone.", {"review": "a"},
              {"one_of": ["positive", "negative"]}),
        _step("ext", "extract", "Year.", {"sentence": "in 1990"}, {"equals": "1990"}),
        _step("calc", "arithmetic", "Compute 2+2.", {"expression": "2+2"}, {"equals": 4}),
        _step("summ", "summarize", "Summarize.", {"text": "t"}, {"equals": "s"}),
    ]
    comps = [_completed("cls", "positive"), _completed("ext", "1990"),
             _completed("calc", 4), _completed("summ", "s")]
    _journal(journals, "run_t", steps, comps)

    mixed = harvest_journals(journals, "mixed", tmp_path / "m")
    assert mixed.added == 3                       # classify + extract + arithmetic
    assert mixed.skipped["type_not_allowed"] == 1  # summarize only

    classify_only = harvest_journals(journals, "classify", tmp_path / "c")
    assert classify_only.added == 1               # classify only
    assert classify_only.skipped["type_not_allowed"] == 3  # extract/arith/summarize


def test_cli_write_lands_under_root_not_cwd(tmp_path, monkeypatch, capsys):
    journals = tmp_path / "journals"
    root = tmp_path / "opt"
    caller = tmp_path / "caller"
    caller.mkdir()
    _journal(journals, "run_w",
             [_step("cls", "classify", "Write path.", {"review": "x"},
                    {"one_of": ["positive", "negative"]})],
             [_completed("cls", "positive")])

    monkeypatch.chdir(caller)
    main(["harvest", "--suite", "mixed",
          "--journals", str(journals), "--root", str(root)])
    out = json.loads(capsys.readouterr().out)
    assert out["added"] == 1
    assert extras_path(root / "mixed").exists()       # under --root/<suite>
    assert not (caller / "extra_tasks.json").exists()  # never relative to cwd
    assert not (caller / "mixed").exists()


def test_bad_check_value_cases_skipped_and_never_crash_verifier(tmp_path):
    journals = tmp_path / "journals"
    steps = [
        # non-float tol → float(check["tol"]) would raise in verify_output
        _step("tol", "classify", "Bad tol.", {"review": "a"},
              {"equals": "1", "tol": "not-a-float"}),
        # empty one_of
        _step("empty", "classify", "Empty one_of.", {"review": "b"}, {"one_of": []}),
        # non-list one_of
        _step("nonlist", "classify", "Non-list one_of.", {"review": "c"},
              {"one_of": "positive"}),
        # two primary keys → caught earlier as bad_check_vocab
        _step("two", "classify", "Two primaries.", {"review": "d"},
              {"equals": "x", "contains": "y"}),
    ]
    comps = [_completed("tol", "1"), _completed("empty", "b"),
             _completed("nonlist", "c"), _completed("two", "x")]
    _journal(journals, "run_v", steps, comps)

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.added == 0
    assert report.skipped["bad_check_value"] == 3   # tol, empty one_of, non-list one_of
    assert report.skipped["bad_check_vocab"] == 1   # two primary keys
    assert not extras_path(tmp_path / "opt" / "mixed").exists()

    # regression: the non-float-tol check really would crash the verifier, which is
    # exactly why harvest must never let it reach extras.
    crafted = Task(task_type=TaskType.CLASSIFY, objective="x", inputs={},
                   success_check={"equals": "1", "tol": "not-a-float"})
    result = WorkerResult(task_id=crafted.id, worker_id="w", tier=Tier.SMALL,
                          model="m", output="1", raw_text="1")
    with pytest.raises(ValueError):
        verify_output(crafted, result)


# -- panel-review regressions --------------------------------------------------------


def test_pathological_tol_values_rejected(tmp_path):
    """Panel F1 (codex P1 + kimi P2): a float-coercible tol is not enough —
    tol=-1 crashes math.isclose in verify_output, and tol=inf/1e309 makes ANY
    numeric output PASS, silently corrupting tuning ground truth; nan must fall
    to the same gate. All four → bad_check_value; a sane tol still harvests."""
    journals = tmp_path / "journals"
    steps = [
        _step("neg", "extract", "Negative tol.", {"text": "a"},
              {"equals": "1", "tol": -1}),
        _step("inf", "extract", "Inf tol.", {"text": "b"},
              {"equals": "1", "tol": "inf"}),
        _step("big", "extract", "Overflowing tol.", {"text": "c"},
              {"equals": "1", "tol": "1e309"}),
        _step("nan", "extract", "NaN tol.", {"text": "d"},
              {"equals": "1", "tol": "nan"}),
        _step("ok", "extract", "Sane tol.", {"text": "e"},
              {"equals": "1", "tol": 0.5}),
    ]
    comps = [_completed(s["id"], "1") for s in steps]
    _journal(journals, "run_tol", steps, comps)

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.skipped["bad_check_value"] == 4
    assert report.added == 1
    (task,) = load_extras(tmp_path / "opt" / "mixed")
    assert task.success_check == {"equals": "1", "tol": 0.5}


def test_check_value_ok_branches_directly():
    """check_value_ok is now public and shared by harvest + the coverage
    endpoint; pin every value-hardening branch directly, including the
    contains branch that no path-level test reaches."""
    # happy paths
    assert check_value_ok({"equals": 5})
    assert check_value_ok({"equals": 5, "tol": 0})
    assert check_value_ok({"equals": 5, "tol": 0.5})
    assert check_value_ok({"one_of": ["a", 1, 2.0]})
    assert check_value_ok({"contains": "x"})
    # tol branch
    assert not check_value_ok({"equals": 5, "tol": "not-a-float"})
    assert not check_value_ok({"equals": 5, "tol": -1})
    assert not check_value_ok({"equals": 5, "tol": float("inf")})
    assert not check_value_ok({"equals": 5, "tol": float("nan")})
    assert not check_value_ok({"equals": 5, "tol": 10 ** 400})   # OverflowError, not ValueError
    # one_of branch
    assert not check_value_ok({"one_of": []})
    assert not check_value_ok({"one_of": "positive"})
    assert not check_value_ok({"one_of": [None]})
    # contains branch (previously unreached)
    assert not check_value_ok({"contains": ""})
    assert not check_value_ok({"contains": 5})


def test_arithmetic_division_by_zero_skipped_not_fatal(tmp_path):
    """Panel F2 (codex P1): eval_arithmetic("1/0") raises ZeroDivisionError —
    NOT SandboxError (sandbox.py:49 operator.truediv) — which used to abort the
    entire harvest. It must count as arithmetic_unevaluable and continue."""
    journals = tmp_path / "journals"
    steps = [
        _step("boom", "arithmetic", "Compute 1/0.", {"expression": "1/0"},
              {"equals": 0}),
        _step("fine", "arithmetic", "Compute 3*3.", {"expression": "3*3"},
              {"equals": 9}),
    ]
    _journal(journals, "run_z", steps,
             [_completed("boom", 0), _completed("fine", 9)])

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.skipped["arithmetic_unevaluable"] == 1
    assert report.added == 1  # harvest continued past the throwing expression
    (task,) = load_extras(tmp_path / "opt" / "mixed")
    assert task.success_check["equals"] == 9


def test_concurrent_extras_writer_survives_save(tmp_path, monkeypatch):
    """Panel F3 (codex P1 + opus P3 + kimi P2): harvest read extras before the
    scan and wrote [stale + new] after it, erasing anything a concurrent writer
    (the coverage endpoint, app.py:405) added in between. The save-time fresh
    re-read + re-dedupe must keep the concurrent task."""
    journals = tmp_path / "journals"
    root = tmp_path / "opt"
    _journal(journals, "run_c",
             [_step("cls", "classify", "Harvested one.", {"review": "x"},
                    {"one_of": ["positive", "negative"]})],
             [_completed("cls", "positive")])

    concurrent = Task(task_type=TaskType.CLASSIFY,
                      objective="Concurrent coverage-endpoint task.",
                      inputs={"review": "landed mid-scan"},
                      success_check={"equals": "positive"})

    real_load = harvest_mod.load_extras
    calls = {"n": 0}

    def racy_load(suite_dir):
        calls["n"] += 1
        result = real_load(suite_dir)
        if calls["n"] == 1:  # the other writer lands right after our stale read
            save_extras(suite_dir, [concurrent])
        return result

    monkeypatch.setattr(harvest_mod, "load_extras", racy_load)
    report = harvest_journals(journals, "mixed", root)
    assert report.added == 1
    assert calls["n"] == 2  # initial read + save-time fresh re-read

    objectives = {t.objective for t in load_extras(root / "mixed")}
    assert "Concurrent coverage-endpoint task." in objectives  # not erased
    assert "Harvested one." in objectives


def test_payload_less_and_step_id_less_lines_tolerated(tmp_path):
    """Panel F4 (codex P2 + opus P2): a line omitting "payload" or "step_id" is
    pydantic-valid (JournalEntry defaults, journal.py:25-26) but the raw-dict
    comprehensions did e["payload"] / e["step_id"] → KeyError aborted every
    remaining file. Such lines must degrade to "not executed", per-file."""
    journals = tmp_path / "journals"
    spec = WorkflowSpec.model_validate({"name": "wf", "steps": [
        _step("cls", "classify", "Good step.", {"review": "x"},
              {"one_of": ["positive", "negative"]}),
    ]})
    header = {"seq": 0, "at": 0.0, "kind": "run.started", "run_id": "run_tol2",
              "payload": {"workflow": spec.model_dump(mode="json"), "context": {}}}
    no_payload = {"seq": 1, "at": 0.0, "kind": "step.completed",
                  "run_id": "run_tol2", "step_id": "cls"}          # no payload key
    no_step_id = {"seq": 2, "at": 0.0, "kind": "step.failed",
                  "run_id": "run_tol2",
                  "payload": {"verdict": "fail", "output": "x"}}   # no step_id key
    good = {"seq": 3, "at": 0.0, "kind": "step.completed", "run_id": "run_tol2",
            "step_id": "cls",
            "payload": {"step_id": "cls", "verdict": "pass", "output": "positive",
                        "attempts": 1, "cost_usd": 0.0}}
    journals.mkdir()
    (journals / "run_tol2.jsonl").write_text(
        "\n".join(json.dumps(e) for e in (header, no_payload, no_step_id, good)) + "\n",
        encoding="utf-8",
    )
    # a second, ordinary journal must still be processed after the odd one
    _journal(journals, "run_tol3",
             [_step("ext", "extract", "After the odd file.", {"text": "y"},
                    {"equals": "y"})],
             [_completed("ext", "y")])

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.files_unreadable == 0     # tolerated, not treated as corrupt
    assert report.added == 2                # cls (from the good line) + ext


def test_arithmetic_non_equals_primary_skipped_never_two_primaries(tmp_path):
    """Panel F5 (opus P3): recomputing an arithmetic answer into a one_of check
    injected a second primary key ({"one_of": [...], "equals": N}) — a shape
    harvest itself rejects — and flipped membership semantics to exact-equality.
    A non-equals arithmetic primary is unrecomputable ground truth → skip."""
    journals = tmp_path / "journals"
    steps = [_step("pick", "arithmetic", "Compute 2+2, pick one.",
                   {"expression": "2+2"}, {"one_of": [4, 5]})]
    _journal(journals, "run_p", steps, [_completed("pick", 4)])

    report = harvest_journals(journals, "mixed", tmp_path / "opt")
    assert report.skipped["arithmetic_unevaluable"] == 1
    assert report.added == 0
    assert not extras_path(tmp_path / "opt" / "mixed").exists()


def test_cli_errors_as_json_exit_1_and_corrupt_extras_never_overwritten(tmp_path, capsys):
    """Panel F6 (opus P3 + kimi P3): the "always exits 0" claim was false — a
    malformed pre-existing extra_tasks.json raised out of load_extras and
    tracebacked to the shell. Contract now: exit 0 = harvest ran, exit 1 =
    could not run, {"error": ...} JSON on stdout, corrupt file left untouched."""
    journals = tmp_path / "journals"
    root = tmp_path / "opt"
    _journal(journals, "run_e",
             [_step("cls", "classify", "Never mind me.", {"review": "x"},
                    {"one_of": ["positive", "negative"]})],
             [_completed("cls", "positive")])
    (root / "mixed").mkdir(parents=True)
    corrupt = b"{not valid json"
    extras_path(root / "mixed").write_bytes(corrupt)

    with pytest.raises(SystemExit) as excinfo:
        main(["harvest", "--suite", "mixed",
              "--journals", str(journals), "--root", str(root)])
    assert excinfo.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "error" in out
    assert extras_path(root / "mixed").read_bytes() == corrupt  # never overwritten

    # happy path still exits 0 (main returns, no SystemExit)
    root2 = tmp_path / "opt2"
    main(["harvest", "--suite", "mixed",
          "--journals", str(journals), "--root", str(root2)])
    assert json.loads(capsys.readouterr().out)["added"] == 1
