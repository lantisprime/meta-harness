"""selflearn doctor: diagnose and repair a knowledge store.

The store's own loader is deliberately loud — any corruption aborts boot
(`PackStore._load` raises on the first bad file). Doctor is the
counterpart: it re-reads the raw files with *tolerant* parsers, names every
problem it finds, and (with ``fix=True``) applies the narrowest repair that
makes the store loadable again without inventing knowledge:

- entry ``.md`` files are the source of truth; manifests are rebuilt
  around them (mark counters are preserved from the old manifest where
  readable, clamped to valid ranges)
- unreadable artifacts that carry knowledge (entry files) are moved aside
  into ``<pack>/broken/`` — never deleted
- regenerable artifacts (``vectors.json``) are reset; probes that violate
  the suite's invariants (unvalidated, orphaned) are dropped or retired
- every repair is a reported finding, and the run ends with a real
  ``PackStore`` load to prove the store boots

Exit contract (see ``cli.cmd_doctor``): healthy store → 0; anything found
that is not (or cannot be) fixed → 1.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from selflearn.contracts import (
    ENTRY_STATUSES,
    CandidateEntry,
    ContractError,
    Probe,
)
from selflearn.learning.gaps import parse_state_data
from selflearn.compilation.models import canonical_procedure_hash
from selflearn.store.packstore import (
    MANIFEST_SCHEMA_VERSION,
    PackStore,
    StoredEntry,
    _candidate_from_front,
    _entry_md,
)


@dataclass
class Finding:
    code: str              # stable machine-readable issue id, e.g. "manifest.dangling"
    where: str             # pack, pack/file, or store root
    detail: str
    fixable: bool = True
    fixed: bool = False
    fix_note: str = ""


@dataclass
class DoctorReport:
    root: Path
    fix: bool
    findings: list[Finding] = field(default_factory=list)
    load_ok: bool = False

    @property
    def ok(self) -> bool:
        """True when the store loads and nothing is left unrepaired."""
        return self.load_ok and all(f.fixed for f in self.findings)

    def render(self) -> str:
        lines = [f"selflearn doctor: {self.root} "
                 f"({'fix' if self.fix else 'report-only'} mode)"]
        if not self.findings:
            lines.append("  no issues found")
        for f in self.findings:
            tag = ("FIXED" if f.fixed
                   else ("ISSUE" if f.fixable else "UNFIXABLE"))
            lines.append(f"  [{tag}] {f.code} @ {f.where} — {f.detail}")
            if f.fix_note:
                lines.append(f"          fix: {f.fix_note}")
        fixed_n = sum(1 for f in self.findings if f.fixed)
        lines.append(f"verdict: {len(self.findings)} issue(s), "
                     f"{fixed_n} fixed; store loads: "
                     f"{'yes' if self.load_ok else 'NO'}")
        if self.findings and not self.fix and any(
                f.fixable for f in self.findings):
            lines.append("run again with --fix to repair")
        return "\n".join(lines)


def run_doctor(root: Path, fix: bool = False) -> DoctorReport:
    root = Path(root)
    report = DoctorReport(root=root, fix=fix)
    if not root.exists():
        report.findings.append(Finding(
            "store.missing", str(root),
            "store root does not exist", fixable=False))
        return report

    _check_learner_state(root, fix, report)
    for pack_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        _doctor_pack(pack_dir, fix, report)

    try:
        PackStore(root)
        report.load_ok = True
    except Exception as exc:
        # Expected in report-only mode when issues exist; surprising (and
        # worth its own finding) after a fix pass or on a "clean" store.
        if fix or not report.findings:
            report.findings.append(Finding(
                "store.load-failed", str(root),
                f"store still fails to load: {exc}", fixable=False))
    return report


# ---------------------------------------------------------------------------
# Root-level state
# ---------------------------------------------------------------------------

def _check_learner_state(root: Path, fix: bool, report: DoctorReport) -> None:
    path = root / "learner-state.json"
    if not path.exists():
        return
    try:
        # Same structural validation the Learner boots through, so every
        # shape that would brick it (not just invalid JSON — a list where
        # 'backoff' should be an object, non-integer counters, a top-level
        # array) is caught here instead of surviving a doctor pass and
        # leaving 'selflearn next' pointing at a doctor that finds nothing.
        parse_state_data(json.loads(path.read_text()))
    except ValueError as exc:      # JSONDecodeError is a ValueError too
        f = Finding("learner.corrupt", path.name,
                    f"learner state is unreadable: {exc}")
        if fix:
            aside = path.with_suffix(".json.corrupt")
            shutil.move(str(path), str(aside))
            f.fixed = True
            f.fix_note = (f"moved aside to {aside.name}; accumulated "
                          "slow-loop evidence is lost but the loop "
                          "restarts cleanly")
        report.findings.append(f)


# ---------------------------------------------------------------------------
# Per-pack checks
# ---------------------------------------------------------------------------

def _doctor_pack(pack_dir: Path, fix: bool, report: DoctorReport) -> None:
    good = _check_entry_files(pack_dir, fix, report)
    _check_manifest(pack_dir, good, fix, report)
    _check_vectors(pack_dir, good, fix, report)
    _check_probes(pack_dir, good, fix, report)
    _check_executors(pack_dir, fix, report)


def _check_executors(pack_dir: Path, fix: bool, report: DoctorReport) -> None:
    """Check executor registry and active executors.

    If executors/registry.json is absent, return silently.
    If corrupt, report Finding with fixable=False.
    For each ACTIVE record: entry file missing -> dangling entry Finding;
    recomputed canonical_procedure_hash != record.spec_hash -> stale spec Finding.
    Never auto-fixes (fix flag ignored for these codes).
    """
    pack = pack_dir.name
    exec_dir = pack_dir / "executors"
    registry_path = exec_dir / "registry.json"

    if not registry_path.exists():
        # No executors registered - nothing to check
        return

    # Check registry is parseable
    try:
        data = json.loads(registry_path.read_text())
        records = data.get("records", [])
    except (json.JSONDecodeError, IOError) as e:
        report.findings.append(Finding(
            "executor.registry-corrupt",
            f"{pack}/executors/registry.json",
            f"Registry unreadable: {e}",
            fixable=False,
        ))
        return

    # Load entries for reference
    entries_dir = pack_dir / "entries"
    entry_files = {f.stem: f for f in entries_dir.glob("*.md")} if entries_dir.exists() else {}

    # Check each ACTIVE record
    for record in records:
        if record.get("status") != "active":
            continue

        entry_id = record.get("entry_id", "")

        # Check: entry file exists
        if entry_id not in entry_files:
            report.findings.append(Finding(
                "executor.dangling-entry",
                f"{pack}/executors/{entry_id}",
                f"Active executor for missing entry {entry_id}",
                fixable=False,
            ))
            continue

        # Check: spec_hash matches current procedure
        try:
            # Read the entry file to get procedure
            entry_path = entry_files[entry_id]
            front, body, err = _read_front(entry_path)
            if err is not None:
                continue

            cand, err = _build_candidate(front, body)
            if err is not None or not cand.procedure:
                continue

            current_hash = canonical_procedure_hash(cand.procedure)
            if current_hash != record.get("spec_hash"):
                report.findings.append(Finding(
                    "executor.stale-spec",
                    f"{pack}/executors/{entry_id}",
                    f"Active executor spec_hash {record.get('spec_hash', '?')[:16]}... "
                    f"does not match current procedure hash {current_hash[:16]}...",
                    fixable=False,
                ))
        except Exception:
            # If we can't verify, skip silently
            pass


@dataclass
class _Entry:
    """One readable entry file as the doctor sees it."""
    cand: CandidateEntry
    status: str          # effective status after any repair
    disk_status: str     # what the file on disk says right now
    helpful: Any = None  # raw frontmatter mark counters (None when absent);
    harmful: Any = None  # fallback when the manifest lost its copy


def _check_entry_files(pack_dir: Path, fix: bool,
                       report: DoctorReport) -> dict[str, _Entry]:
    """Scan entries/*.md with tolerant parsing. Returns the readable
    entries by id after any per-file repairs."""
    pack = pack_dir.name
    entries_dir = pack_dir / "entries"
    good: dict[str, _Entry] = {}
    for md in sorted(entries_dir.glob("*.md")) if entries_dir.exists() else []:
        front, body, err = _read_front(md)
        cand: Optional[CandidateEntry] = None
        if err is None:
            cand, err = _build_candidate(front, body)
        if err is not None:
            f = Finding("entry.corrupt", f"{pack}/{md.name}", err)
            if fix:
                broken = pack_dir / "broken"
                broken.mkdir(exist_ok=True)
                shutil.move(str(md), str(broken / md.name))
                f.fixed = True
                f.fix_note = (f"moved aside to {pack}/broken/{md.name} "
                              "(content preserved, never deleted)")
            report.findings.append(f)
            continue

        disk_status = str(front.get("status", "candidate"))
        status = disk_status
        rewrite = False
        if status not in ENTRY_STATUSES:
            f = Finding("entry.bad-status", f"{pack}/{md.name}",
                        f"invalid status {status!r}")
            status, rewrite = "candidate", True
            if fix:
                f.fixed = True
                f.fix_note = ("reset to 'candidate' — it must pass the "
                              "gates again to publish")
            report.findings.append(f)
        if status == "published" and cand.quarantined:
            f = Finding("entry.quarantined-published", f"{pack}/{md.name}",
                        "entry is quarantined but marked published — the "
                        "store forbids publishing quarantined entries")
            status, rewrite = "candidate", True
            if fix:
                f.fixed = True
                f.fix_note = ("demoted to 'candidate'; a journaled release "
                              "plus verification is the only path back")
            report.findings.append(f)
        # Rewrite the status repair BEFORE the id-mismatch handling: an
        # unresolvable duplicate below bails out of the loop, and the
        # repairs already reported FIXED must have actually happened.
        if fix and rewrite:
            md.write_text(_entry_md(StoredEntry(
                cand=cand, status=status,
                helpful=_num(front.get("helpful")),
                harmful=_num(front.get("harmful")))))
            disk_status = status
        if md.stem != cand.id:
            target = entries_dir / f"{cand.id}.md"
            f = Finding("entry.id-mismatch", f"{pack}/{md.name}",
                        f"file name does not match entry id {cand.id!r}",
                        fixable=not target.exists())
            if fix and not target.exists():
                md.rename(target)
                md = target
                f.fixed = True
                f.fix_note = f"renamed to {target.name}"
            elif target.exists():
                f.detail += f"; {target.name} already exists"
            report.findings.append(f)
            if not f.fixed:
                continue      # unresolvable duplicate: leave it out
        good[cand.id] = _Entry(cand=cand, status=status,
                               disk_status=disk_status,
                               helpful=front.get("helpful"),
                               harmful=front.get("harmful"))
    return good


def _check_manifest(pack_dir: Path, good: dict[str, _Entry],
                    fix: bool, report: DoctorReport) -> None:
    pack = pack_dir.name
    manifest_path = pack_dir / "manifest.json"
    old: dict[str, Any] = {}
    needs_rewrite = False
    if not manifest_path.exists():
        report.findings.append(_maybe_fixed(fix, Finding(
            "manifest.missing", pack, "pack has no manifest.json"),
            "rebuilt from the entry files"))
        needs_rewrite = True
    else:
        try:
            loaded = json.loads(manifest_path.read_text())
            if not isinstance(loaded, dict):
                raise json.JSONDecodeError("not an object", "", 0)
            old = loaded
        except json.JSONDecodeError as exc:
            report.findings.append(_maybe_fixed(fix, Finding(
                "manifest.corrupt", pack,
                f"manifest is not valid JSON: {exc}"),
                "rebuilt from the entry files"))
            needs_rewrite = True

    old_entries = old.get("entries", {})
    old_entries = old_entries if isinstance(old_entries, dict) else {}
    if not needs_rewrite:
        for eid in sorted(set(old_entries) - set(good)):
            report.findings.append(_maybe_fixed(fix, Finding(
                "manifest.dangling", pack,
                f"manifest lists {eid!r} but no readable entry file exists"),
                "dropped from the manifest"))
            needs_rewrite = True
        for eid in sorted(set(good) - set(old_entries)):
            report.findings.append(_maybe_fixed(fix, Finding(
                "manifest.orphan", pack,
                f"entry file {eid}.md is not listed in the manifest"),
                "adopted into the manifest (fresh mark counters)"))
            needs_rewrite = True
        for eid in sorted(set(good) & set(old_entries)):
            ent = good[eid]
            meta = old_entries[eid]
            meta = meta if isinstance(meta, dict) else {}
            if meta.get("status") != ent.status:
                said = (f"entry file says {ent.status!r}"
                        if ent.disk_status == ent.status else
                        f"entry file says {ent.disk_status!r} (repairs to "
                        f"{ent.status!r})")
                report.findings.append(_maybe_fixed(fix, Finding(
                    "manifest.status-mismatch", pack,
                    f"{eid!r}: {said}, manifest says {meta.get('status')!r}"),
                    "manifest updated — the entry file is the source of "
                    "truth"))
                needs_rewrite = True
            marks = ([meta.get("helpful"), meta.get("harmful")]
                     + [x for v in meta.get("marks_by_task", {}).values()
                        if isinstance(v, (list, tuple)) for x in v])
            if any(_mark_bad(m) for m in marks):
                report.findings.append(_maybe_fixed(fix, Finding(
                    "manifest.bad-marks", pack,
                    f"{eid!r}: mark counters are negative or non-numeric"),
                    "clamped to valid non-negative numbers"))
                needs_rewrite = True

    if needs_rewrite:
        legacy = sorted(eid for eid in set(good) & set(old_entries)
                        if _legacy_vector(old_entries[eid]) is not None)
        if legacy:
            report.findings.append(_maybe_fixed(fix, Finding(
                "manifest.legacy-vectors", pack,
                f"manifest rebuild would drop pre-sidecar inline vectors "
                f"for {len(legacy)} entries"),
                "migrated to the vectors.json sidecar"))
    if fix and needs_rewrite:
        _write_manifest(pack_dir, good, old)


def _legacy_vector(meta: Any) -> Optional[list[float]]:
    """The pre-sidecar layout stored 'vector' inline in the manifest
    (still readable by PackStore._load). Returns it when well-formed."""
    if not isinstance(meta, dict):
        return None
    vec = meta.get("vector")
    if (isinstance(vec, (list, tuple)) and vec
            and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                    for x in vec)):
        return [float(x) for x in vec]
    return None


def _write_manifest(pack_dir: Path, good: dict[str, _Entry],
                    old: dict[str, Any]) -> None:
    old_entries = old.get("entries", {})
    old_entries = old_entries if isinstance(old_entries, dict) else {}
    coverage = old.get("coverage", {})
    coverage = dict(coverage) if isinstance(coverage, dict) else {}
    entries_meta: dict[str, Any] = {}
    legacy_vecs: dict[str, Any] = {}
    for eid, ent in sorted(good.items()):
        meta = old_entries.get(eid, {})
        meta = meta if isinstance(meta, dict) else {}
        buckets = meta.get("marks_by_task", {})
        buckets = buckets if isinstance(buckets, dict) else {}
        entries_meta[eid] = {
            "status": ent.status, "kind": ent.cand.kind,
            "topic": ent.cand.topic,
            # manifest counters where valid; the entry file's own copy when
            # the manifest lost them (a rebuilt manifest must not reset a
            # proven entry's evidence to the 0.5 prior)
            "helpful": _pick_mark(meta.get("helpful"), ent.helpful),
            "harmful": _pick_mark(meta.get("harmful"), ent.harmful),
            "marks_by_task": {
                str(k): [_num(v[0]), _num(v[1])]
                for k, v in buckets.items()
                if isinstance(v, (list, tuple)) and len(v) == 2},
            "consecutive_harmful": max(0, _int(
                meta.get("consecutive_harmful"))),
            "marks_updated_at": str(meta.get("marks_updated_at") or ""),
            "embedder_id": str(meta.get("embedder_id") or ""),
        }
        if ent.status == "published":
            coverage.setdefault(ent.cand.topic, "covered")
        vec = _legacy_vector(meta)
        if vec is not None:
            legacy_vecs[eid] = {
                "embedder_id": str(meta.get("embedder_id") or ""),
                "vector": vec}
    if legacy_vecs:
        _migrate_legacy_vectors(pack_dir, legacy_vecs)
    (pack_dir / "manifest.json").write_text(json.dumps({
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "pack": pack_dir.name,
        "coverage": {str(k): str(v) for k, v in coverage.items()},
        "entries": entries_meta,
    }, indent=1, sort_keys=True))


def _migrate_legacy_vectors(pack_dir: Path,
                            legacy_vecs: dict[str, Any]) -> None:
    """Fold pre-sidecar inline vectors into vectors.json instead of losing
    them in the manifest rebuild. Existing sidecar records win; if the
    sidecar itself is unreadable, _check_vectors owns that finding and the
    legacy vectors are left to regeneration like the sidecar's own."""
    path = pack_dir / "vectors.json"
    sidecar: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if not isinstance(loaded, dict):
                return
            sidecar = loaded
        except json.JSONDecodeError:
            return
    merged = {**legacy_vecs, **sidecar}
    path.write_text(json.dumps(merged, sort_keys=True))


def _check_vectors(pack_dir: Path, good: dict[str, _Entry],
                   fix: bool, report: DoctorReport) -> None:
    pack = pack_dir.name
    path = pack_dir / "vectors.json"
    if not path.exists():
        return
    try:
        vecs = json.loads(path.read_text())
        if not isinstance(vecs, dict):
            raise json.JSONDecodeError("not an object", "", 0)
    except json.JSONDecodeError as exc:
        f = Finding("vectors.corrupt", pack,
                    f"vectors.json is not valid JSON: {exc}")
        if fix:
            path.unlink()
            f.fixed = True
            f.fix_note = ("deleted — vectors are regenerable from the "
                          "embedding endpoint; retrieval falls back to "
                          "keyword mode meanwhile")
        report.findings.append(f)
        return
    changed = False
    unknown = sorted(set(vecs) - set(good))
    if unknown:
        f = Finding("vectors.unknown-entry", pack,
                    f"vectors stored for unknown entries: {unknown}")
        if fix:
            for eid in unknown:
                vecs.pop(eid)
            changed = True
            f.fixed = True
            f.fix_note = "pruned the unknown ids"
        report.findings.append(f)
    # A record must have the shape PackStore._load dereferences —
    # {"vector": [numbers...]} — or the loud loader still cannot boot even
    # though the file is valid JSON.
    malformed = sorted(
        eid for eid, rec in vecs.items()
        if not (isinstance(rec, dict)
                and isinstance(rec.get("vector"), list)
                and all(isinstance(x, (int, float))
                        and not isinstance(x, bool)
                        for x in rec["vector"])))
    if malformed:
        f = Finding("vectors.bad-record", pack,
                    f"malformed vector records for entries: {malformed}")
        if fix:
            for eid in malformed:
                vecs.pop(eid)
            changed = True
            f.fixed = True
            f.fix_note = ("pruned — vectors are regenerable from the "
                          "embedding endpoint")
        report.findings.append(f)
    if changed:
        path.write_text(json.dumps(vecs, sort_keys=True))


def _check_probes(pack_dir: Path, good: dict[str, _Entry],
                  fix: bool, report: DoctorReport) -> None:
    pack = pack_dir.name
    path = pack_dir / "evals" / "probes.jsonl"
    if not path.exists():
        return
    kept: list[str] = []
    changed = False
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise TypeError("probe record is not a JSON object")
            retired = bool(rec.pop("retired", False))
            probe = Probe(**rec)
        except (json.JSONDecodeError, ContractError, TypeError) as exc:
            report.findings.append(_maybe_fixed(fix, Finding(
                "probe.corrupt", f"{pack}/evals/probes.jsonl:{i}",
                f"unreadable probe record: {exc}"), "dropped the record"))
            changed = True
            continue
        if probe.entry_id not in good:
            report.findings.append(_maybe_fixed(fix, Finding(
                "probe.unknown-entry", f"{pack}/evals/probes.jsonl:{i}",
                f"probe {probe.id!r} references unknown entry "
                f"{probe.entry_id!r}"), "dropped the record"))
            changed = True
            continue
        if not probe.validated and not retired:
            report.findings.append(_maybe_fixed(fix, Finding(
                "probe.unvalidated", f"{pack}/evals/probes.jsonl:{i}",
                f"probe {probe.id!r} is active but never passed "
                "second-model validation"), "retired the probe"))
            retired = True
            changed = True
        rec["retired"] = retired
        kept.append(json.dumps(rec, sort_keys=True))
    if fix and changed:
        path.write_text("\n".join(kept) + ("\n" if kept else ""))


# ---------------------------------------------------------------------------
# Tolerant parsing helpers
# ---------------------------------------------------------------------------

def _read_front(path: Path) -> tuple[dict, str, Optional[str]]:
    """(frontmatter, body, error): never raises."""
    try:
        text = path.read_text()
    except OSError as exc:
        return {}, "", f"unreadable file: {exc}"
    if not text.startswith("---\n"):
        return {}, "", "no YAML frontmatter"
    try:
        _, header, body = text.split("---\n", 2)
        front = yaml.safe_load(header)
    except (ValueError, yaml.YAMLError) as exc:
        return {}, "", f"corrupt frontmatter: {exc}"
    if not isinstance(front, dict):
        return {}, "", "frontmatter is not a mapping"
    return front, body, None


def _build_candidate(front: dict, body: str
                     ) -> tuple[Optional[CandidateEntry], Optional[str]]:
    """The store loader's own field mapping, tolerantly: returns the error
    instead of raising, and leaves status validation to the caller."""
    try:
        return _candidate_from_front(front, body), None
    except (KeyError, TypeError, AttributeError, ContractError) as exc:
        return None, f"invalid entry fields: {exc!r}"


def _raw_num(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mark_bad(value: Any) -> bool:
    """A mark counter is bad only when PRESENT and negative/non-numeric;
    an absent key is fine (the rebuild defaults it to 0)."""
    if value is None:
        return False
    raw = _raw_num(value)
    return raw is None or raw < 0


def _num(value: Any) -> float:
    """Coerce to a non-negative float; garbage -> 0.0."""
    raw = _raw_num(value)
    return max(0.0, raw) if raw is not None else 0.0


def _pick_mark(manifest_value: Any, front_value: Any) -> float:
    """A mark counter for the rebuilt manifest: the manifest's copy when it
    is a valid non-negative number, else the entry frontmatter's copy
    (clamped) — absent/broken manifests must not zero real evidence."""
    raw = _raw_num(manifest_value)
    if raw is not None and raw >= 0:
        return raw
    return _num(front_value)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _maybe_fixed(fix: bool, finding: Finding, note: str) -> Finding:
    if fix:
        finding.fixed = True
        finding.fix_note = note
    return finding
