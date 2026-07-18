"""Doctor: tolerant diagnosis + narrowest-repair fixing of a pack store."""
import json

from selflearn.cli import main
from selflearn.contracts import CandidateEntry, EntrySource, Probe, PublishDecision
from selflearn.doctor import run_doctor
from selflearn.store import PackStore


def _cand(eid, pack="p", topic="t", quarantined=False):
    return CandidateEntry(
        id=eid, pack=pack, kind="knowledge", body=f"body of {eid}",
        claims=("a claim",), topic=topic,
        quarantined=quarantined,
        quarantine_reason="screen hit" if quarantined else "",
        sources=(EntrySource(url="https://docs.example.com/x",
                             fetched_at="2026-07-01", sha256="0" * 64,
                             tier="official"),))


def _healthy_store(root):
    store = PackStore(root)
    store.add_candidate(_cand("e1"))
    store.publish("e1", PublishDecision(entry_id="e1", publish=True,
                                        basis=("test",),
                                        identity_basis="test"),
                  probes=[Probe(id="pr1", entry_id="e1", kind="recall",
                                question="?", expected="!",
                                check_kind="deterministic", validated=True,
                                validated_by="validator")])
    store.add_candidate(_cand("e2"))
    return store


def test_healthy_store_reports_clean(tmp_path):
    _healthy_store(tmp_path / "s")
    report = run_doctor(tmp_path / "s")
    assert report.ok and not report.findings and report.load_ok


def test_missing_root_is_unfixable(tmp_path):
    report = run_doctor(tmp_path / "nope")
    assert not report.ok
    assert report.findings[0].code == "store.missing"


