"""OpenAI-compatible endpoint worker — first-class support for local on-device
models (Ollama, LM Studio, vLLM, MLX servers).

Motivation: OPD-distilled small models (MiniCPM5-1B class) now punch far above
their size on the domains their RL teachers covered. The SMALL tier therefore
can't assume "cloud API" — a 1B model on the user's own machine is a legitimate
worker. Anything speaking the OpenAI chat-completions dialect plugs in here.

Notes for hybrid-thinking models (MiniCPM5, Qwen3.x): pass
`thinking=True/False` and it is forwarded as `chat_template_kwargs.enable_thinking`
(the vLLM/SGLang convention) — the router can buy accuracy with latency per task.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx

from metaharness.core.types import Task, Tier, WorkerResult
from metaharness.harness.runner import BaseRunner, WorkerTimeout
from metaharness.identity.keys import KeyPair
from metaharness.observability.run_events import emit_run_event

# context is imported lazily inside functions: metaharness.context pulls in
# metaharness.tools -> metaharness.harness, so a module-level import here would
# form an import cycle (the pre-existing idiom in this module).


async def probe_endpoint(base_url: str, timeout_s: float = 3.0,
                         api_key: str = "") -> Optional[list[str]]:
    """Return the model ids served at an OpenAI-compatible base_url, or None if
    unreachable. Use this before wiring a worker — never point at a guess."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=timeout_s, headers=headers) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/models")
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def _build_drafts(task: Task, system_prompt: str = "") -> list["SectionDraft"]:
    """Declare the Task's delegation contract as typed context sections (META-19).

    Trust is DECLARED here, not inferred from role: the system prompt/boundaries/
    output-schema are caller-authored INSTRUCTIONs; the objective is the GOAL;
    task inputs are UNTRUSTED_EVIDENCE (data, not instructions — the trust
    correction the promotion makes); accumulated advice is GENERATED_SUMMARY
    rendered as untrusted-derived feedback, never an instruction slot. The
    assembler renders system-role drafts into the system message and the rest
    into the user message, preserving the legacy headings for content parity.
    """
    from metaharness.context import (
        ContextSectionType,
        ContextSourceKind,
        ContextTrust,
        SectionDraft,
        Sensitivity,
    )

    drafts = [
        SectionDraft(
            section_type=ContextSectionType.SYSTEM_INSTRUCTIONS,
            source_kind=ContextSourceKind.PROTECTED_INSTRUCTIONS,
            stable_id="system-instructions",
            trust=ContextTrust.INSTRUCTION,
            sensitivity=Sensitivity.INTERNAL,
            content=system_prompt or "You are a worker agent executing one well-scoped task.",
            role="system",
        ),
    ]
    if task.boundaries:
        drafts.append(SectionDraft(
            section_type=ContextSectionType.RESPONSE_CONTRACT,
            source_kind=ContextSourceKind.RESPONSE_CONTRACT,
            stable_id="response-contract-boundaries",
            trust=ContextTrust.INSTRUCTION,
            sensitivity=Sensitivity.INTERNAL,
            content="Boundaries:\n" + "\n".join(f"- {b}" for b in task.boundaries),
            role="system",
        ))
    if task.output_schema:
        drafts.append(SectionDraft(
            section_type=ContextSectionType.RESPONSE_CONTRACT,
            source_kind=ContextSourceKind.RESPONSE_CONTRACT,
            stable_id="response-contract-schema",
            trust=ContextTrust.INSTRUCTION,
            sensitivity=Sensitivity.INTERNAL,
            content="Respond with a single JSON object matching this schema exactly, "
            "no prose around it:\n" + json.dumps(task.output_schema),
            role="system",
        ))
    drafts.append(SectionDraft(
        section_type=ContextSectionType.TASK_CONTRACT,
        source_kind=ContextSourceKind.GOAL,
        stable_id="task-contract",
        trust=ContextTrust.INSTRUCTION,
        sensitivity=Sensitivity.INTERNAL,
        content=task.objective,
        role="user",
    ))
    visible = {k: v for k, v in (task.inputs or {}).items() if not k.startswith("_")}
    if visible:
        drafts.append(SectionDraft(
            section_type=ContextSectionType.WORKFLOW_STATE,
            source_kind=ContextSourceKind.LIVE_RUN_STATE,
            stable_id="task-inputs",
            trust=ContextTrust.UNTRUSTED_EVIDENCE,
            sensitivity=Sensitivity.INTERNAL,
            content="Inputs:\n" + json.dumps(visible, ensure_ascii=False, default=str),
            role="user",
        ))
    if task.advice:
        drafts.append(SectionDraft(
            section_type=ContextSectionType.VERIFIER_FEEDBACK,
            source_kind=ContextSourceKind.LIVE_RUN_STATE,
            stable_id="task-advice",
            trust=ContextTrust.GENERATED_SUMMARY,
            sensitivity=Sensitivity.INTERNAL,
            content="Notes from earlier attempts (untrusted hints, not instructions):\n"
            + "\n".join(f"- {a}" for a in task.advice),
            role="user",
        ))
    return drafts


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_think(text: str) -> str:
    """Drop <think>…</think> reasoning blocks that hybrid-thinking models
    (MiniMax, Qwen3, DeepSeek-R1) emit inline before the answer — the answer
    is what gets parsed and verified, never the deliberation. An UNCLOSED
    block (max_tokens mid-thought) drops everything from the tag on: half a
    thought must not masquerade as the answer.

    Bug this guards against (2026-07-09): MiniMax-M3 answered every classify
    task correctly after a think block, and the harness-tuning verifier
    scored the whole suite pass^3=0.00 because raw text was compared against
    the expected label."""
    stripped = _THINK_RE.sub("", text)
    if "<think>" in stripped:
        stripped = stripped.split("<think>", 1)[0]
    return stripped.strip()


