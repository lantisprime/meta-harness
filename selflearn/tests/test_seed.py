"""Seed importers: real memory/knowledge_base files + yt-distill fixtures."""
import json
from pathlib import Path

import pytest

from selflearn.store import PackStore, StoreError, seed_knowledge_base, seed_ytdistill

REPO_KB = Path(__file__).resolve().parents[2] / "memory" / "knowledge_base"


def test_seed_real_knowledge_base(tmp_path):
    if not REPO_KB.is_dir():
        pytest.skip("meta-harness knowledge base not present")
    store = PackStore(tmp_path)
    ids = seed_knowledge_base(store, REPO_KB, pack="meta-research", publish=True)
    assert len(ids) >= 10
    published = store.published("meta-research")
    assert len(published) == len(ids)
    sample = published[0]
    assert sample.cand.sources[0].sha256
    assert store.coverage("meta-research")
    # Round-trips through boot loading.
    reloaded = PackStore(tmp_path)
    assert len(reloaded.published("meta-research")) == len(ids)


def test_seed_candidates_by_default(tmp_path):
    if not REPO_KB.is_dir():
        pytest.skip("meta-harness knowledge base not present")
    store = PackStore(tmp_path)
    ids = seed_knowledge_base(store, REPO_KB, pack="meta-research")
    assert store.published("meta-research") == []
    assert len(store.entries_for("meta-research", "candidate")) == len(ids)


def _write_lecture(dir: Path, with_record_type: bool) -> Path:
    dir.mkdir(parents=True)
    (dir / "analysis.json").write_text(json.dumps(
        {"source": {"url": "https://www.youtube.com/watch?v=abc123",
                    "title": "Agent Memory Masterclass", "channel": "GoodChannel"}}))
    records = []
    if with_record_type:
        records.append({"record_type": "summary", "id": "s", "start": 0, "end": 900,
                        "text": "Summary: memory is a harness primitive.",
                        "summary_points": ["memory is harness state"]})
        records.append({"record_type": "transcript_chunk", "id": "c1", "start": 1.9,
                        "end": 136.2, "text": "The harness stores what survives.",
                        "source_url": "https://www.youtube.com/watch?v=abc123"})
    else:  # old schema: no record_type at all (simulation finding 6)
        records.append({"id": "chunk-0001", "start": 1.9, "end": 136.2,
                        "text": "The harness stores what survives.",
                        "source_url": "https://www.youtube.com/watch?v=abc123"})
    (dir / "chunks.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    return dir


def test_seed_ytdistill_new_schema(tmp_path):
    lecture = _write_lecture(tmp_path / "agent-memory", with_record_type=True)
    store = PackStore(tmp_path / "packs")
    ids = seed_ytdistill(store, lecture, pack="agent-memory", publish=True)
    assert len(ids) == 2  # summary + chunk
    chunk = store.get(ids[1])
    assert chunk.cand.sources[0].locator == "t=2-136s"
    assert chunk.cand.sources[0].url.startswith("https://www.youtube.com")


def test_seed_ytdistill_old_schema_tolerated(tmp_path):
    lecture = _write_lecture(tmp_path / "old-lecture", with_record_type=False)
    store = PackStore(tmp_path / "packs")
    ids = seed_ytdistill(store, lecture, pack="agent-memory")
    assert len(ids) == 1
    assert store.entries_for("agent-memory", "candidate")


def test_seed_missing_dir_is_loud(tmp_path):
    store = PackStore(tmp_path)
    with pytest.raises(StoreError, match="does not exist"):
        seed_knowledge_base(store, tmp_path / "nope", pack="x")
    with pytest.raises(StoreError, match="chunks.jsonl"):
        seed_ytdistill(store, tmp_path / "nolecture", pack="x")
