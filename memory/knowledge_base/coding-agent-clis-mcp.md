---
url: https://developers.openai.com/codex/noninteractive, https://opencode.ai/docs/cli/, https://github.com/badlogic/pi-mono, https://github.com/modelcontextprotocol/python-sdk
fetched: 2026-07-08
summary: Headless invocation cheat-sheet for coding-agent CLIs (Pi, Codex, OpenCode, Claude Code) + Python MCP client integration.
---

# Coding-Agent CLIs (headless) & MCP Python client

## Pi (badlogic/pi-mono, v0.80.3 verified locally)
```bash
echo "$TASK" | pi --mode json --no-session --no-approve --no-extensions \
  --no-skills --no-prompt-templates --no-themes \
  --model provider/id --tools read,bash,edit,write -p
```
- `-p` one-shot; prompt via **stdin** (safest for untrusted text). `--mode json` → JSONL events (session header, tool calls, usage/cost, stopReason).
- cwd = spawn cwd. Keys: standard env vars or `~/.pi/agent/auth.json`. `--append-system-prompt <text-or-file>`.
- Authoritative recipe: `pi-extensions/agents/lib/child-args.ts` (buildChildPiArgs) + `jsonl-monitor.ts` (event reducer) + `child-runner.ts` (spawn/exit handling).

## Codex CLI
```bash
codex exec --json --output-last-message /tmp/last.md \
  --cd /path/to/repo --sandbox workspace-write -m <model> "task"
```
- `codex exec` headless; `--json` JSONL (`thread.started`, `turn.completed`, `item.*`); `--output-schema <path>` enforces JSON-schema output; `--ephemeral` no session persistence; `--skip-git-repo-check`.
- Sandbox: `--sandbox read-only|workspace-write|danger-full-access`. Auth: `codex login` OAuth or `CODEX_API_KEY`/`OPENAI_API_KEY`. Config `$CODEX_HOME/config.toml`.

## OpenCode
```bash
opencode run --format json -m provider/model --auto "task"
opencode serve --port 4096   # OpenAPI server mode; run --attach http://...
```
- Config `~/.config/opencode/opencode.json` + project `opencode.json`; providers via `provider.<id>.options.apiKey` `{env:VAR}`; MCP under `"mcp"`. Auth store `~/.local/share/opencode/auth.json`.

## Claude Code
```bash
claude -p "task" --output-format json        # single JSON result (is_error, total_cost_usd, session_id)
claude -p "task" --output-format stream-json --verbose
```
- `--permission-mode`, `--allowedTools "Bash(npm test:*),Read"`, `--add-dir`; cwd constrains. Keys: `ANTHROPIC_API_KEY` or `claude login`.

## MCP Python client (official `mcp` pkg; v1.28.1 stable, v2.0.0b1 beta — pin v1)
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

params = StdioServerParameters(command="npx", args=["-y", "@some/mcp-server"], env={})
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()                 # .tools[].name/.description/.inputSchema
        result = await session.call_tool("name", {"arg": 1})  # .content, .structuredContent, .isError
# HTTP: streamablehttp_client("http://host/mcp") -> (read, write, _)
```
mcpServers convention: stdio = `{command, args, env}`; remote = `{type: "http", url, headers}`.

## sdlc-plugin (local repo)
Claude Code plugin, 8 gated phases (plan/analyze/design/build/test/deploy/support/docs), hook-enforced gates (plan-gate.sh, phase-gate.sh), artifacts under `.claude/sdlc/`. Reference: `sdlc-plugin/docs/SDLC.md`.

## Failure watch-outs
- Small models: malformed tool calls, confabulation instead of tool use; verify edit well-formedness.
- Test-gaming by frontier models → lock tests, demand evidence not assertion.
- All CLIs: non-zero exit on error; JSONL streams need truncation caps when captured.
