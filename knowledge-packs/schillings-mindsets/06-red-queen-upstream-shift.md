---
url: https://www.youtube.com/watch?v=1P1hJ36rxM0&t=697s
fetched: 2026-07-19
summary: When a detect-and-fix loop re-arms after every round (vulnerability whack-a-mole), recognize it as non-terminating and relocate the intervention upstream — correct-by-construction beats infinite patching.
---
# Red-queen escape — move the intervention upstream

Schillings on model-discovered vulnerabilities: "there is a rush to patch
those vulnerabilities, but that's going to be a **never-ending process** —
models get smarter, they go a bit deeper and find even more subtle
vulnerabilities." Each round of fixes raises the adversary's depth; the loop
re-arms itself.

His response is a general reasoning move, not a security tactic:

1. **Recognize the loop shape.** A cycle where every fix improves the thing
   that generates the next problem is a red-queen race — effort holds
   position, it doesn't gain ground.
2. **Refuse to optimize inside it.** Getting faster at patching is still
   losing; "we need to think at least as much about the implication of a
   piece of code as the code writing itself."
3. **Relocate upstream of generation.** "The grail — something my team is
   working actively on — is instead of detecting the vulnerability and then
   suggesting some fix, **teach the model to write correct things from the
   start**." The slide's phrasing: transition "from post-generation patching
   to secure-by-default architecture at the initial production level."
4. **Accept the upstream problem is harder** ("very hard to do because it is
   very context dependent") — that difficulty is what terminating the loop
   costs, and it is still cheaper than a race with no finish line.

He extends the same logic to language design: since writing code is no
longer painful, deliberately make it *harder* — strong typing, Lean-style
proof burden — "putting the burden of correctness on the model" at
generation time rather than on reviewers afterward.
