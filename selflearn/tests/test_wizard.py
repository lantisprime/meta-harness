"""Wizard: scripted-stdin walkthroughs of the interactive front door."""
import io

from selflearn.cli import main
from selflearn.wizard import Console, WizardExit, run_wizard

import pytest


def _run(script: str, store: str = "") -> tuple[int, str]:
    out = io.StringIO()
    rc = run_wizard(runner=main, store=store,
                    in_stream=io.StringIO(script), out_stream=out)
    return rc, out.getvalue()


def test_quit_immediately(tmp_path):
    rc, out = _run("q\n", store=str(tmp_path / "s"))
    assert rc == 0
    assert "selflearn wizard" in out and "bye." in out


def test_eof_exits_cleanly(tmp_path):
    rc, out = _run("", store=str(tmp_path / "s"))
    assert rc == 0 and "bye." in out


def test_unknown_choice_reprompts(tmp_path):
    rc, out = _run("42\nq\n", store=str(tmp_path / "s"))
    assert rc == 0 and "unknown choice '42'" in out


def test_console_ask_validates_choices_and_defaults():
    con = Console(io.StringIO("bogus\nkb\n\n"), io.StringIO())
    assert con.ask("kind", default="kb", choices=["kb", "yt"]) == "kb"
    assert con.ask("dir", default="/x") == "/x"
    with pytest.raises(WizardExit):
        con.ask("more")


def test_seed_and_list_through_wizard(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "note.md").write_text("FastAPI lifespan replaces on_event.")
    store = str(tmp_path / "store")
    # menu 4 (seed): kind=kb, dir, pack, publish=y, run=y; then 9 (status),
    # run=y; then quit
    script = (f"4\nkb\n{kb}\nfastapi\ny\ny\n"
              "9\ny\n"
              "q\n")
    rc, out = _run(script, store=store)
    assert rc == 0
    assert "command: selflearn seed-kb" in out
    assert "seeded 1 entries" in out or "exit code 0" in out
    assert "command: selflearn list" in out


def test_declined_command_is_not_run(tmp_path):
    store = str(tmp_path / "store")
    script = "10\nn\nq\n"                     # next flow, decline running
    rc, out = _run(script, store=store)
    assert rc == 0
    assert "command: selflearn next" in out
    assert "skipped" in out and "next best actions" not in out


def test_broken_store_snapshot_points_at_doctor(tmp_path):
    root = tmp_path / "store"
    (root / "p").mkdir(parents=True)          # pack without manifest
    script = "11\ny\ny\nq\n"                  # doctor flow with --fix, run
    rc, out = _run(script, store=str(root))
    assert rc == 0
    assert "store failed to load" in out
    assert "command: selflearn doctor" in out and "--fix" in out


def test_wizard_via_cli_entrypoint(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("q\n"))
    rc = main(["wizard", "--store", str(tmp_path / "s")])
    assert rc == 0
