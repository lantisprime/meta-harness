"""One versioned parser for yt-distill ``chunks.jsonl`` artifacts.

The external tool's schema is interpreted in exactly one place (review
finding: the local plugin and the seed importer had drifted — different
record_type filtering, different empty-text handling, different locators).
Tolerance rules, applied identically everywhere:

- absent ``record_type`` ⇒ transcript chunk (pre-record_type folders,
  simulation finding 6);
- ``record_type: summary`` ⇒ summary record, kept and flagged;
- unknown future record_types ⇒ skipped and counted, never imported as
  knowledge;
- empty-text records ⇒ skipped and counted, never raised on, never kept.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class YtRecord:
    text: str
    is_summary: bool
    start: Optional[float]
    end: Optional[float]
    source_url: str

    @property
    def locator(self) -> str:
        if self.start is None:
            return ""
        hi = self.end if self.end is not None else self.start
        return f"t={self.start:.0f}-{hi:.0f}s"


@dataclass(frozen=True)
class YtParse:
    records: tuple[YtRecord, ...]
    skipped_empty: int
    skipped_unknown: int

    @property
    def chunks(self) -> tuple[YtRecord, ...]:
        return tuple(r for r in self.records if not r.is_summary)

    @property
    def span_locator(self) -> str:
        starts = [r.start for r in self.chunks if r.start is not None]
        if not starts:
            return ""
        ends = [r.end for r in self.chunks if r.end is not None]
        return f"t={min(starts):.0f}-{(max(ends) if ends else max(starts)):.0f}s"


def parse_chunks(text: str, default_url: str = "") -> YtParse:
    records: list[YtRecord] = []
    skipped_empty = 0
    skipped_unknown = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        rtype = rec.get("record_type", "transcript_chunk")
        if rtype not in ("transcript_chunk", "summary"):
            skipped_unknown += 1
            continue
        body = str(rec.get("text", "")).strip()
        if not body:
            skipped_empty += 1
            continue
        records.append(YtRecord(
            text=body, is_summary=(rtype == "summary"),
            start=float(rec["start"]) if rec.get("start") is not None else None,
            end=float(rec["end"]) if rec.get("end") is not None else None,
            source_url=str(rec.get("source_url", default_url))))
    return YtParse(records=tuple(records), skipped_empty=skipped_empty,
                   skipped_unknown=skipped_unknown)
