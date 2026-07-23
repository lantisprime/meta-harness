"""selflearn — standalone self-learning knowledge system.

Acquire knowledge from sources, verify it externally, gate it with generated
evals, retrieve it into prompts, and learn from verified outcomes. Host
integration happens exclusively through the five ports in
:mod:`selflearn.ports`; artifacts are plain files.

This package has zero imports from any host harness.
"""
from selflearn.contracts import (
    Asset,
    CandidateEntry,
    ContractError,
    EntrySource,
    GapSignal,
    Probe,
    ProcedureStep,
    Provenance,
    PublishDecision,
    SourceDocument,
    SourceRef,
    TaskOutcome,
)
from selflearn.ports import (
    EmbeddingPort,
    ExecutionPort,
    ExecutionResult,
    IdentityPort,
    JsonlProvenance,
    ModelIdIdentity,
    ModelPort,
    ProvenancePort,
)
from selflearn.acquisition import (
    AcquireContext,
    AcquisitionError,
    PluginRegistry,
    ReputabilityPolicy,
    builtin_plugins,
)
from selflearn.distillation import DistillationError, Distiller, injection_screen
from selflearn.learning import (
    DomainReadinessReport,
    EvaluationCriterion,
    EvaluationItemResult,
    EvaluationSplits,
    ExpertExample,
    FailureCluster,
    ImprovementDecision,
    ImprovementPolicy,
    ImprovementTrial,
    Learner,
    LearningConfig,
    MarkReport,
    RegressionReport,
    apply_outcome,
    check_regression,
    label_topic,
    snapshot_baseline,
    assess_domain_readiness,
    evaluate_improvement_trial,
)
from selflearn.pipeline import AcquisitionReport, approve_entry, run_acquisition
from selflearn.verification import (
    CorroborationRule,
    EvalGen,
    QualificationResult,
    VerificationError,
    VerificationReport,
    Verifier,
    qualify_model,
)
from selflearn.retrieval import (
    InjectionBlock,
    RetrievalResult,
    Retriever,
    render_injection_block,
)
from selflearn.specialist import SpecialistSpec, load_spec, save_spec
from selflearn.store import PackStore, StoredEntry, StoreError
from selflearn.compilation import (
    ApprovalRecord,
    ExecutorCandidate,
    ExecutorRecord,
    ExecutorRegistry,
    ExecutorRuntime,
    ExecutorSpec,
    IndependentTestSuite,
    RunResult,
    CrossValidationReceipt,
    CrossValidationGate,
    WorkflowCompiler,
    WorkflowTestAuthor,
    CompilerError,
    GateError,
    RegistryError,
    RuntimeCompError,
    WorkflowTestAuthorError,
    canonical_procedure_hash,
    content_hash,
)

__version__ = "0.1.0"

__all__ = [
    "Asset", "CandidateEntry", "ContractError", "EntrySource", "GapSignal",
    "Probe", "ProcedureStep", "Provenance", "PublishDecision",
    "SourceDocument", "SourceRef", "TaskOutcome",
    "EmbeddingPort", "ExecutionPort", "ExecutionResult", "IdentityPort",
    "JsonlProvenance", "ModelIdIdentity", "ModelPort", "ProvenancePort",
    "PackStore", "StoredEntry", "StoreError",
    "InjectionBlock", "RetrievalResult", "Retriever", "render_injection_block",
    "SpecialistSpec", "load_spec", "save_spec",
    "MarkReport", "apply_outcome", "Learner", "LearningConfig", "label_topic",
    "RegressionReport", "check_regression", "snapshot_baseline",
    "DomainReadinessReport", "EvaluationCriterion", "EvaluationItemResult",
    "EvaluationSplits",
    "ExpertExample", "FailureCluster", "ImprovementDecision",
    "ImprovementPolicy", "ImprovementTrial", "assess_domain_readiness",
    "evaluate_improvement_trial",
    "AcquireContext", "AcquisitionError", "PluginRegistry",
    "ReputabilityPolicy", "builtin_plugins",
    "DistillationError", "Distiller", "injection_screen",
    "AcquisitionReport", "approve_entry", "run_acquisition",
    "CorroborationRule", "VerificationError", "VerificationReport", "Verifier",
    "EvalGen", "QualificationResult", "qualify_model",
    # Compilation exports
    "ApprovalRecord",
    "ExecutorCandidate",
    "ExecutorRecord",
    "ExecutorRegistry",
    "ExecutorRuntime",
    "ExecutorSpec",
    "IndependentTestSuite",
    "RunResult",
    "CrossValidationReceipt",
    "CrossValidationGate",
    "WorkflowCompiler",
    "WorkflowTestAuthor",
    "CompilerError",
    "GateError",
    "RegistryError",
    "RuntimeCompError",
    "WorkflowTestAuthorError",
    "canonical_procedure_hash",
    "content_hash",
    "__version__",
]
