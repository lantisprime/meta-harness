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
    page.goto(base); page.click("#nav-wizard")
    assert "metaharness" in page.title()
    page.wait_for_selector(".stepper .s.on")
    # mock workers fill all three tiers -> ready badges + enabled continue
    page.wait_for_selector(".tierrow")
    assert page.locator(".tierrow").count() == 3
    assert page.locator(".badge.ok", has_text="ready").count() == 3
    assert page.locator("button", has_text="Continue →").is_enabled()


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
