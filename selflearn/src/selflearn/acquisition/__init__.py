"""Acquisition module: plugin-based source acquisition behind one registry."""
from selflearn.acquisition.backends import (
    BraveBackend,
    DuckDuckGoBackend,
    SearxngBackend,
    WikipediaBackend,
)
from selflearn.acquisition.context import (
    AcquireContext,
    AcquisitionError,
    Fetcher,
    UrllibFetcher,
)
from selflearn.acquisition.plugins import (
    ArxivPlugin,
    LocalPlugin,
    PdfPlugin,
    SearchBackend,
    WebPlugin,
    YoutubePlugin,
    builtin_plugins,
    html_to_text,
    rank_passages,
)
from selflearn.acquisition.registry import (
    PluginRegistry,
    SourcePlugin,
    load_entry_point_plugins,
)
from selflearn.acquisition.reputability import (
    DEFAULT_POLICY,
    ReputabilityPolicy,
    registrable_domain,
)

__all__ = [
    "BraveBackend", "DuckDuckGoBackend", "SearxngBackend", "WikipediaBackend",
    "AcquireContext", "AcquisitionError", "Fetcher", "UrllibFetcher",
    "ArxivPlugin", "LocalPlugin", "PdfPlugin", "SearchBackend", "WebPlugin",
    "YoutubePlugin", "builtin_plugins", "html_to_text", "rank_passages",
    "PluginRegistry", "SourcePlugin", "load_entry_point_plugins",
    "DEFAULT_POLICY", "ReputabilityPolicy", "registrable_domain",
]
