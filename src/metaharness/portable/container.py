"""Deterministic OCI build context for a packaged Harness Blueprint."""
from __future__ import annotations

import json
import re


_IMAGE_COMPONENT = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_IMAGE_REFERENCE = re.compile(
    rf"^(?P<registry>[a-z0-9.-]+|localhost)(?::(?P<port>[0-9]{{1,5}}))?/"
    rf"(?P<repository>{_IMAGE_COMPONENT}(?:/{_IMAGE_COMPONENT})*)"
    r"@sha256:(?P<digest>[0-9a-f]{64})$"
)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
WORKSPACE_PATH = "/var/lib/metaharness/workspace"
JOURNAL_PATH = "/var/lib/metaharness/journal"


def require_digest_image(image: str) -> str:
    """Reject mutable tags and accept only a caller-supplied OCI digest."""
    match = _IMAGE_REFERENCE.fullmatch(image) if len(image) <= 255 else None
    if match is None:
        raise ValueError("image must be pinned as IMAGE@sha256:<64 lowercase hex>")
    registry = match.group("registry")
    port = match.group("port")
    labels = registry.split(".")
    registry_is_explicit = registry == "localhost" or "." in registry or port is not None
    if (
        not registry_is_explicit
        or any(not _DNS_LABEL.fullmatch(label) for label in labels)
        or (port is not None and not 1 <= int(port) <= 65535)
    ):
        raise ValueError("image must be pinned as IMAGE@sha256:<64 lowercase hex>")
    return image


def build_oci_context(runtime_image: str) -> dict[str, bytes]:
    """Return a minimal build context with no install or blueprint-derived commands."""
    image = require_digest_image(runtime_image)
    dockerfile = f"""FROM {image}
COPY . /opt/metaharness/package/
COPY --chown=65532:65532 container/workspace/ {WORKSPACE_PATH}/
COPY --chown=65532:65532 container/journal/ {JOURNAL_PATH}/
USER 65532:65532
WORKDIR {WORKSPACE_PATH}
VOLUME [\"{WORKSPACE_PATH}\", \"{JOURNAL_PATH}\"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD [\"metaharness\", \"healthcheck\", \"--url\", \"http://127.0.0.1:8000/health\"]
ENTRYPOINT [\"metaharness\", \"serve\", \"--package\", \"/opt/metaharness/package\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]
"""
    contract = {
        "schema_version": 1,
        "runtime_image": image,
        "user": "65532:65532",
        "entrypoint": [
            "metaharness", "serve", "--package", "/opt/metaharness/package",
            "--host", "0.0.0.0", "--port", "8000",
        ],
        "healthcheck": [
            "metaharness", "healthcheck", "--url", "http://127.0.0.1:8000/health",
        ],
        "port": 8000,
        "writable_paths": [JOURNAL_PATH, WORKSPACE_PATH],
        "runtime_readiness": {
            "status": "blocked",
            "deployable": False,
            "reason": "deployment environment must supply an enabled agent configuration",
        },
    }
    return {
        "container/Dockerfile": dockerfile.encode(),
        "container/container.json": (
            json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
        "container/workspace/.keep": b"",
        "container/journal/.keep": b"",
    }
