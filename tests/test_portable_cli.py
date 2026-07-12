from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from metaharness import cli
from metaharness.blueprints.models import ArtifactRef, BlueprintVersion
from metaharness.portable import build_portable_package, load_portable_package
from metaharness.portable import cli as portable_cli


def blueprint_document() -> dict:
    return {
        "id": "cli-demo",
        "version": 4,
        "published_at": 123.0,
        "name": "CLI demo",
        "workflow": {"name": "cli-flow", "steps": [{"id": "one", "objective": "Do one."}]},
    }


def draft_document() -> dict:
    value = blueprint_document()
    value.pop("version")
    value.pop("published_at")
    value.update(
        revision=2,
        base_version=1,
        owner="owner",
        created_at=100.0,
        updated_at=101.0,
    )
    return value


def write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def invoke(args: list[str], capsys) -> tuple[int, dict, str]:
    code = 0
    try:
        cli.main(["blueprint", *args])
    except SystemExit as exc:
        code = int(exc.code)
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


def test_validate_published_file_is_json_only_on_stdout(tmp_path, capsys):
    source = write_json(tmp_path / "harness.json", blueprint_document())
    code, report, stderr = invoke(["validate", str(source), "--format", "json"], capsys)
    assert code == 0
    assert report == {
        "artifact": {"id": "cli-demo", "version": 4},
        "kind": "blueprint-version",
        "source": "file",
        "valid": True,
    }
    assert stderr == ""


def test_validate_package_directory_and_zip(tmp_path, capsys):
    blueprint = BlueprintVersion.model_validate(blueprint_document())
    payload = build_portable_package(blueprint, targets=["pi"])
    package_zip = tmp_path / "portable.bundle"
    package_zip.write_bytes(payload)
    package_dir = tmp_path / "package dir"
    package_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(package_dir)

    for source, expected_source in [(package_zip, "zip"), (package_dir, "directory")]:
        code, report, stderr = invoke(["validate", str(source)], capsys)
        assert code == 0
        assert report["kind"] == "portable-package"
        assert report["source"] == expected_source
        assert report["artifact"] == {"id": "cli-demo", "version": 4}
        assert stderr == ""


def test_draft_requires_explicit_flag_and_can_only_be_validated(tmp_path, capsys):
    source = write_json(tmp_path / "draft.json", draft_document())
    code, report, stderr = invoke(["validate", str(source)], capsys)
    assert code == 2 and report["valid"] is False
    assert "--allow-draft" in report["error"] and stderr

    code, report, stderr = invoke(["validate", str(source), "--allow-draft"], capsys)
    assert code == 0 and report["kind"] == "blueprint-draft"
    assert report["artifact"] == {"id": "cli-demo", "revision": 2}
    assert stderr == ""

    output = tmp_path / "must-not-exist.zip"
    code, report, stderr = invoke(
        ["package", str(source), "--target", "local", "--output", str(output)],
        capsys,
    )
    assert code == 2 and "published" in report["error"] and stderr
    assert not output.exists()


def test_invalid_extra_field_has_exit_two_without_echoing_values(tmp_path, capsys):
    value = blueprint_document()
    value["surprise"] = "do-not-echo-this-value"
    source = write_json(tmp_path / "invalid.json", value)
    code, report, stderr = invoke(["validate", str(source)], capsys)
    assert code == 2 and report["valid"] is False
    assert any(detail["location"] == ["surprise"] for detail in report["details"])
    assert "do-not-echo-this-value" not in json.dumps(report)
    assert stderr


def test_validate_rejects_literal_sensitive_default(tmp_path, capsys):
    value = blueprint_document()
    value["inputs"] = [
        {
            "name": "access_token",
            "schema": {"type": "string"},
            "secret": False,
            "default": "not-a-binding",
        }
    ]
    source = write_json(tmp_path / "unsafe.json", value)
    code, report, stderr = invoke(["validate", str(source)], capsys)
    assert code == 2 and "literal default" in report["error"]
    assert "not-a-binding" not in json.dumps(report)
    assert stderr


