"""Concrete SearchBackend implementations for the web plugin.

- ``SearxngBackend``: self-hosted SearXNG JSON API — no API key, the
  platform-agnostic default for standalone use.
- ``BraveBackend``: Brave Search API — needs a subscription token, matching
  the reviewed Brave preset meta-harness already ships for MCP.

Both take an injectable ``transport`` (url, headers) -> bytes so tests run
without network; the default is a stdlib GET with timeout. An MCP search
server is wired as a backend by the host adapter, not here.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Callable, Optional

from selflearn.acquisition.context import AcquisitionError, USER_AGENT

Transport = Callable[[str, dict], bytes]


def _default_transport(timeout_s: float) -> Transport:
    def get(url: str, headers: dict) -> bytes:
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read()
        except urllib.error.URLError as exc:
            raise AcquisitionError(f"search request failed: {exc}")
    return get


class SearxngBackend:
    def __init__(self, base_url: str, timeout_s: float = 15.0,
                 transport: Optional[Transport] = None):
        if not base_url:
            raise AcquisitionError("SearxngBackend needs its instance base_url")
        self.base_url = base_url.rstrip("/")
        self.transport = transport or _default_transport(timeout_s)

    def search(self, query: str, max_results: int) -> list[str]:
        url = (f"{self.base_url}/search?"
               + urllib.parse.urlencode({"q": query, "format": "json"}))
        try:
            payload = json.loads(self.transport(url, {}))
        except (json.JSONDecodeError, KeyError) as exc:
            raise AcquisitionError(f"SearXNG returned unparseable payload: {exc}")
        urls = [r["url"] for r in payload.get("results", []) if r.get("url")]
        return urls[:max_results]


class BraveBackend:
    API = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, timeout_s: float = 15.0,
                 transport: Optional[Transport] = None):
        if not api_key:
            raise AcquisitionError("BraveBackend needs an API key")
        self.api_key = api_key
        self.transport = transport or _default_transport(timeout_s)

    def search(self, query: str, max_results: int) -> list[str]:
        url = self.API + "?" + urllib.parse.urlencode(
            {"q": query, "count": max_results})
        headers = {"Accept": "application/json",
                   "X-Subscription-Token": self.api_key}
        try:
            payload = json.loads(self.transport(url, headers))
        except (json.JSONDecodeError, KeyError) as exc:
            raise AcquisitionError(f"Brave returned unparseable payload: {exc}")
        results = payload.get("web", {}).get("results", [])
        return [r["url"] for r in results if r.get("url")][:max_results]
