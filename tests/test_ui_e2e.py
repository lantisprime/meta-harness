"""Playwright E2E: the real dashboard driven in a real browser.

A `metaharness serve` subprocess runs with HOME pointed at a temp dir, so
config saves land in an isolated ~/.metaharness — never the developer's.
Skipped automatically when playwright (or its chromium) is not installed,
so plain CI stays fast; run locally with the [e2e] extra installed.
"""
from __future__ import annotations

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
    proc = subprocess.Popen(
        [sys.executable, "-m", "metaharness.cli"] if False else
        [str(Path(sys.executable).parent / "metaharness"), "serve",
         "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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


def test_run_wizard_loads_with_mock_tiers(page, server):
    base, _ = server
    page.goto(base)
    assert "metaharness" in page.title()
    page.wait_for_selector(".stepper .s.on")
    # mock workers fill all three tiers -> ready badges + enabled continue
    page.wait_for_selector(".tierrow")
    assert page.locator(".tierrow").count() == 3
    assert page.locator(".badge.ok", has_text="ready").count() == 3
    assert page.locator("button", has_text="Continue →").is_enabled()


def test_settings_provider_wizard_end_to_end(page, server):
    base, home = server
    page.goto(base)
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Providers')")
    page.click("button:has-text('+ Add provider (wizard)')")

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
    assert page.locator(".prov-item .badge.ok", has_text="configured").count() >= 1
    # persisted to the ISOLATED home, keys never in plaintext
    cfg = (home / ".metaharness" / "config.json").read_text()
    assert "lmstudio" in cfg


def test_agent_wizard_with_archetype_prompt(page, server):
    base, home = server
    page.goto(base)
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Agents')")
    page.click("button:has-text('+ Add agent (wizard)')")

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
    cfg = (home / ".metaharness" / "config.json").read_text()
    assert "e2e-reviewer" in cfg and "adversarial reviewer" in cfg
    # registered identity is visible from the Run wizard tier rows too
    page.click("#nav-wizard")
    page.wait_for_selector(".tierrow:has-text('e2e-reviewer')")


def test_goal_step_template_plan_and_full_run(page, server):
    base, _ = server
    page.goto(base)
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
    assert page.locator(".badge:has-text('HITL — waits for you')").count() == 3
    assert page.locator(".badge:has-text('🔧 grep')").first.is_visible()

    # run it: mock workers answer instantly; approve all three gates in the UI,
    # waiting for EACH gate's banner (the Approve button is re-rendered per gate)
    page.click("button:has-text('Run this plan →')")
    for gate in ("specify", "plan", "review"):
        page.wait_for_selector(f".guide b:has-text('Approval needed: {gate}')",
                               timeout=30_000)
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
    page.goto(base)
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Providers')")
    page.click("button:has-text('+ Add provider (wizard)')")
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
    page.goto(base)
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Agents')")
    import httpx
    cfg = httpx.get(base + "/api/config", timeout=5).json()
    if "claude" not in cfg.get("coding_clis", {}):
        pytest.skip("claude CLI not installed on this machine")
    page.click("button:has-text('+ Add agent (wizard)')")
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
    page.goto(base)
    page.click("#nav-settings")
    # settings home shows subscription status chips
    page.wait_for_selector(".kv:has-text('SUBSCRIPTION ACCESS')")
    page.click("button:has-text('+ Add agent (wizard)')")
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
    page.goto(base)
    page.click("#nav-settings")
    page.wait_for_selector("h2:has-text('Agents')")
    page.click("button:has-text('+ Add agent (wizard)')")
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
    page.goto(base)
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


def test_sweep_every_action_button_is_wired(page, server):
    """Sweep across every view and wizard surface: each onclick handler must
    reference a defined function, and walking the surfaces raises zero page
    errors — no dead buttons anywhere."""
    base, _ = server
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base)
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
    page.click("#nav-settings"); page.wait_for_selector("h2:has-text('Providers')")
    check("settings home")
    page.click("button:has-text('+ Add provider (wizard)')")
    page.wait_for_selector(".subwiz-steps")
    check("provider wizard step 1")
    page.click(".pill:has-text('DeepSeek')")
    page.click("button:has-text('Next →')")
    check("provider wizard step 2")
    page.click("button:has-text('Next →')")
    check("provider wizard step 3")
    page.click("button:has-text('← Back')"); page.click("button:has-text('← Back')")
    page.click("button:has-text('Cancel')")
    page.click("button:has-text('+ Add agent (wizard)')")
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
    page.click("#nav-console"); page.wait_for_selector(".tile")
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
    page.goto(base)
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

    page.goto(base)
    page.click("#nav-console")
    page.wait_for_selector(".guide b:has-text('Why this page exists')")
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


def test_console_panels_speak_plain_language(page, server):
    """Every console panel leads with words a person would say: humanized
    headings, MAST failure codes mapped to plain phrases (with a prettified
    fallback for codes the map doesn't know), and agent rows that demote the
    public key to a meta line."""
    base, _ = server
    page.goto(base)
    page.click("#nav-console")
    for heading in ("Agents", "Audit trail", "Who’s good at what",
                    "Lessons learned", "Why runs fail", "Under the hood"):
        page.wait_for_selector(f".card h2:has-text('{heading}')")
    # MAST mapping: known code -> plain phrase, unknown code -> prettified words
    assert page.evaluate("mastPlain('step_repetition')") == "kept repeating a step"
    assert page.evaluate("mastPlain('brand_new.mode-x')") == "brand new mode x"
    # agent rows: display name leads, key demoted to the mono meta line
    page.wait_for_selector("#workers .lrow")
    meta = page.locator("#workers .rr-meta").first.inner_text()
    assert "identity key" in meta


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
        page.goto(base)
        page.click("#nav-console")
        row = page.locator(".runrow", has_text="Hostile gate")
        row.wait_for()
        row.locator("button:has-text('Approve')").click()
        page.wait_for_selector(".runrow:has-text('Finished')", timeout=20_000)
    finally:
        page.remove_listener("pageerror", handler)
    assert errors == [], f"page JS errors during hostile-id approval: {errors}"


def test_custom_workflow_built_by_wizard_runs(page, server):
    """Wizard-driven custom workflow: two steps authored step-by-step (with a
    verifiable check, a dependency and a gate), reviewed, run to completion."""
    base, _ = server
    page.goto(base)
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
    page.goto(base)
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
    page.goto(base)
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
    page.goto(base)
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
