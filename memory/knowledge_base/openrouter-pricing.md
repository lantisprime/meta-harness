# OpenRouter pricing reality check

---
url: https://tokenmix.ai/blog/openrouter-vs-direct-api-cheaper (also truefoundry.com/blog/openrouter-pricing, betterclaw.io/blog/openrouter-vs-direct-api-agents, amnic.com/blogs/openrouter-pricing)
fetched: 2026-07-09
summary: OpenRouter adds no per-token markup on pass-through provider rates; the real costs are a flat 5.5% credit-purchase fee ($0.80 min), BYOK at 5% after 1M free requests/month, and per-model exceptions with large markups (reported ~2x on some Claude models vs direct Anthropic). Fine for low-volume testing/multi-model access; at production scale prefer direct APIs or cheaper aggregators.
---

- No general markup: provider token rates pass through at or near direct cost.
- 5.5% fee on credit purchases ($0.80 minimum) — you pay it up front, not per token.
- BYOK: first 1M requests/month free, then 5% of the equivalent model cost.
- Exceptions exist per model: some hosted listings carry big markups (e.g. Claude 3.5
  Sonnet reported at $6/$30 vs $3/$15 direct = 100%). Always check the per-model page.
- Verdict for this project: probe/eval volume → cost negligible; prefer opencode-go or
  native providers (deepseek, neuralwatt) where the model exists there; openrouter is
  the only pi route for US/EU open-weight models (gpt-oss, nemotron, devstral, gemma).
