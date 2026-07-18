"""Pack store: entries, manifests, probes, coverage map, provenance.

On-disk layout (plain files — readable, git-diffable, host-independent):

    <root>/<pack>/
      entries/<entry-id>.md     # YAML frontmatter + body (source of truth
                                #   for content and lifecycle state)
      evals/probes.jsonl        # one probe per line, retirement flag included
      manifest.json             # retrieval metadata: vectors keyed by
                                #   embedder id, marks, coverage map
      provenance.jsonl          # local pack event log (in addition to the
                                #   host ProvenancePort, when bound)

Everything is write-through: every mutation persists before it returns.
Boot loading reconstructs the full state from disk; a manifest that
references a missing entry file is a loud error, never a silent skip.

State machine: candidate -> published -> deprecated -> (restore) published.
Publishing demands a positive PublishDecision; nothing else flips status.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from selflearn.evidence import MARK_HALF_LIFE_DAYS, decay_factor, laplace_score

from selflearn.contracts import (
    CandidateEntry,
    ContractError,
    EntrySource,
    Probe,
    ProcedureStep,
    PublishDecision,
)
from selflearn.ports import ProvenancePort

MANIFEST_SCHEMA_VERSION = 1


class StoreError(RuntimeError):
    """Loud failure: invalid transition, corrupt file, or missing artifact."""


@dataclass
class StoredEntry:
    """A candidate/published/deprecated entry plus its mutable ledgers."""

    cand: CandidateEntry
    status: str = "candidate"
    helpful: float = 0.0
    harmful: float = 0.0
    # per-task-type evidence: task_type -> [helpful, harmful]. Lets the
    # loop learn "helps for code_edit but misleads for review" instead of
    # one coarse counter (review finding 4).
    marks_by_task: dict[str, list[float]] = field(default_factory=dict)
    # decay-free EVENT counter: consecutive harmful marks with no helpful
    # mark in between. Drives auto-deprecation so the guarantee holds at
    # any cadence — decayed float counters plateau below any threshold for
    # slow cadences (review finding: never-deprecate above ~52-day spacing).
    consecutive_harmful: int = 0
    marks_updated_at: str = ""       # ISO timestamp of the last mark event
    embedder_id: str = ""
    vector: tuple[float, ...] = ()

    @property
    def score(self) -> float:
        """Laplace-smoothed lifetime prior from the learning marks."""
        return laplace_score(self.helpful, self.harmful)

    def score_for(self, task_type: str = "", smoothing: float = 2.0,
                  now: Optional["datetime"] = None,
                  half_life_days: float = MARK_HALF_LIFE_DAYS) -> float:
        """Task-type-aware evidence prior. With ``now``, counters are
        time-decayed first (review fix: retrieval ranked on undecayed
        lifetime counters while staleness used decayed ones — one clock for
        every consumer now). Bucket evidence shrinks toward the global
        score with ``smoothing`` pseudo-counts; no bucket -> global."""
        factor = (decay_factor(self.marks_updated_at, now, half_life_days)
                  if now is not None else 1.0)
        global_score = laplace_score(self.helpful * factor,
                                     self.harmful * factor)
        if not task_type or task_type not in self.marks_by_task:
            return global_score
        bucket_helpful, bucket_harmful = self.marks_by_task[task_type]
        bucket_helpful *= factor
        bucket_harmful *= factor
        return ((bucket_helpful + smoothing * global_score)
                / (bucket_helpful + bucket_harmful + smoothing))


@dataclass
class StoredProbe:
    probe: Probe
    retired: bool = False


class PackStore:
    def __init__(self, root: Path, provenance: Optional[ProvenancePort] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.host_provenance = provenance
        self._entries: dict[str, StoredEntry] = {}
        self._probes: dict[str, list[StoredProbe]] = {}
        self._coverage: dict[str, dict[str, str]] = {}
        self._deferred: Optional[set[str]] = None   # packs pending flush
        self._load()

    @contextmanager
    def deferred_persist(self):
        """Batch mode for bulk operations (review fix: per-entry manifest
        rewrites made seeding O(N²)). Entry .md files still write
        immediately; manifests/probes/vectors flush once at exit."""
        if self._deferred is not None:
            yield          # already batching (nested) — no-op
            return
        self._deferred = set()
        try:
            yield
        finally:
            packs, self._deferred = self._deferred, None
            for pack in sorted(packs):
                self._persist_manifest(pack)
                self._persist_probes(pack)
                self._persist_vectors(pack)

    # ------------------------------------------------------------------
    # Boot loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        for pack_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            manifest_path = pack_dir / "manifest.json"
            if not manifest_path.exists():
                raise StoreError(f"pack {pack_dir.name!r} has no manifest.json")
            try:
                manifest = json.loads(manifest_path.read_text())
            except json.JSONDecodeError as exc:
                raise StoreError(f"corrupt manifest for pack {pack_dir.name!r}: {exc}")
            self._coverage[pack_dir.name] = dict(manifest.get("coverage", {}))
            vectors_path = pack_dir / "vectors.json"
            sidecar: dict[str, dict] = {}
            if vectors_path.exists():
                try:
                    sidecar = json.loads(vectors_path.read_text())
                except json.JSONDecodeError as exc:
                    raise StoreError(
                        f"corrupt vectors.json for pack {pack_dir.name!r}: {exc}")
            for entry_id, meta in manifest.get("entries", {}).items():
                md_path = pack_dir / "entries" / f"{entry_id}.md"
                if not md_path.exists():
                    raise StoreError(
                        f"manifest for pack {pack_dir.name!r} lists {entry_id!r} "
                        f"but {md_path} is missing")
                cand, status = _read_entry_md(md_path)
                if status != meta.get("status"):
                    raise StoreError(
                        f"status mismatch for {entry_id!r}: entry file says "
                        f"{status!r}, manifest says {meta.get('status')!r}")
                self._entries[entry_id] = StoredEntry(
                    cand=cand, status=status,
                    helpful=float(meta.get("helpful", 0.0)),
                    harmful=float(meta.get("harmful", 0.0)),
                    marks_by_task={
                        str(k): [float(v[0]), float(v[1])]
                        for k, v in meta.get("marks_by_task", {}).items()},
                    consecutive_harmful=int(meta.get("consecutive_harmful", 0)),
                    marks_updated_at=meta.get("marks_updated_at", ""),
                    embedder_id=meta.get("embedder_id", ""),
                    # sidecar first; "vector" in the manifest is the legacy
                    # pre-sidecar layout, still readable
                    vector=tuple(sidecar.get(entry_id, {}).get(
                        "vector", meta.get("vector", []))))
            probes_path = pack_dir / "evals" / "probes.jsonl"
            if probes_path.exists():
                for line in probes_path.read_text().splitlines():
                    rec = json.loads(line)
                    retired = rec.pop("retired", False)
                    probe = Probe(**rec)
                    self._probes.setdefault(probe.entry_id, []).append(
                        StoredProbe(probe=probe, retired=retired))

    # ------------------------------------------------------------------
    # Mutations (all write-through)
    # ------------------------------------------------------------------

    def add_candidate(self, cand: CandidateEntry) -> StoredEntry:
        if cand.id in self._entries:
            raise StoreError(f"entry {cand.id!r} already exists")
        stored = StoredEntry(cand=cand, status="candidate")
        self._entries[cand.id] = stored
        self._persist_entry(stored)
        self._event({"event": "candidate.added", "entry": cand.id,
                     "pack": cand.pack, "quarantined": cand.quarantined})
        return stored

    def publish(self, entry_id: str, decision: PublishDecision,
                probes: Iterable[Probe] = (),
                vector: tuple[float, ...] = (), embedder_id: str = "") -> StoredEntry:
        stored = self._get(entry_id)
        if stored.status != "candidate":
            raise StoreError(f"cannot publish {entry_id!r} from status "
                             f"{stored.status!r}")
        if not decision.publish or decision.entry_id != entry_id:
            raise StoreError(f"publish of {entry_id!r} requires a positive "
                             "PublishDecision for that entry")
        if stored.cand.quarantined:
            raise StoreError(f"{entry_id!r} is quarantined "
                             f"({stored.cand.quarantine_reason}); publishing "
                             "requires human review, not a gate decision")
        stored.status = "published"
        stored.vector = tuple(vector)
        stored.embedder_id = embedder_id if vector else ""
        probe_list = [StoredProbe(probe=p) for p in probes]
        for sp in probe_list:
            if sp.probe.entry_id != entry_id:
                raise StoreError(f"probe {sp.probe.id!r} belongs to "
                                 f"{sp.probe.entry_id!r}, not {entry_id!r}")
            if not sp.probe.validated:
                raise StoreError(f"probe {sp.probe.id!r} is not validated; "
                                 "unvalidated probes cannot enter the suite")
        self._probes[entry_id] = probe_list
        cov = self._coverage.setdefault(stored.cand.pack, {})
        cov[stored.cand.topic] = "covered"
        self._persist_entry(stored)
        self._persist_probes(stored.cand.pack)
        if stored.vector and self._deferred is None:
            self._persist_vectors(stored.cand.pack)
        self._event({"event": "entry.published", "entry": entry_id,
                     "pack": stored.cand.pack, "basis": list(decision.basis),
                     "identity_basis": decision.identity_basis,
                     "strict_mode": decision.strict_mode})
        return stored

    def deprecate(self, entry_id: str, reason: str) -> None:
        stored = self._get(entry_id)
        if stored.status != "published":
            raise StoreError(f"cannot deprecate {entry_id!r} from status "
                             f"{stored.status!r}")
        stored.status = "deprecated"
        for sp in self._probes.get(entry_id, []):
            sp.retired = True
        self._persist_entry(stored)
        self._persist_probes(stored.cand.pack)
        self._event({"event": "entry.deprecated", "entry": entry_id,
                     "reason": reason})

    def restore(self, entry_id: str, reason: str) -> None:
        """Deprecation is reversible; restoration un-retires the probes."""
        stored = self._get(entry_id)
        if stored.status != "deprecated":
            raise StoreError(f"cannot restore {entry_id!r} from status "
                             f"{stored.status!r}")
        stored.status = "published"
        for sp in self._probes.get(entry_id, []):
            sp.retired = False
        self._persist_entry(stored)
        self._persist_probes(stored.cand.pack)
        self._event({"event": "entry.restored", "entry": entry_id,
                     "reason": reason})

    def mark(self, entry_id: str, helpful: float = 0.0, harmful: float = 0.0,
             decay: float = 1.0, now_iso: str = "",
             task_type: str = "") -> StoredEntry:
        """Apply marks. ``decay`` multiplies ALL existing counters first —
        global and per-task buckets, one clock (recency decay: old evidence
        fades so recent evidence can win); ``now_iso`` stamps the mark event
        for future decay computation; ``task_type`` additionally credits
        that task bucket so retrieval can rank per task type."""
        if not 0.0 <= decay <= 1.0:
            raise StoreError(f"decay factor must be in [0, 1], got {decay}")
        stored = self._get(entry_id)
        if decay != 1.0:
            stored.helpful *= decay
            stored.harmful *= decay
            for bucket in stored.marks_by_task.values():
                bucket[0] *= decay
                bucket[1] *= decay
        stored.helpful += helpful
        stored.harmful += harmful
        if harmful > 0:
            stored.consecutive_harmful += 1
        elif helpful > 0:
            stored.consecutive_harmful = 0
        if task_type:
            bucket = stored.marks_by_task.setdefault(task_type, [0.0, 0.0])
            bucket[0] += helpful
            bucket[1] += harmful
        if now_iso:
            stored.marks_updated_at = now_iso
        self._persist_entry(stored)
        return stored

    def set_vector(self, entry_id: str, vector: tuple[float, ...],
                   embedder_id: str) -> None:
        if not embedder_id:
            raise StoreError("set_vector requires the producing embedder_id")
        stored = self._get(entry_id)
        stored.vector = tuple(vector)
        stored.embedder_id = embedder_id
        self._persist_entry(stored)
        if self._deferred is None:
            self._persist_vectors(stored.cand.pack)

    def release_quarantine(self, entry_id: str, reason: str,
                           released_by: str) -> StoredEntry:
        """The journaled human transition out of quarantine (review fix: the
        only prior way out was hand-editing frontmatter, bypassing
        provenance). The entry returns to ordinary candidate state and must
        still pass every gate to publish."""
        stored = self._get(entry_id)
        if stored.status != "candidate" or not stored.cand.quarantined:
            raise StoreError(f"{entry_id!r} is not a quarantined candidate")
        if not reason or not released_by:
            raise StoreError("release_quarantine requires a reason and the "
                             "releasing human's identity")
        stored.cand = replace(stored.cand, quarantined=False,
                              quarantine_reason="")
        self._persist_entry(stored)
        self._event({"event": "quarantine.released", "entry": entry_id,
                     "reason": reason, "released_by": released_by})
        return stored

    def claim_topics(self, pack: str, topics: Iterable[str]) -> None:
        cov = self._coverage.setdefault(pack, {})
        for topic in topics:
            cov.setdefault(topic, "claimed")
        if (self.root / pack).exists() or cov:
            self._persist_manifest(pack)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, entry_id: str) -> StoredEntry:
        return self._get(entry_id)

    def packs(self) -> list[str]:
        return sorted({e.cand.pack for e in self._entries.values()}
                      | set(self._coverage))

    def entries_for(self, pack: str, status: Optional[str] = None) -> list[StoredEntry]:
        out = [e for e in self._entries.values() if e.cand.pack == pack]
        if status is not None:
            out = [e for e in out if e.status == status]
        return sorted(out, key=lambda e: e.cand.id)

    def published(self, pack: str) -> list[StoredEntry]:
        return self.entries_for(pack, "published")

    def probes_for(self, entry_id: str, include_retired: bool = False) -> list[Probe]:
        return [sp.probe for sp in self._probes.get(entry_id, [])
                if include_retired or not sp.retired]

    def suite_size(self, pack: str) -> int:
        return sum(len(self.probes_for(eid))
                   for eid, e in ((e.cand.id, e) for e in self._entries.values())
                   if e.cand.pack == pack)

    def coverage(self, pack: str) -> dict[str, str]:
        return dict(self._coverage.get(pack, {}))

    def reindex_needed(self, pack: str, embedder_id: str) -> list[str]:
        return [e.cand.id for e in self.published(pack)
                if e.embedder_id != embedder_id]

    # ------------------------------------------------------------------
    # Persistence internals
    # ------------------------------------------------------------------

    def _get(self, entry_id: str) -> StoredEntry:
        if entry_id not in self._entries:
            raise StoreError(f"unknown entry {entry_id!r}")
        return self._entries[entry_id]

    def _pack_dir(self, pack: str) -> Path:
        d = self.root / pack
        (d / "entries").mkdir(parents=True, exist_ok=True)
        (d / "evals").mkdir(parents=True, exist_ok=True)
        return d

    def _persist_entry(self, stored: StoredEntry) -> None:
        pack_dir = self._pack_dir(stored.cand.pack)
        md_path = pack_dir / "entries" / f"{stored.cand.id}.md"
        md_path.write_text(_entry_md(stored))
        if self._deferred is not None:
            self._deferred.add(stored.cand.pack)
        else:
            self._persist_manifest(stored.cand.pack)

    def _persist_manifest(self, pack: str) -> None:
        # vectors live in a sidecar (review fix: inlining them made every
        # two-float mark update rewrite megabytes of JSON at scale)
        pack_dir = self._pack_dir(pack)
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "pack": pack,
            "coverage": self._coverage.get(pack, {}),
            "entries": {
                e.cand.id: {
                    "status": e.status, "kind": e.cand.kind,
                    "topic": e.cand.topic,
                    "helpful": e.helpful, "harmful": e.harmful,
                    "marks_by_task": {k: list(v)
                                      for k, v in e.marks_by_task.items()},
                    "consecutive_harmful": e.consecutive_harmful,
                    "marks_updated_at": e.marks_updated_at,
                    "embedder_id": e.embedder_id,
                }
                for e in self._entries.values() if e.cand.pack == pack
            },
        }
        (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=1,
                                                           sort_keys=True))

    def _persist_vectors(self, pack: str) -> None:
        pack_dir = self._pack_dir(pack)
        vectors = {e.cand.id: {"embedder_id": e.embedder_id,
                               "vector": list(e.vector)}
                   for e in self._entries.values()
                   if e.cand.pack == pack and e.vector}
        (pack_dir / "vectors.json").write_text(json.dumps(vectors,
                                                          sort_keys=True))

    def _persist_probes(self, pack: str) -> None:
        if self._deferred is not None:
            self._deferred.add(pack)
            return
        pack_dir = self._pack_dir(pack)
        lines = []
        for e in self.entries_for(pack):
            for sp in self._probes.get(e.cand.id, []):
                rec = {"id": sp.probe.id, "entry_id": sp.probe.entry_id,
                       "kind": sp.probe.kind, "question": sp.probe.question,
                       "expected": sp.probe.expected,
                       "check_kind": sp.probe.check_kind,
                       "validated": sp.probe.validated,
                       "validated_by": sp.probe.validated_by,
                       "retired": sp.retired}
                lines.append(json.dumps(rec, sort_keys=True))
        (pack_dir / "evals" / "probes.jsonl").write_text("\n".join(lines) +
                                                         ("\n" if lines else ""))

    def _event(self, event: dict[str, Any]) -> None:
        pack = event.get("pack")
        if pack is None and "entry" in event:
            pack = self._entries[event["entry"]].cand.pack
        if pack:
            local = JsonlAppend(self._pack_dir(pack) / "provenance.jsonl")
            local.append(event)
        if self.host_provenance is not None:
            self.host_provenance.append(event)


class JsonlAppend:
    def __init__(self, path: Path):
        self.path = path

    def append(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Entry <-> Markdown (YAML frontmatter) round-trip
# ---------------------------------------------------------------------------

def _entry_md(stored: StoredEntry) -> str:
    c = stored.cand
    front: dict[str, Any] = {
        "id": c.id, "pack": c.pack, "kind": c.kind, "status": stored.status,
        "topic": c.topic, "extraction": c.extraction,
        "task_types": list(c.task_types),
        "claims": list(c.claims),
        "sources": [{"url": s.url, "fetched_at": s.fetched_at,
                     "sha256": s.sha256, "tier": s.tier,
                     **({"locator": s.locator} if s.locator else {})}
                    for s in c.sources],
        "helpful": stored.helpful, "harmful": stored.harmful,
    }
    if c.quarantined:
        front["quarantined"] = True
        front["quarantine_reason"] = c.quarantine_reason
    if c.procedure:
        front["procedure"] = {
            "steps": [{"id": s.id, "objective": s.objective,
                       "task_type": s.task_type, "tools": list(s.tools),
                       "depends_on": list(s.depends_on),
                       "check": s.check_dict()}
                      for s in c.procedure]}
    if c.skill_check:
        front["check"] = dict(c.skill_check)
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{header}\n---\n{c.body.rstrip()}\n"


def _read_entry_md(path: Path) -> tuple[CandidateEntry, str]:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise StoreError(f"{path} has no YAML frontmatter")
    try:
        _, header, body = text.split("---\n", 2)
        front = yaml.safe_load(header)
    except (ValueError, yaml.YAMLError) as exc:
        raise StoreError(f"corrupt entry file {path}: {exc}")
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
                locator=s.get("locator", "")) for s in front.get("sources", [])),
            procedure=tuple(ProcedureStep(
                id=s["id"], objective=s["objective"],
                task_type=s.get("task_type", ""),
                tools=tuple(s.get("tools", [])),
                depends_on=tuple(s.get("depends_on", [])),
                check=tuple(sorted(s.get("check", {}).items())))
                for s in front.get("procedure", {}).get("steps", [])),
            skill_check=tuple(sorted(front.get("check", {}).items())),
        )
    except (KeyError, ContractError) as exc:
        raise StoreError(f"invalid entry file {path}: {exc}")
    status = front.get("status", "candidate")
    if status not in ("candidate", "published", "deprecated"):
        raise StoreError(f"invalid status {status!r} in {path}")
    return cand, status