def test_directory_rejects_surprise_files_and_symlinks(tmp_path, capsys):
    payload = build_portable_package(BlueprintVersion.model_validate(blueprint_document()))
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(package_dir)
    (package_dir / "surprise.txt").write_text("no", encoding="utf-8")
    code, report, _ = invoke(["validate", str(package_dir)], capsys)
    assert code == 2 and "differ from manifest" in report["error"]

    (package_dir / "surprise.txt").unlink()
    try:
        (package_dir / "linked.json").symlink_to(package_dir / "harness.json")
    except OSError:
        pytest.skip("symlinks unavailable")
    code, report, _ = invoke(["validate", str(package_dir)], capsys)
    assert code == 2 and "symbolic link" in report["error"]


def test_package_is_deterministic_and_supports_spaced_output_path(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    source = write_json(tmp_path / "harness.json", blueprint_document())
    first = tmp_path / "first package.zip"
    second = tmp_path / "second package.zip"
    for output in (first, second):
        code, report, stderr = invoke(
            [
                "package", str(source), "--target", "pi", "--target", "local",
                "--output", str(output),
            ],
            capsys,
        )
        assert code == 0 and report["valid"] is True and stderr == ""
        assert report["targets"] == ["local", "pi"]
    assert first.read_bytes() == second.read_bytes()


def test_package_accepts_verified_directory_and_zip_inputs(tmp_path, capsys):
    original = build_portable_package(
        BlueprintVersion.model_validate(blueprint_document()), targets=["pi"]
    )
    package_zip = tmp_path / "input.zip"
    package_zip.write_bytes(original)
    package_dir = tmp_path / "input-directory"
    package_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        archive.extractall(package_dir)

    outputs = []
    for index, source in enumerate((package_zip, package_dir)):
        output = tmp_path / f"rebuilt-{index}.zip"
        code, report, stderr = invoke(
            ["package", str(source), "--target", "local", "--output", str(output)], capsys
        )
        assert code == 0 and report["blueprint_ref"] == {"id": "cli-demo", "version": 4}
        assert stderr == "" and output.is_file()
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]


def test_package_failure_leaves_no_partial_output(tmp_path, capsys):
    source = write_json(tmp_path / "harness.json", blueprint_document())
    output = tmp_path / "never-created.zip"
    code, report, stderr = invoke(
        ["package", str(source), "--target", "not-a-target", "--output", str(output)], capsys
    )
    assert code == 2 and report["valid"] is False and stderr
    assert not output.exists()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("command", ["validate", "package"])
def test_non_finite_json_is_rejected_consistently(tmp_path, capsys, command, constant):
    source = tmp_path / "invalid.json"
    source.write_text(
        json.dumps(blueprint_document())[:-1] + f',"default_context":{{"n":{constant}}}}}',
        encoding="utf-8",
    )
    args = [command, str(source)]
    if command == "package":
        args += ["--target", "local", "--output", str(tmp_path / "out.zip")]
    code, report, stderr = invoke(args, capsys)
    assert code == 2 and "non-finite" in report["error"] and stderr


@pytest.mark.parametrize("command", ["validate", "package"])
def test_duplicate_json_keys_are_rejected_consistently(tmp_path, capsys, command):
    source = tmp_path / "duplicate.json"
    source.write_text(
        json.dumps(blueprint_document())[:-1] + ',"name":"duplicate"}', encoding="utf-8"
    )
    args = [command, str(source)]
    if command == "package":
        args += ["--target", "local", "--output", str(tmp_path / "out.zip")]
    code, report, stderr = invoke(args, capsys)
    assert code == 2 and "duplicate JSON key" in report["error"] and stderr


