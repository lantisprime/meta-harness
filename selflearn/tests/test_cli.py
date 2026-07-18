"""CLI: gather/seed/list/retrieve round-trip with no host and no network."""
import json

import pytest

from selflearn.cli import main


def test_gather_seed_list_retrieve_roundtrip(tmp_path, capsys):
    note = tmp_path / "notes" / "lifespan.md"
    note.parent.mkdir()
    note.write_text("FastAPI lifespan context manager replaces on_event "
                    "startup shutdown handlers.")

    rc = main(["gather", f"file://{note}", "--workdir", str(tmp_path / "w"),
               "--out", str(tmp_path / "sources.json"), "--no-network"])
    assert rc == 0
    docs = json.loads((tmp_path / "sources.json").read_text())
    assert docs[0]["provenance"]["plugin"] == "local"
    assert "gathered 1 documents" in capsys.readouterr().out

    store = str(tmp_path / "store")
    rc = main(["seed-kb", str(note.parent), "--pack", "fastapi",
               "--store", store, "--publish"])
    assert rc == 0

    rc = main(["list", "--store", store])
    out = capsys.readouterr().out
    assert rc == 0 and "fastapi" in out and "published" in out

    with pytest.warns(UserWarning):   # degraded keyword retrieval is loud
        rc = main(["retrieve", "lifespan on_event handlers",
                   "--packs", "fastapi", "--store", store])
    out = capsys.readouterr().out
    assert rc == 0 and "field notes" in out


def test_cli_errors_are_exit_code_2(tmp_path, capsys):
    rc = main(["gather", "gopher://nope", "--workdir", str(tmp_path / "w"),
               "--out", str(tmp_path / "s.json"), "--no-network"])
    assert rc == 2
    assert "no plugin claims" in capsys.readouterr().err


def test_retrieve_no_match_exit_1(tmp_path, capsys):
    store = str(tmp_path / "store")
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "a.md").write_text("totally unrelated content here")
    main(["seed-kb", str(tmp_path / "kb"), "--pack", "p", "--store", store,
          "--publish"])
    with pytest.warns(UserWarning):
        rc = main(["retrieve", "zzz qqq xxx", "--packs", "p", "--store", store])
    assert rc == 1
