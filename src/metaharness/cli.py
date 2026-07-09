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


def _load_configured_runners(state) -> dict:
    """Wire every enabled agent from ~/.metaharness/config.json into per-tier
    pools. Configured agents are explicit user intent, so they claim their tiers
    before discovery/mocks fill the gaps; multiple agents on a tier all join its
    pool in config order (no last-writer-wins eviction). A bad entry fails the
    boot loudly."""
    from metaharness.config import CONFIG_PATH, HarnessConfig
    from metaharness.core.types import Tier
    from metaharness.factory import build_agent_runner
    from metaharness.identity import KeyPair

    state.config = HarnessConfig.load(CONFIG_PATH)
    state.config_path = CONFIG_PATH
    runners: dict = {}
    for agent in state.config.agents:
        if not agent.enabled:
            continue
        kp = KeyPair.generate()
        runner = build_agent_runner(agent, state.config, keypair=kp)
        state.register_worker(runner, kp, tiers=[agent.tier],
                              task_types=agent.task_types or None)
        runners.setdefault(Tier(agent.tier), []).append(runner)
        print(f"  {agent.tier:9s} ← {runner.model}  [configured: {agent.worker_id}]")
    return runners


def _build_local_state(endpoints: list[str], prefer: dict[str, str] | None = None,
                       critique: bool = False):
    from metaharness.core.types import Tier
    from metaharness.harness import MockLLMWorker, OpenAICompatWorker, SelfCritique
    from metaharness.identity import KeyPair
    from metaharness.web import HarnessState

    state = HarnessState()
    runners = _load_configured_runners(state)
    found = asyncio.run(_discover(endpoints))
    found.sort(key=lambda item: item[2])
    prefer = prefer or {}
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
            if runners.get(tier):  # configured agent already claimed this tier
                continue
            worker_id = f"local-{tier.value}"
            kp = KeyPair.generate()
            runner = OpenAICompatWorker(
                worker_id, base_url=base_url, model=model, tier=tier,
                keypair=kp, max_tokens=4000,
            )
            state.register_worker(runner, kp, tiers=[tier.value])
            runners.setdefault(tier, []).append(runner)
            marker = "" if model not in seen else " (shared)"
            seen.add(model)
            print(f"  {tier.value:9s} ← {model}  [{size:g}B @ {base_url}]{marker}")
    for tier in Tier:  # mock-fill anything undiscovered
        if not runners.get(tier):
            kp = KeyPair.generate()
            runner = MockLLMWorker(f"mock-{tier.value}", tier, keypair=kp)
            state.register_worker(runner, kp, tiers=[tier.value])
            runners.setdefault(tier, []).append(runner)
            print(f"  {tier.value:9s} ← mock (no local model discovered)")
    if critique:
        runners = {t: [SelfCritique(r) for r in members] for t, members in runners.items()}
        print("  self-critique enabled: unverified open-ended tasks get one draft→critique→revise round")
    _finish_wiring(state, runners)
    return state


def _apply_promoted(runners) -> None:
    """Wrap the small-tier runner with the approved harness config — tuning
    results take effect at serve time, loudly announced. The web approval
    writes an `active.json` pointer naming the exact suite/config that went
    live; CLI-promoted `mixed/promoted.json` is the fallback."""
    import json

    from metaharness.core.types import Tier
    from metaharness.optimization import CandidateLedger, HarnessParams

    if not runners.get(Tier.SMALL):
        return
    root = JOURNAL_DIR.parent / "optimization"
    suite, params = "mixed", None
    active_path = root / "active.json"
    if active_path.is_file():
        active = json.loads(active_path.read_text(encoding="utf-8"))
        suite = active.get("suite", "mixed")
        params = HarnessParams.model_validate(active["params"])
    else:
        params = CandidateLedger(root / "mixed").promoted_params()
    if params is None:
        return
    base = runners[Tier.SMALL][0]  # tuning targets the small tier's primary member
    wrapped = params.build(base)
    wrapped._tuning_base = base  # web approvals replace exactly this layer
    runners[Tier.SMALL][0] = wrapped
    defaults = HarnessParams().model_dump()
    knobs = {k: v for k, v in params.model_dump().items() if v != defaults[k]}
    print(f"  promoted harness config active on small tier ({suite} suite): "
          f"{', '.join(f'{k}={v}' for k, v in knobs.items()) or 'defaults'}")


def _finish_wiring(state, runners) -> None:
    """Wire with a persistent journal dir and rehydrate prior runs, so the
    dashboard's run history survives server restarts."""
    _apply_promoted(runners)
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
    runners = _load_configured_runners(state)
    for tier in Tier:
        if runners.get(tier):
            continue
        kp = KeyPair.generate()
        runner = MockLLMWorker(f"w-{tier.value}", tier, keypair=kp)
        state.register_worker(runner, kp, tiers=[tier.value])
        runners.setdefault(tier, []).append(runner)
    _finish_wiring(state, runners)
    return state


