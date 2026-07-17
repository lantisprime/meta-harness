"""AcquireContext: what plugins get — and ALL they get.

Plugins never carry their own network or filesystem policy. The context
hands them a fetcher (rate-limited, size-capped), a jailed workdir for
artifacts, and the reputability policy for tier stamping. Injectable clock
and sleep keep rate limiting deterministic under test.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

from selflearn.acquisition.reputability import DEFAULT_POLICY, ReputabilityPolicy


class AcquisitionError(RuntimeError):
    """Loud failure during gather: fetch failed, no plugin, empty source."""


@runtime_checkable
class Fetcher(Protocol):
    def fetch(self, url: str) -> bytes: ...


MAX_FETCH_BYTES = 8 * 1024 * 1024
USER_AGENT = "selflearn/0.1 (+knowledge acquisition; contact repo owner)"


class UrllibFetcher:
    """Default stdlib fetcher: timeout, size cap, no redirect surprises."""

    def __init__(self, timeout_s: float = 20.0, max_bytes: int = MAX_FETCH_BYTES):
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes

    def fetch(self, url: str) -> bytes:
        if not url.startswith(("http://", "https://")):
            raise AcquisitionError(f"fetcher only handles http(s), got {url!r}")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = resp.read(self.max_bytes + 1)
        except urllib.error.URLError as exc:
            raise AcquisitionError(f"fetch failed for {url!r}: {exc}")
        if len(data) > self.max_bytes:
            raise AcquisitionError(f"{url!r} exceeds fetch size cap "
                                   f"({self.max_bytes} bytes)")
        return data


@dataclass
class AcquireContext:
    workdir: Path
    fetcher: Optional[Fetcher] = None
    policy: ReputabilityPolicy = DEFAULT_POLICY
    min_fetch_interval_s: float = 1.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _last_fetch: float = field(default=-1e9, init=False)

    def __post_init__(self) -> None:
        self.workdir = Path(self.workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

    def fetch(self, url: str) -> bytes:
        """Rate-limited fetch through the context — the only network door."""
        if self.fetcher is None:
            raise AcquisitionError(
                f"no fetcher configured; cannot fetch {url!r} (plugins never "
                "get their own network access)")
        now = self.clock()
        wait = self.min_fetch_interval_s - (now - self._last_fetch)
        if wait > 0:
            self.sleep(wait)
        self._last_fetch = self.clock()
        return self.fetcher.fetch(url)

    def artifact_path(self, name: str) -> Path:
        """A path inside the jail; escaping it is a loud error."""
        path = (self.workdir / name).resolve()
        if not str(path).startswith(str(self.workdir.resolve())):
            raise AcquisitionError(f"artifact path {name!r} escapes the workdir jail")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
