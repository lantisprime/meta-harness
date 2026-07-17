"""SourcePlugin protocol + registry.

Resolution is deterministic: explicit registration order, first
``can_handle`` match wins, unclaimed refs fail the gather loudly.
Third-party plugins load only from an explicit allowlist of entry points
(group ``selflearn.sources``) — a plugin is code, and installing one is a
trust decision; provenance records which plugin produced every document so
one plugin's output is revocable as a unit.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Iterable, Optional, Protocol, runtime_checkable

from selflearn.acquisition.context import AcquireContext, AcquisitionError
from selflearn.contracts import SourceDocument, SourceRef
from selflearn.ports import ProvenancePort

ENTRY_POINT_GROUP = "selflearn.sources"


@runtime_checkable
class SourcePlugin(Protocol):
    id: str
    version: str
    requires: tuple[str, ...]     # optional extras / external CLIs, checked up front

    def can_handle(self, ref: SourceRef) -> bool: ...

    def acquire(self, ref: SourceRef, ctx: AcquireContext) -> list[SourceDocument]: ...


class PluginRegistry:
    def __init__(self, plugins: Iterable[SourcePlugin],
                 provenance: Optional[ProvenancePort] = None):
        self.plugins: list[SourcePlugin] = list(plugins)
        self.provenance = provenance
        ids = [p.id for p in self.plugins]
        if len(ids) != len(set(ids)):
            raise AcquisitionError(f"duplicate plugin ids in registry: {ids}")

    def resolve(self, ref: SourceRef) -> SourcePlugin:
        for plugin in self.plugins:
            if plugin.can_handle(ref):
                return plugin
        raise AcquisitionError(
            f"no plugin claims ref {ref.uri!r} "
            f"(registered: {[p.id for p in self.plugins]})")

    def gather(self, refs: Iterable[SourceRef], ctx: AcquireContext) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        for ref in refs:
            plugin = self.resolve(ref)
            produced = plugin.acquire(ref, ctx)
            if not produced:
                raise AcquisitionError(
                    f"plugin {plugin.id!r} produced no documents for "
                    f"{ref.uri!r} — empty gathers are loud, never thin")
            for doc in produced:
                if self.provenance is not None:
                    self.provenance.append({
                        "event": "source.acquired", "uri": ref.uri,
                        "url": doc.provenance.url, "plugin": plugin.id,
                        "plugin_version": plugin.version,
                        "sha256": doc.provenance.sha256, "tier": doc.tier,
                        "locator": doc.provenance.locator})
            docs.extend(produced)
        return docs


def load_entry_point_plugins(allowlist: Iterable[str]) -> list[SourcePlugin]:
    """Load third-party plugins named in the allowlist; anything else in the
    entry-point group is ignored. A listed-but-missing name is loud."""
    allowed = set(allowlist)
    if not allowed:
        return []
    found: dict[str, SourcePlugin] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name in allowed:
            plugin = ep.load()()
            if not isinstance(plugin, SourcePlugin):
                raise AcquisitionError(
                    f"entry point {ep.name!r} does not implement SourcePlugin")
            found[ep.name] = plugin
    missing = allowed - set(found)
    if missing:
        raise AcquisitionError(
            f"allowlisted source plugins not installed: {sorted(missing)}")
    return [found[name] for name in sorted(found)]
