---
url: https://arxiv.org/abs/2501.15105
fetched: 2026-07-18
summary: The Free Energy Principle casts the brain as minimizing surprise (prediction error); it does so two ways — perception (update beliefs) and action (change the world) — and their combination is active inference.
---
# The Free Energy Principle and active inference

Ghasimi & Movarraei ("A New Approach for Knowledge Generation Using Active Inference", arXiv:2501.15105) build a knowledge-generation model on Friston's **Free Energy Principle (FEP)**: the brain is a hypothesis-testing organ that continually updates its beliefs to minimize **free energy**, an upper bound on **surprise** (self-information, −log p(ϕ) of a sensory observation ϕ).

Free energy is written as two terms:

> **F = Divergence + Surprise = D_KL[q(θ|µ) ∥ p(θ|ϕ)] − ln p(ϕ)**

where θ are hidden environmental **concepts/causes**, ϕ are **sensory stimuli**, q(θ|µ) is the brain's recognition (posterior) density parameterized by internal states µ, and p(θ|ϕ) is the true posterior. Minimizing F is equivalent to minimizing prediction error. Because surprise itself is intractable, the brain minimizes this tractable bound instead (variational inference).

Crucially, an agent can lower free energy **two ways**:

- **Perception** — change internal beliefs/expectations µ to reduce the divergence term (fit the model to the world): µ = argmin Divergence.
- **Action** — act on the environment to change the sensory stimuli themselves, so observations match predictions (fit the world to the model): a = argmax Accuracy, minimizing the bound on surprise via F = Complexity − Accuracy = D_KL(q ∥ p(θ)) − ⟨ln p(ϕ(a)|θ,m)⟩.

**Combining perception and action is active inference.** Pure perception (no action on the world) reduces to ordinary Bayesian belief updating; add action-driven stimulus selection and it becomes *active* inference. This dual mechanism — infer *and* act to make the world predictable — is the engine of the whole knowledge-generation model.
