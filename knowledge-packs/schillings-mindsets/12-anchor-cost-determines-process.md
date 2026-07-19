---
url: https://www.youtube.com/watch?v=1P1hJ36rxM0&t=690s
fetched: 2026-07-19
summary: "Logic anchor C: cost structure determines process structure — derives the eras analysis, correct-by-construction over patching, and 'make writing code harder' language design."
---
# Anchor C — cost structure determines process structure

**Anchor proposition:** engineering processes are adaptations to their
era's cost structure; when a cost collapses, every process priced on it
must be re-derived, and the freed budget should be spent on the new
bottleneck.

Like anchor V, this premise is used as justification and never itself
justified. Three independent decisions derive from it:

1. **The eras analysis** (~04:22): assembly-era precision, cloud-era
   modularity, and code review are each read as adaptations to what was
   expensive then (machine cycles; 7±2-token human working memory) — not
   as timeless best practice. C is the engine of the whole periodization.
2. **Correct-by-construction over patching** (~12:30): once model-found
   vulnerabilities make post-hoc patching a permanent recurring cost, the
   rational move is to relocate spend upstream where cost terminates —
   "teach the model to write correct things from the start."
3. **"Make writing code harder" language design** (~17:01): "since the pain
   of writing the code does not exist anymore, how about we make writing
   the code much harder — strongly typed, Lean-inspired, putting the burden
   of correctness on the model." The freed writing-cost budget is
   deliberately re-spent on the new bottleneck (correctness), and human
   readability is dropped because C says readability was only ever priced
   in for human writers.

Combined with anchor V, these two premises plus a small rule set
(cost→~0 ⇒ obsolete process; re-arming loop ⇒ move upstream; generate
inputs where verification can judge them) re-derive nearly every
conclusion in the talk — a compact generator of Schillings' decisions,
which is what "recreating his logic" operationally means.
