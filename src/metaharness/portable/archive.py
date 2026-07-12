"""Deterministic ZIP production and hostile-archive screening."""
from __future__ import annotations

import io
import os
import stat
import time
import zipfile
from collections.abc import Mapping

from metaharness.portable.integrity import PortableIntegrityError


DEFAULT_MAX_FILES = 256
DEFAULT_MAX_FILE_SIZE = 16 * 1024 * 1024
DEFAULT_MAX_TOTAL_SIZE = 64 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 200


def _zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    # ZIP timestamps cannot precede 1980 and have two-second resolution.
    return time.gmtime(max(epoch, 315532800))[:6]


def source_date_epoch(value: int | None = None) -> int:
    if value is not None:
        if value < 0:
            raise ValueError("SOURCE_DATE_EPOCH cannot be negative")
        return value
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw is None:
        return 0
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError("SOURCE_DATE_EPOCH must be an integer") from exc
    if parsed < 0:
        raise ValueError("SOURCE_DATE_EPOCH cannot be negative")
    return parsed


def write_deterministic_zip(
    files: Mapping[str, bytes], *, epoch: int | None = None
) -> bytes:
    output = io.BytesIO()
    timestamp = _zip_datetime(source_date_epoch(epoch))
    folded: set[str] = set()
    for path in files:
        _validate_member_name(path)
        casefolded = path.casefold()
        if casefolded in folded:
            raise PortableIntegrityError(f"case-colliding ZIP member: {path!r}")
        folded.add(casefolded)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=timestamp)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, files[path], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def _validate_member_name(name: str) -> None:
    if not name or "\x00" in name or "\\" in name:
        raise PortableIntegrityError("ZIP member has an invalid path")
    if name.startswith("/") or (len(name) >= 2 and name[1] == ":"):
        raise PortableIntegrityError(f"absolute ZIP member path: {name!r}")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PortableIntegrityError(f"non-normalized ZIP member path: {name!r}")


def read_safe_zip(
    payload: bytes,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise PortableIntegrityError("invalid ZIP archive") from exc
    with archive:
        members = archive.infolist()
        if len(members) > max_files:
            raise PortableIntegrityError("ZIP contains too many members")
        names: set[str] = set()
        folded: set[str] = set()
        total = 0
        for info in members:
            _validate_member_name(info.filename)
            if info.filename in names:
                raise PortableIntegrityError(f"duplicate ZIP member: {info.filename!r}")
            casefolded = info.filename.casefold()
            if casefolded in folded:
                raise PortableIntegrityError(f"case-colliding ZIP member: {info.filename!r}")
            names.add(info.filename)
            folded.add(casefolded)
            if info.flag_bits & 0x1:
                raise PortableIntegrityError(f"encrypted ZIP member: {info.filename!r}")
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode):
                raise PortableIntegrityError(f"symlink ZIP member: {info.filename!r}")
            if info.is_dir() or file_type not in (0, stat.S_IFREG):
                raise PortableIntegrityError(f"non-regular ZIP member: {info.filename!r}")
            if info.file_size > max_file_size:
                raise PortableIntegrityError(f"ZIP member exceeds size limit: {info.filename!r}")
            total += info.file_size
            if total > max_total_size:
                raise PortableIntegrityError("ZIP exceeds total size limit")
            if info.file_size and info.compress_size == 0:
                raise PortableIntegrityError("ZIP member has an invalid compressed size")
            if info.compress_size and info.file_size / info.compress_size > max_compression_ratio:
                raise PortableIntegrityError(f"ZIP compression ratio is unsafe: {info.filename!r}")
        files: dict[str, bytes] = {}
        for info in members:
            try:
                with archive.open(info, "r") as member:
                    data = member.read(max_file_size + 1)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
                raise PortableIntegrityError(
                    f"cannot safely read ZIP member: {info.filename!r}"
                ) from exc
            if len(data) != info.file_size or len(data) > max_file_size:
                raise PortableIntegrityError(f"ZIP member size mismatch: {info.filename!r}")
            files[info.filename] = data
        return files
