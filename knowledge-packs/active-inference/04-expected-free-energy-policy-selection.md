---
url: https://arxiv.org/abs/2501.15105
fetched: 2026-07-18
summary: Actions are chosen by minimizing expected free energy (EFE); policy probability is softmax(-G), and the POMDP is parameterized by A (likelihood), B (transitions), D (prior), and G (EFE), balancing goal-seeking against uncertainty reduction.
---
# Expected free energy and policy selection

Where perception fits beliefs to observations, **action requires choosing a policy** (a sequence of actions) — and the choice is governed by **expected free energy (EFE)**, denoted G. Because the future is not yet observed, the agent evaluates the free energy it *expects* under each candidate policy π and prefers policies that will minimize it. Policy probability is a softmax over negative EFE:

> **p(π) = σ(−G(π))**

so lower expected free energy ⇒ higher probability of selecting that policy. This makes action selection a **Bayesian decision process** over policies.

EFE decomposes into two drives (Complexity − Accuracy, or equivalently a pragmatic and an epistemic term):

- **Pragmatic / goal value** — reach preferred (low-surprise) outcomes; the "accuracy" the agent wants to maximize.
- **Epistemic / information value** — reduce uncertainty about hidden concepts; policies that are expected to yield informative observations are favored even before any goal payoff. This is the built-in drive to *explore where the model is most uncertain*.

The generative model is a **POMDP** parameterized by matrices (Table 3 / Figure 4):

- **A** — likelihood mapping hidden concepts → stimuli (p(ϕ|θ)),
- **B** — transition probabilities between hidden concepts under a policy (p(θ_{τ+1}|θ_τ, π)),
- **D** — the initial prior over concepts,
- **G** — expected free energy per policy.

Full generative density: p(ϕ̃, θ̃, π) = p(θ₁) p(π) Πτ p(θ_{τ+1}|θ_τ, π) p(ϕ_τ|θ_τ). Perception, planning, and action all run as free-energy minimization over this structure — the agent doesn't just estimate the nearest match, it estimates the whole distribution over concepts and chooses actions that make future observations least surprising.
