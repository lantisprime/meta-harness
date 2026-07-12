"""Canonical serialization, digests, and secret-safety checks."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from metaharness.blueprints.models import SecretBindingRef
from metaharness.blueprints.secrets import validate_secret_binding_name
from metaharness.portable.models import FileDigest


class PortableIntegrityError(ValueError):
    """A portable artifact failed integrity or safety validation."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_files(files: Mapping[str, bytes]) -> list[FileDigest]:
    return [
        FileDigest(path=path, sha256=sha256_hex(files[path]), size=len(files[path]))
        for path in sorted(files)
    ]


def package_content_digest(
    files: list[FileDigest],
    *,
    blueprint_ref: dict[str, Any],
    eval_refs: list[dict[str, Any]],
    targets: list[str],
    requirements: dict[str, Any],
    generator_version: str,
    schema_version: int = 1,
    deployment_options: dict[str, Any] | None = None,
    deployments: list[dict[str, Any]] | None = None,
    deployment_digest: str | None = None,
) -> str:
    """Digest semantic package content, deliberately excluding generated metadata."""
    document = {
        "blueprint_ref": blueprint_ref,
        "eval_refs": eval_refs,
        "files": [item.model_dump(mode="json") for item in files],
        "generator_version": generator_version,
        "requirements": requirements,
        "schema_version": schema_version,
        "targets": targets,
    }
    # Omit new optional fields entirely for schema-v1 local/launcher packages,
    # preserving their historical content digests and deterministic ZIP bytes.
    if deployment_options is not None:
        document["deployment_options"] = deployment_options
    if deployments:
        document["deployments"] = deployments
    if deployment_digest is not None:
        document["deployment_digest"] = deployment_digest
    return sha256_hex(canonical_json_bytes(document))


_SENSITIVE_KEY = re.compile(
    r"(?:^|[-_])(authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|"
    r"oauth[-_]?token|bearer|password|passwd|private[-_]?key|client[-_]?secret|secret)(?:$|[-_])",
    re.IGNORECASE,
)
_HIGH_CONFIDENCE_VALUE = re.compile(
    r"(?:"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?i:enc1)[:._-][A-Za-z0-9+/=_-]{8,}|"
    r"(?:gh[opusr]_|github_pat_|sk-(?:live-|test-)?|xox[baprs]-|ya29\.)[A-Za-z0-9._-]{8,}"
    r")"
)
_MASKED_VALUE = re.compile(
    r"^(?:<redacted>|\[redacted\]|redacted|masked|\*{3,}|x{4,}|•{3,}|"
    r"(?:(?i:bearer)\s+)?(?:sk-|gh[opusr]_|xox[baprs]-)?[*x•]{3,})$",
    re.IGNORECASE,
)
_AUTH_HOME = re.compile(
    r"^(?:/[A-Za-z0-9._~ -]+)+/(?:\.codex|\.claude|\.pi|\.config/(?:opencode|gh))(?:/|$)|"
    r"^[A-Za-z]:[\\/].*[\\/](?:\.codex|\.claude|\.pi|opencode)[\\/]?",
    re.IGNORECASE,
)


def _is_binding(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"binding"}:
        return False
    try:
        SecretBindingRef.model_validate(value)
    except (TypeError, ValueError):
        return False
    return True


def assert_logical_binding_name_safe(binding: str, *, location: str) -> None:
    """Reject credential material disguised as a logical binding identifier."""
    try:
        validate_secret_binding_name(binding)
    except ValueError as exc:
        raise PortableIntegrityError(f"credential material at {location}") from exc
    stripped = binding.strip()
    if _HIGH_CONFIDENCE_VALUE.search(stripped) or _MASKED_VALUE.fullmatch(stripped):
        raise PortableIntegrityError(f"credential material at {location}")
    if _AUTH_HOME.search(stripped.replace("~", "/home/user", 1)):
        raise PortableIntegrityError(f"absolute authentication-home path at {location}")


def assert_secret_safe(value: Any, *, location: str = "harness") -> None:
    """Reject credential material while allowing ordinary high-entropy prose.

    Sensitive field names are rejected only when they directly hold a scalar
    value. JSON Schema declarations and exact logical ``SecretBindingRef``
    objects remain valid.
    """
    if isinstance(value, dict):
        if _is_binding(value):
            assert_logical_binding_name_safe(
                value["binding"], location=f"{location}.binding"
            )
            return
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if _SENSITIVE_KEY.search(str(key)) and isinstance(child, str):
                if child:
                    raise PortableIntegrityError(
                        f"literal secret-like value at {child_location}"
                    )
            assert_secret_safe(child, location=child_location)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            assert_secret_safe(child, location=f"{location}[{index}]")
        return
    if not isinstance(value, str):
        return
    stripped = value.strip()
    if _HIGH_CONFIDENCE_VALUE.search(stripped):
        raise PortableIntegrityError(f"credential material at {location}")
    if _MASKED_VALUE.fullmatch(stripped):
        raise PortableIntegrityError(f"masked secret placeholder at {location}")
    if _AUTH_HOME.search(stripped.replace("~", "/home/user", 1)):
        raise PortableIntegrityError(f"absolute authentication-home path at {location}")


def assert_reference_values_safe(value: Any, *, location: str) -> None:
    """Scan provider-reference VALUES without treating names like `secret_refs` as leaks."""
    if isinstance(value, dict):
        for key, child in value.items():
            assert_reference_values_safe(child, location=f"{location}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            assert_reference_values_safe(child, location=f"{location}[{index}]")
        return
    if not isinstance(value, str):
        return
    stripped = value.strip()
    if _HIGH_CONFIDENCE_VALUE.search(stripped):
        raise PortableIntegrityError(f"credential material at {location}")
    if _MASKED_VALUE.fullmatch(stripped):
        raise PortableIntegrityError(f"masked secret placeholder at {location}")
    if _AUTH_HOME.search(stripped.replace("~", "/home/user", 1)):
        raise PortableIntegrityError(f"absolute authentication-home path at {location}")


_SENSITIVE_INPUT_NAMES = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "oauthtoken",
    "password",
    "passwd",
    "privatekey",
    "clientsecret",
    "authorization",
    "bearer",
    "bearertoken",
    "authtoken",
    "secret",
    "credential",
    "credentials",
}


def assert_blueprint_input_defaults_safe(blueprint: Any) -> None:
    """Reject scalar defaults for inputs whose names denote credentials.

    This is semantic rather than key-recursive: an ``InputSpec`` stores its
    sensitive name under ``name`` and its value under ``default``. A false
    ``secret`` flag therefore cannot bypass portable-package safety.
    """
    for input_spec in blueprint.inputs:
        normalized = re.sub(r"[-_]", "", input_spec.name).casefold()
        if normalized not in _SENSITIVE_INPUT_NAMES or input_spec.default is None:
            continue
        if _is_binding(input_spec.default):
            assert_logical_binding_name_safe(
                input_spec.default["binding"],
                location=f"input {input_spec.name}.default.binding",
            )
            continue
        if isinstance(input_spec.default, SecretBindingRef):
            assert_logical_binding_name_safe(
                input_spec.default.binding,
                location=f"input {input_spec.name}.default.binding",
            )
            continue
        raise PortableIntegrityError(
            f"sensitive input {input_spec.name!r} has a literal default; "
            "an exact SecretBindingRef is required"
        )
