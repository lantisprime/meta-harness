---
url: https://arxiv.org/abs/2311.13341
fetched: 2026-07-18
summary: The learning principle — all learning (machine and brain) is equivalent to estimating the probability of the input data; the model function returns that probability under a normalization constraint.
---
# The learning principle: learning is probability estimation

Katayose ("Learning principle and mathematical realization of the learning mechanism in the brain", arXiv:2311.13341v1) derives a single framework meant to describe *all* learning — deep learning and learning in the brain alike. The central claim: **all learning is equivalent to estimating the probability of the input data.**

The derivation rests on three elements common to any learning:

1. **A model** — the structure holding the targets of optimization (weights/biases in a network; synaptic connections in the brain). A model is a *model function* mapping input to output.
2. **Input data** — the model's input (sensory information, for the brain).
3. **A loss function** — the objective minimized during optimization.

Under idealized conditions (universal approximation, infinite data and compute), the solution that truly minimizes the loss is **unique and depends only on the input dataset and the loss function — not on the model's internal structure**. So the internal architecture is irrelevant to *what* is learned; it only affects *how fast* you converge.

The only information always available for *any* input, regardless of format, is the **probability the input has** (its frequency of appearance in the data distribution). Therefore the natural target of unsupervised learning — the most general case — is that probability itself. The solution is the model function that returns the true probability of the input, and the loss must be the function minimized by exactly that model function.

This reframes the usual questions. "Why does deep learning work?" becomes "the model approximates the true input probability distribution, and depth + compute buy enough universal-approximation capacity to do so." The notion of a "feature" is downstream, not fundamental (see the features entry).
