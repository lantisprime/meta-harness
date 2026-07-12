"""Machine-readable CLI operations for portable Harness Blueprints."""
from __future__ import annotations

import ctypes
import errno
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from metaharness.blueprints.models import ArtifactRef, BlueprintDraft, BlueprintVersion
from metaharness.portable.archive import (
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_MAX_TOTAL_SIZE,
    read_safe_zip,
    write_deterministic_zip,
)
from metaharness.portable.builder import build_portable_package
from metaharness.portable.integrity import (
    PortableIntegrityError,
    assert_blueprint_input_defaults_safe,
    assert_secret_safe,
)
from metaharness.portable.loader import load_portable_package
from metaharness.portable.models import PortableManifest
from metaharness.portable.runtime import (
    PortableRuntimeError,
    build_portable_state,
    journal_path_for,
    shim_workers_enabled,
    validate_run_id,
)


_CREDENTIAL_TOKEN = re.compile(
    r"(?:gh[opusr]_|github_pat_|sk-(?:live-|test-)?|xox[baprs]-|ya29\.)"
    r"[A-Za-z0-9._-]+",
    re.IGNORECASE,
)
_CREDENTIAL_NAME = re.compile(
    r"(?:authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|"
    r"oauth[-_]?token|bearer(?:[-_]?token)?|password|passwd|private[-_]?key|"
    r"client[-_]?secret|credentials?)",
    re.IGNORECASE,
)
MAX_JSON_NESTING = 100


class PortableCLIError(ValueError):
    """A user-supplied CLI artifact failed validation."""

    def __init__(self, message: str, *, details: list[dict[str, Any]] | None = None):
        super().__init__(_redact_text(message))
        self.details = details or []


@dataclass(frozen=True)
class BlueprintInput:
    kind: Literal["blueprint-version", "blueprint-draft", "portable-package"]
    source: Literal["file", "directory", "zip"]
    blueprint: BlueprintVersion | BlueprintDraft
    eval_refs: tuple[ArtifactRef, ...]
    manifest: PortableManifest | None = None


def _redact_segment(value: object) -> str:
    text = str(value)
    if _CREDENTIAL_TOKEN.search(text) or _CREDENTIAL_NAME.search(text):
        return "<redacted>"
    return text


def _redact_text(value: str) -> str:
    text = _CREDENTIAL_TOKEN.sub("<redacted>", value)
    return re.sub(
        r"(?i)(?<![A-Za-z0-9])(?:authorization|api[-_]?key|access[-_]?token|"
        r"refresh[-_]?token|oauth[-_]?token|bearer[-_]?token|password|passwd|"
        r"private[-_]?key|client[-_]?secret|credentials?)(?![A-Za-z0-9])",
        "<redacted>",
        text,
    )


def _display_path(path: Path | str) -> str:
    parts = Path(path).parts
    return str(Path(*(_redact_segment(part) for part in parts)))


