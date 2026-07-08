"""Tool registry, builtin tools, context optimization, tool-call loop,
planner tool detection, and MCP integration (live stdio subprocess)."""
from __future__ import annotations

import json

import httpx
import pytest

from metaharness.config import HarnessConfig, MCPServerConfig
from metaharness.context import budget_for, fit_messages, messages_tokens
from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness import OpenAICompatWorker
from metaharness.tools import (
    ToolError,
    ToolRegistry,
    ToolSpec,
    default_registry,
    digest_text,
    load_mcp_tools,
)


@pytest.fixture()
def registry(tmp_path):
    return default_registry(workspace=tmp_path)


# ---------------------------------------------------------------- registry

def test_registry_registers_and_rejects_duplicates(registry):
    names = registry.names()
    assert {"read_file", "write_file", "edit_file", "grep", "list_files",
            "web_fetch", "calculator"} <= set(names)
    with pytest.raises(ValueError):
        registry.register(ToolSpec("grep", "dup", {}, lambda: ""))


def test_select_for_returns_small_relevant_subset(registry):
    picked = registry.select_for("Read the config file and edit the port value")
    assert "read_file" in picked and "edit_file" in picked
    assert "calculator" not in picked
    assert len(picked) <= 7
    # pure text work gets NO tools — tools would only confuse the worker
    assert registry.select_for("Summarize the sentiment of this review") == []


def test_openai_schemas_deterministic_and_dialect_safe(registry):
    registry.register(ToolSpec("srv.remote_tool", "an mcp tool",
                               {"type": "object", "properties": {}},
                               lambda: "x", source="mcp:srv"))
    schemas = registry.openai_schemas(["srv.remote_tool", "grep", "grep", "ghost"])
    names = [s["function"]["name"] for s in schemas]
    assert names == ["grep", "srv__remote_tool"]  # sorted, deduped, dots escaped
    assert registry.resolve_call_name("srv__remote_tool") == "srv.remote_tool"


async def test_call_prunes_and_reports_errors_as_data(registry):
    result = await registry.call("calculator", {"expression": "6*7"})
    assert result == "42"
    result = await registry.call("calculator", {"expression": "import os"})
    assert result.startswith("tool error:")
    result = await registry.call("read_file", {"wrong_arg": "x"})
    assert result.startswith("tool error: bad arguments")
    with pytest.raises(ToolError):
        await registry.call("no_such_tool", {})


def test_digest_text_is_loud_and_keeps_focus_lines():
    text = "\n".join(f"line {i} {'PORT=8321' if i == 500 else 'filler'}" for i in range(1000))
    digest = digest_text(text, 2000, {"port"})
    assert len(digest) < len(text)
    assert "pruned" in digest
    assert "PORT=8321" in digest


# ---------------------------------------------------------------- builtins

async def test_file_tools_roundtrip_and_jail(registry, tmp_path):
    await registry.call("write_file", {"path": "src/app.py", "content": "x = 1\ny = 2\n"})
    assert (tmp_path / "src/app.py").read_text() == "x = 1\ny = 2\n"
    assert "x = 1" in await registry.call("read_file", {"path": "src/app.py"})
    await registry.call("edit_file", {"path": "src/app.py", "old": "x = 1", "new": "x = 9"})
    assert "x = 9" in (tmp_path / "src/app.py").read_text()
    listing = await registry.call("list_files", {"pattern": "**/*.py"})
    assert "src/app.py" in listing
    matches = await registry.call("grep", {"pattern": r"y = \d"})
    assert "src/app.py:2" in matches
    # jail: escapes come back as tool errors, never as file access
    for path in ("../outside.txt", "/etc/passwd"):
        result = await registry.call("read_file", {"path": path})
        assert "escapes the workspace" in result or "no such file" in result


# ----------------------------------------------------------- context fitting

def test_fit_messages_prunes_middle_keeps_edges():
    messages = [
        {"role": "system", "content": "contract " * 50},
        {"role": "user", "content": "big blob " * 4000},
        {"role": "tool", "content": "observation " * 4000},
        {"role": "user", "content": "final question"},
    ]
    fitted = fit_messages(messages, budget_tokens=3000)
    assert messages_tokens(fitted) <= 3300  # ~budget (estimation slack)
    assert fitted[0]["content"] == messages[0]["content"]      # system untouched
    assert fitted[-1]["content"] == "final question"           # last untouched
    assert "pruned" in fitted[2]["content"]                    # tool obs digested first
    assert fit_messages(messages, 10**6) == messages           # under budget: no-op


