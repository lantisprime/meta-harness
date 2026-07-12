"""Playwright E2E: the real dashboard driven in a real browser.

A `metaharness serve` subprocess runs with HOME pointed at a temp dir, so
config saves land in an isolated ~/.metaharness — never the developer's.
Skipped automatically when playwright (or its chromium) is not installed,
so plain CI stays fast; run locally with the [e2e] extra installed.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api",
                                      reason="playwright not installed")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    home = tmp_path_factory.mktemp("e2e-home")
    port = _free_port()
    env = {**os.environ, "HOME": str(home)}
    # A module-scoped server can emit more than an OS pipe buffer over the full
    # browser suite (especially with live run-event journals).  An undrained
    # stdout=PIPE eventually blocks the server process itself, making otherwise
    # unrelated late-suite HTTP calls time out.  Keep diagnostics in a regular
    # file, which has no producer backpressure.
    server_log = (home / "server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "metaharness.cli"] if False else
        [str(Path(sys.executable).parent / "metaharness"), "serve",
         "--port", str(port)],
        env=env, stdout=server_log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        import httpx
        for _ in range(60):
            try:
                if httpx.get(base + "/api/workers", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            proc.kill()
            raise RuntimeError("server did not come up")
        yield base, home
    finally:
        proc.kill()
        proc.wait()
        server_log.close()


@pytest.fixture(scope="module")
def page(server):
    from playwright.sync_api import sync_playwright
    base, _home = server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(10_000)
        yield page
        browser.close()


def submit_fork(page, blueprint_id: str, name: str) -> None:
    page.wait_for_selector("#fork-dialog[open]")
    page.fill("#fork-id", blueprint_id)
    page.fill("#fork-name", name)
    page.click("#fork-dialog button:has-text('Create fork')")


def test_run_wizard_loads_with_mock_tiers(page, server):
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    assert "metaharness" in page.title()
    page.wait_for_selector(".stepper .s.on")
    # mock workers fill all three tiers -> ready badges + enabled continue
    page.wait_for_selector(".tierrow")
    assert page.locator(".tierrow").count() == 3
    assert page.locator(".badge.ok", has_text="ready").count() == 3
    assert page.locator("button", has_text="Continue →").is_enabled()


def test_guided_harness_fields_capability_bundle_and_agent_assignment_persist(page, server):
    base, _ = server
    page.goto(base)
    page.click("button:has-text('Create a harness')")
    page.fill("#harness-name", "Guided calculator")
    page.fill("#harness-slug", "e2e-guided-calculator")
    page.fill("#harness-description", "A reusable guided harness")
    page.click("button:has-text('+ Add input')")
    row = page.locator("[data-input-row]").first
    row.locator("[data-part=name]").fill("amount")
    row.locator("[data-part=type]").select_option("number")
    row.locator("[data-part=required]").check()
    page.click(".pill:has-text('Custom (build by hand)')")
    page.fill("#goal", "Calculate the supplied amount.")
    page.click("#planbtn")
    page.fill("#sb-obj", "Calculate the supplied amount exactly.")
    page.check('[data-map-prefix="sb"][data-map-source="amount"]')
    page.click("button:has-text('Next →')")
    page.click("button[data-tool-mode='builder']:has-text('Calculate')")
    worker = page.locator("#sb-worker option").nth(1).get_attribute("value")
    assert worker
    page.select_option("#sb-worker", worker)
    assert page.locator("#sb-role").count() == 1
    assert page.locator("#sb-capabilities").count() == 1
    page.click("button:has-text('Next →')")
    page.click("button:has-text('Add step to workflow')")
    page.click("button:has-text('Done — review workflow')")
    page.click("button:has-text('Save as harness')")
    page.wait_for_timeout(600)
    assert page.evaluate("wiz.dirty") is False, page.locator("#toast").inner_text()
    saved = page.evaluate("fetch('/api/blueprint-drafts/e2e-guided-calculator').then(r => r.json())")
    assert saved["description"] == "A reusable guided harness"
    assert next(i for i in saved["inputs"] if i["name"] == "amount")["schema"]["type"] == "number"
    stage = saved["workflow"]["steps"][0]
    assert stage["inputs"]["amount"] == "$context.amount"
    assert stage["worker_id"] == worker
    assert not stage.get("role")
    assert not stage.get("required_capabilities")
    assert stage["tools"] == ["calculator"]


def test_live_timeline_names_agent_and_accessible_tabs(page, server):
    base, _ = server
    page.goto(base)
    page.evaluate("""() => {
      wiz.plan={name:'timeline',steps:[{id:'work',task_type:'general',objective:'Work',tools:[]}]};
      wiz.runId='run_demo'; wiz.run={status:'running',completed:{},skipped:{},awaiting:null};
      wiz.journal=[
        {kind:'step.ready',step_id:'work',payload:{}},
        {kind:'attempt.assigned',step_id:'work',payload:{n:1,worker_id:'agent-a',model:'model-a',tier:'small'}},
        {kind:'attempt.started',step_id:'work',payload:{n:1,worker_id:'agent-a'}},
        {kind:'tool.requested',step_id:'work',payload:{tool:'calculator'}},
        {kind:'verification.started',step_id:'work',payload:{worker_id:'agent-a'}},
      ]; setStep(3);
    }""")
    assert "Assigned attempt 1 to agent-a" in page.locator(".timeline").inner_text()
    assert "Using calculator" in page.locator(".timeline").inner_text()
    assert page.locator("[role=tab]").get_attribute("aria-selected") == "true"
    assert page.locator("[role=tabpanel]").count() == 1


def test_run_with_new_inputs_returns_to_inputs_for_ad_hoc_plan(page, server):
    base, _ = server
    page.goto(base)
    page.evaluate("""() => {
      resetWizard(false,true); currentView='wizard'; wiz.step=4;
      wiz.plan={name:'adhoc',steps:[{id:'one',task_type:'general',objective:'Use goal',inputs:{goal:'$context.goal'},tools:[],depends_on:[],hitl:false}]};
      wiz.runId='run_done'; wiz.run={status:'completed',completed:{one:{verdict:'pass',output:'ok',attempts:1}},skipped:{}};
      showView('wizard',true); renderStepper(); renderDoneStep();
    }""")
    page.click("button:has-text('Run with new inputs')")
    page.wait_for_selector("#goal")
    assert page.evaluate("wiz.step") == 1
    assert "keeps the current stages" in page.locator("#wiz-body").inner_text()


def test_readiness_repair_can_visit_settings_and_return_without_losing_stage(page, server):
    base, _ = server
    page.goto(base)
    page.evaluate("""() => {
      showView('wizard',true); wiz.step=2; wiz.dirty=true;
      wiz.plan={name:'repair',steps:[{id:'send',task_type:'general',objective:'Send',tools:['mail.send'],depends_on:[],hitl:true}]};
      wiz.readinessIssues=[{stage_id:'send',message:'Pinned worker is unavailable',repair:{action:'configure_agent',label:'Configure an agent',target:'missing'}}];
      renderStepper(); renderPlanStep();
    }""")
    page.evaluate("repairReadiness(0)")
    page.wait_for_selector("#settings-return")
    assert page.locator("#view-settings").is_visible()
    assert page.evaluate("wiz.dirty") is True
    page.click("#settings-return button")
    page.wait_for_selector(".planstep:has-text('send')")
    assert page.evaluate("wiz.plan.steps[0].tools[0]") == "mail.send"


def test_readiness_mcp_repair_honors_typed_load_failure(page, server):
    base, _ = server
    page.goto(base)
    page.route(
        "**/api/config/mcp/expired-mail/load",
        lambda route: route.fulfill(json={
            "ok": False, "status": "load_failed", "tools": 0,
            "detail": "OAuth token expired",
        }),
    )
    try:
        page.evaluate("""() => {
          wiz.readinessIssues=[{stage_id:'send',message:'Capability unavailable',
            repair:{action:'load_mcp',label:'Load capability',target:'expired-mail'}}];
          document.getElementById('wiz-body').innerHTML='<div id="planmsg"></div>';
        }""")
        page.evaluate("repairReadiness(0)")
        page.wait_for_selector("#toast.on:has-text('OAuth token expired')")
        assert "could not load" in page.locator("#planmsg").inner_text().lower()
        assert page.evaluate("wiz.readinessIssues.length") == 1
    finally:
        page.unroute("**/api/config/mcp/expired-mail/load")


def test_harness_library_lists_builtins_and_run_waits_for_confirmation(page, server):
    base, _ = server
    page.goto(base)
    page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="research"]')
    assert page.locator('[data-harness-id="research"] .badge', has_text="builtin").count() == 1
    assert page.locator('[data-harness-id="software-engineering"]').count() == 1

    before = page.evaluate("fetch('/api/runs').then(r => r.json()).then(x => x.length)")
    page.locator('[data-harness-id="research"] [data-action="run"]').click()
    page.wait_for_selector("b:has-text('Run Research & report with new inputs.')")
    assert page.evaluate("fetch('/api/runs').then(r => r.json()).then(x => x.length)") == before
    page.fill("#goal", "Research the workspace and summarize the result.")
    page.click("button:has-text('Review harness')")
    page.wait_for_selector("button:has-text('Confirm and run v1')")
    assert page.evaluate("fetch('/api/runs').then(r => r.json()).then(x => x.length)") == before


def test_library_evaluate_tune_package_actions_are_real(page, server):
    base, _ = server
    page.goto(base); page.click("#nav-library")
    card = page.locator('[data-harness-id="research"]')
    card.wait_for()
    for label in ("Evaluate", "Tune", "Package"):
        assert card.locator("button", has_text=label).is_enabled()

    package_calls = []
    page.route(
        "**/api/blueprints/research/versions/1/package",
        lambda route: (package_calls.append(route.request.post_data_json),
                       route.fulfill(status=200, body=b"PK-e2e-package",
                                     headers={"Content-Type": "application/zip"})),
    )
    try:
        with page.expect_download():
            card.locator("button", has_text="Package").click()
        assert package_calls == [{"targets": ["local"]}]
    finally:
        page.unroute("**/api/blueprints/research/versions/1/package")

    card.locator("button", has_text="Evaluate").click()
    page.wait_for_selector("#library-action-dialog[open]")
    # Built-ins currently have no frozen eval suite; say what to do next.
    assert "no evaluation suite attached" in page.locator("#library-action-body").inner_text().lower()
    page.click("#library-action-body button:has-text('Close')")

    tune_calls = []
    page.route(
        "**/api/blueprints/research/versions/1/tune",
        lambda route: (tune_calls.append(route.request.post_data_json),
                       route.fulfill(status=201, json={"proposal": {"id": "p"}, "applied_draft": None, "published": False})),
    )
    try:
        card.locator("button", has_text="Tune").click()
        page.fill("#la-report-refs", '[{"id":"report-1","content_digest":"' + "a" * 64 + '","split":"development"}]')
        page.fill("#la-patches", '[{"op":"set_description","value":"Improved"}]')
        page.fill("#la-rationale", "Evidence from the visible evaluation report.")
        page.click("#library-action-body button:has-text('Create proposal')")
        page.wait_for_selector("#library-action-dialog", state="hidden")
        assert len(tune_calls) == 1
        assert tune_calls[0]["human_approved"] is False
    finally:
        page.unroute("**/api/blueprints/research/versions/1/tune")


def test_builtin_edit_forks_and_custom_draft_survives_refresh(page, server):
    base, _ = server
    page.goto(base); page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="research"]')
    page.locator('[data-harness-id="research"] [data-action="edit"]').click()
    submit_fork(page, "e2e-research-fork", "E2E research fork")
    page.wait_for_selector("button:has-text('Save draft')")
    assert page.evaluate("wiz.blueprintId") == "e2e-research-fork"
    assert page.evaluate("wiz.blueprintSource") == {"id": "research", "version": 1}
    page.reload(); page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="e2e-research-fork"]')
    card = page.locator('[data-harness-id="e2e-research-fork"]')
    assert card.locator(".badge", has_text="draft").count() >= 1
    assert card.locator('[data-action="delete_draft"]').count() == 1


def test_owned_harness_save_publish_version_archive_restore_and_dirty_guard(page, server):
    base, _ = server
    page.goto(base)
    created = page.evaluate("""async () => {
      const r = await fetch('/api/blueprints/research/fork', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({
          new_id:'e2e-versioned', source_version:1, display_name:'E2E versioned'})});
      return {ok:r.ok, text:await r.text()};
    }""")
    assert created["ok"], created["text"]
    page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="e2e-versioned"]')
    page.locator('[data-harness-id="e2e-versioned"] [data-action="edit"]').click()
    page.wait_for_selector("button:has-text('Save draft')")

    page.locator("button[title='edit']").first.click()
    page.fill("#se-obj", "Immutable version one objective.")
    page.click("button:has-text('Save step')")
    assert page.evaluate("wiz.dirty") is True

    def stay_on_dirty(dialog):
        assert "unsaved harness changes" in dialog.message
        dialog.dismiss()
    page.once("dialog", stay_on_dirty)
    page.click("#nav-library")
    assert page.locator("#view-wizard").is_visible()

    page.click("button:has-text('Save draft')")
    page.wait_for_function("wiz.dirty === false")
    page.locator("#view-wizard button", has_text="Publish").click()
    page.wait_for_function("wiz.blueprintVersion === 1 && wiz.blueprintMode === 'run'")
    v1 = page.evaluate("fetch('/api/blueprints/e2e-versioned/versions/1').then(r => r.json())")
    assert v1["workflow"]["steps"][0]["objective"] == "Immutable version one objective."

    page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="e2e-versioned"]')
    page.locator('[data-harness-id="e2e-versioned"] [data-action="versions"]').click()
    page.wait_for_function("(LIB.versions['e2e-versioned'] || []).length === 1")
    page.locator('[data-harness-id="e2e-versioned"] [data-action="edit"]').click()
    page.locator("button[title='edit']").first.click()
    page.fill("#se-obj", "Immutable version two objective.")
    page.click("button:has-text('Save step')")
    page.click("button:has-text('Save draft')")
    page.wait_for_function("wiz.dirty === false")
    page.locator("#view-wizard button", has_text="Publish").click()
    page.wait_for_function("wiz.blueprintVersion === 2")
    assert page.evaluate("LIB.versions['e2e-versioned']") is None
    versions = page.evaluate("fetch('/api/blueprints/e2e-versioned/versions').then(r => r.json())")
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["workflow"]["steps"][0]["objective"] == "Immutable version one objective."
    assert versions[1]["workflow"]["steps"][0]["objective"] == "Immutable version two objective."

    page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="e2e-versioned"]')
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-harness-id="e2e-versioned"] [data-action="archive"]').click()
    page.wait_for_selector('[data-harness-id="e2e-versioned"]', state="detached")
    page.check("#library-archived")
    page.wait_for_selector('[data-harness-id="e2e-versioned"] [data-action="restore"]')
    page.locator('[data-harness-id="e2e-versioned"] [data-action="versions"]').click()
    page.wait_for_selector('[data-harness-id="e2e-versioned"] .version-list .lrow')
    archived_rows = page.locator('[data-harness-id="e2e-versioned"] .version-list')
    assert archived_rows.locator("button", has_text="Run").count() == 0
    assert archived_rows.locator("button", has_text="Edit").count() == 0
    assert archived_rows.locator("button", has_text="Fork").count() == 0
    page.locator('[data-harness-id="e2e-versioned"] [data-action="restore"]').click()
    page.wait_for_selector('[data-harness-id="e2e-versioned"] [data-action="archive"]')


def test_delete_draft_is_distinct_from_archive_and_run_without_save_is_isolated(page, server):
    base, _ = server
    page.goto(base)
    setup = page.evaluate("""async () => {
      const make = async (id) => fetch('/api/blueprints/research/fork', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({
          new_id:id, source_version:1, display_name:id})});
      const disposable = await make('e2e-disposable');
      const editable = await make('e2e-run-unsaved');
      const draft = await editable.json();
      const published = await fetch('/api/blueprint-drafts/e2e-run-unsaved/publish', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({expected_revision:draft.revision})});
      return disposable.ok && published.ok;
    }""")
    assert setup is True
    page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="e2e-disposable"] [data-action="delete_draft"]')
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-harness-id="e2e-disposable"] [data-action="delete_draft"]').click()
    page.wait_for_selector('[data-harness-id="e2e-disposable"]', state="detached")
    missing = page.evaluate("fetch('/api/blueprint-drafts/e2e-disposable').then(r => r.status)")
    assert missing == 404

    page.locator('[data-harness-id="e2e-run-unsaved"] [data-action="edit"]').click()
    page.locator("button[title='edit']").first.click()
    page.fill("#se-obj", "This change must remain run-only.")
    page.click("button:has-text('Save step')")
    page.locator("#view-wizard button", has_text="Run without saving").click()
    page.wait_for_function("wiz.runId !== null")
    assert page.evaluate("wiz.run.blueprint_ref") is None
    immutable = page.evaluate("fetch('/api/blueprints/e2e-run-unsaved/versions/1').then(r => r.json())")
    assert immutable["workflow"]["steps"][0]["objective"] != "This change must remain run-only."


def test_open_step_direct_actions_capture_tools_and_dirty_exact_fork_preserves_content(page, server):
    base, _ = server
    page.goto(base)
    created = page.evaluate("""async () => {
      const r = await fetch('/api/blueprints/research/fork', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({
          new_id:'e2e-direct-actions', source_version:1, display_name:'Direct actions'})});
      return r.ok;
    }""")
    assert created is True
    page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="e2e-direct-actions"]')
    page.locator('[data-harness-id="e2e-direct-actions"] [data-action="edit"]').click()

    # A global save captures the still-open form; clicking Save step is not required.
    page.locator("button[title='edit']").first.click()
    page.fill("#se-obj", "Captured directly from the open editor.")
    page.locator("#view-wizard button", has_text="Save draft").click()
    page.wait_for_function("wiz.dirty === false && wiz.editingStep === null")
    saved = page.evaluate("fetch('/api/blueprint-drafts/e2e-direct-actions').then(r => r.json())")
    assert saved["workflow"]["steps"][0]["objective"] == "Captured directly from the open editor."

    # Tool-only edits also mark the subeditor dirty and Publish captures them.
    page.locator("button[title='edit']").first.click()
    tool = page.locator('.step-edit [data-tool="calculator"]')
    tool.click()
    assert page.evaluate("wiz.stepEditorDirty") is True
    page.locator("#view-wizard button", has_text="Publish").click()
    page.wait_for_function("wiz.blueprintVersion === 1 && wiz.blueprintMode === 'run'")
    v1 = page.evaluate("fetch('/api/blueprints/e2e-direct-actions/versions/1').then(r => r.json())")
    assert "calculator" in v1["workflow"]["steps"][0]["tools"]

    # Forking a modified exact version preserves the canonical open-form edit.
    page.locator("button[title='edit']").first.click()
    page.fill("#se-obj", "Preserve this exact unsaved edit in the fork.")
    page.locator("#view-wizard button", has_text="Fork to edit").click()
    submit_fork(page, "e2e-preserved-fork", "Preserved fork")
    page.wait_for_function("wiz.blueprintId === 'e2e-preserved-fork'")
    forked = page.evaluate("fetch('/api/blueprint-drafts/e2e-preserved-fork').then(r => r.json())")
    assert forked["workflow"]["steps"][0]["objective"] == "Preserve this exact unsaved edit in the fork."
    assert forked["source"] == {"id": "e2e-direct-actions", "version": 1}

    # Invalid open forms block navigation with a clear message before any discard dialog.
    page.locator("button[title='edit']").first.click()
    page.fill("#se-obj", "")
    page.click("#nav-library")
    assert page.locator("#view-wizard").is_visible()
    assert "needs an objective" in page.locator("#toast").inner_text()

    page.fill("#se-obj", "Valid again")
    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="e2e-direct-actions"]')
    page.locator('[data-harness-id="e2e-direct-actions"] [data-action="versions"]').click()
    page.wait_for_selector('[data-harness-id="e2e-direct-actions"] .version-list button')
    row = page.locator('[data-harness-id="e2e-direct-actions"] .version-list .lrow').first
    assert row.locator("button", has_text="Run").count() == 1
    assert row.locator("button", has_text="Edit").count() == 1
    assert row.locator("button", has_text="Fork").count() == 1


def test_historical_edit_with_mismatched_draft_offers_exact_fork(page, server):
    base, _ = server
    page.goto(base)
    setup = page.evaluate("""async () => {
      const send = (url, body, method='POST') => fetch(url, {method,
        headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      let r = await send('/api/blueprints/research/fork', {new_id:'e2e-history', source_version:1, display_name:'History'});
      let d = await r.json();
      r = await send('/api/blueprint-drafts/e2e-history/publish', {expected_revision:d.revision});
      await r.json();
      r = await send('/api/blueprint-drafts', {blueprint_id:'e2e-history', base_version:1});
      d = await r.json(); d.workflow.steps[0].objective = 'Version two only';
      const content = {schema_version:d.schema_version, name:d.name, description:d.description,
        workflow:d.workflow, inputs:d.inputs, default_context:d.default_context,
        eval_suites:d.eval_suites, source:d.source};
      r = await send('/api/blueprint-drafts/e2e-history', {content, expected_revision:d.revision}, 'PATCH');
      d = await r.json();
      await send('/api/blueprint-drafts/e2e-history/publish', {expected_revision:d.revision});
      r = await send('/api/blueprint-drafts', {blueprint_id:'e2e-history', base_version:2});
      return r.ok;
    }""")
    assert setup is True
    page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="e2e-history"]')
    page.locator('[data-harness-id="e2e-history"] [data-action="versions"]').click()
    page.wait_for_selector('[data-harness-id="e2e-history"] .version-list .lrow')
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('[data-harness-id="e2e-history"] .version-list .lrow').first.locator("button", has_text="Edit").click()
    submit_fork(page, "e2e-history-v1-fork", "History v1 fork")
    page.wait_for_function("wiz.blueprintId === 'e2e-history-v1-fork'")
    forked = page.evaluate("fetch('/api/blueprint-drafts/e2e-history-v1-fork').then(r => r.json())")
    existing = page.evaluate("fetch('/api/blueprint-drafts/e2e-history').then(r => r.json())")
    assert forked["source"] == {"id": "e2e-history", "version": 1}
    assert forked["base_version"] is None
    assert forked["workflow"]["steps"][0]["objective"] != "Version two only"
    assert existing["base_version"] == 2


def test_cancel_step_edit_restores_prior_global_dirty_state(page, server):
    base, _ = server
    page.goto(base); page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="research"]')
    page.locator('[data-harness-id="research"] [data-action="run"]').click()
    page.fill("#goal", "cancel editor regression")
    page.click("button:has-text('Review harness')")
    before = page.evaluate("JSON.stringify(wiz.plan)")
    page.locator("button[title='edit']").first.click()
    assert page.evaluate("""() => [...document.querySelectorAll('.step-edit label[for]')]
      .every(label => document.getElementById(label.htmlFor))""") is True
    assert page.locator(".step-edit fieldset legend").count() >= 1
    page.fill("#se-obj", "do not keep")
    page.locator('.step-edit [data-tool="calculator"]').click()
    assert page.evaluate("wiz.dirty && wiz.stepEditorDirty") is True
    page.click("button:has-text('Cancel')")
    assert page.evaluate("wiz.dirty") is False
    assert page.evaluate("JSON.stringify(wiz.plan)") == before


@pytest.mark.parametrize("transition", ["edit-other", "delete", "move", "add", "yaml"])
def test_plan_surface_transition_preserves_open_step_values(page, server, transition):
    base, _ = server
    page.goto(base); page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="research"]')
    page.locator('[data-harness-id="research"] [data-action="run"]').click()
    page.fill("#goal", f"transition regression {transition}")
    page.click("button:has-text('Review harness')")
    page.locator("button[title='edit']").first.click()
    marker = f"Uncommitted value preserved by {transition}."
    page.fill("#se-obj", marker)

    if transition == "edit-other":
        page.locator("button[title='edit']").first.click()
        page.wait_for_function("wiz.editingStep === 1")
    elif transition == "delete":
        page.locator("button[title='remove']").first.click()
    elif transition == "move":
        page.locator("button[title='move down']").first.click()
    elif transition == "add":
        page.click("button:has-text('+ Add step (wizard)')")
        page.wait_for_function("wiz.builderMode === true")
    else:
        page.click("button:has-text('Edit as YAML')")
        page.wait_for_function("wiz.yamlMode === true")

    assert page.evaluate(
        f"wiz.plan.steps.some(step => step.objective === {json.dumps(marker)})"
    ) is True
    if transition == "yaml":
        assert marker in page.locator("#yaml-box").input_value()


def test_untouched_step_transition_keeps_exact_blueprint_run_identity(page, server):
    base, _ = server
    page.goto(base); page.click("#nav-library")
    page.wait_for_selector('[data-harness-id="research"]')
    page.locator('[data-harness-id="research"] [data-action="run"]').click()
    page.fill("#goal", "untouched editor exact-run regression")
    page.click("button:has-text('Review harness')")

    page.locator("button[title='edit']").first.click()
    page.locator("button[title='edit']").first.click()  # transition to another untouched editor
    assert page.evaluate("wiz.dirty") is False
    page.locator("#view-wizard button", has_text="Confirm and run v1").click()
    page.wait_for_function("wiz.runId !== null")

    assert page.evaluate("wiz.run.blueprint_ref") == {"id": "research", "version": 1}


def test_library_route_aborts_preserve_screen_and_announce_retry(page, server):
    base, _ = server
    page.goto(base)
    assert page.evaluate("""async () => {
      const send = (url, body) => fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      let r = await send('/api/blueprints/research/fork', {new_id:'e2e-abort-draft', source_version:1});
      let d = await r.json();
      r = await send('/api/blueprints/research/fork', {new_id:'e2e-abort-published', source_version:1});
      d = await r.json(); await send('/api/blueprint-drafts/e2e-abort-published/publish', {expected_revision:d.revision});
      r = await send('/api/blueprints/research/fork', {new_id:'e2e-abort-restore', source_version:1});
      d = await r.json(); await send('/api/blueprint-drafts/e2e-abort-restore/publish', {expected_revision:d.revision});
      await send('/api/blueprints/e2e-abort-restore/archive', {}); return true;
    }""") is True
    page.click("#nav-library"); page.wait_for_selector('[data-harness-id="e2e-abort-draft"]')

    def abort(path, expression, dialogs=()):
        page.route(path, lambda route: route.abort())
        page.evaluate("document.getElementById('toast').textContent = ''")
        answers = list(dialogs)
        def answer(dialog):
            value = answers.pop(0)
            dialog.accept(value) if value is not None else dialog.accept()
            if not answers: page.remove_listener("dialog", answer)
        if answers: page.on("dialog", answer)
        page.evaluate(expression)
        page.wait_for_function("document.getElementById('toast').textContent.includes('retry')")
        assert page.locator("#view-library").is_visible()
        assert page.locator("#toast").get_attribute("role") == "status"
        page.unroute(path)

    abort("**/api/blueprint-drafts/e2e-abort-draft", "libraryEdit('e2e-abort-draft', null, 'fork')")
    abort("**/api/blueprints/research/fork", "libraryFork('research', 1, null, {id:'e2e-abort-fork',name:'Abort fork'})")
    abort("**/api/blueprints/e2e-abort-published/archive", "libraryArchive('e2e-abort-published')", (None,))
    abort("**/api/blueprints/e2e-abort-restore/restore", "libraryRestore('e2e-abort-restore')")
    abort("**/api/blueprint-drafts/e2e-abort-draft", "libraryDeleteDraft('e2e-abort-draft')", (None,))
    abort("**/api/blueprint-drafts/e2e-abort-draft/publish", "libraryPublish('e2e-abort-draft')")


def test_agents_step_lists_pool_members_from_routing(page, server):
    """The Run wizard's agents step renders pool members from /api/routing:
    each tier's default mock worker shows on its own line with a mono
    'worker_id · model' identity line."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.wait_for_selector(".tierrow .poolmember")
    # mock fill gives one member per tier -> three pool-member lines
    assert page.locator(".tierrow .poolmember").count() == 3
    for tier in ("mock-small", "mock-mid", "mock-frontier"):
        assert page.locator(f".tierrow .poolmember .mono:has-text('{tier}')").count() == 1


def test_settings_provider_wizard_end_to_end(page, server):
    base, home = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Where do completions come from?')")
    page.click("button:has-text('+ Add a provider')")

    # step 1: pick a keyless local provider from the catalog
    page.wait_for_selector(".subwiz-steps")
    page.click(".pill:has-text('LM Studio (local)')")
    page.click("button:has-text('Next →')")
    # step 2: base URL pre-filled from catalog, keyless hint shown
    assert page.locator("#pw-base").input_value() == "http://localhost:1234/v1"
    assert page.locator(".hint-panel:has-text('no API key needed')").count() == 1
    page.click("button:has-text('Next →')")
    # step 3: save (skip live test — no LM Studio in CI)
    page.click("button:has-text('Save provider')")

    page.wait_for_selector(".prov-item:has-text('LM Studio')")
    assert page.locator(".prov-item .badge.ok", has_text="connected").count() >= 1
    # persisted to the ISOLATED home, keys never in plaintext
    cfg = (home / ".metaharness" / "config.json").read_text()
    assert "lmstudio" in cfg


def test_mcp_wizard_configures_local_server_without_shell_splitting(page, server):
    base, home = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Extra tool servers')")
    page.click("button:has-text('+ Connect MCP server')")

    page.wait_for_selector(".subwiz-steps")
    page.click(".pill:has-text('Custom local')")
    page.click("button:has-text('Next →')")
    page.fill("#mw-name", "e2e-local-tools")
    page.fill("#mw-command", "npx")
    page.fill(
        "#mw-args",
        "-y\n@modelcontextprotocol/server-filesystem\n/path with spaces",
    )
    page.click("button:has-text('+ Add variable')")
    page.fill("#mw-env-key-0", "MCP_ROOT")
    page.fill("#mw-env-value-0", "/path with spaces")
    page.click("button:has-text('Next →')")

    assert page.locator(".hint-panel:has-text('e2e-local-tools')").count() == 1
    assert page.locator(".hint-panel:has-text('3 arguments')").count() == 1
    page.click("button:has-text('Save connection')")
    page.wait_for_selector(".prov-item:has-text('e2e-local-tools')")

    saved = json.loads((home / ".metaharness" / "config.json").read_text())
    local = saved["mcp_servers"]["e2e-local-tools"]
    assert local["command"] == "npx"
    assert local["args"] == [
        "-y", "@modelcontextprotocol/server-filesystem", "/path with spaces",
    ]
    assert local["env"]["MCP_ROOT"].startswith("enc1:")


def test_mcp_wizard_configures_remote_server(page, server):
    base, home = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Extra tool servers')")
    page.click("button:has-text('+ Connect MCP server')")

    page.wait_for_selector(".subwiz-steps")
    page.click(".pill:has-text('Custom remote')")
    page.click("button:has-text('Next →')")
    page.fill("#mw-name", "e2e-remote-tools")
    page.fill("#mw-url", "https://tools.example.test/mcp")
    page.click("button:has-text('Next →')")
    assert page.locator(
        ".hint-panel:has-text('https://tools.example.test/mcp')"
    ).count() == 1
    page.click("button:has-text('Save connection')")
    page.wait_for_selector(".prov-item:has-text('e2e-remote-tools')")

    saved = json.loads((home / ".metaharness" / "config.json").read_text())
    remote = saved["mcp_servers"]["e2e-remote-tools"]
    assert remote["transport"] == "http"
    assert remote["url"] == "https://tools.example.test/mcp"


def test_mcp_wizard_offers_curated_oauth_only_presets(page, server):
    base, home = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.click("button:has-text('+ Connect MCP server')")
    for label in (
        "Filesystem", "Brave Search", "Playwright", "Gmail", "Google Calendar",
        "Custom local", "Custom remote",
    ):
        assert page.locator(f".pill:has-text('{label}')").count() == 1

    page.click(".pill:has-text('Gmail')")
    page.click("button:has-text('Next →')")
    assert page.locator("#mw-name").input_value() == "gmail"
    assert page.locator("#mw-url").input_value() == (
        "https://gmailmcp.googleapis.com/mcp/v1"
    )
    assert page.locator("#mw-oauth-token").get_attribute("type") == "password"
    assert page.locator("text=mailbox password").count() == 0
    page.fill("#mw-oauth-token", "e2e-oauth-access-token")
    page.fill("#mw-oauth-project", "e2e-workspace-project")
    page.click("button:has-text('Next →')")
    assert "OAuth token set" in page.locator(".hint-panel").inner_text()
    page.click("button:has-text('Save connection')")
    page.wait_for_selector(".prov-item:has-text('gmail')")

    saved = json.loads((home / ".metaharness" / "config.json").read_text())
    assert "e2e-oauth-access-token" not in json.dumps(saved)
    assert saved["mcp_servers"]["gmail"]["oauth_token"].startswith("enc1:")
    original_envelope = saved["mcp_servers"]["gmail"]["oauth_token"]
    assert page.locator(
        ".prov-item:has-text('gmail') button:has-text('Load tools')"
    ).count() == 1

    # Editing round-trips the masked token without replacing the stored secret.
    page.click(".prov-item:has-text('gmail') button:has-text('Re-authenticate / Edit')")
    assert page.locator("#mw-name").is_disabled()
    assert page.locator("#mw-oauth-token").input_value()
    page.fill("#mw-oauth-project", "e2e-updated-project")
    page.click("button:has-text('Next →')")
    page.click("button:has-text('Update connection')")
    page.wait_for_selector(".prov-item:has-text('gmail')")
    preserved = json.loads((home / ".metaharness" / "config.json").read_text())
    assert preserved["mcp_servers"]["gmail"]["oauth_token"] == original_envelope

    # A real replacement token produces a new encrypted envelope.
    page.click(".prov-item:has-text('gmail') button:has-text('Re-authenticate / Edit')")
    page.fill("#mw-oauth-token", "e2e-replacement-oauth-token")
    page.click("button:has-text('Next →')")
    page.click("button:has-text('Update connection')")
    page.wait_for_selector("#toast.on:has-text('Updated MCP server gmail')")
    replaced = json.loads((home / ".metaharness" / "config.json").read_text())
    assert replaced["mcp_servers"]["gmail"]["oauth_token"] != original_envelope
    assert "e2e-replacement-oauth-token" not in json.dumps(replaced)

    page.route(
        "**/api/config/mcp/gmail/load",
        lambda route: route.fulfill(json={"ok": True, "tools": 1}),
    )
    page.route(
        "**/api/tools",
        lambda route: route.fulfill(json=[{
            "name": "gmail.search_threads", "description": "Search Gmail threads.",
            "source": "mcp:gmail", "annotations": {"readOnlyHint": True},
        }]),
    )
    try:
        page.click(".prov-item:has-text('gmail') button:has-text('Load tools')")
        page.wait_for_selector(".toast.on:has-text('Loaded 1 tool from gmail')")
        page.click("summary:has-text('Browse the full tool catalog')")
        page.wait_for_selector("text=Search Gmail threads.")
    finally:
        page.unroute("**/api/config/mcp/gmail/load")
        page.unroute("**/api/tools")
    page.evaluate("""() => {
        TOOLS_NAMES = ['gmail.search_threads'];
        TOOLS_CATALOG = [{name:'gmail.search_threads', source:'mcp:gmail'}];
    }""")
    page.click(".prov-item:has-text('gmail') button:has-text('Remove')")
    page.wait_for_selector(".prov-item:has-text('gmail')", state="detached")
    assert page.evaluate("TOOLS_NAMES") is None
    assert page.evaluate("TOOLS_CATALOG") is None


def test_workflow_tool_picker_groups_mcp_tools_without_cli(page, server):
    base, _ = server

    def tools(route):
        route.fulfill(json=[
            {"name": "read_file", "description": "Read a workspace file.",
             "source": "builtin"},
            {"name": "gmail.search_threads",
             "description": "Search Gmail threads with the connected account.",
             "source": "mcp:gmail", "annotations": {}},
        ])

    page.route("**/api/tools", tools)
    try:
        page.goto(base); page.click("#nav-wizard")
        page.wait_for_selector(".tierrow")
        page.click("button:has-text('Continue →')")
        page.click(".pill:has-text('Custom (build by hand)')")
        page.fill("#goal", "find a customer email")
        page.click("#planbtn")
        page.wait_for_selector("h2:has-text('Add step 1')")
        page.fill("#sb-obj", "Search Gmail for the customer thread.")
        page.click("button:has-text('Next →')")
        assert page.locator(".kv:has-text('Built-in tools · 1')").count() == 1
        assert page.locator(".kv:has-text('MCP server · gmail · 1')").count() == 1
        gmail = page.locator(".tool-toggle", has_text="search_threads")
        assert gmail.get_attribute("data-tool") == "gmail.search_threads"
        assert page.locator(
            "text=Search Gmail threads with the connected account."
        ).count() == 1
        assert page.locator(".badge.warn:has-text('human gate')").count() == 1
        gmail.click()
        assert page.evaluate("wiz.builder.draft.tools") == ["gmail.search_threads"]
        page.click("button:has-text('Next →')")
        assert page.locator("#sb-hitl").is_checked()
    finally:
        page.unroute("**/api/tools")


def test_agent_wizard_with_archetype_prompt(page, server):
    base, home = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Who does the work?')")
    page.click("#settings-body >> button:has-text('+ Add an agent')")

    # step 1: kind
    page.wait_for_selector(".subwiz-steps")
    page.click(".pill:has-text('Mock (testing)')")
    assert page.locator(".hint-panel:has-text('Deterministic offline worker')").count() == 1
    page.click("button:has-text('Next →')")
    # step 2: mock needs no connection
    page.click("button:has-text('Next →')")
    # step 3: role & prompt — archetype fills the textarea with guidance visible
    page.fill("#aw-id", "e2e-reviewer")
    page.select_option("#aw-tier", "mid")
    page.fill("#aw-roles", "reviewer, release-gate")
    page.fill("#aw-capabilities", "workspace.read, tests.run")
    page.click(".pill:has-text('Reviewer')")
    prompt = page.locator("#aw-prompt").input_value()
    assert "adversarial reviewer" in prompt and "SHIP or NO-SHIP" in prompt
    assert page.locator(".hint-panel:has-text('Output discipline')").count() == 1
    page.click("button:has-text('Next →')")
    # step 4: live test then register
    page.click("button:has-text('Test')")
    page.wait_for_selector("#aw-test .green")
    page.click("button:has-text('Register agent')")

    page.wait_for_selector(".prov-item:has-text('e2e-reviewer')")
    cfg = json.loads((home / ".metaharness" / "config.json").read_text())
    saved_agent = next(a for a in cfg["agents"] if a["worker_id"] == "e2e-reviewer")
    assert "adversarial reviewer" in saved_agent["system_prompt"]
    assert saved_agent["roles"] == ["reviewer", "release-gate"]
    assert saved_agent["capabilities"] == ["workspace.read", "tests.run"]
    assert page.evaluate("ASSIGNMENT_WORKERS_LOADED") is False
    # registered identity is visible from the Run wizard tier rows too
    page.click("#nav-wizard")
    page.wait_for_selector(".tierrow:has-text('e2e-reviewer')")


def test_goal_step_template_plan_and_full_run(page, server):
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.wait_for_selector(".tierrow")
    page.click("button:has-text('Continue →')")

    # workflow-type pills come from /api/workflow-types
    page.wait_for_selector(".pill:has-text('Software engineering')")
    page.click(".pill:has-text('Software engineering')")
    assert page.locator(".hint-panel:has-text('deterministic phase spine')").count() == 1
    page.fill("#goal", "add a --version flag to the CLI")
    page.click("#planbtn")

    # template plan renders: 6 phases, gate + tool badges, template note
    page.wait_for_selector(".planstep")
    assert page.locator(".planstep").count() == 6
    assert page.locator(".badge:has-text('software_engineering template')").count() == 1
    assert page.locator(".badge:has-text('HITL — approve output')").count() == 3
    assert page.locator(".badge:has-text('🔧 grep')").first.is_visible()

    # run it: mock workers answer instantly; approve all three gates in the UI,
    # waiting for EACH gate's banner (the Approve button is re-rendered per gate)
    page.click("button:has-text('Run this plan →')")
    for gate in ("specify", "plan", "review"):
        page.wait_for_selector(f".guide b:has-text('Approval needed: {gate}')",
                               timeout=30_000)
        gate_guide = page.locator("#wiz-body .guide", has_text=f"Approval needed: {gate}")
        assert "Review the completed artifact below" in gate_guide.inner_text()
        assert page.locator(".steppanel .out").count() == 1
        page.click(f"button:has-text('Approve {gate}')")
    page.wait_for_selector(".guide b:has-text('Run completed.')", timeout=30_000)
    # done screen: one tab per step, all six done
    assert page.locator(".stab").count() == 6
    assert page.locator(".stab .ticon.done").count() == 6
    # clicking a tab pins it and swaps the panel
    page.click(".stab:has-text('explore')")
    assert "Explore the workspace" in page.locator(".steppanel .pd").inner_text()
    page.click(".stab:has-text('review')")
    assert "Adversarially review" in page.locator(".steppanel .pd").inner_text()
    assert page.locator(".stab.on:has-text('review')").count() == 1


def test_provider_wizard_lists_models_live(page, server):
    """The default-model field offers what the provider actually serves."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Where do completions come from?')")
    page.click("button:has-text('+ Add a provider')")
    page.wait_for_selector(".subwiz-steps")
    page.click(".pill:has-text('LM Studio (local)')")
    page.click("button:has-text('Next →')")
    # entering step 2 auto-fetches; a manual button also exists
    assert page.locator("button:has-text('List models')").is_visible()
    # the fetch resolves either way: live model count, or a loud can't-list note
    page.wait_for_selector(
        "#pw-models-msg:has-text('live from the endpoint'), "
        "#pw-models-msg:has-text('did not list models')", timeout=15_000)
    msg = page.locator("#pw-models-msg").inner_text()
    if "live from the endpoint" in msg:  # LM Studio actually running here
        rows = page.locator("#pw-pick .pl-row")
        assert rows.count() > 0            # VISIBLE pick list, not a hidden datalist
        first = rows.first.inner_text()
        rows.first.click()                 # clicking a row fills the input
        assert page.locator("#pw-model").input_value() == first
    page.click("button:has-text('← Back')")
    page.click("button:has-text('Cancel')")


def test_agent_wizard_coding_cli_models_and_key_hint(page, server):
    """Coding CLIs: own-credentials hint + model choices from the CLI."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Who does the work?')")
    import httpx
    cfg = httpx.get(base + "/api/config", timeout=5).json()
    if "claude" not in cfg.get("coding_clis", {}):
        pytest.skip("claude CLI not installed on this machine")
    page.click("#settings-body >> button:has-text('+ Add an agent')")
    page.wait_for_selector(".subwiz-steps")
    page.click(".pill:has-text('Coding CLI')")
    page.click("button:has-text('Next →')")
    page.click(".pill:has-text('claude')")
    # each harness brings its own credentials — hint names its auth home
    page.wait_for_selector(".hint-panel:has-text('brings its own credentials')")
    assert "claude login" in page.locator(".hint-panel").inner_text()
    # model list arrives from /api/cli_models (static aliases for claude)
    page.wait_for_selector("#aw-cli-msg:has-text('pick from the list')")
    rows = page.locator("#aw-pick .pl-row")
    assert rows.count() >= 3
    # typing filters the visible list live
    page.fill("#aw-model", "sonnet")
    page.locator("#aw-model").dispatch_event("input")
    page.wait_for_selector("#aw-pick .pl-row:has-text('sonnet')")
    assert page.locator("#aw-pick .pl-row").count() < rows.count() or rows.count() == 1
    page.fill("#aw-model", "")
    page.click("button:has-text('← Back')")
    page.click("button:has-text('Cancel')")


def test_agent_wizard_subscription_kind(page, server):
    """Subscription access via signed-in Claude Code / Codex CLI."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    # settings home shows subscription status chips
    page.wait_for_selector(".kv:has-text('SUBSCRIPTIONS')")
    page.click("#settings-body >> button:has-text('+ Add an agent')")
    page.wait_for_selector(".subwiz-steps")
    page.click(".pill:has-text('Subscription (Claude Code / Codex)')")
    assert "No API key stored" in page.locator(".hint-panel").inner_text()
    page.click("button:has-text('Next →')")
    import httpx
    subs = httpx.get(base + "/api/config", timeout=5).json()["subscriptions"]
    if subs["claude"]["installed"]:
        page.click(".pill:has-text('Claude Code (Anthropic subscription)')")
        page.wait_for_selector(".hint-panel:has-text('Signed in'), "
                               ".hint-panel:has-text('Not signed in yet')")
        assert page.locator("#aw-pick .pl-row").count() >= 3
    else:
        assert page.locator(".pill:has-text('Claude Code')").is_disabled()
    page.click("button:has-text('← Back')")
    page.click("button:has-text('Cancel')")


def test_agent_test_sends_provider_ref_not_stale_url(page, server):
    """Regression: with a provider selected, the wizard's Test must resolve
    through the provider — a lingering direct-URL default (localhost:1234)
    must never reach the request (bug: DeepSeek test hit LM Studio)."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Who does the work?')")
    page.click("#settings-body >> button:has-text('+ Add an agent')")
    page.wait_for_selector(".subwiz-steps")
    page.click("button:has-text('Next →')")   # kind: LLM endpoint (default)
    # direct-URL default renders first — this is the value that used to leak
    assert "localhost:1234" in page.locator("#aw-base").input_value()
    page.click(".pill:has-text('LM Studio')")  # provider saved by earlier test
    page.fill("#aw-model", "some-model")
    page.click("button:has-text('Next →')")
    page.fill("#aw-id", "ds-agent")
    page.click("button:has-text('Next →')")

    captured = {}

    def intercept(route):
        captured.update(json=route.request.post_data_json)
        route.fulfill(json={"ok": True, "latency_ms": 1, "reply": "OK"})

    page.route("**/api/test_worker", intercept)
    page.click("button:has-text('Test')")
    page.wait_for_selector("#aw-test .green")
    page.unroute("**/api/test_worker")
    assert captured["json"]["provider"] == "lmstudio"
    assert captured["json"]["base_url"] == ""   # stale default never leaks
    page.click("button:has-text('← Back')")
    page.click("button:has-text('← Back')")
    page.click("button:has-text('← Back')")
    page.click("button:has-text('Cancel')")


