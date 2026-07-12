from __future__ import annotations

import sys

import httpx
import pytest

from metaharness.blueprints import BlueprintContent
from metaharness.evals.models import EvalAssertion, EvalCase, EvalSuiteContent
from metaharness.portable import load_portable_package
from metaharness.web import HarnessState, create_app
from metaharness.workflows.dsl import StepSpec, WorkflowSpec


RUNNER_CODE = """
import json, sys
request = json.load(sys.stdin)
answer = request["task"]["inputs"]["prompt"]
print(json.dumps({"output": answer, "raw_text": answer}))
"""


def _suite_content() -> EvalSuiteContent:
    def case(case_id: str, prompt: str) -> EvalCase:
        return EvalCase(
            id=case_id,
            name=case_id,
            context={"prompt": prompt},
            assertion=EvalAssertion(success_check={"equals": prompt}),
            output_step="answer",
        )

    return EvalSuiteContent(
        name="Quality",
        development_cases=[case("development", "visible")],
        validation_cases=[case("validation", "validation")],
        holdout_cases=[case("sealed", "private")],
    )


def _state_with_exact_pair(tmp_path) -> tuple[HarnessState, object, object]:
    state = HarnessState()
    state.enable_persistence(tmp_path / "state")
    draft = state.eval_suite_store.create_draft(
        "quality", _suite_content(), owner="tester", now=1.0
    )
    suite = state.eval_suite_store.publish(
        "quality", expected_revision=draft.revision, now=2.0
    )
    draft = state.blueprint_store.create_draft(
        "answerer",
        BlueprintContent(
            name="Answerer",
            workflow=WorkflowSpec(
                name="answerer",
                steps=[
                    StepSpec(
                        id="answer",
                        objective="Echo input.",
                        inputs={"prompt": "$context.prompt"},
                    )
                ],
            ),
            eval_suites=[suite.ref],
        ),
        owner="tester",
        now=3.0,
    )
    blueprint = state.blueprint_store.publish(
        "answerer", expected_revision=draft.revision, now=4.0
    )
    return state, blueprint, suite


@pytest.fixture(autouse=True)
def _portable_test_sandbox(monkeypatch):
    monkeypatch.setattr(
        "metaharness.evals.evaluator._system_sandbox",
        lambda command, _workspace, _scratch: (list(command), "test-isolation"),
    )


async def _client(state: HarnessState):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)), base_url="http://test"
    )


async def test_eval_suite_api_seals_holdout_and_survives_restart(tmp_path):
    state = HarnessState()
    state.enable_persistence(tmp_path / "state")
    async with await _client(state) as client:
        created = await client.post(
            "/api/eval-suites",
            json={"suite_id": "quality", "content": _suite_content().model_dump(mode="json")},
        )
        assert created.status_code == 201
        published = await client.post(
            "/api/eval-suites/quality/versions", json={"expected_revision": 1}
        )
        assert published.status_code == 200
        assert "holdout_cases" not in published.json()
        assert published.json()["holdout_count"] == 1

    restarted = HarnessState()
    restarted.enable_persistence(tmp_path / "state")
    assert restarted.eval_suite_store.get_version("quality", 1).holdout_count == 1
    assert restarted.evaluation_report_store.list() == []


async def test_exact_eval_api_persists_immutable_report_and_rejects_bypass(tmp_path):
    state, blueprint, suite = _state_with_exact_pair(tmp_path)
    body = {
        "report_id": "report-one",
        "eval_ref": suite.ref.model_dump(mode="json"),
        "split": "development",
        "runner": {
            "runner_id": "fixture-runner",
            "argv": [sys.executable, "-c", RUNNER_CODE],
        },
    }
    async with await _client(state) as client:
        response = await client.post(
            f"/api/blueprints/{blueprint.id}/versions/{blueprint.version}/evaluate",
            json=body,
        )
        assert response.status_code == 201, response.text
        assert response.json()["passed"] == 1
        duplicate = await client.post(
            f"/api/blueprints/{blueprint.id}/versions/{blueprint.version}/evaluate",
            json=body,
        )
        assert duplicate.status_code == 409
        bypass = body | {
            "report_id": "report-two",
            "runner": body["runner"] | {"sealed_holdout_access": True},
        }
        assert (await client.post(
            f"/api/blueprints/{blueprint.id}/versions/{blueprint.version}/evaluate",
            json=bypass,
        )).status_code == 422
        holdout = body | {"report_id": "report-three", "split": "holdout"}
        assert (await client.post(
            f"/api/blueprints/{blueprint.id}/versions/{blueprint.version}/evaluate",
            json=holdout,
        )).status_code == 422


async def test_tune_is_inert_until_approved_and_never_publishes(tmp_path):
    state, blueprint, suite = _state_with_exact_pair(tmp_path)
    async with await _client(state) as client:
        evaluated = await client.post(
            f"/api/blueprints/{blueprint.id}/versions/{blueprint.version}/evaluate",
            json={
                "report_id": "report-one",
                "eval_ref": suite.ref.model_dump(mode="json"),
                "split": "development",
                "runner": {
                    "runner_id": "fixture-runner",
                    "argv": [sys.executable, "-c", RUNNER_CODE],
                },
            },
        )
        report = evaluated.json()
        base = {
            "report_refs": [{
                "id": report["id"],
                "content_digest": report["content_digest"],
                "split": report["split"],
            }],
            "patches": [{"op": "set_description", "value": "Tuned by review"}],
            "rationale": "Visible evidence supports this wording.",
        }
        inert = await client.post(
            f"/api/blueprints/{blueprint.id}/versions/{blueprint.version}/tune",
            json=base | {"proposal_id": "proposal-one", "human_approved": False},
        )
        assert inert.status_code == 201, inert.text
        assert inert.json()["applied_draft"] is None
        assert state.blueprint_store.get_catalog_entry(blueprint.id).latest_version == 1

        applied = await client.post(
            f"/api/blueprints/{blueprint.id}/versions/{blueprint.version}/tune",
            json=base | {"proposal_id": "proposal-two", "human_approved": True},
        )
        assert applied.status_code == 201, applied.text
        assert applied.json()["applied_draft"]["description"] == "Tuned by review"
        assert applied.json()["published"] is False
        assert state.blueprint_store.get_catalog_entry(blueprint.id).latest_version == 1


async def test_exact_portable_package_api_round_trip(tmp_path):
    state, blueprint, suite = _state_with_exact_pair(tmp_path)
    async with await _client(state) as client:
        response = await client.post(
            f"/api/blueprints/{blueprint.id}/versions/{blueprint.version}/package",
            json={"targets": ["local"], "generated_at": 10},
        )
    assert response.status_code == 200, response.text
    loaded = load_portable_package(response.content)
    assert loaded.blueprint.ref == blueprint.ref
    assert loaded.manifest.eval_refs == [suite.ref]