def test_corrupt_manifest_rebuilt(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    (root / "p" / "manifest.json").write_text("{not json")
    report = run_doctor(root)                       # report-only
    assert not report.ok
    assert any(f.code == "manifest.corrupt" and not f.fixed
               for f in report.findings)
    assert (root / "p" / "manifest.json").read_text() == "{not json"

    report = run_doctor(root, fix=True)
    assert report.ok and report.load_ok
    store = PackStore(root)                         # loads again
    assert {e.status for e in store.entries_for("p")} == \
        {"published", "candidate"}
    # coverage rebuilt from published entries
    assert store.coverage("p")["t"] == "covered"


def test_missing_manifest_rebuilt(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    (root / "p" / "manifest.json").unlink()
    report = run_doctor(root, fix=True)
    assert report.ok
    PackStore(root)


def test_dangling_manifest_entry_dropped(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    (root / "p" / "entries" / "e2.md").unlink()
    report = run_doctor(root, fix=True)
    assert any(f.code == "manifest.dangling" and f.fixed
               for f in report.findings)
    assert "e2" not in json.loads(
        (root / "p" / "manifest.json").read_text())["entries"]
    PackStore(root)


def test_orphan_entry_adopted(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    manifest = json.loads((root / "p" / "manifest.json").read_text())
    del manifest["entries"]["e2"]
    (root / "p" / "manifest.json").write_text(json.dumps(manifest))
    report = run_doctor(root, fix=True)
    assert any(f.code == "manifest.orphan" and f.fixed
               for f in report.findings)
    assert PackStore(root).get("e2").status == "candidate"


def test_status_mismatch_entry_file_wins(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    manifest = json.loads((root / "p" / "manifest.json").read_text())
    manifest["entries"]["e1"]["status"] = "candidate"   # file says published
    (root / "p" / "manifest.json").write_text(json.dumps(manifest))
    report = run_doctor(root, fix=True)
    assert any(f.code == "manifest.status-mismatch" for f in report.findings)
    assert PackStore(root).get("e1").status == "published"


def test_negative_marks_clamped_and_preserved(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    manifest = json.loads((root / "p" / "manifest.json").read_text())
    manifest["entries"]["e1"]["helpful"] = 4.0
    manifest["entries"]["e1"]["harmful"] = -3.0
    (root / "p" / "manifest.json").write_text(json.dumps(manifest))
    report = run_doctor(root, fix=True)
    assert any(f.code == "manifest.bad-marks" for f in report.findings)
    stored = PackStore(root).get("e1")
    assert stored.helpful == 4.0 and stored.harmful == 0.0


def test_corrupt_entry_moved_aside_not_deleted(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    bad = root / "p" / "entries" / "e2.md"
    bad.write_text("no frontmatter at all")
    report = run_doctor(root, fix=True)
    assert report.ok
    assert any(f.code == "entry.corrupt" and f.fixed for f in report.findings)
    moved = root / "p" / "broken" / "e2.md"
    assert moved.read_text() == "no frontmatter at all"
    assert "e2" not in json.loads(
        (root / "p" / "manifest.json").read_text())["entries"]


def test_invalid_status_reset_to_candidate(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    md = root / "p" / "entries" / "e2.md"
    md.write_text(md.read_text().replace("status: candidate",
                                         "status: bogus"))
    report = run_doctor(root, fix=True)
    assert any(f.code == "entry.bad-status" and f.fixed
               for f in report.findings)
    assert PackStore(root).get("e2").status == "candidate"


def test_quarantined_published_demoted(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    md = root / "p" / "entries" / "e1.md"
    text = md.read_text().replace("status: published",
                                  "status: published\nquarantined: true\n"
                                  "quarantine_reason: injected later")
    md.write_text(text)
    report = run_doctor(root, fix=True)
    assert any(f.code == "entry.quarantined-published" and f.fixed
               for f in report.findings)
    stored = PackStore(root).get("e1")
    assert stored.status == "candidate" and stored.cand.quarantined


def test_probe_repairs(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    probes = root / "p" / "evals" / "probes.jsonl"
    good = probes.read_text()
    unvalidated = json.dumps({
        "id": "pr2", "entry_id": "e1", "kind": "recall", "question": "?",
        "expected": "!", "check_kind": "deterministic", "validated": False,
        "validated_by": "", "retired": False})
    orphan = unvalidated.replace('"e1"', '"ghost"').replace('"pr2"', '"pr3"')
    probes.write_text(good + "{broken json\n" + unvalidated + "\n"
                      + orphan + "\n")
    report = run_doctor(root, fix=True)
    codes = {f.code for f in report.findings}
    assert {"probe.corrupt", "probe.unvalidated",
            "probe.unknown-entry"} <= codes
    store = PackStore(root)
    assert [p.id for p in store.probes_for("e1")] == ["pr1"]      # pr2 retired
    assert [p.id for p in store.probes_for("e1", include_retired=True)] == \
        ["pr1", "pr2"]


def test_corrupt_vectors_reset_and_unknown_pruned(tmp_path):
    root = tmp_path / "s"
    store = _healthy_store(root)
    store.set_vector("e1", (1.0, 0.0), "emb-1")
    vectors = root / "p" / "vectors.json"
    data = json.loads(vectors.read_text())
    data["ghost"] = {"embedder_id": "emb-1", "vector": [0.0]}
    vectors.write_text(json.dumps(data))
    report = run_doctor(root, fix=True)
    assert any(f.code == "vectors.unknown-entry" and f.fixed
               for f in report.findings)
    assert set(json.loads(vectors.read_text())) == {"e1"}

    vectors.write_text("[oops")
    report = run_doctor(root, fix=True)
    assert any(f.code == "vectors.corrupt" and f.fixed
               for f in report.findings)
    assert not vectors.exists()
    PackStore(root)


def test_corrupt_learner_state_moved_aside(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    (root / "learner-state.json").write_text("{nope")
    report = run_doctor(root, fix=True)
    assert any(f.code == "learner.corrupt" and f.fixed
               for f in report.findings)
    assert not (root / "learner-state.json").exists()
    assert (root / "learner-state.json.corrupt").exists()


def test_report_mode_never_writes(tmp_path):
    root = tmp_path / "s"
    _healthy_store(root)
    (root / "p" / "manifest.json").write_text("{not json")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    run_doctor(root)
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_cli_doctor_exit_codes(tmp_path, capsys):
    root = tmp_path / "s"
    _healthy_store(root)
    assert main(["doctor", "--store", str(root)]) == 0
    assert "no issues" in capsys.readouterr().out

    (root / "p" / "manifest.json").write_text("{not json")
    assert main(["doctor", "--store", str(root)]) == 1
    assert "--fix" in capsys.readouterr().out

    assert main(["doctor", "--store", str(root), "--fix"]) == 0
    out = capsys.readouterr().out
    assert "FIXED" in out and "store loads: yes" in out
    assert main(["doctor", "--store", str(root)]) == 0
