"""Console entry point.

    metaharness serve           WebUI over mock workers (offline demo)
    metaharness serve --local   discover local OpenAI-compatible endpoints
                                (Ollama :11434, LM Studio :1234) and wire the
                                discovered models across tiers by size
"""
from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

JOURNAL_DIR = Path.home() / ".metaharness" / "journals"

DEFAULT_ENDPOINTS = [
    "http://localhost:11434/v1",   # ollama
    "http://localhost:1234/v1",    # lm studio
]


def _size_guess(model_id: str) -> float:
    """Largest standalone '<N>b' figure (1–999) in the model id — a3b/a4b
    active-param suffixes lose to the total size by taking the max. Bounded so
    hex hashes and version strings can't masquerade as parameter counts."""
    sizes = [
        float(m)
        for m in re.findall(r"(?<![a-z0-9.])(\d{1,3}(?:\.\d)?)b(?![a-z0-9])", model_id.lower())
    ]
    return max(sizes) if sizes else 0.0


def _looks_usable(model_id: str) -> bool:
    """Skip hash-named blobs and embedding models — neither can chat."""
    name = model_id.lower()
    if re.fullmatch(r"[0-9a-f]{16,}", name):
        return False
    return "embed" not in name


async def _discover(endpoints: list[str]) -> list[tuple[str, str, float]]:
    from metaharness.harness import probe_endpoint

    found: list[tuple[str, str, float]] = []
    for base_url in endpoints:
        models = await probe_endpoint(base_url)
        for model in models or []:
            if _looks_usable(model):
                found.append((base_url, model, _size_guess(model)))
    return found


def _build_local_state(endpoints: list[str], prefer: dict[str, str] | None = None,
                       critique: bool = False):
    from metaharness.core.types import Tier
    from metaharness.harness import MockLLMWorker, OpenAICompatWorker, SelfCritique
    from metaharness.identity import KeyPair
    from metaharness.web import HarnessState

    state = HarnessState()
    found = asyncio.run(_discover(endpoints))
    found.sort(key=lambda item: item[2])
    prefer = prefer or {}
    runners = {}
    if found:
        # explicit --pick substrings win; otherwise smallest → SMALL,
        # largest → FRONTIER, a middle pick → MID
        picks = {
            Tier.SMALL: found[0],
            Tier.MID: found[len(found) // 2],
            Tier.FRONTIER: found[-1],
        }
        for tier in Tier:
            want = prefer.get(tier.value)
            if want:
                match = next((f for f in found if want.lower() in f[1].lower()), None)
                if match is None:
                    raise SystemExit(f"--pick {tier.value}={want}: no discovered model matches")
                picks[tier] = match
        seen: set[str] = set()
        for tier, (base_url, model, size) in picks.items():
            worker_id = f"local-{tier.value}"
            kp = KeyPair.generate()
            runner = OpenAICompatWorker(
                worker_id, base_url=base_url, model=model, tier=tier,
                keypair=kp, max_tokens=4000,
            )
            state.register_worker(runner, kp, tiers=[tier.value])
            runners[tier] = runner
            marker = "" if model not in seen else " (shared)"
            seen.add(model)
            print(f"  {tier.value:9s} ← {model}  [{size:g}B @ {base_url}]{marker}")
    for tier in Tier:  # mock-fill anything undiscovered
        if tier not in runners:
            kp = KeyPair.generate()
            runner = MockLLMWorker(f"mock-{tier.value}", tier, keypair=kp)
            state.register_worker(runner, kp, tiers=[tier.value])
            runners[tier] = runner
            print(f"  {tier.value:9s} ← mock (no local model discovered)")
    if critique:
        runners = {t: SelfCritique(r) for t, r in runners.items()}
        print("  self-critique enabled: unverified open-ended tasks get one draft→critique→revise round")
    _finish_wiring(state, runners)
    return state


def _finish_wiring(state, runners) -> None:
    """Wire with a persistent journal dir and rehydrate prior runs, so the
    dashboard's run history survives server restarts."""
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    state.wire(runners, journal_dir=JOURNAL_DIR)
    state.enable_persistence(JOURNAL_DIR.parent)
    adopted = state.engine.adopt_all(JOURNAL_DIR)
    if adopted:
        print(f"  restored {len(adopted)} journaled run(s) from {JOURNAL_DIR}")
    if state.playbook.bullets():
        print(f"  playbook: {len(state.playbook.bullets())} active bullet(s) loaded")


def _build_mock_state():
    from metaharness.core.types import Tier
    from metaharness.harness import MockLLMWorker
    from metaharness.identity import KeyPair
    from metaharness.web import HarnessState

    state = HarnessState()
    runners = {}
    for tier in Tier:
        kp = KeyPair.generate()
        runner = MockLLMWorker(f"w-{tier.value}", tier, keypair=kp)
        state.register_worker(runner, kp, tiers=[tier.value])
        runners[tier] = runner
    _finish_wiring(state, runners)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(prog="metaharness")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="serve the WebUI over a wired harness")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8321)
    serve.add_argument("--local", action="store_true",
                       help="wire discovered local models instead of mocks")
    serve.add_argument("--endpoint", action="append", default=None,
                       help="extra OpenAI-compatible base URL to probe (repeatable)")
    serve.add_argument("--pick", action="append", default=None, metavar="TIER=SUBSTR",
                       help="pin a tier to a model by substring, e.g. --pick frontier=qwen3.6 (repeatable)")
    serve.add_argument("--critique", action="store_true",
                       help="draft→critique→revise round for unverified open-ended tasks (slower, better plans)")
    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        from metaharness.web import create_app

        if args.local:
            endpoints = args.endpoint or DEFAULT_ENDPOINTS
            prefer = dict(p.split("=", 1) for p in (args.pick or []))
            print(f"Discovering local models at: {', '.join(endpoints)}")
            state = _build_local_state(endpoints, prefer=prefer, critique=args.critique)
        else:
            state = _build_mock_state()
        uvicorn.run(create_app(state), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