def test_agent_edit_flow_readmits_same_id(page, server):
    """Edit = retire + re-register under the same id — must not 409."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector(".prov-item:has-text('e2e-reviewer')")
    page.click(".prov-item:has-text('e2e-reviewer') >> button:has-text('Edit')")
    page.wait_for_selector(".subwiz-steps")
    page.click("button:has-text('Next →')")   # kind -> connection
    page.click("button:has-text('Next →')")   # -> role & prompt
    page.click("button:has-text('Next →')")   # -> test & save
    page.click("button:has-text('Update agent')")
    page.wait_for_selector(".toast.on:has-text('Updated e2e-reviewer')")
    page.wait_for_selector(".prov-item:has-text('e2e-reviewer')")


def test_agent_wizard_timeout_advanced_block(page, server):
    """issue #2: the wizard's collapsed Advanced timeout field is wired
    end-to-end — set on add, persisted to config.json, preloaded on edit.
    Uses a coding_cli agent (Advanced is hidden for mock, which has no
    timeout to apply it to)."""
    base, home = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Who does the work?')")
    import httpx
    clis = list(httpx.get(base + "/api/config", timeout=5).json().get("coding_clis", {}))
    if not clis:
        pytest.skip("no coding CLI installed on this machine")
    cli = clis[0]

    page.click("#settings-body >> button:has-text('+ Add an agent')")
    page.wait_for_selector(".subwiz-steps")
    page.click(".pill:has-text('Coding CLI')")
    page.click("button:has-text('Next →')")
    page.click(f".pill:has-text('{cli}')")
    page.click("button:has-text('Next →')")
    # role & prompt step: open the collapsed Advanced block and set a timeout
    page.wait_for_selector("details summary:has-text('Advanced')")
    page.click("details summary:has-text('Advanced')")
    page.fill("#aw-id", "e2e-timeout-agent")
    page.fill("#aw-timeout", "555")
    page.click("button:has-text('Next →')")
    # step 4 summary line shows the timeout before saving
    assert "timeout 555s" in page.locator(".hint-panel .kv").inner_text()
    page.click("button:has-text('Register agent')")
    page.wait_for_selector(".prov-item:has-text('e2e-timeout-agent')")
    # settings-home agent card also shows it
    assert "timeout 555s" in page.locator(
        ".prov-item:has-text('e2e-timeout-agent') .kv").inner_text()

    import json
    saved = json.loads((home / ".metaharness" / "config.json").read_text())
    agent = next(a for a in saved["agents"] if a["worker_id"] == "e2e-timeout-agent")
    assert agent["timeout_s"] == 555

    # edit flow preloads the saved value
    page.click(".prov-item:has-text('e2e-timeout-agent') >> button:has-text('Edit')")
    page.wait_for_selector(".subwiz-steps")
    page.click("button:has-text('Next →')")   # kind -> connection
    page.click("button:has-text('Next →')")   # -> role & prompt
    page.click("details summary:has-text('Advanced')")
    assert page.locator("#aw-timeout").input_value() == "555"
    page.click("button:has-text('← Back')"); page.click("button:has-text('← Back')")
    page.click("button:has-text('Cancel')")


def test_agent_wizard_kind_switch_to_mock_drops_timeout(page, server):
    """issue #2 spec Part 5 (panel, GLM P2 — the mandated test was missing):
    a timeout entered for coding_cli must not silently persist after switching
    the kind to mock — mock has no timeout to apply it to."""
    base, home = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Who does the work?')")
    import httpx
    clis = list(httpx.get(base + "/api/config", timeout=5).json().get("coding_clis", {}))
    if not clis:
        pytest.skip("no coding CLI installed on this machine")

    page.click("#settings-body >> button:has-text('+ Add an agent')")
    page.wait_for_selector(".subwiz-steps")
    page.click(".pill:has-text('Coding CLI')")
    page.click("button:has-text('Next →')")
    page.click(f".pill:has-text('{clis[0]}')")
    page.click("button:has-text('Next →')")
    # enter a timeout for the coding_cli kind…
    page.click("details summary:has-text('Advanced')")
    page.fill("#aw-id", "e2e-kindswitch")
    page.fill("#aw-timeout", "77")
    # …then walk back and switch the kind to Mock
    page.click("button:has-text('← Back')")
    page.click("button:has-text('← Back')")
    page.click(".pill:has-text('Mock (testing)')")
    page.click("button:has-text('Next →')")
    page.click("button:has-text('Next →')")   # mock needs no connection
    # Advanced (and its timeout) is gone for mock
    assert page.locator("details summary:has-text('Advanced')").count() == 0
    page.click("button:has-text('Next →')")
    assert "timeout" not in page.locator(".hint-panel .kv").inner_text()
    page.click("button:has-text('Register agent')")
    page.wait_for_selector(".prov-item:has-text('e2e-kindswitch')")

    import json
    saved = json.loads((home / ".metaharness" / "config.json").read_text())
    agent = next(a for a in saved["agents"] if a["worker_id"] == "e2e-kindswitch")
    assert agent.get("timeout_s") is None
    # settings card shows no fake timeout either
    assert "timeout" not in page.locator(
        ".prov-item:has-text('e2e-kindswitch') .kv").inner_text()


def test_sweep_every_action_button_is_wired(page, server):
    """Sweep across every view and wizard surface: each onclick handler must
    reference a defined function, and walking the surfaces raises zero page
    errors — no dead buttons anywhere."""
    base, _ = server
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base); page.click("#nav-wizard")
    page.wait_for_selector(".tierrow")

    def dead_handlers():
        return page.evaluate("""() => {
          const dead = [];
          document.querySelectorAll('[onclick]').forEach(el => {
            const m = el.getAttribute('onclick').match(/^\\s*([A-Za-z_$][\\w$]*)\\s*\\(/);
            if(m && typeof window[m[1]] !== 'function')
              dead.push(m[1]);
          });
          return dead;
        }""")

    surfaces = []

    def check(name):
        surfaces.append(name)
        assert dead_handlers() == [], f"dead onclick handler(s) on {name}"

    check("run wizard / agents step")
    page.click("#nav-library"); page.wait_for_selector('[data-harness-id="research"]')
    check("harness library")
    page.click("#nav-settings"); page.wait_for_selector("h2:has-text('Where do completions come from?')")
    check("settings home")
    page.click("button:has-text('+ Add a provider')")
    page.wait_for_selector(".subwiz-steps")
    check("provider wizard step 1")
    page.click(".pill:has-text('DeepSeek')")
    page.click("button:has-text('Next →')")
    check("provider wizard step 2")
    page.click("button:has-text('Next →')")
    check("provider wizard step 3")
    page.click("button:has-text('← Back')"); page.click("button:has-text('← Back')")
    page.click("button:has-text('Cancel')")
    page.click("#settings-body >> button:has-text('+ Add an agent')")
    page.wait_for_selector(".subwiz-steps")
    for kind in ("Coding CLI", "Subscription", "Mock (testing)", "LLM endpoint"):
        page.click(f".pill:has-text('{kind}')")
        check(f"agent wizard step 1 · {kind}")
    page.click("button:has-text('Next →')")
    check("agent wizard step 2 · LLM endpoint")
    page.click("button:has-text('Next →')")
    check("agent wizard step 3 · role & prompt")
    page.click("button:has-text('← Back')"); page.click("button:has-text('← Back')")
    page.click("button:has-text('Cancel')")
    page.click("#nav-console"); page.wait_for_selector("#tiles .tile")
    check("console")
    page.click("#nav-wizard"); page.wait_for_selector(".tierrow")
    page.click("button:has-text('Continue →')"); page.wait_for_selector("#goal")
    check("goal step")

    assert len(surfaces) >= 10
    assert errors == [], f"page errors during sweep: {errors}"


def test_approval_never_flashes_an_error(page, server):
    """Regression: the gate banner lingered until the next 2s poll after
    Approve, so an eager second click hit a 409 and flashed 'failed: run is
    not awaiting approval'. Now the banner hides instantly and every toast
    across a double-clicked triple-gate run is recorded and error-free."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    # record every toast the UI ever shows during the run
    page.evaluate("""() => {
      window.__toasts = [];
      const orig = window.toast;
      window.toast = m => { window.__toasts.push(String(m)); orig(m); };
    }""")
    page.wait_for_selector(".tierrow")
    page.click("button:has-text('Continue →')")
    page.wait_for_selector(".pill:has-text('Software engineering')")
    page.click(".pill:has-text('Software engineering')")
    page.fill("#goal", "double-click gate torture test")
    page.click("#planbtn")
    page.wait_for_selector(".planstep")
    page.click("button:has-text('Run this plan →')")

    for gate in ("specify", "plan", "review"):
        page.wait_for_selector(f".guide b:has-text('Approval needed: {gate}')",
                               timeout=30_000)
        button = page.locator(f"button:has-text('Approve {gate}')")
        button.click()
        # banner must vanish immediately (optimistic), not after the next poll
        page.wait_for_selector(".guide b:has-text('Approval needed')",
                               state="detached", timeout=1_000)
        # an eager second click finds no button — nothing to mis-fire
        assert page.locator(f"button:has-text('Approve {gate}')").count() == 0

    page.wait_for_selector(".guide b:has-text('Run completed.')", timeout=30_000)
    toasts = page.evaluate("() => window.__toasts")
    assert toasts, "expected approval toasts to be recorded"
    bad = [t for t in toasts if "failed" in t.lower() or "already handled" in t]
    assert bad == [], f"error toast flashed during approvals: {bad}"


def test_console_run_ledger_reads_plain_language(page, server):
    """Humanize pass: console runs are ledger rows — the goal (capitalized) is
    the title, the run id and relative date are demoted to a mono meta line,
    each run gets one plain-language story, and the expanded row uses humanized
    verdict wording instead of raw enum values."""
    import httpx
    base, _ = server
    wf = (
        "name: ledger-demo\n"
        "steps:\n"
        "  - id: draft_reply\n"
        "    task_type: transform\n"
        "    objective: Draft a short reply.\n"
    )
    resp = httpx.post(base + "/api/runs", json={
        "workflow_yaml": wf,
        "context": {"goal": "prove the ledger reads like english"}})
    assert resp.status_code == 200, resp.text

    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-console")
    page.wait_for_selector("#view-console .guide b:has-text('Why this page exists')")
    row = page.locator(".runrow", has_text="Prove the ledger reads like english")
    row.wait_for()
    # the machine-y composite name never leads the row
    assert "ledger-demo" not in row.locator(".rr-title").inner_text()
    meta = row.locator(".rr-meta").inner_text()
    assert "run_" in meta and "ago" in meta or "just now" in meta
    # wait for the mock run to finish, then check the plain-language story
    page.wait_for_selector(".runrow:has-text('Finished —')", timeout=20_000)
    # expand: humanized verdict wording (no raw 'unverified' enum text)
    row.click()
    page.wait_for_selector(".rr-out")
    head = page.locator(".rr-out-h").first.inner_text()
    assert "draft reply" in head          # step id prettified
    assert "unverified" not in head       # enum value never shown raw
    assert row.locator(".badge", has_text="done").count() >= 1


def test_settings_reads_as_numbered_questions(page, server):
    """Humanize pass (design handoff 08-admin): Settings is organised as
    numbered plain-language questions, the guide banner explains the page, and
    the tool catalog is collapsed behind a summary instead of dumped inline."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-settings")
    page.wait_for_selector("#view-settings .guide b:has-text('Why this page exists')")
    for q in ("1 · Where do completions come from?", "2 · Who does the work?",
              "3 · What can they use?"):
        page.wait_for_selector(f"h2:has-text('{q}')")
        assert page.locator(f"h2:has-text('{q}')").count() == 1
    # catalog: collapsed by default, tools revealed on toggle
    summary = page.locator("summary:has-text('Browse the full tool catalog')")
    assert summary.count() == 1
    assert page.locator("#settings-body details .mono").first.is_hidden()
    summary.click()
    assert page.locator("#settings-body details .mono").first.is_visible()


def test_console_panels_speak_plain_language(page, server):
    """Every console panel leads with words a person would say: humanized
    headings, MAST failure codes mapped to plain phrases (with a prettified
    fallback for codes the map doesn't know), and agent rows that demote the
    public key to a meta line."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-console")
    for heading in ("Agents", "Audit trail", "Who’s good at what",
                    "Lessons learned", "Why runs fail", "Under the hood"):
        page.wait_for_selector(f".card h2:has-text('{heading}')")
    # MAST mapping: known code -> plain phrase, unknown code -> prettified words
    assert page.evaluate("mastPlain('step_repetition')") == "kept repeating a step"
    assert page.evaluate("mastPlain('brand_new.mode-x')") == "brand new mode x"
    # issue #2 (panel, GLM P2): the new TIMEOUT mode has console vocabulary
    assert page.evaluate("mastPlain('timeout')") == "ran out of time before finishing"
    # agent rows: display name leads, key demoted to the mono meta line
    page.wait_for_selector("#workers .lrow")
    meta = page.locator("#workers .rr-meta").first.inner_text()
    assert "identity key" in meta


def test_console_cards_paginate(page, server):
    """Long cards page instead of growing forever: 8 runs per page, a pager
    with Newer/Older and an 'x–y of N' counter, page position kept across the
    live refresh, and pager clicks never toggle a run row open."""
    import httpx
    base, _ = server
    wf = ("name: page-filler\nsteps:\n"
          "  - id: noop\n    task_type: transform\n    objective: Tiny.\n")
    for i in range(10):
        httpx.post(base + "/api/runs", json={
            "workflow_yaml": wf,
            "context": {"goal": f"pagination probe number {i}"}})

    page.goto(base); page.click("#nav-wizard")
    page.click("#nav-console")
    page.wait_for_selector("#runs .pager")
    assert page.locator("#runs .runrow").count() == 8
    info = page.locator("#runs .pager-info").inner_text()
    assert info.startswith("1–8 of ")
    # newest first: the last run posted leads page one
    first = page.locator("#runs .rr-title").first.inner_text()
    assert "Pagination probe number 9" in first

    page.click("#runs button[data-page='runs:1']")          # Older →
    page.wait_for_selector("#runs .pager-info:has-text('9–')")
    assert "Pagination probe number 9" not in page.locator("#runs .rr-title").first.inner_text()
    # paging is not a row click — nothing expanded
    assert page.locator("#runs .rr-detail").count() == 0

    page.click("#runs button[data-page='runs:-1']")         # ← Newer
    page.wait_for_selector("#runs .pager-info:has-text('1–8')")
    assert "Pagination probe number 9" in page.locator("#runs .rr-title").first.inner_text()


def test_console_ledger_survives_non_string_goal(page, server):
    """Regression (codex review P2): /api/runs context is arbitrary JSON, so a
    run posted with a non-string goal must not crash the ledger — the title
    falls back to the workflow name and no page error fires."""
    import httpx
    base, _ = server
    wf = (
        "name: numeric-goal-demo\n"
        "steps:\n"
        "  - id: noop\n"
        "    task_type: transform\n"
        "    objective: Do a tiny thing.\n"
    )
    resp = httpx.post(base + "/api/runs",
                      json={"workflow_yaml": wf, "context": {"goal": 123}})
    assert resp.status_code == 200, resp.text

    errors: list[str] = []
    handler = lambda e: errors.append(str(e))  # noqa: E731
    page.on("pageerror", handler)
    try:
        page.goto(base); page.click("#nav-wizard")
        page.click("#nav-console")
        row = page.locator(".runrow", has_text="Numeric goal demo")
        row.wait_for()
    finally:
        page.remove_listener("pageerror", handler)
    assert errors == [], f"page JS errors rendering non-string goal: {errors}"


def test_console_approval_survives_hostile_step_id(page, server):
    """Regression (codex review P1): step ids are user-authored, so an id
    containing a quote must not break the console's Approve button. The fix
    moved run/step ids out of inline JS strings into HTML-escaped data-*
    attributes with a delegated click handler."""
    import httpx
    base, _ = server
    wf = (
        "name: hostile-gate\n"
        "steps:\n"
        "  - id: \"it's-a-gate\"\n"
        "    task_type: transform\n"
        "    objective: Wait for a human.\n"
        "    hitl: true\n"
    )
    resp = httpx.post(base + "/api/runs", json={"workflow_yaml": wf, "context": {}})
    assert resp.status_code == 200, resp.text

    errors: list[str] = []
    handler = lambda e: errors.append(str(e))  # noqa: E731
    page.on("pageerror", handler)
    try:
        page.goto(base); page.click("#nav-wizard")
        page.click("#nav-console")
        row = page.locator(".runrow", has_text="Hostile gate")
        row.wait_for()
        row.locator("button:has-text('Approve')").click()
        page.wait_for_selector(".runrow:has-text('Finished')", timeout=20_000)
    finally:
        page.remove_listener("pageerror", handler)
    assert errors == [], f"page JS errors during hostile-id approval: {errors}"


def test_console_post_artifact_gate_exposes_output_before_approval(page, server):
    """A post-step gate must show the artifact the human is deciding on."""
    import httpx
    base, _ = server
    wf = (
        "name: post-artifact-gate\n"
        "steps:\n"
        "  - id: draft\n"
        "    task_type: transform\n"
        "    objective: Produce the artifact to review.\n"
        "    hitl: true\n"
        "    hitl_timing: after\n"
    )
    resp = httpx.post(base + "/api/runs", json={"workflow_yaml": wf, "context": {}})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    page.goto(base); page.click("#nav-console")
    row = page.locator(".runrow", has_text="Post artifact gate")
    row.wait_for()
    assert row.locator("button:has-text('Approve')").count() == 1
    page.evaluate("id => { openRuns.add(id); return refreshConsole(); }", run_id)
    assert page.locator("#runs .rr-detail .rr-out").count() == 1
    assert "nothing recorded yet" not in page.locator("#runs").inner_text()


def test_custom_workflow_built_by_wizard_runs(page, server):
    """Wizard-driven custom workflow: two steps authored step-by-step (with a
    verifiable check, a dependency and a gate), reviewed, run to completion."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.wait_for_selector(".tierrow")
    page.click("button:has-text('Continue →')")
    page.wait_for_selector(".pill:has-text('Custom (build by hand)')")
    page.click(".pill:has-text('Custom (build by hand)')")
    assert "No planner involved" in page.locator(".hint-panel").inner_text()
    page.fill("#goal", "triage the incident by hand")
    page.click("#planbtn")

    # step builder, sub-wizard 1: objective
    page.wait_for_selector("h2:has-text('Add step 1')")
    page.fill("#sb-id", "classify")
    page.fill("#sb-obj", "Classify severity as exactly one of: low, high.")
    page.click("button:has-text('Next →')")
    # sub 2: type & tools
    page.click(".pill:has-text('classify')")
    page.click("button:has-text('Next →')")
    # sub 3: verify & gate
    page.select_option("#sb-check", "one_of")
    page.fill("#sb-checkval", "low, high")
    page.click("button:has-text('Add step to workflow')")

    # second step depends on the first, behind a gate
    page.wait_for_selector("h2:has-text('Add step 2')")
    page.fill("#sb-id", "page-oncall")
    page.fill("#sb-obj", "Draft the on-call page for the classified severity.")
    page.click("button:has-text('Next →')")
    page.click("button:has-text('Next →')")
    page.click(".pill:has-text('classify')")     # dependency toggle
    page.check("#sb-hitl")
    page.click("button:has-text('Add step to workflow')")

    page.click("button:has-text('Done — review workflow →')")
    page.wait_for_selector(".planstep")
    assert page.locator(".planstep").count() == 2
    assert page.locator(".badge:has-text('custom — built by hand')").count() == 1
    assert page.locator(".badge:has-text('verifiable')").count() == 1
    assert page.locator(".badge:has-text('HITL')").count() == 1

    page.click("button:has-text('Run this plan →')")
    page.wait_for_selector(".guide b:has-text('Approval needed: page-oncall')",
                           timeout=30_000)
    page.click("button:has-text('Approve page-oncall')")
    page.wait_for_selector(".guide b:has-text('Run completed.')", timeout=30_000)


def test_template_plan_is_editable_inline_and_via_yaml(page, server):
    """The LLM/template-suggested plan is editable: inline step edit, delete,
    reorder, plus YAML mode with validation that refuses bad specs."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.wait_for_selector(".tierrow")
    page.click("button:has-text('Continue →')")
    page.wait_for_selector(".pill:has-text('Software engineering')")
    page.click(".pill:has-text('Software engineering')")
    page.fill("#goal", "editable plan test")
    page.click("#planbtn")
    page.wait_for_selector(".planstep")

    # inline edit the first step: tweak objective, add a gate
    page.locator(".planstep .step-actions button[title='edit']").first.click()
    page.wait_for_selector(".step-edit")
    page.fill("#se-obj", "EDITED objective: survey the workspace read-only.")
    page.check("#se-hitl")
    page.click("button:has-text('Save step')")
    page.wait_for_selector(".badge:has-text('edited by you')")
    assert "EDITED objective" in page.locator(".planstep").first.inner_text()
    assert page.locator(".badge:has-text('HITL')").count() == 4  # 3 template + 1 new

    # delete the last step; dependent references are cleaned up
    before = page.locator(".planstep").count()
    page.locator(".planstep .step-actions button[title='remove']").last.click()
    assert page.locator(".planstep").count() == before - 1

    # YAML mode: a duplicate id is refused with the validator's message
    page.click("button:has-text('Edit as YAML')")
    page.wait_for_selector("#yaml-box")
    yaml_text = page.locator("#yaml-box").input_value()
    assert "steps:" in yaml_text
    page.fill("#yaml-box", yaml_text.replace("id: specify", "id: explore", 1))
    page.click("button:has-text('Apply YAML')")
    page.wait_for_selector("#yamlmsg:has-text('invalid')")
    # restore a valid spec and apply
    page.fill("#yaml-box", yaml_text)
    page.click("button:has-text('Apply YAML')")
    page.wait_for_selector(".planstep")


def test_branching_workflow_in_wizard_skips_untaken_path(page, server):
    """Option-1 branching end-to-end: classify (mock answers the first one_of
    option, 'low') -> page-oncall runs only 'if classify equals high' and is
    visibly SKIPPED; archive runs 'if classify equals low'."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.wait_for_selector(".tierrow")
    page.click("button:has-text('Continue →')")
    page.wait_for_selector(".pill:has-text('Custom (build by hand)')")
    page.click(".pill:has-text('Custom (build by hand)')")
    page.fill("#goal", "branching triage")
    page.click("#planbtn")

    # step 1: classify with a one_of check
    page.wait_for_selector("h2:has-text('Add step 1')")
    page.fill("#sb-id", "classify")
    page.fill("#sb-obj", "Classify severity as exactly one of: low, high.")
    page.click("button:has-text('Next →')")
    page.click(".pill:has-text('classify')")
    page.click("button:has-text('Next →')")
    page.select_option("#sb-check", "one_of")
    page.fill("#sb-checkval", "low, high")
    page.click("button:has-text('Add step to workflow')")

    # step 2: page-oncall, branch: if classify equals high
    page.wait_for_selector("h2:has-text('Add step 2')")
    page.fill("#sb-id", "page-oncall")
    page.fill("#sb-obj", "Draft the on-call page.")
    page.click("button:has-text('Next →')")
    page.click("button:has-text('Next →')")
    page.select_option("#sb-when-step", "classify")
    page.select_option("#sb-when-kind", "equals")
    page.fill("#sb-when-val", "high")
    page.click("button:has-text('Add step to workflow')")

    # step 3: archive, branch: if classify equals low
    page.wait_for_selector("h2:has-text('Add step 3')")
    page.fill("#sb-id", "archive")
    page.fill("#sb-obj", "Archive the ticket quietly.")
    page.click("button:has-text('Next →')")
    page.click("button:has-text('Next →')")
    page.select_option("#sb-when-step", "classify")
    page.fill("#sb-when-val", "low")
    page.click("button:has-text('Add step to workflow')")

    page.click("button:has-text('Done — review workflow →')")
    page.wait_for_selector(".planstep")
    # review shows the branch conditions as badges
    assert page.locator(".badge:has-text('if classify equals high')").count() == 1
    assert page.locator(".badge:has-text('if classify equals low')").count() == 1

    page.click("button:has-text('Run this plan →')")
    page.wait_for_selector(".guide b:has-text('Run completed.')", timeout=30_000)
    # tabs: two ran, one skipped (⤳); the skip reason lives in its tab's panel
    assert page.locator(".stab .ticon.done").count() == 2  # classify + archive
    page.click(".stab:has(.ticon:has-text('⤳'))")
    assert page.locator(".badge:has-text('skipped — condition not met')").count() == 1
    assert "if classify equals high" in page.locator(".steppanel").inner_text()


def test_humanized_output_markdown_json_and_xss(page, server):
    """v0.4 humanize contract: markdown subset renders, JSON becomes a
    collapsible tree, and worker output can NEVER smuggle markup or script."""
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.wait_for_selector(".stepper .s.on")

    # markdown: heading + GFM table + inline styles + allowlisted link
    md = "# Title\\n\\n| a | b |\\n|---|---|\\n| 1 | 2 |\\n\\n**bold** `code` [ok](https://example.com)"
    html = page.evaluate(f"humanizeOutput('{md}')")
    assert "<table>" in html and "<th>a</th>" in html and "<td>1</td>" in html
    assert "<b>bold</b>" in html and "<code>code</code>" in html
    assert 'href="https://example.com"' in html and 'rel="noopener noreferrer"' in html

    # JSON string → typed collapsible tree
    html = page.evaluate(
        "humanizeOutput(JSON.stringify({all_met: true, criteria: ['a', 2]}))")
    assert "<details" in html and "jbool" in html and "jstr" in html and "jnum" in html

    # XSS probes: raw HTML inert, javascript:/data: links refused,
    # no transforms inside code fences
    html = page.evaluate("humanizeOutput('<img src=x onerror=alert(1)>')")
    assert "<img" not in html
    html = page.evaluate("humanizeOutput('# h\\n<script>alert(1)</script>')")
    assert "<script" not in html
    # refused schemes stay inert TEXT (never become an href)
    html = page.evaluate("humanizeOutput('[x](javascript:alert(1)) **b**')")
    assert "href" not in html and "<b>b</b>" in html
    html = page.evaluate("humanizeOutput('[x](data:text/html,foo) **b**')")
    assert "href" not in html
    html = page.evaluate("humanizeOutput('```\\n**not bold** <b>raw</b>\\n```')")
    assert "<b>" not in html.replace("**not bold**", "")
    # step ids with quotes can't break out of attributes (esc covers ')
    assert page.evaluate("esc(\"a'b\")") == "a&#39;b"


def test_home_landing_shows_next_action_and_metrics(page, server):
    """The calm landing: eyebrow date, one next-action card, stat tiles, and
    orientation cards — Home is the default view."""
    base, _ = server
    page.goto(base)
    page.wait_for_selector(".next-action h2")
    assert page.locator("#nav-home.on").count() == 1
    assert page.locator("#home-tiles .tile").count() == 3
    assert page.locator(".next-action .btn").is_visible()
    page.wait_for_selector("h2:has-text('Latest result')")
    page.wait_for_selector("h2:has-text('Self-tuning')")


def test_help_page_explains_the_sparkle(page, server):
    base, _ = server
    page.goto(base)
    page.click("#nav-help")
    page.wait_for_selector("h1:has-text('How to drive the harness')")
    assert page.locator("h2:has-text('AI insights')").count() == 1
    assert page.locator("#view-help .ai-chip").count() >= 1


def test_tuning_card_controls_present(page, server):
    base, _ = server
    page.goto(base)
    page.click("#nav-console")
    page.wait_for_selector("h2:has-text('Harness tuning')")
    page.wait_for_selector("#tune-suite")
    assert page.locator("button[data-tune-start]").is_visible()


def test_goal_step_has_prompt_assistant(page, server):
    base, _ = server
    page.goto(base); page.click("#nav-wizard")
    page.wait_for_selector(".tierrow")
    page.click("button:has-text('Continue →')")
    page.wait_for_selector("#goal")
    assert page.locator("#advise-goal-btn").is_visible()
    page.fill("#goal", "fix the disk thing on db-1")
    page.click("#advise-goal-btn")
    page.wait_for_selector("#goal-advice .advisor, #goal-advice .empty")


def test_failures_card_advisor_panel_and_actions(page, server):
    """Card-level advisor (codex F4): the ✦ header button on 'Why runs fail'
    opens a panel whose advisory chip + read survive a live refresh tick; its
    action buttons carry params — a valid-suite start_tune fires POST
    /api/optimization/runs for THAT suite, a missing/invalid-suite action
    renders no mutating button, and open_settings reaches the Settings view."""
    base, _ = server
    # deterministic advice: one valid-suite start_tune, one invalid-suite
    # add_coverage (must be dropped), one navigation action
    fake = {
        "read": "The extract cluster is the top failure — a tuning experiment could shrink it.",
        "next_actions": [
            {"action": "start_tune", "label": "Tune the mixed suite",
             "params": {"suite": "mixed"}},
            {"action": "add_coverage", "label": "Broaden a ghost suite",
             "params": {"suite": "ghost-suite"}},
            {"action": "open_settings", "label": "Open Settings"},
            {"action": "none", "label": "nothing to do"},
        ],
        "advisory": True, "model": "mock",
    }
    page.route("**/api/advise", lambda route: route.fulfill(json=fake))
    started = {}
    page.route("**/api/optimization/runs",
               lambda route: (started.update(json=route.request.post_data_json),
                              route.fulfill(json={"ok": True}))[-1])

    page.goto(base); page.click("#nav-console")
    card = "div.card:has(#failures)"
    page.wait_for_selector(f"{card} h2:has-text('Why runs fail')")

    # header sparkle opens the panel: advisory chip + the exact read text
    page.click(f"{card} button[data-advise-page='failures']")
    page.wait_for_selector("#failures .advisor")
    assert page.locator("#failures .ai-chip").count() == 1
    assert "advisory, not verified" in page.locator(
        "#failures .ai-chip").inner_text().lower()
    assert "extract cluster is the top failure" in page.locator(
        "#failures .advisor .takes p").inner_text()

    # render guard: invalid-suite add_coverage dropped, 'none' dropped —
    # only start_tune + open_settings remain as mutating/nav buttons
    acts = page.locator("#failures .advisor button[data-advise-act]")
    assert acts.count() == 2
    labels = acts.all_inner_texts()
    assert "Tune the mixed suite" in labels
    assert "Open Settings" in labels
    assert not any("ghost" in t for t in labels)

    # survives one live refresh tick (re-appended from cache, no re-fetch)
    page.wait_for_timeout(3300)
    assert page.locator("#failures .ai-chip").count() == 1
    assert "extract cluster is the top failure" in page.locator(
        "#failures .advisor .takes p").inner_text()

    # start_tune carries its params.suite through to the API
    page.click("#failures .advisor button:has-text('Tune the mixed suite')")
    page.wait_for_selector(".toast.on:has-text('Tuning started on the mixed suite')")
    assert started["json"]["suite"] == "mixed"

    # open_settings navigation action reaches the Settings view
    page.wait_for_selector("#failures .advisor button:has-text('Open Settings')")
    page.click("#failures .advisor button:has-text('Open Settings')")
    page.wait_for_selector("#nav-settings.on")
    assert page.locator("#view-settings").is_visible()

    page.unroute("**/api/advise")
    page.unroute("**/api/optimization/runs")


def test_routing_facts_zero_traffic_pool_elects_no_leader(page, server):
    """Regression (review F-A): routingFacts seeded its leader scan with
    best = -1, so a freshly-wired pool with zero routed traffic falsely crowned
    members[0] as 'routing here'. best now starts at 0 (the wizard's leadN
    convention at renderAgentsStep) — an all-zero pool claims no leader."""
    base, _ = server
    page.goto(base); page.click("#nav-console")
    page.wait_for_selector("#tiles .tile")
    zero = page.evaluate("""() => routingFacts({
      small: {members: [{worker_id: 'w1', model: 'm1'}], routed: {}}})""")
    assert "routing here" not in zero.replace("nobody routing here yet", "")
    assert "nobody routing here yet" in zero
    # sanity: real traffic still elects the right leader
    routed = page.evaluate("""() => routingFacts({
      small: {members: [{worker_id: 'w1', model: 'm1'},
                        {worker_id: 'w2', model: 'm2'}], routed: {w2: 3}}})""")
    assert "m2 routing here" in routed


def test_card_advice_closed_while_pending_stays_closed(page, server):
    """Regression (review F-B): closing a card panel while its /api/advise POST
    was still in flight let the late response rewrite CARD_ADVICE and zombie-
    reopen the panel with the sparkle's .on state desynced. The response now
    commits only if its own request token is still the cached entry."""
    base, _ = server
    pending = []
    page.route("**/api/advise", lambda route: pending.append(route))
    page.goto(base); page.click("#nav-console")
    card = "div.card:has(#failures)"
    page.wait_for_selector(f"{card} h2:has-text('Why runs fail')")
    spark = f"{card} button[data-advise-page='failures']"

    page.click(spark)                          # open -> loading panel
    page.wait_for_selector("#failures .advisor")
    page.click(spark)                          # close while the POST is pending
    page.wait_for_selector("#failures .advisor", state="detached")
    assert pending, "the advise request should have been captured"
    pending[0].fulfill(json={"read": "zombie advice", "next_actions": [],
                             "advisory": True, "model": "mock"})
    page.wait_for_timeout(3500)                # a full live-refresh tick
    assert page.locator("#failures .advisor").count() == 0
    assert "zombie advice" not in page.locator("#failures").inner_text()
    assert page.locator(f"{spark}.on").count() == 0   # sparkle state in sync
    page.unroute("**/api/advise")


def test_card_advice_facts_drift_disables_mutating_actions(page, server):
    """Regression (review F-C): cached advice outlives the facts it was
    computed from. When the card's data changes on the live loop, the panel
    keeps the read but shows a 'facts changed' note and drops mutating
    (suite-targeting) buttons; navigation actions stay."""
    base, _ = server
    fake = {
        "read": "drift probe read",
        "next_actions": [
            {"action": "start_tune", "label": "Tune the mixed suite",
             "params": {"suite": "mixed"}},
            {"action": "open_settings", "label": "Open Settings"},
        ],
        "advisory": True, "model": "mock",
    }
    page.route("**/api/advise", lambda route: route.fulfill(json=fake))
    page.route("**/api/failures",
               lambda route: route.fulfill(json={"transform": {"tool_error": 3}}))
    page.goto(base); page.click("#nav-console")
    card = "div.card:has(#failures)"
    page.wait_for_selector(f"{card} h2:has-text('Why runs fail')")
    page.wait_for_selector("#failures td:has-text('3×')")   # data A on screen

    page.click(f"{card} button[data-advise-page='failures']")
    page.wait_for_selector("#failures .advisor .takes p:has-text('drift probe read')")
    assert page.locator(
        "#failures .advisor button:has-text('Tune the mixed suite')").count() == 1

    # the failure counts change on the next live tick -> facts fingerprint drifts
    page.route("**/api/failures",
               lambda route: route.fulfill(json={"transform": {"tool_error": 9}}))
    page.wait_for_selector(
        "#failures .advisor:has-text('facts changed since this advice')",
        timeout=8_000)
    assert "drift probe read" in page.locator(
        "#failures .advisor .takes p").inner_text()       # read stays visible
    assert page.locator(
        "#failures .advisor button:has-text('Tune the mixed suite')").count() == 0
    assert page.locator(
        "#failures .advisor button:has-text('Open Settings')").count() == 1
    page.unroute("**/api/advise")
    page.unroute("**/api/failures")


def test_tuning_advice_start_tune_ignores_advisory_params_suite(page, server):
    """Regression (review F-D): behavior-preservation pin for the tuning card.
    renderAdvicePanel deliberately embeds NO data-params, so a tuning-row
    start_tune posts {suite: TUNE.suite} even when the advisory action carries
    a (valid) params.suite. If data-params is ever added to renderAdvicePanel,
    the handler would honor 'math' here and this test fails."""
    base, _ = server
    page.goto(base); page.click("#nav-console")
    page.wait_for_selector("h2:has-text('Harness tuning')")
    started = {}
    page.route("**/api/optimization/runs",
               lambda route: (started.update(json=route.request.post_data_json),
                              route.fulfill(json={"ok": True}))[-1])
    # inject a tuning-row advice panel whose action names a valid other suite,
    # and click it — all in one synchronous evaluate so the 3s refresh can't race
    page.evaluate("""() => {
      ADVICE['math/c1'] = {read: 'r', next_actions: [
        {action: 'start_tune', label: 'Tune it', params: {suite: 'math'}}]};
      const el = document.getElementById('tuning');
      el.innerHTML = renderAdvicePanel('math/c1', {});
      el.querySelector('button[data-advise-act]').click();
    }""")
    page.wait_for_selector(".toast.on:has-text('Tuning started on the mixed suite')")
    assert started["json"]["suite"] == "mixed"   # TUNE.suite, not the advisory's math
    # review K-A2 extension: with no advisory proposer, the user's dropdown
    # (TUNE.proposer, default 'rule') rides along instead of a silent omission
    assert started["json"]["proposer"] == "rule"
    page.unroute("**/api/optimization/runs")


def test_tuning_row_advice_closed_while_pending_stays_closed(page, server):
    """Regression (review K-D1): the tuning-row sparkle listener kept the
    pre-F-B unconditional ADVICE[key] assignment, so closing a row panel while
    its /api/advise POST was pending let the late response repopulate the cache
    and zombie-reopen the panel on the next refresh. Same token guard as F-B.
    Tuning rows only render with real candidates, so the row's sparkle button
    is injected; the assertions target the ADVICE cache the panels render from."""
    base, _ = server
    pending = []
    page.route("**/api/advise", lambda route: pending.append(route))
    page.goto(base); page.click("#nav-console")
    page.wait_for_selector("h2:has-text('Harness tuning')")
    inject = """() => {
      const el = document.getElementById('tuning');
      el.innerHTML = '<button class="why" data-advise="c9" data-suite="mixed"></button>';
      el.querySelector('button[data-advise]').click();
    }"""
    page.evaluate(inject)                 # open: caches the loading marker
    assert page.evaluate("() => !!ADVICE['mixed/c9']")
    for _ in range(50):                   # the intercepted POST is now pending
        if pending:
            break
        page.wait_for_timeout(100)
    assert pending, "the advise request should have been captured"
    page.evaluate(inject)                 # close while the POST is in flight
    assert page.evaluate("() => ADVICE['mixed/c9'] === undefined")
    pending[0].fulfill(json={"read": "zombie row advice", "next_actions": [],
                             "advisory": True, "model": "mock"})
    page.wait_for_timeout(800)            # let the late response resolve
    assert page.evaluate("() => ADVICE['mixed/c9'] === undefined"), \
        "late advise response must not repopulate a closed tuning-row panel"
    page.unroute("**/api/advise")


def test_advisor_start_tune_carries_validated_tuning_knobs(page, server):
    """Regression (review K-A1/A2): advisor-triggered start_tune posted only
    {suite}, silently dropping the advisor's params AND the user's proposer
    dropdown (the manual button already sent TUNE.proposer). It now posts
    proposer (valid params.proposer, else TUNE.proposer) plus rounds/k when
    they are sane positive ints (rounds<=12, k<=5) — junk values are omitted
    so TuneRequest's server defaults apply."""
    base, _ = server
    page.goto(base); page.click("#nav-console")
    page.wait_for_selector("h2:has-text('Harness tuning')")
    posts = []
    page.route("**/api/optimization/runs",
               lambda route: (posts.append(route.request.post_data_json),
                              route.fulfill(json={"ok": True}))[-1])
    # full advisory params ride through
    page.evaluate("() => runAdviseAction('start_tune', "
                  "{suite: 'mixed', proposer: 'llm', rounds: 4, k: 2}, null)")
    assert posts[0] == {"suite": "mixed", "proposer": "llm", "rounds": 4, "k": 2}
    # junk knobs dropped; bogus proposer falls back to the user's dropdown pick
    page.evaluate("() => { TUNE.proposer = 'code'; }")
    page.evaluate("() => runAdviseAction('start_tune', "
                  "{suite: 'mixed', proposer: 'bogus', rounds: -3, k: 99}, null)")
    assert posts[1] == {"suite": "mixed", "proposer": "code"}
    page.unroute("**/api/optimization/runs")


def test_dynamic_suite_from_optimization_payload_is_advisable(page, server):
    """Regression (review K-C1): the client suite list was a static four-name
    constant, so a suite living on disk (advisable by the backend, whose
    context.suites comes from real dirs) was dropped by the render guard and
    missing from the tuning dropdown. The list now unions the built-in
    defaults with every suite /api/optimization reports on the live loop."""
    base, _ = server
    fifth = {"suite": "code", "running": False, "active": False,
             "candidates": [], "frontier": [], "promoted": None,
             "pending": None, "report": None, "findings": []}
    page.route("**/api/optimization", lambda route: route.fulfill(json=[fifth]))
    fake = {"read": "the code suite needs a tune",
            "next_actions": [{"action": "start_tune", "label": "Tune the code suite",
                              "params": {"suite": "code"}}],
            "advisory": True, "model": "mock"}
    page.route("**/api/advise", lambda route: route.fulfill(json=fake))
    started = {}
    page.route("**/api/optimization/runs",
               lambda route: (started.update(json=route.request.post_data_json),
                              route.fulfill(json={"ok": True}))[-1])

    page.goto(base); page.click("#nav-console")
    page.wait_for_selector("#tune-suite")
    options = page.locator("#tune-suite option").all_inner_texts()
    assert any("code" in o for o in options)    # disk suite joins the dropdown
    assert any("mixed" in o for o in options)   # built-ins stay startable (union)

    # a card-level advisory action naming the disk suite survives the render
    # guard and posts for THAT suite
    card = "div.card:has(#failures)"
    page.click(f"{card} button[data-advise-page='failures']")
    page.wait_for_selector(
        "#failures .advisor button:has-text('Tune the code suite')")
    page.click("#failures .advisor button:has-text('Tune the code suite')")
    page.wait_for_selector(".toast.on:has-text('Tuning started on the code suite')")
    assert started["json"]["suite"] == "code"
    for u in ("**/api/optimization", "**/api/advise", "**/api/optimization/runs"):
        page.unroute(u)


def test_bad_data_params_degrades_to_empty_not_a_throw(page, server):
    """Regression (review G-FU2): JSON.parse(act.dataset.params) was unguarded
    in BOTH advise-action listeners, so a malformed data-params attribute threw
    inside the click handler — dead button plus console error. btnParams() now
    degrades to {} and the action still runs; the navigation assertions are the
    load-bearing check (an unfixed throw means Settings is never reached)."""
    base, _ = server
    errors = []
    handler = lambda e: errors.append(str(e))  # noqa: E731
    page.on("pageerror", handler)
    try:
        page.goto(base); page.click("#nav-console")
        page.wait_for_selector("h2:has-text('Harness tuning')")
        # tuning listener: broken JSON in data-params on a navigation action
        page.evaluate("""() => {
          const el = document.getElementById('tuning');
          el.innerHTML = '<button data-advise-act="open_settings" data-params="{broken json">go</button>';
          el.querySelector('button[data-advise-act]').click();
        }""")
        page.wait_for_selector("#nav-settings.on")
        # card-advisor listener: same probe inside the failures card
        page.click("#nav-console")
        page.wait_for_selector("h2:has-text('Why runs fail')")
        page.evaluate("""() => {
          const el = document.getElementById('failures');
          el.innerHTML = '<button data-advise-act="open_settings" data-params="not json at all">go</button>';
          el.querySelector('button[data-advise-act]').click();
        }""")
        page.wait_for_selector("#nav-settings.on")
    finally:
        page.remove_listener("pageerror", handler)
    assert errors == [], f"bad data-params must never throw: {errors}"


def test_console_cards_can_be_rearranged(page, server):
    """Cards move with the hover ‹ › controls and the order persists in
    localStorage for the next visit."""
    base, _ = server
    page.goto(base)
    page.click("#nav-console")
    page.wait_for_selector("#tiles .tile")
    ids = page.evaluate("() => [...document.querySelectorAll('#view-console .grid .card')]"
                        ".map(c => c.dataset.cardId)")
    page.hover("#view-console .grid .card:first-child")
    page.click("#view-console .grid .card:first-child button[data-move='1']")
    ids2 = page.evaluate("() => [...document.querySelectorAll('#view-console .grid .card')]"
                         ".map(c => c.dataset.cardId)")
    assert ids2[0] == ids[1] and ids2[1] == ids[0]
    saved = page.evaluate("() => JSON.parse(localStorage.getItem('console-card-order'))")
    assert saved == ids2
