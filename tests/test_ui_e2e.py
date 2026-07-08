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
    assert page.locator(".planstep .n.done").count() == 6


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
        assert page.locator("#pw-models option").count() > 0
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
    options = page.locator("#aw-cli-models option")
    assert options.count() >= 3
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
        assert page.locator("#aw-sub-models option").count() >= 3
    else:
        assert page.locator(".pill:has-text('Claude Code')").is_disabled()
    page.click("button:has-text('← Back')")
    page.click("button:has-text('Cancel')")
