"""Tools workers can call: builtin (workspace-jailed files, web, calculator)
and MCP-server tools, behind one registry with small-subset selection."""
from metaharness.tools.builtin import DEFAULT_WORKSPACE, build_file_tools, default_registry
from metaharness.tools.mcp import load_mcp_tools, mcp_config_fingerprint
from metaharness.tools.registry import (
    DEFAULT_SUBSET_CAP,
    ToolError,
    ToolRegistry,
    ToolSpec,
    digest_text,
)

__all__ = [
    "ToolRegistry",
    "ToolSpec",
    "ToolError",
    "digest_text",
    "DEFAULT_SUBSET_CAP",
    "default_registry",
    "build_file_tools",
    "DEFAULT_WORKSPACE",
    "load_mcp_tools",
    "mcp_config_fingerprint",
]
