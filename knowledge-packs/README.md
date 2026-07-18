# Distilled knowledge packs

Portable, version-controlled source for selflearn knowledge packs. Each
subdirectory is one pack: a set of Markdown entries with YAML frontmatter
(`url`, `fetched`, `summary`), traceable to a cited source. These are the
durable artifacts — a live knowledge store (`~/.metaharness/knowledge`)
is ephemeral, but these files re-seed it deterministically.

## Packs

| Pack | Source | Entries | About |
|---|---|---|---|
| `human-learning` | Allen, Redish & Kizilcec, *Fundamental Mechanisms of Human Learning* ([arXiv:2509.17202](https://arxiv.org/abs/2509.17202)) | 10 | Four-system neuroscience model of learning (perceptual/instinctual/deliberative/procedural), system boundaries, and teaching implications. |
| `mathematical-learning` | Katayose, *Learning principle and mathematical realization of the learning mechanism in the brain* ([arXiv:2311.13341](https://arxiv.org/abs/2311.13341)) | 7 | Learning as probability estimation, the log-loss/self-information principle, exact normalization methods, and local/backprop-free optimization. |
| `active-inference` | Ghasimi & Movarraei, *A New Approach for Knowledge Generation Using Active Inference* ([arXiv:2501.15105](https://arxiv.org/abs/2501.15105)) | 6 | Free Energy Principle model of generating declarative/procedural/conditional knowledge; perception+action loops, expected-free-energy policy selection, and unsupervised concept growth. |

## Seeding a pack into a store

```bash
# publish immediately (pre-gate bootstrap); drop --publish to hold as candidates
selflearn seed-kb knowledge-packs/human-learning \
    --pack human-learning --store ~/.metaharness/knowledge --publish
selflearn seed-kb knowledge-packs/mathematical-learning \
    --pack mathematical-learning --store ~/.metaharness/knowledge --publish

selflearn list --store ~/.metaharness/knowledge
selflearn graph --store ~/.metaharness/knowledge --format mermaid
```

Bind a pack to a specialist agent (durable config, or the web UI's
"Knowledge packs" field) to have its published entries retrieved into that
agent's task prompts. See `docs/selflearn-manual.md`.

## Adding a pack

Write one `.md` file per entry with frontmatter, then `seed-kb` the
directory. Keep each entry faithful to its cited source and under ~400
words; one claim-dense idea per file (the filename becomes the topic).