def test_budget_tiers_scale_down_for_weak_models():
    assert budget_for(Tier.SMALL) < budget_for(Tier.MID) < budget_for(Tier.FRONTIER)
    assert budget_for(Tier.SMALL, override=100_000) == 75_000


# ------------------------------------------------------------- tool loop

class _FakeClient:
    """OpenAI-compat server double: first reply asks for a tool, second answers."""

    def __init__(self):
        self.requests: list[dict] = []

    async def post(self, url, json=None, headers=None):
        self.requests.append(json)
        if len(self.requests) == 1:
            payload = {"choices": [{"message": {
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "calculator",
                    "arguments": "{\"expression\": \"21*2\"}"}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        else:
            tool_msg = next(m for m in json["messages"] if m["role"] == "tool")
            payload = {"choices": [{"message": {"content": f"The answer is {tool_msg['content']}"}}],
                       "usage": {"prompt_tokens": 20, "completion_tokens": 5}}
        return httpx.Response(200, json=payload,
                              request=httpx.Request("POST", url))


async def test_openai_worker_tool_loop(registry):
    client = _FakeClient()
    worker = OpenAICompatWorker("w", base_url="http://fake/v1", model="m",
                                tool_registry=registry, client=client)
    task = Task(id="t", task_type=TaskType.ARITHMETIC,
                objective="compute 21*2", tools=["calculator"])
    result = await worker.run(task)
    assert result.error is None
    assert result.output == "The answer is 42"
    assert result.tool_calls == [{"tool": "calculator",
                                  "arguments": {"expression": "21*2"},
                                  "result_preview": "42"}]
    assert "tools" in client.requests[0]
    assert client.requests[0]["tools"][0]["function"]["name"] == "calculator"
    assert result.tokens_in == 30 and result.tokens_out == 10  # summed rounds


async def test_worker_without_task_tools_sends_no_schemas(registry):
    class _Plain:
        def __init__(self):
            self.requests = []

        async def post(self, url, json=None, headers=None):
            self.requests.append(json)
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]},
                                  request=httpx.Request("POST", url))

    client = _Plain()
    worker = OpenAICompatWorker("w", base_url="http://fake/v1", model="m",
                                tool_registry=registry, client=client)
    result = await worker.run(Task(id="t", objective="say hi"))
    assert result.error is None and "tools" not in client.requests[0]


# ------------------------------------------------------- planner detection

async def test_planner_assigns_tools_to_steps(registry, tmp_path):
    from metaharness.harness import ScriptedWorker
    from metaharness.identity import KeyPair
    from metaharness.workflows.planner import plan_workflow

    plan = {"name": "job", "steps": [
        {"id": "fetch", "task_type": "general",
         "objective": "Fetch the release notes web page from the url",
         "tools": ["web_fetch", "not_a_real_tool"]},
        {"id": "summarize", "task_type": "summarize",
         "objective": "Summarize the tone of the notes", "depends_on": ["fetch"]},
    ]}
    seen_tasks = []

    def handler(t):
        seen_tasks.append(t)
        return plan

    planner = ScriptedWorker("p", handler, tier=Tier.FRONTIER,
                             keypair=KeyPair.generate())
    spec, source, _reason = await plan_workflow("do the job", planner, tools=registry)
    assert source == "planner"
    by_id = {s.id: s for s in spec.steps}
    assert "web_fetch" in by_id["fetch"].tools
    assert "not_a_real_tool" not in by_id["fetch"].tools
    assert by_id["summarize"].tools == []
    # the planner saw the catalog in its prompt
    assert "web_fetch:" in seen_tasks[0].objective


# ----------------------------------------------------------------- MCP e2e

MCP_SERVER = """
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("testsrv")

@mcp.tool()
def shout(text: str) -> str:
    "Uppercase the text."
    return text.upper()

mcp.run()
"""


async def test_mcp_stdio_server_tools_load_and_call(tmp_path):
    pytest.importorskip("mcp")
    import sys

    server_py = tmp_path / "server.py"
    server_py.write_text(MCP_SERVER)
    config = HarnessConfig()
    config.mcp_servers["testsrv"] = MCPServerConfig(
        name="testsrv", transport="stdio",
        command=sys.executable, args=[str(server_py)])
    config.mcp_servers["broken"] = MCPServerConfig(
        name="broken", transport="stdio", command="definitely-not-a-binary")

    registry = ToolRegistry()
    report = await load_mcp_tools(registry, config)
    assert report["testsrv"]["ok"] and report["testsrv"]["tools"] == 1
    assert report["broken"]["ok"] is False  # loud failure, not a crash

    assert registry.get("testsrv.shout") is not None
    result = await registry.call("testsrv.shout", {"text": "quiet"})
    assert "QUIET" in result
