"""Strict domain models for immutable, versioned Harness Blueprints."""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from metaharness.workflows.dsl import WorkflowSpec


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_INPUT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
MAX_SLUG_LENGTH = 80


def _validate_slug(value: str, *, label: str = "id") -> str:
    if len(value) > MAX_SLUG_LENGTH or not _SLUG_RE.fullmatch(value):
        raise ValueError(
            f"{label} must be a lowercase slug of at most {MAX_SLUG_LENGTH} "
            "characters (letters, digits, and single hyphens)"
        )
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, serialize_by_alias=True
    )


class ArtifactRef(StrictModel):
    """An exact immutable artifact reference; ``latest`` is never valid here."""

    id: str
    version: int = Field(ge=1, strict=True)

    @field_validator("id")
    @classmethod
    def _id_is_safe_slug(cls, value: str) -> str:
        return _validate_slug(value)


class SecretBindingRef(StrictModel):
    """Logical name of a locally configured secret, never the secret value."""

    binding: str

    @field_validator("binding")
    @classmethod
    def _binding_is_safe_slug(cls, value: str) -> str:
        return _validate_slug(value, label="binding")


_SCHEMA_NAME_MAPS = {
    "$defs", "definitions", "properties", "patternProperties", "dependentSchemas"
}


def _contains_schema_default(value: Any) -> bool:
    """Find JSON-Schema ``default`` keywords without mistaking property names.

    Keys below schema-name maps are user-defined names. Their values are still
    schemas and are searched recursively, but a property literally named
    ``default`` is not itself the JSON-Schema default annotation.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "default":
                return True
            if key in _SCHEMA_NAME_MAPS and isinstance(child, dict):
                if any(_contains_schema_default(schema) for schema in child.values()):
                    return True
            elif _contains_schema_default(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_schema_default(v) for v in value)
    return False


def _contains_context_reference(value: Any, names: set[str]) -> bool:
    """Recursively detect a declared secret being read through run context."""
    if isinstance(value, str) and value.startswith("$context."):
        referenced = value[len("$context."):].split(".", 1)[0]
        return referenced in names
    if isinstance(value, dict):
        return any(_contains_context_reference(child, names) for child in value.values())
    if isinstance(value, list):
        return any(_contains_context_reference(child, names) for child in value)
    return False


class InputSpec(StrictModel):
    name: str
    value_schema: dict[str, Any] = Field(
        alias="schema", serialization_alias="schema"
    )
    required: bool = False
    default: Any = None
    secret: bool = False

    @property
    def schema(self) -> dict[str, Any]:
        """The documented field name, backed by an alias to avoid shadow warnings."""
        return self.value_schema

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str) -> str:
        if len(value) > MAX_SLUG_LENGTH or not _INPUT_NAME_RE.fullmatch(value):
            raise ValueError(
                "input name must start with a letter and contain only letters, "
                "digits, underscores, or hyphens"
            )
        return value

    @model_validator(mode="after")
    def _secret_default_is_reference_only(self) -> "InputSpec":
        if not self.secret:
            return self
        if _contains_schema_default(self.schema):
            raise ValueError("secret input schemas cannot declare defaults")
        if self.default is None:
            return self
        try:
            self.default = SecretBindingRef.model_validate(self.default)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "secret input defaults must be a SecretBindingRef, never a literal"
            ) from exc
        return self


class BlueprintContent(StrictModel):
    schema_version: Literal[1] = 1
    name: str
    description: str = ""
    workflow: WorkflowSpec
    inputs: list[InputSpec] = Field(default_factory=list)
    default_context: dict[str, Any] = Field(default_factory=dict)
    eval_suites: list[ArtifactRef] = Field(default_factory=list)
    source: Optional[ArtifactRef] = None

    @field_validator("workflow", mode="before")
    @classmethod
    def _workflow_boundary_is_strict(cls, value: Any) -> Any:
        """Reject Blueprint-only workflow drift without changing legacy DSL parsing."""
        if isinstance(value, WorkflowSpec):
            return value
        if not isinstance(value, dict):
            return value
        unknown = set(value) - set(WorkflowSpec.model_fields)
        if unknown:
            raise ValueError(f"unknown workflow fields: {sorted(unknown)}")
        for index, step in enumerate(value.get("steps", [])):
            if not isinstance(step, dict):
                continue
            from metaharness.workflows.dsl import StepSpec

            extra = set(step) - set(StepSpec.model_fields)
            if extra:
                raise ValueError(
                    f"workflow step {index} has unknown fields: {sorted(extra)}"
                )
        return value

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blueprint name cannot be blank")
        return value

    @model_validator(mode="after")
    def _inputs_are_unambiguous_and_secrets_stay_out_of_context(self) -> "BlueprintContent":
        names = [item.name for item in self.inputs]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate input names: {duplicates}")
        secret_names = {item.name for item in self.inputs if item.secret}
        exposed = sorted(secret_names & set(self.default_context))
        if exposed:
            raise ValueError(
                f"secret inputs cannot appear in default_context: {exposed}"
            )
        for step in self.workflow.steps:
            if _contains_context_reference(step.inputs, secret_names):
                raise ValueError(
                    f"workflow step {step.id!r} cannot read a declared secret "
                    "through $context"
                )
            for secret_name in secret_names & set(step.inputs):
                raw = step.inputs[secret_name]
                try:
                    marker = SecretBindingRef.model_validate(raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"workflow step {step.id!r} input {secret_name!r} must be "
                        "an exact SecretBindingRef, never a literal"
                    ) from exc
                step.inputs[secret_name] = marker.model_dump(mode="json")
        return self


class BlueprintDraft(BlueprintContent):
    id: str
    revision: int = Field(ge=1, strict=True)
    base_version: Optional[int] = Field(default=None, ge=1, strict=True)
    owner: str
    created_at: float
    updated_at: float

    @field_validator("id")
    @classmethod
    def _id_is_safe_slug(cls, value: str) -> str:
        return _validate_slug(value)

    @field_validator("owner")
    @classmethod
    def _owner_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("draft owner cannot be blank")
        return value


class BlueprintVersion(BlueprintContent):
    id: str
    version: int = Field(ge=1, strict=True)
    published_at: float

    @field_validator("id")
    @classmethod
    def _id_is_safe_slug(cls, value: str) -> str:
        return _validate_slug(value)

    @property
    def ref(self) -> ArtifactRef:
        return ArtifactRef(id=self.id, version=self.version)


class BlueprintCatalogEntry(StrictModel):
    id: str
    display_name: str
    archived_at: Optional[float] = None
    latest_version: Optional[int] = Field(default=None, ge=1, strict=True)

    @field_validator("id")
    @classmethod
    def _id_is_safe_slug(cls, value: str) -> str:
        return _validate_slug(value)

    @field_validator("display_name")
    @classmethod
    def _display_name_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("display name cannot be blank")
        return value


class BlueprintPublishIntent(StrictModel):
    """Durable transaction record for an idempotent Blueprint publication."""

    id: str
    expected_revision: int = Field(ge=1, strict=True)
    version: BlueprintVersion

    @field_validator("id")
    @classmethod
    def _id_is_safe_slug(cls, value: str) -> str:
        return _validate_slug(value)

    @model_validator(mode="after")
    def _version_belongs_to_intent(self) -> "BlueprintPublishIntent":
        if self.version.id != self.id:
            raise ValueError("publish intent version id does not match intent id")
        return self