def test_retarget_preserves_source_manifest_eval_refs(tmp_path, capsys):
    blueprint = BlueprintVersion.model_validate(blueprint_document())
    source = tmp_path / "source.zip"
    source.write_bytes(
        build_portable_package(
            blueprint, eval_refs=[ArtifactRef(id="sealed-suite", version=9)]
        )
    )
    output = tmp_path / "retargeted.zip"
    code, report, stderr = invoke(
        ["package", str(source), "--target", "pi", "--output", str(output)], capsys
    )
    assert code == 0 and report["targets"] == ["pi"] and stderr == ""
    assert load_portable_package(output.read_bytes()).manifest.eval_refs == [
        ArtifactRef(id="sealed-suite", version=9)
    ]


def test_output_refuses_collisions_aliases_and_hardlinks(tmp_path, capsys):
    source = write_json(tmp_path / "harness.json", blueprint_document())
    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"preserve")
    code, report, _ = invoke(
        ["package", str(source), "--target", "local", "--output", str(existing)], capsys
    )
    assert code == 2 and "already exists" in report["error"]
    assert existing.read_bytes() == b"preserve"

    code, report, stderr = invoke(
        [
            "package", str(source), "--target", "local", "--output", str(existing),
            "--force",
        ],
        capsys,
    )
    assert code == 0 and report["output_format"] == "zip" and stderr == ""
    load_portable_package(existing.read_bytes())

    original = source.read_bytes()
    code, report, _ = invoke(
        ["package", str(source), "--target", "local", "--output", str(source), "--force"],
        capsys,
    )
    assert code == 2 and "alias" in report["error"]
    assert source.read_bytes() == original

    hardlink = tmp_path / "hardlink.json"
    os.link(source, hardlink)
    code, report, _ = invoke(
        [
            "package", str(source), "--target", "local", "--output", str(hardlink),
            "--force",
        ],
        capsys,
    )
    assert code == 2 and "alias" in report["error"]


def test_directory_output_roundtrip_and_failure_cleanup(tmp_path, capsys, monkeypatch):
    source = write_json(tmp_path / "harness.json", blueprint_document())
    output = tmp_path / "portable directory"
    code, report, stderr = invoke(
        [
            "package", str(source), "--target", "local", "--output", str(output),
            "--output-format", "directory",
        ],
        capsys,
    )
    assert code == 0 and report["output_format"] == "directory" and stderr == ""
    code, validated, stderr = invoke(["validate", str(output)], capsys)
    assert code == 0 and validated["source"] == "directory" and stderr == ""

    code, report, _ = invoke(
        [
            "package", str(source), "--target", "local", "--output", str(output),
            "--output-format", "directory",
        ],
        capsys,
    )
    assert code == 2 and "already exists" in report["error"]

    forced_directory = tmp_path / "forced-directory"
    code, report, _ = invoke(
        [
            "package", str(source), "--target", "local", "--output",
            str(forced_directory), "--output-format", "directory", "--force",
        ],
        capsys,
    )
    assert code == 2 and "only for ZIP" in report["error"]
    assert not forced_directory.exists()

    native_publish = portable_cli._rename_directory_no_replace
    raced = tmp_path / "raced-directory"

    def inject_destination(staged, destination):
        destination.mkdir()
        (destination / "sentinel.txt").write_text("preserve", encoding="utf-8")
        native_publish(staged, destination)

    with monkeypatch.context() as patch:
        patch.setattr(portable_cli, "_rename_directory_no_replace", inject_destination)
        code, report, stderr = invoke(
            [
                "package", str(source), "--target", "local", "--output", str(raced),
                "--output-format", "directory",
            ],
            capsys,
        )
    assert code == 2 and "already exists" in report["error"] and stderr
    assert (raced / "sentinel.txt").read_text(encoding="utf-8") == "preserve"
    assert not list(tmp_path.glob(".metaharness-package.*.tmp"))

    failed = tmp_path / "unsupported-directory"
    with monkeypatch.context() as patch:
        patch.setattr(
            portable_cli,
            "_rename_directory_no_replace",
            lambda *_: (_ for _ in ()).throw(
                portable_cli.PortableCLIError(
                    "atomic no-replace directory publication is unavailable"
                )
            ),
        )
        code, report, stderr = invoke(
            [
                "package", str(source), "--target", "local", "--output", str(failed),
                "--output-format", "directory",
            ],
            capsys,
        )
    assert code == 2 and "unavailable" in report["error"] and stderr
    assert not failed.exists()
    assert not list(tmp_path.glob(".metaharness-package.*.tmp"))


