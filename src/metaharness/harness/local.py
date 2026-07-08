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
from metaharness.harness.runner import BaseRunner
from metaharness.identity.keys import KeyPair


async def probe_endpoint(base_url: str, timeout_s: float = 3.0) -> Optional[list[str]]:
    """Return the model ids served at an OpenAI-compatible base_url, or None if
    unreachable. Use this before wiring a worker — never point at a guess."""
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/models")
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def _build_messages(task: Task, system_prompt: str = "") -> list[dict[str, str]]:
    """Render the Task's explicit delegation contract into chat messages."""
    system_parts = [system_prompt or "You are a worker agent executing one well-scoped task."]
    if task.boundaries:
        system_parts.append("Boundaries:\n" + "\n".join(f"- {b}" for b in task.boundaries))
    if task.output_schema:
        system_parts.append(
            "Respond with a single JSON object matching this schema exactly, "
            "no prose around it:\n" + json.dumps(task.output_schema)
        )
    user_parts = [task.objective]
    if task.inputs:
        visible = {k: v for k, v in task.inputs.items() if not k.startswith("_")}
        if visible:
            user_parts.append("Inputs:\n" + json.dumps(visible, ensure_ascii=False, default=str))
    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


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
        client: Optional[httpx.AsyncClient] = None,  # injectable for tests
    ) -> None:
        super().__init__(worker_id=worker_id, tier=tier, model=model, keypair=keypair)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.extra_body = extra_body or {}
        self.cost_per_1k_tokens = cost_per_1k_tokens
        self.timeout_s = timeout_s
        self._client = client

    def _body(self, task: Task) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _build_messages(task, self.system_prompt),
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        if task.output_schema:
            body["response_format"] = {"type": "json_object"}
        if self.thinking is not None:
            body["chat_template_kwargs"] = {"enable_thinking": self.thinking}
        body.update(self.extra_body)
        return body

    async def _execute(self, task: Task) -> WorkerResult:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        client = self._client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            body = self._body(task)
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
            data = resp.json()
        finally:
            if self._client is None:
                await client.aclose()

        message = data["choices"][0]["message"]
        text = message.get("content") or ""
        if not text and message.get("reasoning_content"):
            # thinking model hit max_tokens mid-reasoning; surface what exists
            text = message["reasoning_content"]
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        return WorkerResult(
            task_id=task.id,
            worker_id=self.worker_id,
            tier=self.tier,
            model=self.model,
            output=parse_output(text, expect_json=bool(task.output_schema)),
            raw_text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=(tokens_in + tokens_out) / 1000 * self.cost_per_1k_tokens,
        )
