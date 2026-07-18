"""Evalgen: every entry must teach the harness how to test for it (M5).

``probe-author`` generates probes from a candidate entry; a **different**
worker (``probe-validator``, distinctness enforced through the IdentityPort
— never prompt text) must answer each probe correctly *from the source
excerpts alone* before the probe may gate anything. The generator never
grades itself.

Density default per the plan: 1 recall + 1 application probe per entry;
skill entries carry their executable ``check:`` as a probe for free.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from selflearn.contracts import CandidateEntry, Probe
from selflearn.ports import IdentityPort, ModelPort

PROBE_AUTHOR_ROLE = "probe-author"
PROBE_VALIDATOR_ROLE = "probe-validator"

AUTHOR_PROMPT = (
    "Write eval probes for this entry. Return JSON {\"probes\": [{\"kind\": "
    "\"recall\"|\"application\", \"question\": \"...\", \"expected\": "
    "\"answer key drawn from the SOURCES, never from memory\"}]}. Exactly "
    "one recall probe (a fact question with a short deterministic key) and "
    "one application probe (a novel scenario needing the knowledge)."
)

VALIDATOR_PROMPT = (
    "Answer the question using ONLY the provided source excerpts. If the "
    "excerpts do not contain the answer, reply exactly: cannot determine "
    "from sources. Return JSON {\"answer\": \"...\"}."
)


class EvalGenError(RuntimeError):
    """SchemaGuard violation or identity violation. Always loud."""


@dataclass(frozen=True)
class EvalGenReport:
    generated: tuple[Probe, ...]
    validated: tuple[Probe, ...]
    rejected: tuple[str, ...]        # probe ids the validator could not answer


class EvalGen:
    def __init__(self, author: ModelPort, validator: ModelPort,
                 identity: IdentityPort):
        self.author = author
        self.validator = validator
        self.identity = identity
        if not identity.distinct(author, validator):
            raise EvalGenError(
                "identity violation: probe validator must be a distinct "
                f"worker from the probe author (basis: {identity.basis})")

    def generate(self, entry: CandidateEntry) -> list[Probe]:
        result = self.author.complete(PROBE_AUTHOR_ROLE, AUTHOR_PROMPT, {
            "body": entry.body, "claims": list(entry.claims),
            "kind": entry.kind})
        specs = result.get("probes") if isinstance(result, dict) else None
        if not isinstance(specs, list) or not specs:
            raise EvalGenError("SchemaGuard: probe author returned no "
                               "'probes' list")
        probes: list[Probe] = []
        for i, spec in enumerate(specs):
            if not isinstance(spec, dict) or not str(spec.get("question", "")):
                raise EvalGenError(f"SchemaGuard: malformed probe spec #{i}")
            kind = str(spec.get("kind", "recall"))
            probes.append(Probe(
                id=f"{entry.id}-p{i}", entry_id=entry.id, kind=kind,
                question=str(spec["question"]),
                expected=str(spec.get("expected", "")).strip(),
                check_kind="deterministic" if kind == "recall" else "judge"))
        if entry.kind == "skill" and entry.skill_check:
            probes.append(Probe(
                id=f"{entry.id}-check", entry_id=entry.id, kind="skill",
                question="execute the skill's check block",
                expected="pass", check_kind="execution"))
        return probes

    def validate(self, probes: Sequence[Probe],
                 source_excerpts: str) -> EvalGenReport:
        """Second-model check: accepted only if the validator answers
        correctly from sources alone."""
        if not source_excerpts.strip():
            raise EvalGenError("cannot validate probes without source "
                               "excerpts — answer keys must trace to sources")
        validated: list[Probe] = []
        rejected: list[str] = []
        for probe in probes:
            if probe.check_kind == "execution":
                # executable probes are validated by the sandbox, not a model
                validated.append(Probe(
                    id=probe.id, entry_id=probe.entry_id, kind=probe.kind,
                    question=probe.question, expected=probe.expected,
                    check_kind=probe.check_kind, validated=True,
                    validated_by="execution-port"))
                continue
            if not probe.expected:
                rejected.append(probe.id)
                continue
            answer = self.validator.complete(
                PROBE_VALIDATOR_ROLE, VALIDATOR_PROMPT,
                {"question": probe.question,
                 "source_excerpts": source_excerpts[:20000]})
            text = str(answer.get("answer", ""))
            if _matches(probe.expected, text):
                validated.append(Probe(
                    id=probe.id, entry_id=probe.entry_id, kind=probe.kind,
                    question=probe.question, expected=probe.expected,
                    check_kind=probe.check_kind, validated=True,
                    validated_by=getattr(self.validator, "model_id", "?")))
            else:
                rejected.append(probe.id)
        return EvalGenReport(generated=tuple(probes),
                             validated=tuple(validated),
                             rejected=tuple(rejected))


def _matches(expected: str, answer: str) -> bool:
    """Deterministic key match: the expected key (or a long prefix of it)
    appears in the answer, case-insensitive."""
    e, a = expected.lower().strip(), answer.lower()
    return bool(e) and (e in a or e[: max(20, len(e) // 2)] in a)
