"""Strict, immutable evaluation-suite artifact contracts.

Holdout cases are exposed only through the trusted version model.  Ordinary
callers receive a separate public projection with a count and digest, so an API
cannot accidentally serialize sealed cases.
"""
from __future__ import annotations

import math
import json
from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from metaharness.blueprints.models import ArtifactRef, _validate_slug
from metaharness.evals.verifiers import check_value_problems
from metaharness.portable.integrity import (
    PortableIntegrityError,
    _SENSITIVE_KEY,
    assert_secret_safe,
    canonical_json_bytes,
    sha256_hex,
)


class StrictEvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


_SCHEMA_NAME_MAPS = {
    "$defs", "definitions", "properties", "patternProperties", "dependentSchemas"
}


def _contains_secret_binding(value: Any, *, schema: bool = False) -> bool:
    if isinstance(value, dict):
        if "binding" in value:
            return True
        for key, child in value.items():
            if schema and key in _SCHEMA_NAME_MAPS and isinstance(child, dict):
                # Keys in schema name maps are user-defined names, not instance
                # data.  Their values remain schemas and are scanned normally.
                if any(
                    _contains_secret_binding(nested, schema=True)
                    for nested in child.values()
                ):
                    return True
            elif _contains_secret_binding(child, schema=schema):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_binding(child, schema=schema) for child in value)
    return False


