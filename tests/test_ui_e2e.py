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