def _run_optimize(args) -> None:
    """Meta-Harness outer loop (arXiv 2603.28052): search harness configs for a
    fixed worker against a domain suite; promote through the held-out gate."""
    from metaharness.core.budget import Budget
    from metaharness.core.types import Tier
    from metaharness.harness import MockLLMWorker, OpenAICompatWorker
    from metaharness.identity import KeyPair
    from metaharness.optimization import (
        CandidateLedger,
        HarnessOptimizer,
        LLMProposer,
        RuleProposer,
        search_and_holdout,
    )

    root = Path(args.root) if args.root else Path.home() / ".metaharness" / "optimization" / args.suite
    search, holdout = search_and_holdout(args.suite, extras_dir=root)
    ledger = CandidateLedger(root)

    found: list[tuple[str, str, float]] = []
    if args.local:
        endpoints = args.endpoint or DEFAULT_ENDPOINTS
        found = sorted(asyncio.run(_discover(endpoints)), key=lambda f: f[2])
        if not found:
            raise SystemExit(f"--local: no models discovered at {', '.join(endpoints)}")
        base_url, model, size = found[0]

        def base_factory():
            return OpenAICompatWorker(
                "opt-target", base_url=base_url, model=model, tier=Tier.SMALL,
                keypair=KeyPair.generate(), max_tokens=2000,
            )
        print(f"  target    ← {model}  [{size:g}B @ {base_url}]")
    else:
        def base_factory():
            return MockLLMWorker("opt-target", Tier.SMALL, keypair=KeyPair.generate(), seed=7)
        print("  target    ← mock small worker (offline demo; --local for a real model)")

    if args.proposer == "llm":
        if not found:
            raise SystemExit("--proposer llm needs --local: a discovered model must do the proposing")
        p_url, p_model, p_size = found[-1]
        proposer = LLMProposer(OpenAICompatWorker(
            "opt-proposer", base_url=p_url, model=p_model, tier=Tier.FRONTIER,
            keypair=KeyPair.generate(), max_tokens=2000,
        ))
        print(f"  proposer  ← {p_model}  [{p_size:g}B]")
    else:
        proposer = RuleProposer()
        print("  proposer  ← deterministic rules (--proposer llm for the paper-shaped agentic proposer)")

    budget = None
    if args.max_tokens or args.max_cost:
        budget = Budget(max_tokens=args.max_tokens, max_cost_usd=args.max_cost)

    optimizer = HarnessOptimizer(
        base_factory, proposer, search, holdout, ledger, k=args.k, budget=budget,
    )
    report = asyncio.run(optimizer.optimize(rounds=args.rounds))

    print(f"\nCandidates ({root}):")
    for c in ledger.candidates():
        if c.scores:
            line = (f"pass^{c.scores.k}={c.scores.pass_hat_k:.2f} "
                    f"pass@1={c.scores.pass_at_1:.2f} tokens={c.scores.tokens_total}")
        else:
            line = f"rejected: {c.rejected_reason}"
        print(f"  {c.id}  [{c.status:9s}] {line}")
        print(f"          {c.hypothesis[:100]}")
    print(f"\nStopped after {report.rounds_run} round(s): {report.stopped}")
    print(f"Pareto frontier: {', '.join(report.frontier)}")
    print(f"Seed {report.seed_id} → best {report.best_id}")
    if report.gate is not None:
        verdict = "GO" if report.gate.go else "NO-GO"
        print(f"Held-out gate [{report.gate.incumbent_model} vs {report.gate.candidate_model}]: "
              f"{verdict} "
              f"({report.gate.overall_incumbent:.2f} → {report.gate.overall_candidate:.2f}, "
              f"{report.gate.wins}W/{report.gate.losses}L/{report.gate.ties}T)")
    for note in report.notes:
        print(f"  - {note}")
    if report.promoted:
        print(f"Promoted: {root / 'promoted.json'}")


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

    from metaharness.optimization.suites import SUITE_NAMES

    opt = sub.add_parser(
        "optimize",
        help="search harness configurations against an eval suite (Meta-Harness outer loop, arXiv 2603.28052)",
    )
    opt.add_argument("--suite", choices=SUITE_NAMES, default="mixed",
                     help="domain suite to optimize against (default: mixed — classification+extraction+math)")
    opt.add_argument("--rounds", type=int, default=6, help="max proposer rounds (default 6)")
    opt.add_argument("--k", type=int, default=3, help="attempts per task for pass^k (default 3)")
    opt.add_argument("--proposer", choices=["rule", "llm"], default="rule",
                     help="rule = deterministic diagnosis; llm = agentic proposer over raw traces (needs --local)")
    opt.add_argument("--local", action="store_true",
                     help="use discovered local models (smallest = optimization target, largest = llm proposer)")
    opt.add_argument("--endpoint", action="append", default=None,
                     help="extra OpenAI-compatible base URL to probe (repeatable)")
    opt.add_argument("--root", default=None,
                     help="candidate ledger directory (default ~/.metaharness/optimization/<suite>)")
    opt.add_argument("--max-tokens", type=int, default=None, help="hard token ceiling for the whole search")
    opt.add_argument("--max-cost", type=float, default=None, help="hard cost ceiling in USD for the whole search")
    args = parser.parse_args()

    if args.command == "optimize":
        _run_optimize(args)
        return

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
