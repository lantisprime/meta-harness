---
url: https://arxiv.org/abs/2311.13341
fetched: 2026-07-18
summary: A variational derivation fixes the loss to L = -log Phi(x), the self-information; minimizing any loss L is equivalent to approximating the input probability by exp(-L).
---
# The loss is self-information: L = −log Φ(x)

The paper derives the loss function form from first principles rather than assuming it. Let Φ(x) be the model function we want to approach the true probability P(x). Minimizing the expected loss E[L(Φ)] = Σ P(x)·L(Φ(x)) naively drives every Φ(x) to the same minimizing constant, so a **normalization condition** is imposed: Σ Φ(x) = 1 (or ∫ Φ dx = 1 for continuous input).

Applying the variational method under that constraint (perturb Φ(x) up by ε and Φ(x′) down by ε, which preserves normalization), the condition for a true minimum at Φ(x) = P(x) forces:

> **L(Φ(x)) = −log Φ(x)** — the self-information (surprisal) of the input.

This yields the key equivalence:

> **E[−log Φ(x)] → min  ⇔  Φ(x) → P(x)**

So minimizing the log-loss makes the model function converge to the true input probability. Crucially, this works **only** when the normalization condition holds and Φ(x) is positive; conversely, *any* positive, normalized Φ is a valid probability estimate.

A more general restatement: for **any** loss function L (e.g. mean squared error), minimizing E[L] is equivalent to making **exp(−L(x)) → P(x)**. In other words, every loss implicitly defines a probability estimate exp(−L), and "training" is always approximating the input distribution — whether or not the practitioner framed it that way. The choice of loss is really a choice of how exp(−L) is parameterized and normalized.
