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
| `schillings-mindsets` | Benoit Schillings — AI Engineer World's Fair keynote ([YouTube](https://www.youtube.com/watch?v=1P1hJ36rxM0)) + BeOS Bible interview ([birdhouse.org](http://birdhouse.org/beos/bible/bos/int_schillings.html)) | 14 + 8 | Mental models of a 45-year veteran (DeepMind VP of Research): 14 knowledge entries — prose mental models, 2 triangulated logic anchors, BeOS-era repertoire cases, and values — plus `seed_workflows.py` encoding 4 reasoning procedures as workflow-kind entries (ProcedureStep chains) and 4 judgment dispositions as skill-kind entries. First expert-mindset pack — extraction target is *how the expert thinks*, attributed and timestamp/URL-cited, per the seven-pillar schema in `docs/mental-model-acquisition-proposal.md`. |

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
