"""HarnessState: one container wiring every layer together for the WebUI/API.

This is also the canonical "how do the pieces compose" reference: identity
(registry + issuer + provenance), routing (runners + matrix), correction
(playbook + learning loop), workflows (engine + executor), observability
(span store).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from metaharness.config import HarnessConfig
from metaharness.core.budget import Budget
from metaharness.core.executor import TaskExecutor
from metaharness.core.types import Tier
from metaharness.correction.learning import LearningLoop
from metaharness.correction.playbook import Playbook
from metaharness.correction.reflexion import grounded_reflector
from metaharness.harness.runner import Runner
from metaharness.identity.keys import KeyPair
from metaharness.identity.provenance import ProvenanceLog
from metaharness.identity.registry import WorkerRegistry, registration_payload
from metaharness.identity.tokens import TokenIssuer
from metaharness.routing.router import CapabilityMatrix, Router
from metaharness.tools import ToolRegistry, default_registry
from metaharness.workflows.engine import WorkflowEngine


@dataclass
class HarnessState:
    registry: WorkerRegistry = field(default_factory=WorkerRegistry)
    issuer: TokenIssuer = field(default_factory=TokenIssuer)
    provenance: ProvenanceLog = field(default_factory=ProvenanceLog)
    matrix: CapabilityMatrix = field(default_factory=CapabilityMatrix)
    playbook: Playbook = field(default_factory=Playbook)
    orchestrator_keypair: KeyPair = field(default_factory=KeyPair.generate)
    learning: LearningLoop = None  # type: ignore[assignment]
    router: Optional[Router] = None
    executor: Optional[TaskExecutor] = None
    engine: Optional[WorkflowEngine] = None
    budget: Optional[Budget] = None
    config: HarnessConfig = field(default_factory=HarnessConfig)
    config_path: Optional[object] = None  # Path; set when config should persist
    optimization_root: Optional[object] = None  # Path; harness-tuning ledgers live here
    tools: ToolRegistry = field(default_factory=default_registry)

    def save_config(self) -> None:
        """Write-through for the durable config, mirroring matrix/playbook."""
        if self.config_path is not None:
            self.config.save(self.config_path)

    def __post_init__(self) -> None:
        if self.learning is None:
            self.learning = LearningLoop(self.playbook)
        # the orchestrator is itself a registered actor, so provenance entries
        # it signs are verifiable through the same registry as everyone else
        if self.registry.get("orchestrator") is None:
            challenge = self.registry.begin_registration("orchestrator")
            payload = registration_payload(
                "orchestrator", self.orchestrator_keypair.public_b64(), challenge.nonce
            )
            self.registry.complete_registration(
                "orchestrator",
                self.orchestrator_keypair.public_b64(),
                self.orchestrator_keypair.sign(payload),
                display_name="Meta-harness orchestrator",
            )

    def enable_playbook_persistence(self, path) -> None:
        """Load the playbook from disk (if present) and keep it saved: the slow
        loop's lessons must survive restarts, or the harness re-learns them
        forever. Also switches auto-curation on — in a long-running server there
        is no other moment for the slow loop to run."""
        from pathlib import Path

        path = Path(path)
        if path.exists():
            self.playbook = Playbook.load(path)
            self.learning.playbook = self.playbook
        self.learning.auto_curate = True
        self.learning.persist_path = path

    def enable_persistence(self, directory) -> None:
        """Make ALL learned state durable: playbook (lessons), capability matrix
        (routing evidence), failure stats (cluster tallies). Loads whatever is
        already on disk, then writes through on every change."""
        from pathlib import Path

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.optimization_root = directory / "optimization"
        self.enable_playbook_persistence(directory / "playbook.json")

        matrix_path = directory / "matrix.json"
        if matrix_path.exists():
            loaded = CapabilityMatrix.load(matrix_path, smoothing=self.matrix.smoothing)
            self.matrix = loaded
            if self.router is not None:
                self.router.matrix = loaded
        self.matrix.persist_path = matrix_path

        stats_path = directory / "failures.json"
        if stats_path.exists():
            from metaharness.correction.mast import FailureStats
            self.learning.stats = FailureStats.load(stats_path)
        self.learning.stats_path = stats_path

    def register_worker(self, runner: Runner, keypair: KeyPair, tiers: list[str],
                        task_types: Optional[list[str]] = None) -> None:
        """Admit a runner's identity through the normal challenge ceremony."""
        challenge = self.registry.begin_registration(runner.worker_id)
        payload = registration_payload(
            runner.worker_id, keypair.public_b64(), challenge.nonce
        )
        self.registry.complete_registration(
            runner.worker_id, keypair.public_b64(), keypair.sign(payload),
            display_name=runner.model, tiers=tiers, task_types=task_types or [],
        )

    def wire(self, runners: "dict[Tier, Runner | list[Runner]]", journal_dir=None,
             threshold: float = 0.7, judge: bool = True) -> None:
        """Build router → executor → engine from per-tier runner pools (a bare
        Runner value is normalized to a one-member pool by the Router).

        judge=True (default) grades every UNVERIFIED step output with the most
        capable wired runner before dependents consume it (rubric-judge slot of
        the verifier hierarchy). The judge call runs in a fresh context with
        external-role framing; when the same model serves generator and judge
        tiers the decorrelation is contextual, not model-level."""
        explore_rate = float(self.config.settings.get("explore_rate", 0.1))
        self.router = Router(runners, matrix=self.matrix, threshold=threshold,
                             explore_rate=explore_rate)
        for members in self.router.pools.values():
            for runner in members:
                self.attach_tools(runner)
        judge_fn = None
        if judge and self.router.pools:
            from metaharness.evals.judge import make_judge
            for tier in (Tier.FRONTIER, Tier.MID, Tier.SMALL):
                members = self.router.pool(tier)
                if members:
                    judge_fn = make_judge(members[0])
                    break
        self.executor = TaskExecutor(
            self.router,
            registry=self.registry,
            provenance=self.provenance,
            orchestrator_keypair=self.orchestrator_keypair,
            budget=self.budget,
            reflector=grounded_reflector,
            playbook_hints=self.learning.hints_for,
            observer=self.learning.observe,
            judge=judge_fn,
        )
        self.engine = WorkflowEngine(self.executor, journal_dir=journal_dir)

    def attach_tools(self, runner: Runner) -> None:
        """Point a runner (and whatever it wraps) at the shared tool registry."""
        target: Optional[object] = runner
        while target is not None:
            if hasattr(target, "tool_registry"):
                target.tool_registry = self.tools
            target = getattr(target, "inner", None)

    def planner_runner(self) -> Runner:
        """The most capable wired runner — used to plan workflows from goals."""
        if self.router is None:
            raise RuntimeError("harness not wired")
        for tier in (Tier.FRONTIER, Tier.MID, Tier.SMALL):
            members = self.router.pool(tier)
            if members:
                return members[0]
        raise RuntimeError("no runners wired")

    def add_worker(self, runner: Runner, tier: Tier) -> None:
        """Admit a new worker at runtime and add it to the tier's routing pool.

        The harness generates and holds the worker's keypair (in-process workers
        share the harness's trust boundary; a remote worker would bring its own
        public key instead). The runner joins the tier's pool (created if empty).
        Re-adding a live worker_id rotates its identity — the old record is
        deactivated first, and the registry counts the rotation in the audit
        trail — and replaces the pool member in place, keeping its position."""
        if self.router is None:
            raise RuntimeError("harness not wired — call wire() first")
        keypair = getattr(runner, "keypair", None)
        if keypair is None:
            raise ValueError("runner needs a keypair to sign its results")
        existing = self.registry.get(runner.worker_id)
        if existing is not None and existing.active:
            # begin_registration refuses live ids; retiring first turns the
            # re-add into the registry's sanctioned key-rotation path
            self.registry.deactivate(runner.worker_id)
        self.register_worker(runner, keypair, tiers=[tier.value])
        self.attach_tools(runner)
        pool = self.router.pools.setdefault(tier, [])
        for i, member in enumerate(pool):
            if member.worker_id == runner.worker_id:
                pool[i] = runner
                return
        pool.append(runner)
