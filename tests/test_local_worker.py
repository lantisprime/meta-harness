"""OpenAI-compatible local worker tests.

Unit tests run against an httpx MockTransport that speaks the real chat-completions
wire shape (request body asserted, response parsed). The integration test talks to
an actual local endpoint (Ollama) and skips cleanly when none is running.
"""
from __future__ import annotations

import json

import httpx
import pytest

from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness import OpenAICompatWorker, parse_output, probe_endpoint

OLLAMA = "http://localhost:11434/v1"


def completion_response(content: str, prompt_tokens: int = 30, completion_tokens: int = 12):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def make_worker(handler, **kw) -> OpenAICompatWorker:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatWorker(
        "local-1", base_url="http://fake/v1", model="minicpm5-1b", client=client, **kw
    )


async def test_request_shape_and_text_output():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=completion_response("The answer is yes."))

    worker = make_worker(handler, thinking=False)
    task = Task(
        task_type=TaskType.CLASSIFY,
        objective="Is this review positive?",
        inputs={"text": "loved it", "_hidden": "not sent"},
        boundaries=["answer only from the given text"],
    )
    result = await worker.run(task)

    assert seen["url"] == "http://fake/v1/chat/completions"
    body = seen["body"]
    assert body["model"] == "minicpm5-1b"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["messages"][0]["role"] == "system"
    assert "answer only from the given text" in body["messages"][0]["content"]
    assert "loved it" in body["messages"][1]["content"]
    assert "_hidden" not in body["messages"][1]["content"]
    assert result.output == "The answer is yes."
    assert result.tokens_in == 30 and result.tokens_out == 12
    assert result.error is None


async def test_json_schema_output_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200, json=completion_response('```json\n{"label": "positive", "confidence": 0.93}\n```')
        )

    worker = make_worker(handler)
    task = Task(
        objective="classify",
        output_schema={"type": "object", "required": ["label"], "properties": {"label": {"type": "string"}}},
    )
    result = await worker.run(task)
    assert result.output == {"label": "positive", "confidence": 0.93}


async def test_server_error_becomes_result_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="model exploded")

    worker = make_worker(handler)
    result = await worker.run(Task(objective="x"))
    assert result.error is not None and "500" in result.error


def test_parse_output_variants():
    assert parse_output("plain text", expect_json=False) == "plain text"
    assert parse_output('{"a": 1}', expect_json=True) == {"a": 1}
    assert parse_output('```json\n{"a": 1}\n```', expect_json=True) == {"a": 1}
    assert parse_output('Sure! Here it is: {"a": 1} hope that helps', expect_json=True) == {"a": 1}
    assert parse_output("not json at all", expect_json=True) == "not json at all"


async def test_probe_endpoint_unreachable_returns_none():
    assert await probe_endpoint("http://localhost:59999/v1", timeout_s=0.3) is None


# -- live integration (skips when no local endpoint is running) --------------------


@pytest.mark.anyio
async def test_live_local_endpoint_roundtrip():
    models = await probe_endpoint(OLLAMA, timeout_s=1.5)
    if not models:
        pytest.skip("no local OpenAI-compatible endpoint at :11434")
    # thinking models spend tokens on reasoning before the answer — leave headroom
    worker = OpenAICompatWorker(
        "ollama-live", base_url=OLLAMA, model=models[0], tier=Tier.SMALL,
        temperature=0.0, max_tokens=2000,
    )
    task = Task(
        task_type=TaskType.CLASSIFY,
        objective="Reply with exactly one word, 'positive' or 'negative', for the sentiment of the input.",
        inputs={"text": "I absolutely loved this product, best purchase all year."},
    )
    result = await worker.run(task)
    assert result.error is None
    assert "positive" in result.raw_text.lower()
    assert result.tokens_out > 0


async def test_workspace_root_stamped_from_tool_registry(tmp_path):
    """v0.4 root binding: the runner that KNOWS where file side-effects land
    records it on the result — evidence/packaging must never infer from cwd."""
    from metaharness.tools import default_registry

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion_response("done"))

    registry = default_registry(tmp_path / "ws")
    worker = make_worker(handler, tool_registry=registry)
    result = await worker.run(Task(objective="do"))
    assert result.workspace_root == str(tmp_path / "ws")

    # no registry → no root claimed (never guess)
    result = await make_worker(handler).run(Task(objective="do"))
    assert result.workspace_root == ""


def test_strip_think_removes_reasoning_blocks():
    """Regression (2026-07-09): MiniMax-M3's <think> blocks made every
    correct classify answer fail verification — the whole tuning suite
    scored pass^3=0.00. The answer after the block is what gets verified."""
    from metaharness.harness.local import strip_think

    assert strip_think(
        '<think>The review mentions "fast checkout" - positive.</think>\n\npositive'
    ) == "positive"
    assert strip_think(
        "<think>\nreasoning…\n</think>\nfirst\n<think>more</think>\nsecond"
    ) == "first\nsecond"
    assert strip_think("no blocks here") == "no blocks here"
    # unclosed block (max_tokens mid-thought): half a thought is not an answer
    assert strip_think("real answer\n<think>I wonder if") == "real answer"
    assert strip_think("<think>only a thought, never closed") == ""
