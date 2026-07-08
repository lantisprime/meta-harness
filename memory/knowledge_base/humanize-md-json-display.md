---
url: https://github.com/markedjs/marked, https://github.com/developit/snarkdown, https://www.cssscript.com/collapsible-json-viewer-tree/
fetched: 2026-07-08
summary: Options for rendering markdown + JSON humanely in a zero-build web UI
---

# Humanizing Markdown & JSON for display (web dashboards)

## Markdown → HTML in the browser
- **marked** (~50KB min) — full GFM incl. tables, actively maintained, single-file
  `marked.min.js` vendors cleanly into a static dir. The default choice.
- **snarkdown** (~1KB) — bold/italic/links/lists/headers only; **no tables** (deal-breaker
  when agent outputs contain GFM tables). No sanitization.
- **micromark** — strictest CommonMark/GFM compliance, heavier, ESM-oriented; overkill
  for dashboard rendering.
- Always escape/sanitize: render MD from untrusted/model output → escape raw HTML
  (marked: `marked.parse(text)` then sanitize, or set renderer to escape inline HTML).

## JSON → human-readable
- Best practice: collapsible tree (expand/collapse per node), syntax highlighting,
  type-colored values; optional key search/filter for large payloads.
- Vanilla-JS libs exist (json-view, TreeJSON, JSONViewer) but a hand-rolled renderer
  using native `<details>/<summary>` is ~50 lines, zero deps, keyboard-accessible,
  and matches a no-build codebase. Objects/arrays → nested <details>; primitives →
  type-classed <span>s; collapse deep nodes by default, show `{n keys}` / `[n items]`
  summaries.
- Heuristic for mixed agent output: try `JSON.parse` first → tree; else if it looks
  like markdown (headers/tables/fences) → marked; else escaped `<pre>`.

Sources: [marked](https://github.com/markedjs/marked), [snarkdown](https://github.com/developit/snarkdown), [micromark](https://github.com/micromark/micromark), [TreeJSON](https://www.cssscript.com/collapsible-json-viewer-tree/), [JSON viewer roundup](https://www.jqueryscript.net/blog/best-json-viewer.html)
