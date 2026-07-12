from __future__ import annotations

import io
import stat
import zipfile

import pytest

from metaharness.portable.archive import read_safe_zip, write_deterministic_zip
from metaharness.portable.integrity import PortableIntegrityError, assert_secret_safe


def archive_with(entries, *, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for entry in entries:
            if len(entry) == 2:
                archive.writestr(entry[0], entry[1])
            else:
                archive.writestr(entry[0], entry[1], compress_type=entry[2])
    return output.getvalue()


@pytest.mark.parametrize("name", ["/abs", "../escape", "a/../escape", "a\\b", "C:/auth"])
def test_safe_zip_rejects_traversal_absolute_and_backslash_paths(name):
    with pytest.raises(PortableIntegrityError):
        read_safe_zip(archive_with([(name, b"x")]))


@pytest.mark.parametrize("name", ["/abs", "../escape", "a/../escape", "a\\b", "C:/auth"])
def test_deterministic_zip_writer_rejects_unsafe_paths_for_direct_callers(name):
    with pytest.raises(PortableIntegrityError):
        write_deterministic_zip({name: b"x"})


def test_deterministic_zip_writer_rejects_casefold_collisions():
    with pytest.raises(PortableIntegrityError, match="case-colliding"):
        write_deterministic_zip({"File": b"a", "file": b"b"})


def test_safe_zip_rejects_duplicates_and_casefold_collisions():
    with pytest.warns(UserWarning):
        duplicate = archive_with([("same", b"a"), ("same", b"b")])
    with pytest.raises(PortableIntegrityError, match="duplicate"):
        read_safe_zip(duplicate)
    with pytest.raises(PortableIntegrityError, match="case-colliding"):
        read_safe_zip(archive_with([("File", b"a"), ("file", b"b")]))


def test_safe_zip_rejects_symlink_members():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target")
    with pytest.raises(PortableIntegrityError, match="symlink"):
        read_safe_zip(output.getvalue())


def test_safe_zip_enforces_member_total_count_and_ratio_caps():
    with pytest.raises(PortableIntegrityError, match="too many"):
        read_safe_zip(archive_with([("a", b"a"), ("b", b"b")]), max_files=1)
    with pytest.raises(PortableIntegrityError, match="size limit"):
        read_safe_zip(archive_with([("large", b"1234")]), max_file_size=3)
    with pytest.raises(PortableIntegrityError, match="total size"):
        read_safe_zip(archive_with([("a", b"12"), ("b", b"34")]), max_total_size=3)
    bomb = archive_with([("bomb", b"0" * 100_000, zipfile.ZIP_DEFLATED)])
    with pytest.raises(PortableIntegrityError, match="compression ratio"):
        read_safe_zip(bomb, max_compression_ratio=10)


@pytest.mark.parametrize(
    "unsafe",
    [
        {"authorization": "Bearer abcdefghijklmnopqrstuvwxyz"},
        {"api_key": "literal-value"},
        {"value": "gho_abcdefghijklmnopqrstuvwxyz"},
        {"value": "-----BEGIN PRIVATE KEY-----"},
        {"value": "enc1:abcdefghijklmnopqrstuvwxyz"},
        {"value": "<redacted>"},
        {"value": "Bearer ********"},
        {"bearer": "opaque-placeholder"},
        {"value": "/Users/alice/.claude/credentials.json"},
    ],
)
def test_secret_scanner_rejects_high_confidence_material(unsafe):
    with pytest.raises(PortableIntegrityError):
        assert_secret_safe(unsafe)


def test_secret_scanner_allows_binding_schema_and_high_entropy_prose():
    assert_secret_safe({"api_key": {"binding": "service-api-key"}})
    assert_secret_safe({"properties": {"api_key": {"type": "string"}}})
    assert_secret_safe(
        {"description": "A9Sx7kQp1Rm8Vz4Lc2Jn6Hd0Tw5By3Uf is an ordinary opaque prose identifier."}
    )


@pytest.mark.parametrize("credential", [
    "sk-test-abcdefghijk",
    "sk-live-abcdefghijk",
    "xoxb-abcdefghijk",
])
def test_secret_scanner_rejects_credential_material_disguised_as_binding(credential):
    with pytest.raises(PortableIntegrityError, match="credential material"):
        assert_secret_safe({"binding": credential})
