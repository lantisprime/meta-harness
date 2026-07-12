"""MCP integration: pull tools from configured MCP servers into the registry.

Servers come from HarnessConfig.mcp_servers (the standard mcpServers shape:
stdio = command/args/env, remote = url). Connections are per-call — simple and
robust for v1; a failed server surfaces loudly at load time and its tools are
simply absent, never silently stubbed.

Requires the official `mcp` package (pip install 'metaharness[mcp]').
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from metaharness.config import HarnessConfig, MCPServerConfig
from metaharness.tools.registry import ToolError, ToolRegistry, ToolSpec

_CALL_TIMEOUT_S = 60.0


def mcp_config_fingerprint(server: MCPServerConfig) -> str:
    """A non-reversible identity for the exact config whose tools were loaded."""
    payload = json.dumps(
        server.model_dump(mode="json"), sort_keys=True,
        ensure_ascii=False, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _require_mcp():
    try:
        import mcp  # noqa: F401
        return mcp
    except ImportError as exc:
        raise RuntimeError(
            "MCP support needs the 'mcp' package: pip install 'metaharness[mcp]'"
        ) from exc


def _session_cm(server: MCPServerConfig):
    """An async context manager yielding an initialized ClientSession."""
    from contextlib import asynccontextmanager

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    @asynccontextmanager
    async def _cm():
        if server.transport == "http":
            from mcp.client.streamable_http import streamablehttp_client
            token = server.plain_oauth_token()
            headers = {"Authorization": f"Bearer {token}"} if token else None
            if server.oauth_project:
                headers = dict(headers or {})
                headers["x-goog-user-project"] = server.oauth_project
            async with streamablehttp_client(server.url, headers=headers) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        else:
            params = StdioServerParameters(
                command=server.command, args=server.args, env=server.plain_env() or None)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    return _cm()


async def _call_mcp_tool(server: MCPServerConfig, tool: str,
                         arguments: dict[str, Any]) -> str:
    import asyncio

    _require_mcp()
    try:
        async with asyncio.timeout(_CALL_TIMEOUT_S):
            async with _session_cm(server) as session:
                result = await session.call_tool(tool, arguments)
    except TimeoutError:
        raise ToolError(f"MCP {server.name}.{tool}: timed out")
    except Exception as exc:  # connection/protocol failures are tool errors
        raise ToolError(f"MCP {server.name}.{tool}: {type(exc).__name__}: connection failed")
    parts = []
    for item in result.content or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    text = "\n".join(parts)
    if getattr(result, "isError", False):
        raise ToolError(f"MCP {server.name}.{tool}: {text or 'tool reported an error'}")
    return text


async def load_mcp_tools(registry: ToolRegistry, config: HarnessConfig) -> dict[str, Any]:
    """Connect each enabled MCP server, mirror its tools into the registry as
    '<server>.<tool>'. Returns a per-server report; failures are loud entries
    in the report (and the server's tools stay absent), never silent."""
    report: dict[str, Any] = {}
    for name, server in config.mcp_servers.items():
        source = f"mcp:{name}"
        # Stale capabilities are more dangerous than temporary unavailability:
        # clear them before every disabled/load/failure/zero-tools branch.
        registry.unregister_source(source)
        fingerprint = mcp_config_fingerprint(server)
        if not server.enabled:
            report[name] = {
                "ok": False, "status": "disabled", "detail": "disabled",
                "tools": 0, "fingerprint": fingerprint,
            }
            continue
        try:
            _require_mcp()
            async with _session_cm(server) as session:
                listing = await session.list_tools()
        except Exception as exc:
            report[name] = {
                "ok": False, "status": "load_failed",
                "detail": f"{type(exc).__name__}: connection failed",
                "tools": 0, "fingerprint": fingerprint,
            }
            continue
        count = 0
        for tool in listing.tools:
            def _handler(_server=server, _tool=tool.name, **arguments: Any):
                return _call_mcp_tool(_server, _tool, arguments)

            registry.register(ToolSpec(
                name=f"{name}.{tool.name}",
                description=tool.description or f"{tool.name} (MCP: {name})",
                input_schema=tool.inputSchema or {"type": "object", "properties": {}},
                handler=_handler,
                source=source,
                annotations=(tool.annotations.model_dump(
                    by_alias=True, exclude_none=True
                ) if tool.annotations else {}),
            ))
            count += 1
        if count == 0:
            report[name] = {
                "ok": False, "status": "zero_tools", "detail": "zero tools",
                "tools": 0, "fingerprint": fingerprint,
            }
        else:
            report[name] = {
                "ok": True, "status": "loaded", "tools": count,
                "fingerprint": fingerprint,
            }
    return report
