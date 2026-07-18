---
url: https://arxiv.org/abs/2501.15105
fetched: 2026-07-18
summary: Two coupled loops generate three knowledge types — Loop I (perception only) makes declarative knowledge, Loop II (action) makes procedural knowledge, and both together make conditional knowledge.
---
# Three knowledge types from two loops

The model's central contribution: three kinds of knowledge fall out of **two coupled processing loops** over the free-energy machinery.

- **Loop I — prediction → perception → prediction error** (no action on the world). This is passive Bayesian belief updating: prediction error revises predictions through perception, re-coding the brain's internal model. It generates **declarative knowledge** ("what a thing is") — the semantic-network case. *Learning here means detecting and reducing prediction error by updating beliefs.*

- **Loop II — prediction error → policy selection → action → environment.** When the agent must *act* to change stimuli and avoid future surprise, Bayesian inference becomes Bayesian **variational** inference (policies are needed). Learning a series of actions that reliably minimize free energy yields **procedural knowledge** ("how things are done"). Once learned, these action sequences become automatic, and Loop I can go quiet while the agent executes them from memory.

- **Both loops active together** — automatic action (Loop II) *simultaneously* with concept extraction/perception (Loop I) — generates **conditional knowledge**: knowing *when* and *whether* to apply declarative and procedural knowledge. Conditional knowledge is the guidance layer that binds the other two to context.

Two structural notes:

- **Removing Loop II collapses the model to a pure declarative (semantic-network) generator** — active inference is exactly what adds procedural and conditional knowledge on top of Bayesian perception.
- This trichotomy (declarative/procedural/conditional) lines up with other cognitive taxonomies of knowledge and gives each type a distinct *generative mechanism*, not just a distinct label — which is what earlier descriptive models lacked.
