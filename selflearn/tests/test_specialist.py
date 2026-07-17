"""Specialist spec: declarative, validated, YAML round-trip, no model binding."""
import pytest

from selflearn.contracts import ContractError
from selflearn.specialist import SpecialistSpec, load_spec, save_spec


def test_spec_requires_packs():
    with pytest.raises(ContractError, match="pack"):
        SpecialistSpec(name="sec-reviewer", packs=())


def test_spec_has_no_model_field():
    spec = SpecialistSpec(name="sec-reviewer", packs=("security",))
    assert "model" not in spec.to_dict()


def test_serves_task_types():
    spec = SpecialistSpec(name="s", packs=("p",), task_types=("review",))
    assert spec.serves("review") and not spec.serves("code_edit")
    assert SpecialistSpec(name="s", packs=("p",)).serves("anything")


def test_yaml_round_trip(tmp_path):
    spec = SpecialistSpec(name="fastapi-dev", packs=("fastapi", "http"),
                          archetype_prompt="You build APIs.",
                          task_types=("code_edit",), min_tier="mid",
                          retrieval_k=3, retrieval_budget_tokens=800)
    path = tmp_path / "specs" / "fastapi-dev.yaml"
    save_spec(spec, path)
    assert load_spec(path) == spec


def test_load_missing_or_corrupt_is_loud(tmp_path):
    with pytest.raises(ContractError, match="does not exist"):
        load_spec(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a list\n")
    with pytest.raises(ContractError, match="not a mapping"):
        load_spec(bad)