def test_directory_read_rejects_same_size_symlink_swap(tmp_path, monkeypatch):
    payload = build_portable_package(BlueprintVersion.model_validate(blueprint_document()))
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(package_dir)
    victim = package_dir / "harness.json"
    replacement = tmp_path / "same-size.json"
    replacement.write_bytes(b"x" * victim.stat().st_size)
    real_open = portable_cli.os.open
    swapped = False

    def swap_then_open(path, flags, *args, dir_fd=None, **kwargs):
        nonlocal swapped
        if path == "harness.json" and dir_fd is not None and not swapped:
            swapped = True
            victim.unlink()
            victim.symlink_to(replacement)
        return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(portable_cli.os, "open", swap_then_open)
    with pytest.raises(portable_cli.PortableCLIError):
        portable_cli.load_blueprint_input(package_dir)


def test_directory_read_rejects_oversized_swap(tmp_path, monkeypatch):
    payload = build_portable_package(BlueprintVersion.model_validate(blueprint_document()))
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(package_dir)
    victim = package_dir / "harness.json"
    replacement = tmp_path / "oversized.json"
    replacement.write_bytes(b"x" * (portable_cli.DEFAULT_MAX_FILE_SIZE + 1))
    real_open = portable_cli.os.open
    swapped = False

    def swap_then_open(path, flags, *args, dir_fd=None, **kwargs):
        nonlocal swapped
        if path == "harness.json" and dir_fd is not None and not swapped:
            swapped = True
            replacement.replace(victim)
        return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(portable_cli.os, "open", swap_then_open)
    with pytest.raises(portable_cli.PortableCLIError):
        portable_cli.load_blueprint_input(package_dir)


def test_credentials_are_redacted_from_locations_and_filenames(tmp_path, capsys):
    token = "gho_abcdefghijklmnopqrstuvwxyz"
    value = blueprint_document()
    value[token] = "value"
    source = write_json(tmp_path / "invalid.json", value)
    code, report, stderr = invoke(["validate", str(source)], capsys)
    rendered = json.dumps(report) + stderr
    assert code == 2 and token not in rendered and "<redacted>" in rendered

    payload = build_portable_package(BlueprintVersion.model_validate(blueprint_document()))
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(package_dir)
    (package_dir / f"{token}.txt").write_text("x", encoding="utf-8")
    code, report, stderr = invoke(["validate", str(package_dir)], capsys)
    rendered = json.dumps(report) + stderr
    assert code == 2 and token not in rendered and "<redacted>" in rendered


@pytest.mark.parametrize("command", ["validate", "package"])
def test_excessive_json_nesting_fails_without_traceback_or_output(
    tmp_path, capsys, command
):
    token = "gho_abcdefghijklmnopqrstuvwxyz"
    nested: object = {token: "leaf"}
    for _ in range(500):
        nested = {"next": nested}
    value = blueprint_document()
    value["default_context"] = {"nested": nested}
    source = tmp_path / "deep.json"
    source.write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "must-not-exist.zip"
    args = [command, str(source)]
    if command == "package":
        args += ["--target", "local", "--output", str(output)]
    code, report, stderr = invoke(args, capsys)
    rendered = json.dumps(report) + stderr
    assert code == 2 and "nesting" in report["error"]
    assert token not in rendered and "Traceback" not in rendered
    assert not output.exists()