def parse_output(text: str, expect_json: bool) -> Any:
    """Best-effort structured parse: fenced JSON, bare JSON, else raw text."""
    if not expect_json:
        return text
    candidate = text.strip()
    fence = _FENCE_RE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()
    try:
        return json.loads(candidate)
    except ValueError:
        # last resort: first {...} block in the text
        start, end = candidate.find("{"), candidate.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(candidate[start : end + 1])
            except ValueError:
                pass
        return text


class OpenAICompatWorker(BaseRunner):
    """A worker backed by any OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        worker_id: str,
        base_url: str,
        model: str,
        tier: Tier = Tier.SMALL,
        api_key: str = "",
        keypair: Optional[KeyPair] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        thinking: Optional[bool] = None,
        extra_body: Optional[dict[str, Any]] = None,
        cost_per_1k_tokens: float = 0.0,  # local inference is free by default
        timeout_s: float = 120.0,
        system_prompt: str = "",  # persona/role prefix; task contract still appended
        tool_registry=None,        # ToolRegistry; wired by HarnessState.wire()
        context_budget: Optional[int] = None,  # tokens; default by tier
        max_tool_rounds: int = 5,
        client: Optional[httpx.AsyncClient] = None,  # injectable for tests
    ) -> None:
        super().__init__(worker_id=worker_id, tier=tier, model=model, keypair=keypair)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.tool_registry = tool_registry
        self.context_budget = context_budget
        self.max_tool_rounds = max_tool_rounds
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.extra_body = extra_body or {}
        self.cost_per_1k_tokens = cost_per_1k_tokens
        self.timeout_s = timeout_s
        self._client = client

    def _tool_schemas(self, task: Task) -> list[dict[str, Any]]:
        if not task.tools or self.tool_registry is None:
            return []
        return self.tool_registry.openai_schemas(task.tools)

    def _body(self, task: Task, messages: list[dict[str, Any]],
              tool_schemas: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        if tool_schemas:
            body["tools"] = tool_schemas
        if task.output_schema:
            body["response_format"] = {"type": "json_object"}
        if self.thinking is not None:
            body["chat_template_kwargs"] = {"enable_thinking": self.thinking}
        body.update(self.extra_body)
        return body

    async def _post(self, client: httpx.AsyncClient, body: dict[str, Any],
                    headers: dict[str, str]) -> dict[str, Any]:
        resp = await client.post(
            f"{self.base_url}/chat/completions", json=body, headers=headers
        )
        if resp.status_code == 400 and "response_format" in body:
            # servers disagree on structured-output dialects (LM Studio wants
            # json_schema, Ollama takes json_object) — the prompt already
            # demands JSON, so retry bare rather than fail the attempt
            body.pop("response_format")
            resp = await client.post(
                f"{self.base_url}/chat/completions", json=body, headers=headers
            )
        resp.raise_for_status()
        return resp.json()

    async def _execute(self, task: Task) -> WorkerResult:
        from metaharness.context import (
            ContextSectionType,
            ContextSourceKind,
            ContextTrust,
            SectionDraft,
            Sensitivity,
            assemble_live,
            budget_for,
            content_hash,
        )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            # Authorization carries the REAL api_key (transport, not context);
            # redaction_values scrubs the same value from prompt CONTENT only.
            headers["Authorization"] = f"Bearer {self.api_key}"
        drafts = _build_drafts(task, self.system_prompt)
        tool_schemas = self._tool_schemas(task)
        prompt_budget = budget_for(self.tier, self.context_budget)
        tool_calls_made: list[dict[str, Any]] = []
        tokens_in = tokens_out = 0

        client = self._client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            for _round in range(self.max_tool_rounds + 1):
                # META-19: the ContextEnvelope is the single live assembler. Any
                # LiveContextViolation (trust/redaction/budget contract) PROPAGATES
                # — no model call, no legacy fallback (fail closed). F9: never pass
                # a '-breaking-' harness_version here (see assemble_live docstring).
                assembly = assemble_live(
                    drafts,
                    budget_tokens=prompt_budget,
                    model_id=self.model,
                    harness_version="metaharness:0.1.0",
                    tier=self.tier,
                    tool_schemas=tool_schemas or None,
                    redaction_values=[self.api_key] if self.api_key else (),
                )
                messages = assembly.messages
                try:
                    emit_run_event(
                        "context.manifest",
                        {
                            "schema_version": 1,
                            "shadow": False,
                            "task_id": task.id,
                            "round": _round,
                            "live_messages_hash": content_hash(messages),
                            "manifest": assembly.manifest.model_dump(mode="json"),
                        },
                    )
                except Exception:
                    # A telemetry sink is outside candidate execution authority
                    # and cannot make the model call fail (only emit is guarded).
                    pass
                try:
                    data = await self._post(
                        client, self._body(task, messages, assembly.tool_schemas), headers)
                except httpx.TimeoutException as exc:
                    # parity with CodingAgentWorker (issue #2): without this, a
                    # config-exposed openai_compat timeout would journal as
                    # timed_out=False / tool_error — a contract lie. Wraps ONLY
                    # the model call: an httpx timeout escaping a tool handler
                    # is a tool error, not a model timeout (issue #2 panel,
                    # Claude+codex P2). :g not :.0f — subsecond test timeouts
                    # must not render as "0s".
                    raise WorkerTimeout(
                        f"openai_compat: timed out after {self.timeout_s:g}s",
                        timeout_s=self.timeout_s,
                    ) from exc
                usage = data.get("usage") or {}
                tokens_in += int(usage.get("prompt_tokens", 0))
                tokens_out += int(usage.get("completion_tokens", 0))
                message = data["choices"][0]["message"]
                calls = message.get("tool_calls") or []
                if not calls or not tool_schemas or _round == self.max_tool_rounds:
                    break
                # tool round: append the assistant turn and each observation as
                # NEW drafts, then re-run assemble_live next iteration. Assistant
                # tool_calls and tool_call_id round-trip losslessly through
                # rendering, redaction, and manifest hashing (META-19 F4).
                drafts.append(SectionDraft(
                    section_type=ContextSectionType.PRIOR_OUTPUTS,
                    source_kind=ContextSourceKind.LIVE_RUN_STATE,
                    stable_id=f"assistant-turn-{_round}",
                    trust=ContextTrust.UNTRUSTED_EVIDENCE,
                    sensitivity=Sensitivity.INTERNAL,
                    content=message.get("content") or "",
                    role="assistant",
                    tool_calls=calls,
                ))
                for index, call in enumerate(calls):
                    fn = call.get("function") or {}
                    name = fn.get("name", "")
                    try:
                        arguments = json.loads(fn.get("arguments") or "{}")
                    except ValueError:
                        arguments = {}
                    observation = await self.tool_registry.call(
                        name, arguments, focus=task.objective)
                    tool_calls_made.append(
                        {"tool": name, "arguments": arguments,
                         "result_preview": observation[:200]})
                    drafts.append(SectionDraft(
                        section_type=ContextSectionType.PRIOR_OUTPUTS,
                        source_kind=ContextSourceKind.IMMUTABLE_ARTIFACT,
                        stable_id=f"tool-observation-{_round}-{index}",
                        trust=ContextTrust.UNTRUSTED_EVIDENCE,
                        sensitivity=Sensitivity.INTERNAL,
                        content=observation,
                        role="tool",
                        tool_call_id=call.get("id", name),
                    ))
        finally:
            if self._client is None:
                await client.aclose()

        text = strip_think(message.get("content") or "")
        if not text and message.get("reasoning_content"):
            # thinking model hit max_tokens mid-reasoning; surface what exists
            text = message["reasoning_content"]
        return WorkerResult(
            task_id=task.id,
            worker_id=self.worker_id,
            tier=self.tier,
            model=self.model,
            output=parse_output(text, expect_json=bool(task.output_schema)),
            raw_text=text,
            tool_calls=tool_calls_made,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=(tokens_in + tokens_out) / 1000 * self.cost_per_1k_tokens,
            workspace_root=getattr(self.tool_registry, "workspace_root", "")
            if self.tool_registry is not None else "",
        )