def _os_diagnostic(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _safe_validation_details(exc: ValidationError) -> list[dict[str, Any]]:
    """Expose locations and messages without echoing potentially secret inputs."""
    return [
        {
            "location": [_redact_segment(part) for part in error["loc"]],
            "message": _redact_text(error["msg"]),
        }
        for error in exc.errors(include_input=False, include_url=False)
    ]


def _assert_json_nesting(value: Any, *, maximum: int = MAX_JSON_NESTING) -> None:
    """Bound recursive model/serialization work using an iterative walk."""
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        child, depth = stack.pop()
        if depth > maximum:
            raise PortableCLIError(
                f"input JSON nesting exceeds the maximum depth of {maximum}"
            )
        if isinstance(child, dict):
            stack.extend((nested, depth + 1) for nested in child.values())
        elif isinstance(child, list):
            stack.extend((nested, depth + 1) for nested in child)


def _bounded_fd_read(fd: int, *, label: str) -> bytes:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise PortableCLIError(f"{label} is not a regular file")
    if before.st_size > DEFAULT_MAX_FILE_SIZE:
        raise PortableCLIError(f"{label} exceeds the size limit")
    chunks: list[bytes] = []
    remaining = DEFAULT_MAX_FILE_SIZE + 1
    while remaining:
        chunk = os.read(fd, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > DEFAULT_MAX_FILE_SIZE:
        raise PortableCLIError(f"{label} exceeds the size limit")
    after = os.fstat(fd)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or len(payload) != before.st_size:
        raise PortableCLIError(f"{label} changed while it was being read")
    return payload


def _read_regular_file(path: Path) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PortableCLIError(f"cannot open input: {_os_diagnostic(exc)}") from exc
    try:
        return _bounded_fd_read(fd, label="input file")
    finally:
        os.close(fd)


def _walk_error(exc: OSError) -> None:
    raise PortableCLIError(
        f"cannot traverse package directory: {_os_diagnostic(exc)}"
    ) from exc


def _read_package_directory(path: Path) -> bytes:
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(path, root_flags)
    except OSError as exc:
        raise PortableCLIError(
            f"cannot open package directory: {_os_diagnostic(exc)}"
        ) from exc

    files: dict[str, bytes] = {}
    total = 0
    try:
        for directory, names, filenames, directory_fd in os.fwalk(
            ".", topdown=True, follow_symlinks=False, dir_fd=root_fd, onerror=_walk_error
        ):
            prefix = "" if directory == "." else directory.removeprefix("./") + "/"
            for name in names:
                relative = f"{prefix}{name}"
                metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    raise PortableCLIError(
                        "package directory contains a symbolic link: "
                        f"{_display_path(relative)}"
                    )
                if not stat.S_ISDIR(metadata.st_mode):
                    raise PortableCLIError(
                        "package directory contains a non-directory entry: "
                        f"{_display_path(relative)}"
                    )
            for name in filenames:
                relative = f"{prefix}{name}"
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISLNK(before.st_mode):
                    raise PortableCLIError(
                        "package directory contains a symbolic link: "
                        f"{_display_path(relative)}"
                    )
                if not stat.S_ISREG(before.st_mode):
                    raise PortableCLIError(
                        "package directory contains a non-regular file: "
                        f"{_display_path(relative)}"
                    )
                if before.st_size > DEFAULT_MAX_FILE_SIZE:
                    raise PortableCLIError(
                        f"package file {_display_path(relative)} exceeds the size limit"
                    )
                if len(files) >= DEFAULT_MAX_FILES:
                    raise PortableCLIError("package directory contains too many files")
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                )
                try:
                    fd = os.open(name, flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise PortableCLIError(
                        f"cannot open package file {_display_path(relative)}: "
                        f"{_os_diagnostic(exc)}"
                    ) from exc
                try:
                    opened = os.fstat(fd)
                    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                        raise PortableCLIError(
                            f"package file changed before it was opened: {_display_path(relative)}"
                        )
                    data = _bounded_fd_read(
                        fd, label=f"package file {_display_path(relative)}"
                    )
                finally:
                    os.close(fd)
                total += len(data)
                if total > DEFAULT_MAX_TOTAL_SIZE:
                    raise PortableCLIError("package directory exceeds the total size limit")
                files[relative] = data
    except OSError as exc:
        raise PortableCLIError(
            f"cannot inspect package directory: {_os_diagnostic(exc)}"
        ) from exc
    finally:
        os.close(root_fd)
    if "manifest.json" not in files:
        raise PortableCLIError("portable package directory is missing manifest.json")
    return write_deterministic_zip(files)


def _parse_blueprint_document(payload: bytes, *, allow_draft: bool) -> BlueprintInput:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise PortableCLIError(
                    f"input contains duplicate JSON key {_redact_segment(key)!r}"
                )
            value[key] = child
        return value

    def reject_constant(value: str) -> None:
        raise PortableCLIError(f"input contains non-finite JSON number {value}")

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except PortableCLIError:
        raise
    except RecursionError as exc:
        raise PortableCLIError("input JSON nesting is too deep") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortableCLIError("input is not valid UTF-8 JSON") from exc
    _assert_json_nesting(document)
    try:
        version = BlueprintVersion.model_validate(document)
    except ValidationError as version_error:
        try:
            draft = BlueprintDraft.model_validate(document)
        except ValidationError:
            raise PortableCLIError(
                "input does not match the strict BlueprintVersion schema",
                details=_safe_validation_details(version_error),
            ) from version_error
        except (RecursionError, ValueError) as exc:
            raise PortableCLIError("blueprint validation exceeded safe limits") from exc
        if not allow_draft:
            raise PortableCLIError("raw BlueprintDraft input requires --allow-draft")
        _assert_portable_safety(draft)
        return BlueprintInput(
            kind="blueprint-draft",
            source="file",
            blueprint=draft,
            eval_refs=tuple(draft.eval_suites),
        )
    except (RecursionError, ValueError) as exc:
        raise PortableCLIError("blueprint validation exceeded safe limits") from exc
    _assert_portable_safety(version)
    return BlueprintInput(
        kind="blueprint-version",
        source="file",
        blueprint=version,
        eval_refs=tuple(version.eval_suites),
    )


def _assert_portable_safety(blueprint: BlueprintVersion | BlueprintDraft) -> None:
    try:
        assert_blueprint_input_defaults_safe(blueprint)
        assert_secret_safe(blueprint.model_dump(mode="json"))
    except PortableIntegrityError as exc:
        raise PortableCLIError(str(exc)) from exc
    except (RecursionError, ValueError) as exc:
        raise PortableCLIError("blueprint cannot be safely serialized") from exc


def load_blueprint_input(path: Path, *, allow_draft: bool = False) -> BlueprintInput:
    """Load one exact Blueprint file or integrity-checked portable package."""
    if path.is_dir():
        try:
            loaded = load_portable_package(_read_package_directory(path))
        except (PortableIntegrityError, ValidationError, ValueError, RecursionError) as exc:
            if isinstance(exc, PortableCLIError):
                raise
            if isinstance(exc, RecursionError):
                raise PortableCLIError("portable package JSON nesting is too deep") from exc
            raise PortableCLIError(str(exc)) from exc
        return BlueprintInput(
            kind="portable-package",
            source="directory",
            blueprint=loaded.blueprint,
            eval_refs=tuple(loaded.manifest.eval_refs),
            manifest=loaded.manifest,
        )

    payload = _read_regular_file(path)
    if zipfile.is_zipfile(io.BytesIO(payload)):
        try:
            loaded = load_portable_package(payload)
        except (PortableIntegrityError, ValidationError, ValueError, RecursionError) as exc:
            if isinstance(exc, RecursionError):
                raise PortableCLIError("portable package JSON nesting is too deep") from exc
            raise PortableCLIError(str(exc)) from exc
        return BlueprintInput(
            kind="portable-package",
            source="zip",
            blueprint=loaded.blueprint,
            eval_refs=tuple(loaded.manifest.eval_refs),
            manifest=loaded.manifest,
        )
    return _parse_blueprint_document(payload, allow_draft=allow_draft)


def validation_report(path: Path, *, allow_draft: bool = False) -> dict[str, Any]:
    loaded = load_blueprint_input(path, allow_draft=allow_draft)
    artifact: dict[str, Any] = {"id": loaded.blueprint.id}
    if isinstance(loaded.blueprint, BlueprintVersion):
        artifact["version"] = loaded.blueprint.version
    else:
        artifact["revision"] = loaded.blueprint.revision
    return {
        "artifact": artifact,
        "kind": loaded.kind,
        "source": loaded.source,
        "valid": True,
    }


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without ever replacing a destination."""
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    result: int
    if sys.platform.startswith("linux"):
        renameat2 = getattr(library, "renameat2", None)
        if renameat2 is None:
            raise PortableCLIError(
                "atomic no-replace directory publication is unavailable"
            )
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100, source_bytes, -100, destination_bytes, 1  # AT_FDCWD, RENAME_NOREPLACE
        )
    elif sys.platform == "darwin":
        renamex = getattr(library, "renamex_np", None)
        if renamex is not None:
            renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            renamex.restype = ctypes.c_int
            result = renamex(source_bytes, destination_bytes, 0x00000004)
        else:
            renameatx = getattr(library, "renameatx_np", None)
            if renameatx is None:
                raise PortableCLIError(
                    "atomic no-replace directory publication is unavailable"
                )
            renameatx.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameatx.restype = ctypes.c_int
            result = renameatx(
                -2, source_bytes, -2, destination_bytes, 0x00000004
            )  # AT_FDCWD, RENAME_EXCL
    else:
        raise PortableCLIError(
            "atomic no-replace directory publication is unavailable"
        )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise PortableCLIError("directory output already exists")
    if error in {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL}:
        raise PortableCLIError(
            "atomic no-replace directory publication is unavailable"
        )
    raise OSError(error, os.strerror(error))


def _reject_output_alias(source: Path, output: Path) -> None:
    if os.path.abspath(source) == os.path.abspath(output):
        raise PortableCLIError("output must not alias the input artifact")
    if _path_exists(output):
        try:
            if os.path.samefile(source, output):
                raise PortableCLIError("output must not alias the input artifact")
        except OSError as exc:
            raise PortableCLIError(
                f"cannot compare input and output: {_os_diagnostic(exc)}"
            ) from exc


def _atomic_write(path: Path, payload: bytes, *, force: bool) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise PortableCLIError(
            f"output directory does not exist: {_display_path(parent)}"
        )
    if _path_exists(path):
        metadata = path.lstat()
        if not force:
            raise PortableCLIError("output already exists; pass --force to replace it")
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PortableCLIError("--force can replace only an existing regular ZIP file")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".metaharness-package.",
            suffix=".tmp",
            dir=parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
            temporary.unlink()
        temporary = None
        _fsync_directory(parent)
    except OSError as exc:
        raise PortableCLIError(f"cannot write output: {_os_diagnostic(exc)}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_write_directory(path: Path, payload: bytes) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise PortableCLIError(
            f"output directory does not exist: {_display_path(parent)}"
        )
    if _path_exists(path):
        raise PortableCLIError("directory output already exists")
    files = read_safe_zip(payload)
    staged = Path(
        tempfile.mkdtemp(
            prefix=".metaharness-package.", suffix=".tmp", dir=parent
        )
    )
    published = False
    try:
        for relative, data in sorted(files.items()):
            destination = staged.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("short write")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        for _, _, _, directory_fd in os.fwalk(staged, topdown=False):
            os.fsync(directory_fd)
        _rename_directory_no_replace(staged, path)
        published = True
        _fsync_directory(parent)
    except OSError as exc:
        raise PortableCLIError(f"cannot write output: {_os_diagnostic(exc)}") from exc
    finally:
        if not published:
            shutil.rmtree(staged, ignore_errors=True)


def package_blueprint(
    path: Path,
    *,
    targets: list[str],
    output: Path,
    output_format: Literal["zip", "directory"] = "zip",
    force: bool = False,
) -> dict[str, Any]:
    if force and output_format != "zip":
        raise PortableCLIError("--force is supported only for ZIP output")
    loaded = load_blueprint_input(path, allow_draft=True)
    if not isinstance(loaded.blueprint, BlueprintVersion):
        raise PortableCLIError(
            "packaging requires an exact published BlueprintVersion; drafts must be published first"
        )
    try:
        payload = build_portable_package(
            loaded.blueprint, targets=targets, eval_refs=loaded.eval_refs
        )
        verified = load_portable_package(payload)
    except (PortableIntegrityError, ValidationError, ValueError) as exc:
        raise PortableCLIError(str(exc)) from exc
    _reject_output_alias(path, output)
    if output_format == "directory":
        _atomic_write_directory(output, payload)
    else:
        _atomic_write(output, payload, force=force)
    return {
        "blueprint_ref": verified.blueprint.ref.model_dump(mode="json"),
        "bytes": len(payload),
        "content_digest": verified.manifest.content_digest,
        "output": _display_path(output),
        "output_format": output_format,
        "targets": list(verified.manifest.targets),
        "valid": True,
    }


# ---------------------------------------------------------------------------
# Run-time commands
# ---------------------------------------------------------------------------

_RUN_MAX_CONTEXT_BYTES = 16 * 1024 * 1024


def _read_context(source: str) -> dict[str, Any]:
    if source == "-":
        data = sys.stdin.buffer.read(_RUN_MAX_CONTEXT_BYTES + 1)
    else:
        path = Path(source)
        if not path.is_file():
            raise PortableCLIError(f"context file not found: {_display_path(source)}")
        data = path.read_bytes()
    if len(data) > _RUN_MAX_CONTEXT_BYTES:
        raise PortableCLIError("context file exceeds the size limit")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortableCLIError("context file is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PortableCLIError("context must be a JSON object")
    return value


def _print_jsonl_events(events: list[Any], *, printed: set[int]) -> set[int]:
    for event in events:
        seq = getattr(event, "seq", None)
        if seq in printed:
            continue
        print(
            json.dumps(
                event.model_dump(mode="json"),
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        printed.add(seq)
    return printed


def _run_sidecar_path(run_id: str, journal_dir: Path) -> Path:
    validate_run_id(run_id)
    sidecar_dir = journal_dir / "runs"
    return sidecar_dir / f"{run_id}.json"


def _record_run_workspace(run_id: str, journal_dir: Path, workspace: Path) -> None:
    path = _run_sidecar_path(run_id, journal_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        payload = json.dumps({"workspace_root": str(workspace.resolve())}).encode()
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _load_run_workspace(run_id: str, journal_dir: Path) -> Path:
    path = _run_sidecar_path(run_id, journal_dir)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Path(data["workspace_root"])
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            raise PortableCLIError(f"cannot read run sidecar: {exc}") from exc
    raise PortableCLIError(
        "workspace is not recorded for this run; pass --workspace"
    )


def _load_run_engine(
    run_id: str,
    journal_dir: Path,
    workspace: Path | None,
    *,
    shim: bool = False,
) -> tuple[Any, Any]:
    import asyncio

    from metaharness.portable.runtime import refresh_portable_capabilities

    journal_path = journal_path_for(run_id, journal_dir)
    if not journal_path.is_file():
        raise PortableCLIError(f"run journal not found: {_display_path(journal_path)}")
    if workspace is None:
        workspace = _load_run_workspace(run_id, journal_dir)
    try:
        state = build_portable_state(
            journal_dir=journal_dir,
            workspace_root=workspace,
            shim=shim_workers_enabled(shim),
        )
    except PortableRuntimeError as exc:
        raise PortableCLIError(str(exc)) from exc
    asyncio.run(refresh_portable_capabilities(state))
    run_state = state.engine.adopt(journal_path)
    return state.engine, run_state


def run_blueprint(
    artifact: Path,
    *,
    context_file: str,
    workspace: Path,
    journal_dir: Path,
    approval: str,
    shim: bool = False,
) -> dict[str, Any]:
    import asyncio

    from metaharness.portable.runtime import prepare_portable_blueprint_run

    loaded = load_blueprint_input(artifact, allow_draft=False)
    blueprint = loaded.blueprint
    if not isinstance(blueprint, BlueprintVersion):
        raise PortableCLIError("run requires an exact published BlueprintVersion")

    request_context = _read_context(context_file)
    try:
        state = build_portable_state(
            journal_dir=journal_dir,
            workspace_root=workspace,
            shim=shim_workers_enabled(shim),
        )
    except PortableRuntimeError as exc:
        raise PortableCLIError(str(exc)) from exc
    readiness, resolved_workflow = asyncio.run(
        prepare_portable_blueprint_run(state, blueprint, request_context)
    )
    if not readiness.ready:
        return {
            "ready": False,
            "issues": [
                issue.model_dump(mode="json") for issue in readiness.issues
            ],
            "exit_code": 2,
        }

    context = dict(readiness.normalized_context)
    assert resolved_workflow is not None
    run_state = state.engine.start(
        resolved_workflow,
        context=context,
        blueprint_ref=blueprint.ref.model_dump(mode="json"),
        blueprint_snapshot=blueprint.model_dump(mode="json"),
    )
    _record_run_workspace(run_state.run_id, journal_dir, workspace)

    printed: set[int] = set()
    printed = _print_jsonl_events(
        state.engine.journal(run_state.run_id).events(), printed=printed
    )

    advanced = asyncio.run(state.engine.advance(run_state.run_id))
    printed = _print_jsonl_events(
        state.engine.journal(run_state.run_id).events(), printed=printed
    )

    status = advanced.status.value
    if status == "awaiting_approval":
        return {
            "run_id": run_state.run_id,
            "status": status,
            "awaiting_step": advanced.awaiting,
            "exit_code": 20,
        }
    if status == "failed":
        return {
            "run_id": run_state.run_id,
            "status": status,
            "failed_step": advanced.failed_step,
            "exit_code": 1,
        }
    return {
        "run_id": run_state.run_id,
        "status": status,
        "exit_code": 0,
    }


def inspect_run(
    run_id: str,
    *,
    journal_dir: Path,
    workspace: Path | None = None,
    shim: bool = False,
) -> dict[str, Any]:
    import asyncio

    engine, run_state = _load_run_engine(run_id, journal_dir, workspace, shim=shim)
    _, fresh, events, entries = asyncio.run(engine.inspect(run_id))
    return {
        "run_id": run_id,
        "status": fresh.status.value,
        "awaiting_step": fresh.awaiting,
        "failed_step": fresh.failed_step,
        "events": [e.model_dump(mode="json") for e in events],
        "journal": [e.model_dump(mode="json") for e in entries],
    }


def approve_run(
    run_id: str,
    step_id: str,
    *,
    journal_dir: Path,
    workspace: Path | None = None,
    shim: bool = False,
) -> dict[str, Any]:
    engine, _ = _load_run_engine(run_id, journal_dir, workspace, shim=shim)
    import asyncio

    asyncio.run(engine.resolve_hitl(run_id, step_id, approved=True))
    return {"run_id": run_id, "step_id": step_id, "approved": True}


def reject_run(
    run_id: str,
    step_id: str,
    *,
    journal_dir: Path,
    workspace: Path | None = None,
    shim: bool = False,
) -> dict[str, Any]:
    engine, _ = _load_run_engine(run_id, journal_dir, workspace, shim=shim)
    import asyncio

    asyncio.run(engine.resolve_hitl(run_id, step_id, approved=False))
    return {"run_id": run_id, "step_id": step_id, "approved": False}


def resume_run(
    run_id: str,
    *,
    journal_dir: Path,
    workspace: Path | None = None,
    shim: bool = False,
) -> dict[str, Any]:
    import asyncio

    engine, _ = _load_run_engine(run_id, journal_dir, workspace, shim=shim)
    advanced = asyncio.run(engine.advance(run_id))
    printed: set[int] = set()
    _print_jsonl_events(engine.journal(run_id).events(), printed=printed)

    status = advanced.status.value
    if status == "awaiting_approval":
        return {
            "run_id": run_id,
            "status": status,
            "awaiting_step": advanced.awaiting,
            "exit_code": 20,
        }
    if status == "failed":
        return {
            "run_id": run_id,
            "status": status,
            "failed_step": advanced.failed_step,
            "exit_code": 1,
        }
    return {"run_id": run_id, "status": status, "exit_code": 0}
