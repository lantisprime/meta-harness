"""Console entry point.

    metaharness serve           WebUI over mock workers (offline demo)
    metaharness serve --local   discover local OpenAI-compatible endpoints
                                (Ollama :11434, LM Studio :1234) and wire the
                                discovered models across tiers by size
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
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
                              task_types=agent.task_types or None,
                              roles=agent.roles or None,
                              capabilities=agent.capabilities or None,
                              host=agent.cli)
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
    # code-backed params resolve their artifact relative to the suite's ledger
    # root — the same dir the promoted/active params were read from.
    # F5 (panel 2026-07-09, opus P2): a promoted code artifact that fails to build
    # (missing/tampered file, hash mismatch, bad build) must NOT crash serve boot —
    # log it and serve the unwrapped worker instead.
    try:
        wrapped = params.build(base, ledger_root=root / suite)
    except (RuntimeError, AttributeError, TypeError) as exc:
        print(f"  WARNING: promoted harness config for {suite!r} failed to build "
              f"({type(exc).__name__}: {exc}); serving the unwrapped small-tier worker")
        return
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
    from metaharness.harness import (
        CLI_ADAPTERS,
        CodingAgentWorker,
        MockLLMWorker,
        OpenAICompatWorker,
        available_clis,
    )
    from metaharness.identity import KeyPair
    from metaharness.optimization import (
        CandidateLedger,
        CodeProposer,
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

    budget = None
    # `is not None`, not truthiness (issue-#5 panel round 2, convergent P2):
    # an explicit 0 cap (--max-wall-s 0 etc.) must build a Budget that stops on
    # the first positive charge, not silently run uncapped and unaccounted.
    # Mirrors serve's in-place mutation block, which already uses `is not None`.
    if args.max_tokens is not None or args.max_cost is not None or args.max_wall_s is not None:
        budget = Budget(max_tokens=args.max_tokens, max_cost_usd=args.max_cost,
                        max_wall_s=args.max_wall_s)

    if args.proposer == "llm":
        if not found:
            raise SystemExit("--proposer llm needs --local: a discovered model must do the proposing")
        p_url, p_model, p_size = found[-1]
        proposer = LLMProposer(OpenAICompatWorker(
            "opt-proposer", base_url=p_url, model=p_model, tier=Tier.FRONTIER,
            keypair=KeyPair.generate(), max_tokens=2000,
        ), budget=budget)
        print(f"  proposer  ← {p_model}  [{p_size:g}B]")
    elif args.proposer == "code":
        # a real coding agent reads the raw ledger itself (arXiv 2603.28052);
        # it authenticates ITSELF, so no --local target is required. Use the
        # first coding CLI detected on PATH.
        clis = available_clis()
        if not clis:
            raise SystemExit(
                "--proposer code needs a coding CLI on PATH; none found "
                f"(supported: {', '.join(sorted(CLI_ADAPTERS))})"
            )
        cli_name, cli_path = next(iter(clis.items()))
        proposer = CodeProposer(
            CodingAgentWorker("opt-code-proposer", cli=cli_name,
                              keypair=KeyPair.generate(), binary=cli_path),
            budget=budget,
        )
        print(f"  proposer  ← coding agent [{cli_name}] reading the ledger directly")
    else:
        proposer = RuleProposer()
        print("  proposer  ← deterministic rules (--proposer llm for the paper-shaped agentic proposer)")

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


def _run_harvest(args) -> None:
    """Turn real run journals into optimization-suite extras (per Rule 16 this
    writes shared state a later tuning run consumes, so it takes `--dry-run`).
    Exit 0 = the harvest ran (an empty harvest is a reported outcome, not an
    error); exit 1 = it could not run — e.g. a corrupt pre-existing
    extra_tasks.json (which errors out, never gets silently overwritten) or a
    failed write — reported as an {"error": ...} JSON object on stdout."""
    import json

    from metaharness.optimization.harvest import harvest_journals

    root = Path(args.root) if args.root else Path.home() / ".metaharness" / "optimization"
    try:
        report = harvest_journals(
            Path(args.journals) if args.journals else JOURNAL_DIR,
            args.suite,
            root,
            dry_run=args.dry_run,
            max_task_chars=args.max_task_chars,
        )
    except Exception as exc:  # noqa: BLE001 — the report is the contract, JSON either way
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        raise SystemExit(1)
    print(json.dumps(report.model_dump(), indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="metaharness")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="serve the WebUI over a wired harness, or a packaged blueprint")
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
    serve.add_argument("--max-cost-usd", type=float, default=None,
                       help="hard USD ceiling on the served harness's shared budget (default: unbounded accounting)")
    serve.add_argument("--max-tokens", type=int, default=None,
                       help="hard token ceiling on the served harness's shared budget (default: unbounded accounting)")
    serve.add_argument("--max-wall-s", type=float, default=None,
                       help="hard wall-clock ceiling (sum of worker latency_s) on the served harness's shared budget (default: unbounded accounting)")
    serve.add_argument("--package", type=Path, default=None,
                       help="serve a portable package instead of the WebUI")
    serve.add_argument("--package-workspace", type=Path, default=None,
                       help="workspace root for the package service")
    serve.add_argument("--package-journal-dir", type=Path, default=None,
                       help="journal directory for the package service")

    health = sub.add_parser("healthcheck", help="probe a package service health endpoint")
    health.add_argument("--url", default="http://127.0.0.1:8000/health")

    from metaharness.optimization.suites import SUITE_NAMES

    opt = sub.add_parser(
        "optimize",
        help="search harness configurations against an eval suite (Meta-Harness outer loop, arXiv 2603.28052)",
    )
    opt.add_argument("--suite", choices=SUITE_NAMES, default="mixed",
                     help="domain suite to optimize against (default: mixed — classification+extraction+math)")
    opt.add_argument("--rounds", type=int, default=6, help="max proposer rounds (default 6)")
    opt.add_argument("--k", type=int, default=3, help="attempts per task for pass^k (default 3)")
    opt.add_argument("--proposer", choices=["rule", "llm", "code"], default="rule",
                     help="rule = deterministic diagnosis; llm = agentic proposer over raw traces "
                          "(needs --local); code = coding agent reads the ledger and stages code/knob "
                          "deltas (uses the first coding CLI on PATH)")
    opt.add_argument("--local", action="store_true",
                     help="use discovered local models (smallest = optimization target, largest = llm proposer)")
    opt.add_argument("--endpoint", action="append", default=None,
                     help="extra OpenAI-compatible base URL to probe (repeatable)")
    opt.add_argument("--root", default=None,
                     help="candidate ledger directory (default ~/.metaharness/optimization/<suite>)")
    opt.add_argument("--max-tokens", type=int, default=None, help="hard token ceiling for the whole search")
    opt.add_argument("--max-cost", type=float, default=None, help="hard cost ceiling in USD for the whole search")
    opt.add_argument("--max-wall-s", type=float, default=None,
                     help="hard wall-clock ceiling (sum of worker latency_s) for the whole search")

    harvest = sub.add_parser(
        "harvest",
        help="extract suite tasks from real run journals into <suite>/extra_tasks.json",
    )
    harvest.add_argument("--suite", choices=SUITE_NAMES, default="mixed",
                         help="target suite to grow (default: mixed)")
    harvest.add_argument("--journals", default=None,
                         help=f"journal directory to scan (default {JOURNAL_DIR})")
    harvest.add_argument("--root", default=None,
                         help="optimization root; suite dir = <root>/<suite> "
                              "(default ~/.metaharness/optimization)")
    harvest.add_argument("--dry-run", action="store_true",
                         help="report what would be harvested without writing extra_tasks.json")
    harvest.add_argument("--max-task-chars", type=int, default=16000,
                         help="skip a resolved task whose JSON exceeds this many chars (default 16000)")

    blueprint = sub.add_parser("blueprint", help="validate and package Harness Blueprints")
    blueprint_sub = blueprint.add_subparsers(dest="blueprint_command", required=True)
    validate = blueprint_sub.add_parser("validate", help="validate a Blueprint or portable package")
    validate.add_argument("file", type=Path)
    validate.add_argument("--format", choices=["json"], default="json")
    validate.add_argument("--allow-draft", action="store_true")
    package = blueprint_sub.add_parser("package", help="build a deterministic portable package")
    package.add_argument("file", type=Path)
    package.add_argument("--target", action="append", required=True, dest="targets")
    package.add_argument("--output", type=Path, required=True)
    package.add_argument(
        "--output-format", choices=["zip", "directory"], default="zip"
    )
    package.add_argument("--force", action="store_true")

    run = blueprint_sub.add_parser("run", help="run an exact BlueprintVersion or portable package")
    run.add_argument("file", type=Path)
    run.add_argument("--context-file", default="-")
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--journal-dir", type=Path, default=None)
    run.add_argument("--format", choices=["jsonl"], default="jsonl")
    run.add_argument("--approval", choices=["stop"], default="stop")
    run.add_argument("--shim", action="store_true", help=argparse.SUPPRESS)

    run_cmd = sub.add_parser("run", help="inspect, approve, reject, or resume a run")
    run_sub = run_cmd.add_subparsers(dest="run_command", required=True)

    inspect = run_sub.add_parser("inspect", help="inspect a run journal")
    inspect.add_argument("run_id")
    inspect.add_argument("--journal-dir", type=Path, default=None)
    inspect.add_argument("--workspace", type=Path, default=None)
    inspect.add_argument("--format", choices=["json"], default="json")
    inspect.add_argument("--shim", action="store_true", help=argparse.SUPPRESS)

    approve = run_sub.add_parser("approve", help="approve a HITL step")
    approve.add_argument("run_id")
    approve.add_argument("step_id")
    approve.add_argument("--journal-dir", type=Path, default=None)
    approve.add_argument("--workspace", type=Path, default=None)
    approve.add_argument("--shim", action="store_true", help=argparse.SUPPRESS)

    reject = run_sub.add_parser("reject", help="reject a HITL step")
    reject.add_argument("run_id")
    reject.add_argument("step_id")
    reject.add_argument("--journal-dir", type=Path, default=None)
    reject.add_argument("--workspace", type=Path, default=None)
    reject.add_argument("--shim", action="store_true", help=argparse.SUPPRESS)

    resume = run_sub.add_parser("resume", help="resume a run after approval")
    resume.add_argument("run_id")
    resume.add_argument("--journal-dir", type=Path, default=None)
    resume.add_argument("--workspace", type=Path, default=None)
    resume.add_argument("--format", choices=["jsonl"], default="jsonl")
    resume.add_argument("--shim", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args.command == "serve":
        package_only = (args.package_workspace, args.package_journal_dir)
        if args.package is None and any(value is not None for value in package_only):
            parser.error("--package-workspace and --package-journal-dir require --package")
        if args.package is not None and args.local:
            parser.error("--package cannot be combined with --local")

    if args.command == "blueprint":
        from metaharness.portable.cli import (
            PortableCLIError,
            approve_run,
            inspect_run,
            package_blueprint,
            reject_run,
            resume_run,
            run_blueprint,
            validation_report,
        )

        journal_dir = (
            args.journal_dir
            if getattr(args, "journal_dir", None) is not None
            else JOURNAL_DIR
        )

        try:
            if args.blueprint_command == "validate":
                report = validation_report(args.file, allow_draft=args.allow_draft)
            elif args.blueprint_command == "package":
                report = package_blueprint(
                    args.file,
                    targets=args.targets,
                    output=args.output,
                    output_format=args.output_format,
                    force=args.force,
                )
            elif args.blueprint_command == "run":
                report = run_blueprint(
                    args.file,
                    context_file=args.context_file,
                    workspace=args.workspace,
                    journal_dir=journal_dir,
                    approval=args.approval,
                    shim=args.shim,
                )
            else:
                parser.error(f"unknown blueprint subcommand: {args.blueprint_command}")
                return  # pragma: no cover
        except PortableCLIError as exc:
            report = {"error": str(exc), "valid": False}
            if exc.details:
                report["details"] = exc.details
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            print(f"metaharness blueprint: {exc}", file=sys.stderr)
            raise SystemExit(2)
        if args.blueprint_command in {"run"}:
            print(json.dumps(report, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        else:
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        if "exit_code" in report:
            raise SystemExit(report["exit_code"])
        return

    if args.command == "run":
        from metaharness.portable.cli import (
            PortableCLIError,
            approve_run,
            inspect_run,
            reject_run,
            resume_run,
        )

        journal_dir = args.journal_dir if args.journal_dir is not None else JOURNAL_DIR
        workspace = args.workspace if getattr(args, "workspace", None) else None

        try:
            if args.run_command == "inspect":
                report = inspect_run(
                    args.run_id,
                    journal_dir=journal_dir,
                    workspace=workspace,
                    shim=args.shim,
                )
            elif args.run_command == "approve":
                report = approve_run(
                    args.run_id,
                    args.step_id,
                    journal_dir=journal_dir,
                    workspace=workspace,
                    shim=args.shim,
                )
            elif args.run_command == "reject":
                report = reject_run(
                    args.run_id,
                    args.step_id,
                    journal_dir=journal_dir,
                    workspace=workspace,
                    shim=args.shim,
                )
            elif args.run_command == "resume":
                report = resume_run(
                    args.run_id,
                    journal_dir=journal_dir,
                    workspace=workspace,
                    shim=args.shim,
                )
            else:
                parser.error(f"unknown run subcommand: {args.run_command}")
                return  # pragma: no cover
        except PortableCLIError as exc:
            report = {"error": str(exc)}
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            print(f"metaharness run: {exc}", file=sys.stderr)
            raise SystemExit(2)
        if args.run_command == "resume":
            print(json.dumps(report, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        else:
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        if "exit_code" in report:
            raise SystemExit(report["exit_code"])
        return

    if args.command == "optimize":
        _run_optimize(args)
        return

    if args.command == "harvest":
        _run_harvest(args)
        return

    if args.command == "serve":
        import uvicorn

        if args.package is not None:
            from metaharness.portable.cli import PortableCLIError
            from metaharness.portable.runtime import PortableRuntimeError
            from metaharness.portable.service import create_package_app

            try:
                app = create_package_app(
                    args.package,
                    workspace=args.package_workspace,
                    journal_dir=args.package_journal_dir,
                )
            except (PortableCLIError, PortableRuntimeError, OSError, ValueError) as exc:
                print(
                    json.dumps({"status": "invalid-package", "error": str(exc)}),
                    file=sys.stderr,
                )
                raise SystemExit(2)
            uvicorn.run(app, host=args.host, port=args.port)
            return

        from metaharness.web import create_app

        if args.local:
            endpoints = args.endpoint or DEFAULT_ENDPOINTS
            prefer = dict(p.split("=", 1) for p in (args.pick or []))
            print(f"Discovering local models at: {', '.join(endpoints)}")
            state = _build_local_state(endpoints, prefer=prefer, critique=args.critique)
        else:
            state = _build_mock_state()
        # F1 (panel 2026-07-09): the state always carries a cap-less accumulator;
        # these flags upgrade it to a hard ceiling on the SAME object the executor
        # and endpoints already hold a reference to (mutated in place after wiring).
        if args.max_cost_usd is not None:
            state.budget.max_cost_usd = args.max_cost_usd
        if args.max_tokens is not None:
            state.budget.max_tokens = args.max_tokens
        if args.max_wall_s is not None:
            state.budget.max_wall_s = args.max_wall_s
        if args.max_cost_usd is not None or args.max_tokens is not None or args.max_wall_s is not None:
            print(f"  budget cap: max_cost_usd={args.max_cost_usd} max_tokens={args.max_tokens} "
                  f"max_wall_s={args.max_wall_s}")
        uvicorn.run(create_app(state), host=args.host, port=args.port)

    if args.command == "healthcheck":
        import urllib.error
        import urllib.parse
        import urllib.request

        parsed = urllib.parse.urlsplit(args.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            print(json.dumps({"status": "unhealthy", "error": "URL must use HTTP or HTTPS"}))
            raise SystemExit(2)

        try:
            with urllib.request.urlopen(args.url, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            print(json.dumps({"status": "unhealthy", "error": f"HTTP {exc.code}"}))
            raise SystemExit(1)
        except Exception:  # connection and response details may contain URL credentials
            print(json.dumps({"status": "unhealthy", "error": "health probe failed"}))
            raise SystemExit(1)
        if not isinstance(data, dict):
            print(json.dumps({"status": "unhealthy", "error": "invalid health response"}))
            raise SystemExit(1)
        print(json.dumps(data, sort_keys=True))
        if data.get("status") != "healthy":
            raise SystemExit(1)
        return


if __name__ == "__main__":
    main()
