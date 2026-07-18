"""Seed importers: bulk-seeding acquisition mode (human-initiated).

Two importers prove the pack format end-to-end (plan milestone M1):

- ``seed_knowledge_base``: a directory of research-cache Markdown files with
  YAML frontmatter (the meta-harness ``memory/knowledge_base/`` convention).
- ``seed_ytdistill``: a yt-distill ``distilled/<lecture>/`` folder —
  ``analysis.json`` metadata plus ``chunks.jsonl`` transcript chunks.
  Schema-tolerant per simulation finding 6: records without ``record_type``
  are transcript chunks; ``record_type: summary`` records are the lecture
  summary.

Seeded entries are **candidates** by default — they have not passed any
gate. ``publish=True`` marks them published with an explicit pre-gate basis
recorded in provenance, which is honest for human-initiated bulk seeding
(acquisition mode 2) but should be re-verified once M4/M5 land the gates.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from selflearn.contracts import CandidateEntry, EntrySource, PublishDecision
from selflearn.store.packstore import PackStore, StoreError

SEED_BASIS = ("seed import: human-initiated bulk seeding (acquisition mode 2); "
              "pre-gate — re-verify when verification lands",)
SEED_IDENTITY_BASIS = "none (seed import; no probe validation occurred)"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _maybe_publish(store: PackStore, entry: CandidateEntry, publish: bool) -> None:
    store.add_candidate(entry)
    if publish:
        store.publish(entry.id, PublishDecision(
            entry_id=entry.id, publish=True, basis=SEED_BASIS,
            identity_basis=SEED_IDENTITY_BASIS))


def seed_knowledge_base(store: PackStore, kb_dir: Path, pack: str,
                        tier: str = "primary", publish: bool = False,
                        fetched_at: str = "") -> list[str]:
    """Import ``*.md`` research-cache files (frontmatter: url/fetched/summary)."""
    kb_dir = Path(kb_dir)
    if not kb_dir.is_dir():
        raise StoreError(f"knowledge base dir {kb_dir} does not exist")
    imported: list[str] = []
    with store.deferred_persist():      # bulk import: one flush at the end
        for md in sorted(kb_dir.glob("*.md")):
            text = md.read_text()
            front: dict = {}
            body = text
            if text.startswith("---\n"):
                try:
                    _, header, body = text.split("---\n", 2)
                except ValueError as exc:
                    raise StoreError(f"corrupt frontmatter in {md}: {exc}")
                try:
                    front = yaml.safe_load(header) or {}
                except yaml.YAMLError:
                    # Human-authored caches aren't always strict YAML (e.g. an
                    # unquoted url containing ': '). Fall back to line-based
                    # key: value extraction — this is a tolerant bulk importer,
                    # not the store's own strict format.
                    front = {}
                    for line in header.splitlines():
                        m = re.match(r"^(\w[\w-]*):\s*(.+)$", line)
                        if m:
                            front.setdefault(m.group(1), m.group(2).strip())
            body = body.strip()
            if not body:
                raise StoreError(f"empty knowledge-base file {md}")
            topic = _slug(md.stem)
            entry = CandidateEntry(
                id=f"kn-{pack}-{topic}"[:80], pack=pack, kind="knowledge",
                body=body, topic=topic,
                claims=(str(front.get("summary", body.splitlines()[0]))[:300],),
                sources=(EntrySource(
                    url=str(front.get("url", f"file://{md}")),
                    fetched_at=str(front.get("fetched", fetched_at)),
                    sha256=_sha(text), tier=tier),))
            _maybe_publish(store, entry, publish)
            imported.append(entry.id)
    if not imported:
        raise StoreError(f"no .md files found in {kb_dir}")
    return imported


def seed_ytdistill(store: PackStore, lecture_dir: Path, pack: str,
                   tier: str = "primary", publish: bool = False,
                   max_chunks: int = 40) -> list[str]:
    """Import one yt-distill lecture folder as chunk entries (+ summary)."""
    lecture_dir = Path(lecture_dir)
    chunks_path = lecture_dir / "chunks.jsonl"
    if not chunks_path.exists():
        raise StoreError(f"{lecture_dir} has no chunks.jsonl")
    meta: dict = {}
    analysis = lecture_dir / "analysis.json"
    if analysis.exists():
        try:
            meta = json.loads(analysis.read_text())
        except json.JSONDecodeError as exc:
            raise StoreError(f"corrupt analysis.json in {lecture_dir}: {exc}")
    from selflearn.acquisition.ytdistill import parse_chunks

    topic = _slug(lecture_dir.name)
    default_url = (meta.get("source", {}).get("url")
                   or f"file://{chunks_path}")
    parsed = parse_chunks(chunks_path.read_text(), default_url=default_url)
    imported: list[str] = []
    n = 0
    with store.deferred_persist():      # bulk import: one flush at the end
        for rec in parsed.records:
            kind_note = "summary" if rec.is_summary else "chunk"
            n += 1
            entry = CandidateEntry(
                id=f"kn-{pack}-{topic}-{kind_note}-{n:03d}"[:80], pack=pack,
                kind="knowledge", body=rec.text[:2000], topic=topic,
                claims=(rec.text.split(".")[0][:300],),
                sources=(EntrySource(url=rec.source_url, fetched_at="",
                                     sha256=_sha(rec.text), tier=tier,
                                     locator=rec.locator),))
            _maybe_publish(store, entry, publish)
            imported.append(entry.id)
            if not rec.is_summary and n >= max_chunks:
                break
    if not imported:
        raise StoreError(f"no records imported from {chunks_path} "
                         f"({parsed.skipped_empty} empty and "
                         f"{parsed.skipped_unknown} unknown records skipped)")
    return imported
