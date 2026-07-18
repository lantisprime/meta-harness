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
    EntrySource,
    Probe,
    ProcedureStep,
)
from selflearn.store.packstore import (
    MANIFEST_SCHEMA_VERSION,
    PackStore,
    StoredEntry,
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
        json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        f = Finding("learner.corrupt", path.name,
                    f"learner state is not valid JSON: {exc}")
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
    pack = pack_dir.name
    good = _check_entry_files(pack_dir, fix, report)
    _check_manifest(pack_dir, good, fix, report)
    _check_vectors(pack_dir, good, fix, report)
    _check_probes(pack_dir, good, fix, report)


def _check_entry_files(pack_dir: Path, fix: bool,
                       report: DoctorReport
                       ) -> dict[str, tuple[CandidateEntry, str]]:
    """Scan entries/*.md with tolerant parsing. Returns the readable
    entries as {id: (candidate, status)} after any per-file repairs."""
    pack = pack_dir.name
    entries_dir = pack_dir / "entries"
    good: dict[str, tuple[CandidateEntry, str]] = {}
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

        status = str(front.get("status", "candidate"))
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
        if fix and rewrite:
            md.write_text(_entry_md(StoredEntry(
                cand=cand, status=status,
                helpful=_num(front.get("helpful")),
                harmful=_num(front.get("harmful")))))
        good[cand.id] = (cand, status)
    return good


def _check_manifest(pack_dir: Path,
                    good: dict[str, tuple[CandidateEntry, str]],
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
            meta = old_entries[eid]
            meta = meta if isinstance(meta, dict) else {}
            if meta.get("status") != good[eid][1]:
                report.findings.append(_maybe_fixed(fix, Finding(
                    "manifest.status-mismatch", pack,
                    f"{eid!r}: entry file says {good[eid][1]!r}, manifest "
                    f"says {meta.get('status')!r}"),
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

    if fix and needs_rewrite:
        _write_manifest(pack_dir, good, old)


def _write_manifest(pack_dir: Path,
                    good: dict[str, tuple[CandidateEntry, str]],
                    old: dict[str, Any]) -> None:
    old_entries = old.get("entries", {})
    old_entries = old_entries if isinstance(old_entries, dict) else {}
    coverage = old.get("coverage", {})
    coverage = dict(coverage) if isinstance(coverage, dict) else {}
    entries_meta: dict[str, Any] = {}
    for eid, (cand, status) in sorted(good.items()):
        meta = old_entries.get(eid, {})
        meta = meta if isinstance(meta, dict) else {}
        buckets = meta.get("marks_by_task", {})
        buckets = buckets if isinstance(buckets, dict) else {}
        entries_meta[eid] = {
            "status": status, "kind": cand.kind, "topic": cand.topic,
            "helpful": _num(meta.get("helpful")),
            "harmful": _num(meta.get("harmful")),
            "marks_by_task": {
                str(k): [_num(v[0]), _num(v[1])]
                for k, v in buckets.items()
                if isinstance(v, (list, tuple)) and len(v) == 2},
            "consecutive_harmful": max(0, _int(
                meta.get("consecutive_harmful"))),
            "marks_updated_at": str(meta.get("marks_updated_at") or ""),
            "embedder_id": str(meta.get("embedder_id") or ""),
        }
        if status == "published":
            coverage.setdefault(cand.topic, "covered")
    (pack_dir / "manifest.json").write_text(json.dumps({
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "pack": pack_dir.name,
        "coverage": {str(k): str(v) for k, v in coverage.items()},
        "entries": entries_meta,
    }, indent=1, sort_keys=True))


def _check_vectors(pack_dir: Path,
                   good: dict[str, tuple[CandidateEntry, str]],
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
    unknown = sorted(set(vecs) - set(good))
    if unknown:
        f = Finding("vectors.unknown-entry", pack,
                    f"vectors stored for unknown entries: {unknown}")
        if fix:
            for eid in unknown:
                vecs.pop(eid)
            path.write_text(json.dumps(vecs, sort_keys=True))
            f.fixed = True
            f.fix_note = "pruned the unknown ids"
        report.findings.append(f)


def _check_probes(pack_dir: Path,
                  good: dict[str, tuple[CandidateEntry, str]],
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
    """Same field mapping as the store loader, but returns the error
    instead of raising, and leaves status validation to the caller."""
    try:
        cand = CandidateEntry(
            id=front["id"], pack=front["pack"], kind=front["kind"],
            body=body.strip(), topic=front.get("topic", ""),
            claims=tuple(front.get("claims", [])),
            task_types=tuple(front.get("task_types", [])),
            extraction=front.get("extraction", "text"),
            quarantined=front.get("quarantined", False),
            quarantine_reason=front.get("quarantine_reason", ""),
            sources=tuple(EntrySource(
                url=s["url"], fetched_at=s.get("fetched_at", ""),
                sha256=s.get("sha256", ""), tier=s.get("tier", "unknown"),
                locator=s.get("locator", ""))
                for s in front.get("sources", [])),
            procedure=tuple(ProcedureStep(
                id=s["id"], objective=s["objective"],
                task_type=s.get("task_type", ""),
                tools=tuple(s.get("tools", [])),
                depends_on=tuple(s.get("depends_on", [])),
                check=tuple(sorted(s.get("check", {}).items())))
                for s in front.get("procedure", {}).get("steps", [])),
            skill_check=tuple(sorted(front.get("check", {}).items())),
        )
        return cand, None
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