def _assert_secret_safe_json(
    value: Any,
    *,
    location: str,
    reject_sensitive_keys: bool = False,
    schema_aware: bool = False,
) -> None:
    """Prove a JSON value is serializable and contains no credential material."""
    try:
        normalized = json.loads(canonical_json_bytes(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{location} must be finite JSON data") from exc
    if schema_aware and isinstance(normalized, dict):
        binding_found = any(
            _contains_secret_binding(child, schema=(key == "output_schema"))
            for key, child in normalized.items()
        )
    else:
        binding_found = _contains_secret_binding(normalized)
    if binding_found:
        raise ValueError(f"{location} cannot contain secret binding markers")

    def reject_keys(child: Any, child_location: str) -> None:
        if isinstance(child, dict):
            for key, nested in child.items():
                nested_location = f"{child_location}.{key}"
                if _SENSITIVE_KEY.search(str(key)):
                    raise ValueError(
                        f"sensitive context key is not allowed at {nested_location}"
                    )
                reject_keys(nested, nested_location)
        elif isinstance(child, list):
            for index, nested in enumerate(child):
                reject_keys(nested, f"{child_location}[{index}]")

    if reject_sensitive_keys:
        reject_keys(normalized, location)
    try:
        assert_secret_safe(normalized, location=location)
    except PortableIntegrityError as exc:
        raise ValueError(str(exc)) from exc


class EvalAssertion(StrictEvalModel):
    """Exactly one check route, ordered deterministic -> schema -> rubric."""

    success_check: Optional[dict[str, Any]] = None
    output_schema: Optional[dict[str, Any]] = None
    rubric: Optional[str] = None

    @model_validator(mode="after")
    def _one_route(self) -> "EvalAssertion":
        routes = [
            self.success_check is not None,
            self.output_schema is not None,
            self.rubric is not None,
        ]
        if sum(routes) != 1:
            raise ValueError(
                "exactly one non-null assertion route is required: "
                "success_check, output_schema, or rubric"
            )
        if self.success_check is not None:
            if not self.success_check:
                raise ValueError("success_check must be a nonempty object")
            allowed = {"equals", "contains", "one_of", "tol"}
            unknown = sorted(set(self.success_check) - allowed)
            if unknown:
                raise ValueError(f"unknown success_check keys: {unknown}")
            check_routes = [key for key in ("equals", "contains", "one_of") if key in self.success_check]
            if len(check_routes) != 1:
                raise ValueError(
                    "success_check requires exactly one of equals, contains, or one_of"
                )
            route = check_routes[0]
            if "tol" in self.success_check and route != "equals":
                raise ValueError("success_check tol is valid only with equals")
            if "tol" in self.success_check and (
                isinstance(self.success_check["tol"], bool)
                or not isinstance(self.success_check["tol"], (int, float))
            ):
                raise ValueError("success_check tol must be a finite nonnegative number")
            if route == "contains" and (
                not isinstance(self.success_check["contains"], str)
                or not self.success_check["contains"].strip()
            ):
                raise ValueError("success_check contains must be a nonempty string")
            if route == "one_of" and (
                not isinstance(self.success_check["one_of"], list)
                or not self.success_check["one_of"]
            ):
                raise ValueError("success_check one_of must be a nonempty list")
            problems = check_value_problems(self.success_check)
            if problems:
                raise ValueError("unsafe success_check: " + "; ".join(problems))
        for label, value in (
            ("success_check", self.success_check),
            ("output_schema", self.output_schema),
        ):
            if value is not None:
                try:
                    canonical_json_bytes(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{label} must be finite JSON data") from exc
        if self.output_schema is not None:
            if not self.output_schema:
                raise ValueError("output_schema must be a nonempty JSON Schema")
            try:
                Draft202012Validator.check_schema(self.output_schema)
            except SchemaError as exc:
                raise ValueError("output_schema must be a valid JSON Schema") from exc
        if self.rubric is not None:
            self.rubric = self.rubric.strip()
            if not self.rubric:
                raise ValueError("rubric must be nonblank")
        _assert_secret_safe_json(
            self.model_dump(mode="json"),
            location="eval assertion",
            schema_aware=True,
        )
        return self


class EvalCase(StrictEvalModel):
    id: str
    name: str
    context: dict[str, Any] = Field(default_factory=dict)
    assertion: EvalAssertion
    output_step: Optional[str] = None
    tags: tuple[str, ...] = Field(default_factory=tuple)
    source: Literal["authored", "frontier", "production-regression"] = "authored"

    @field_validator("id")
    @classmethod
    def _id_is_safe(cls, value: str) -> str:
        return _validate_slug(value, label="case id")

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("case name cannot be blank")
        return value

    @field_validator("output_step")
    @classmethod
    def _output_step_is_safe(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _validate_slug(value, label="output step")

    @field_validator("tags")
    @classmethod
    def _tags_are_safe_and_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = [_validate_slug(value, label="tag") for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("case tags must be unique")
        return tuple(sorted(normalized))

    @model_validator(mode="after")
    def _context_contains_no_credentials(self) -> "EvalCase":
        _assert_secret_safe_json(
            self.name, location=f"eval case {self.id}.name"
        )
        _assert_secret_safe_json(
            self.context,
            location=f"eval case {self.id}.context",
            reject_sensitive_keys=True,
        )
        return self


class EvalToolBinding(StrictEvalModel):
    tool: str
    binding: str
    non_production: Literal[True]
    isolation: Literal["deterministic_fake", "fixture", "disposable_workspace"]

    @field_validator("tool", "binding")
    @classmethod
    def _names_are_safe(cls, value: str) -> str:
        return _validate_slug(value, label="tool binding name")


class EvalPolicy(StrictEvalModel):
    k: int = Field(default=3, ge=1, le=7, strict=True)
    judge_allowed: Literal[False] = False
    hitl_mode: Literal["block", "approve_isolated_only"] = "block"
    tool_bindings: list[EvalToolBinding] = Field(default_factory=list)
    max_cost_usd: Optional[float] = Field(default=None, gt=0.0, strict=True)
    max_tokens: Optional[int] = Field(default=None, gt=0, strict=True)
    max_wall_s: Optional[float] = Field(default=None, gt=0.0, strict=True)

    @field_validator("max_cost_usd", "max_wall_s")
    @classmethod
    def _finite_optional_bounds(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not math.isfinite(value):
            raise ValueError("eval policy bounds must be finite")
        return value


class EvalSuiteContent(StrictEvalModel):
    schema_version: Literal[1] = 1
    name: str
    description: str = ""
    development_cases: list[EvalCase] = Field(default_factory=list)
    validation_cases: list[EvalCase] = Field(default_factory=list)
    holdout_cases: list[EvalCase] = Field(default_factory=list)
    policy: EvalPolicy = Field(default_factory=EvalPolicy)

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("eval suite name cannot be blank")
        return value

    @model_validator(mode="after")
    def _case_ids_are_unique_across_splits(self) -> "EvalSuiteContent":
        case_ids = [
            case.id
            for split in (
                self.development_cases,
                self.validation_cases,
                self.holdout_cases,
            )
            for case in split
        ]
        duplicates = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate eval case ids across splits: {duplicates}")
        return self


class EvalSuiteDraft(EvalSuiteContent):
    id: str
    revision: int = Field(ge=1, strict=True)
    base_version: Optional[int] = Field(default=None, ge=1, strict=True)
    owner: str
    created_at: float
    updated_at: float

    @field_validator("id")
    @classmethod
    def _id_is_safe(cls, value: str) -> str:
        return _validate_slug(value, label="eval suite id")

    @field_validator("owner")
    @classmethod
    def _owner_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("eval suite draft owner cannot be blank")
        return value


class EvalSuiteVersion(EvalSuiteContent):
    id: str
    version: int = Field(ge=1, strict=True)
    created_at: float

    @field_validator("id")
    @classmethod
    def _id_is_safe(cls, value: str) -> str:
        return _validate_slug(value, label="eval suite id")

    @property
    def ref(self) -> ArtifactRef:
        return ArtifactRef(id=self.id, version=self.version)

    @property
    def holdout_digest(self) -> str:
        cases = [case.model_dump(mode="json") for case in self.holdout_cases]
        return sha256_hex(canonical_json_bytes(cases))

    def public(self) -> "EvalSuitePublic":
        data = self.model_dump(mode="python")
        data.pop("holdout_cases")
        return EvalSuitePublic(
            **data,
            holdout_count=len(self.holdout_cases),
            holdout_digest=self.holdout_digest,
        )


class EvalSuitePublic(StrictEvalModel):
    schema_version: Literal[1] = 1
    id: str
    version: int = Field(ge=1, strict=True)
    name: str
    description: str = ""
    development_cases: list[EvalCase] = Field(default_factory=list)
    validation_cases: list[EvalCase] = Field(default_factory=list)
    policy: EvalPolicy = Field(default_factory=EvalPolicy)
    created_at: float
    holdout_count: int = Field(ge=0, strict=True)
    holdout_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def ref(self) -> ArtifactRef:
        return ArtifactRef(id=self.id, version=self.version)

    @field_validator("id")
    @classmethod
    def _id_is_safe(cls, value: str) -> str:
        return _validate_slug(value, label="eval suite id")

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("eval suite name cannot be blank")
        return value


class EvalSuiteCatalogEntry(StrictEvalModel):
    id: str
    display_name: str
    archived_at: Optional[float] = None
    latest_version: Optional[int] = Field(default=None, ge=1, strict=True)

    @field_validator("id")
    @classmethod
    def _id_is_safe(cls, value: str) -> str:
        return _validate_slug(value, label="eval suite id")

    @field_validator("display_name")
    @classmethod
    def _display_name_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("display name cannot be blank")
        return value


class EvalSuitePublishIntent(StrictEvalModel):
    id: str
    expected_revision: int = Field(ge=1, strict=True)
    version: EvalSuiteVersion

    @field_validator("id")
    @classmethod
    def _id_is_safe(cls, value: str) -> str:
        return _validate_slug(value, label="eval suite id")

    @model_validator(mode="after")
    def _version_belongs_to_intent(self) -> "EvalSuitePublishIntent":
        if self.version.id != self.id:
            raise ValueError("publish intent version id does not match intent id")
        return self


class EvalCaseProposal(StrictEvalModel):
    """An inert, review-only candidate; no store operation promotes it."""

    id: str
    blueprint_ref: ArtifactRef
    source_run_ids: list[str] = Field(default_factory=list)
    cases: list[EvalCase] = Field(default_factory=list)
    redaction_report: dict[str, Any] = Field(default_factory=dict)
    status: Literal["proposed", "accepted", "rejected"] = "proposed"
    created_at: float
    updated_at: float

    @field_validator("id")
    @classmethod
    def _ids_are_safe(cls, value: str) -> str:
        return _validate_slug(value)

    @field_validator("source_run_ids")
    @classmethod
    def _source_run_ids_are_nonblank_unique(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("source run ids cannot be blank")
        if len(set(values)) != len(values):
            raise ValueError("source run ids must be unique")
        return values

    @model_validator(mode="after")
    def _proposal_case_ids_are_unique(self) -> "EvalCaseProposal":
        ids = [case.id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("proposal case ids must be unique")
        _assert_secret_safe_json(
            self.source_run_ids, location=f"eval proposal {self.id}.source_run_ids"
        )
        _assert_secret_safe_json(
            self.redaction_report,
            location=f"eval proposal {self.id}.redaction_report",
            reject_sensitive_keys=True,
        )
        return self
