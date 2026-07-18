"""Wizard-driven selflearn CLI: an interactive front door for the pipeline.

``selflearn wizard`` walks an operator through every workflow — acquisition
(gather/distill or the full pipeline), seeding, verification and approval,
quarantine release, retrieval, next-best-action advice, store diagnostics,
and the graph export — prompting for each parameter with sensible defaults. Before
running anything it prints the exact equivalent non-interactive command, so
the wizard *teaches* the plain CLI instead of replacing it.

Testable by construction: all I/O goes through ``Console`` with injectable
streams, and the command runner is injected too (the real CLI passes
``cli.main``). Nothing here talks to the network or the model endpoints
itself — it only assembles argv lists for the existing subcommands.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

BANNER = """\
=== selflearn wizard ===
Interactive setup for the self-learning knowledge pipeline.
Press Enter to accept a [default]; enter q at the menu to quit."""

MENU = """\
what would you like to do?
   1) acquire   - full pipeline: gather -> distill -> verify -> hold
   2) gather    - fetch sources into a sources.json (no model needed)
   3) distill   - turn gathered sources into candidate entries
   4) seed      - bulk-import a knowledge-base or lecture folder (no model)
   5) verify    - check a pack's candidates against the gates
   6) approve   - human-publish a verified candidate
   7) release   - journaled release of a quarantined candidate
   8) retrieve  - test what a specialist would be handed
   9) status    - packs, entries, suites, coverage
  10) next      - suggest the next best action for this store
  11) doctor    - diagnose (and optionally repair) the store
  12) graph     - export the store's knowledge graph (json/dot/mermaid)
   q) quit"""


class WizardExit(Exception):
    """Raised on EOF / explicit quit; unwinds to a clean exit 0."""


class Console:
    def __init__(self, in_stream=None, out_stream=None):
        self.stdin = in_stream if in_stream is not None else sys.stdin
        self.out = out_stream if out_stream is not None else sys.stdout

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def ask(self, prompt: str, default: str = "", required: bool = False,
            choices: Optional[Sequence[str]] = None) -> str:
        while True:
            hint = f" [{default}]" if default else ""
            if choices:
                hint = f" ({'/'.join(choices)}){hint}"
            self.say(f"{prompt}{hint}: ")
            line = self.stdin.readline()
            if not line:                      # EOF: never loop forever
                raise WizardExit
            value = line.strip() or default
            if required and not value:
                self.say("  a value is required here.")
                continue
            if choices and value and value not in choices:
                self.say(f"  please pick one of: {', '.join(choices)}")
                continue
            return value

    def confirm(self, prompt: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            answer = self.ask(f"{prompt} ({hint})").lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("  please answer y or n.")


# ---------------------------------------------------------------------------
# Per-action flows: each collects parameters and returns an argv list
# ---------------------------------------------------------------------------

def _ask_model(con: Console) -> list[str]:
    endpoint = con.ask("OpenAI-compatible endpoint",
                       default="http://127.0.0.1:1234/v1", required=True)
    model = con.ask("model id", required=True)
    argv = ["--endpoint", endpoint, "--model", model]
    api_key = con.ask("API key (empty for none)")
    if api_key:
        argv += ["--api-key", api_key]
    return argv


def _ask_tokens(con: Console, prompt: str, required: bool = False,
                default: str = "") -> list[str]:
    """Prompt for a space-separated list, re-asking on shell-quoting errors
    (an apostrophe in free text must not crash the wizard)."""
    while True:
        raw = con.ask(prompt, default=default, required=required)
        try:
            return shlex.split(raw)
        except ValueError as exc:
            con.say(f"  could not parse that ({exc}); balance any ' or \" "
                    "quotes and try again.")


def _ask_refs(con: Console) -> list[str]:
    con.say("references: 'search:<question>', https:// URLs, or file:// "
            "paths — space-separated for several.")
    return _ask_tokens(con, "refs", required=True)


def _ask_search(con: Console) -> list[str]:
    backend = con.ask("search backend", default="auto",
                      choices=["auto", "ddg", "wikipedia", "searxng",
                               "brave"])
    argv = [] if backend == "auto" else ["--search-backend", backend]
    if backend == "searxng":
        argv += ["--searxng", con.ask("SearXNG base url", required=True)]
    if backend == "brave":
        key = con.ask("Brave API key (empty to use BRAVE_API_KEY)")
        if key:
            argv += ["--brave-key", key]
    return argv


def _flow_acquire(con: Console, store: str) -> list[str]:
    refs = _ask_refs(con)
    pack = con.ask("pack name", required=True)
    topic = con.ask("topic", required=True)
    workdir = con.ask("working directory", default="./selflearn-work")
    argv = (["acquire", *refs, "--pack", pack, "--topic", topic,
             "--store", store, "--workdir", workdir]
            + _ask_model(con) + _ask_search(con))
    if con.confirm("offline mode (file:// refs only)?", default=False):
        argv.append("--no-network")
    con.say("(judge/embedding endpoints are also supported — see "
            "'selflearn acquire --help')")
    return argv


def _flow_gather(con: Console, store: str) -> list[str]:
    refs = _ask_refs(con)
    workdir = con.ask("working directory", default="./selflearn-work")
    out = con.ask("output file", default="sources.json")
    argv = ["gather", *refs, "--workdir", workdir, "--out", out]
    argv += _ask_search(con)
    if con.confirm("offline mode (file:// refs only)?", default=False):
        argv.append("--no-network")
    return argv


def _flow_distill(con: Console, store: str) -> list[str]:
    sources = con.ask("gathered sources file", default="sources.json",
                      required=True)
    pack = con.ask("pack name", required=True)
    topic = con.ask("topic", required=True)
    return (["distill", sources, "--pack", pack, "--topic", topic,
             "--store", store] + _ask_model(con))


def _flow_seed(con: Console, store: str) -> list[str]:
    kind = con.ask("seed a knowledge-base dir or a yt-distill lecture dir",
                   default="kb", choices=["kb", "yt"])
    directory = con.ask("directory to import", required=True)
    pack = con.ask("pack name", required=True)
    argv = [f"seed-{kind}", directory, "--pack", pack, "--store", store]
    if con.confirm("publish immediately (pre-gate bootstrap)?",
                   default=False):
        argv.append("--publish")
    return argv


def _flow_verify(con: Console, store: str) -> list[str]:
    pack = con.ask("pack to verify", required=True)
    return ["verify", "--pack", pack, "--store", store]


def _flow_approve(con: Console, store: str) -> list[str]:
    entry_id = con.ask("entry id to approve", required=True)
    by = con.ask("your identity (recorded in the decision basis)",
                 default="human")
    return ["approve", entry_id, "--store", store, "--approved-by", by]


def _flow_release(con: Console, store: str) -> list[str]:
    entry_id = con.ask("quarantined entry id", required=True)
    reason = con.ask("reason it is safe to release", required=True)
    by = con.ask("your identity (journaled)", required=True)
    return ["release", entry_id, "--store", store, "--reason", reason,
            "--by", by]


def _flow_retrieve(con: Console, store: str) -> list[str]:
    query = con.ask("query", required=True)
    packs = _ask_tokens(con, "packs (space-separated)", required=True)
    k = con.ask("how many entries", default="3")
    return ["retrieve", query, "--packs", *packs, "--store", store, "-k", k]


def _flow_list(con: Console, store: str) -> list[str]:
    return ["list", "--store", store]


def _flow_next(con: Console, store: str) -> list[str]:
    return ["next", "--store", store]


def _flow_doctor(con: Console, store: str) -> list[str]:
    argv = ["doctor", "--store", store]
    if con.confirm("apply fixes (otherwise report-only)?", default=False):
        argv.append("--fix")
    return argv


def _flow_graph(con: Console, store: str) -> list[str]:
    fmt = con.ask("output format", default="mermaid",
                  choices=["json", "dot", "mermaid"])
    argv = ["graph", "--store", store, "--format", fmt]
    packs = _ask_tokens(con, "limit to packs (empty for all)")
    if packs:
        argv += ["--packs", *packs]
    out = con.ask("write to file (empty for stdout)")
    if out:
        argv += ["--out", out]
    return argv


FLOWS: dict[str, Callable[[Console, str], list[str]]] = {
    "1": _flow_acquire, "2": _flow_gather, "3": _flow_distill,
    "4": _flow_seed, "5": _flow_verify, "6": _flow_approve,
    "7": _flow_release, "8": _flow_retrieve, "9": _flow_list,
    "10": _flow_next, "11": _flow_doctor, "12": _flow_graph,
}


# ---------------------------------------------------------------------------
# Status snapshot shown on entry (integrates the next-best-action advisor)
# ---------------------------------------------------------------------------

def _store_snapshot(con: Console, store_path: str) -> None:
    root = Path(store_path)
    if not root.exists():
        con.say(f"(new store — {store_path} will be created on first write)")
        return
    try:
        from selflearn.store import PackStore
        store = PackStore(root)
    except Exception as exc:
        con.say(f"WARNING: store failed to load: {exc}")
        con.say("pick 'doctor' from the menu to diagnose and repair it.")
        return
    packs = store.packs()
    if not packs:
        con.say("(store is empty)")
    for pack in packs:
        entries = store.entries_for(pack)
        by_status: dict[str, int] = {}
        for e in entries:
            by_status[e.status] = by_status.get(e.status, 0) + 1
        con.say(f"  {pack}: {len(entries)} entries {by_status}")
    try:
        from selflearn.advisor import suggest_actions
        top = suggest_actions(store)[:3]
    except Exception:
        return
    if top:
        con.say("suggested next actions:")
        for s in top:
            con.say(f"  - {s.action}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_wizard(runner: Callable[[list[str]], int], store: str = "",
               in_stream=None, out_stream=None) -> int:
    """``runner`` executes an argv list the way ``cli.main`` does and
    returns its exit code."""
    con = Console(in_stream, out_stream)
    try:
        con.say(BANNER)
        con.say()
        store = store or con.ask("knowledge store directory",
                                 default="./knowledge-store")
        _store_snapshot(con, store)
        while True:
            con.say()
            con.say(MENU)
            choice = con.ask("choice", required=True)
            if choice.lower() in ("q", "quit", "exit"):
                con.say("bye.")
                return 0
            flow = FLOWS.get(choice)
            if flow is None:
                con.say(f"  unknown choice {choice!r}")
                continue
            argv = flow(con, store)
            con.say()
            con.say("command: " + shlex.join(["selflearn"] + argv))
            if not con.confirm("run it now?"):
                con.say("(skipped — the command above is copy-pasteable)")
                continue
            try:
                rc = runner(argv)
            except SystemExit as exc:
                # argparse rejected the assembled argv (e.g. a non-integer
                # answer routed into a typed flag) — report it like any
                # failed command instead of killing the whole session.
                rc = exc.code if isinstance(exc.code, int) else 2
            con.say(f"-> finished with exit code {rc}"
                    + ("" if rc == 0 else " (non-zero: see output above)"))
    except WizardExit:
        con.say("bye.")
        return 0
