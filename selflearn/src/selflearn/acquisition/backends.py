"""Concrete SearchBackend implementations for the web plugin.

No-subscription options first:

- ``DuckDuckGoBackend``: keyless — parses the public HTML results page.
  Zero-config default. Caveat stated plainly: HTML scraping is brittle by
  nature and automated querying sits outside DDG's intended use; requests
  ride the context's rate limiting and this backend should stay low-volume.
- ``WikipediaBackend``: the official MediaWiki search API — free, stable
  JSON, ideal for concept/encyclopedic research topics.
- ``SearxngBackend``: self-hosted SearXNG JSON API — no key, full-web
  results, the recommended free option when you can run an instance.
- ``BraveBackend``: Brave Search API — optional, needs a subscription
  token.

All take an injectable ``transport`` (url, headers) -> bytes so tests run
without network; the default is a stdlib GET with timeout. An MCP search
server is wired as a backend by the host adapter, not here.
"""
from __future__ import annotations

import json
import re
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


class DuckDuckGoBackend:
    """Keyless full-web search via DDG's HTML results page (see module note)."""

    HTML_URL = "https://html.duckduckgo.com/html/"
    _RESULT = re.compile(r'class="result__a"[^>]*href="([^"]+)"')

    def __init__(self, timeout_s: float = 15.0,
                 transport: Optional[Transport] = None):
        self.transport = transport or _default_transport(timeout_s)

    def search(self, query: str, max_results: int) -> list[str]:
        url = self.HTML_URL + "?" + urllib.parse.urlencode({"q": query})
        html = self.transport(url, {}).decode(errors="replace")
        urls: list[str] = []
        for href in self._RESULT.findall(html):
            target = self._decode(href)
            if target and target not in urls:
                urls.append(target)
            if len(urls) >= max_results:
                break
        if not urls:
            raise AcquisitionError(
                "DuckDuckGo returned no parseable results — the HTML layout "
                "may have changed or the request was rate-limited; consider "
                "--searxng or a Brave key")
        return urls

    @staticmethod
    def _decode(href: str) -> str:
        # DDG links results through //duckduckgo.com/l/?uddg=<urlencoded>
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            vals = qs.get("uddg", [])
            return urllib.parse.unquote(vals[0]) if vals else ""
        return href if href.startswith("http") else ""


class WikipediaBackend:
    """Official MediaWiki search API — free, stable, encyclopedic sources."""

    def __init__(self, lang: str = "en", timeout_s: float = 15.0,
                 transport: Optional[Transport] = None):
        self.lang = lang
        self.transport = transport or _default_transport(timeout_s)

    def search(self, query: str, max_results: int) -> list[str]:
        api = (f"https://{self.lang}.wikipedia.org/w/api.php?"
               + urllib.parse.urlencode({"action": "query", "list": "search",
                                         "srsearch": query, "format": "json",
                                         "srlimit": max_results}))
        payload = _fetch_json(self.transport, api, {}, "Wikipedia")
        try:
            hits = payload["query"]["search"]
        except (KeyError, TypeError) as exc:
            raise AcquisitionError(f"Wikipedia API unexpected shape: {exc}")
        base = f"https://{self.lang}.wikipedia.org/wiki/"
        return [base + urllib.parse.quote(h["title"].replace(" ", "_"))
                for h in hits][:max_results]


def _fetch_json(transport: Transport, url: str, headers: dict, who: str) -> dict:
    """Shared GET+parse: unparseable payloads become one loud error class.
    (Replaces per-backend copies whose except-tuples had unreachable
    KeyError arms — review finding.)"""
    try:
        return json.loads(transport(url, headers))
    except json.JSONDecodeError as exc:
        raise AcquisitionError(f"{who} returned unparseable payload: {exc}")


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
        payload = _fetch_json(self.transport, url, {}, "SearXNG")
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
        payload = _fetch_json(self.transport, url, headers, "Brave")
        results = payload.get("web", {}).get("results", [])
        return [r["url"] for r in results if r.get("url")][:max_results]
