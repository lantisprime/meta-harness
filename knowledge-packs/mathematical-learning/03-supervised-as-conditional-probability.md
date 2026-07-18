---
url: https://arxiv.org/abs/2311.13341
fetched: 2026-07-18
summary: Supervised learning is estimating a conditional probability P(b|a); the loss is the same -log Phi, and only the normalization condition changes (per-condition sum to 1).
---
# Supervised learning is conditional-probability estimation

Supervised learning fits the same principle as a special case. Treat the input as a pair x = (a, b) — e.g. a = features, b = label — and decompose the joint: P(x) = P(a, b) = P(a)·P(b|a). The goal of supervised learning is to estimate **P(b|a)**, the label's conditional probability given the input.

Running the same variational derivation with the model function Φ(a, b) targeting P(b|a) gives:

- **Loss: L(Φ(a,b)) = −log Φ(a, b)** — identical to the unsupervised case.
- **Normalization changes**: instead of summing over all inputs, it is imposed *per condition* — for every a, Σ_b Φ(a, b) = 1.

Result: **E[−log Φ(a, b)] → min ⇔ Φ(a, b) → P(b|a)**.

Consequences worth carrying forward:

- **Classification** is the discrete case: given inputs {xᵢ} and labels {tᵢ}, treat (x, t) as one input and estimate P(t|x). Cross-entropy loss is exactly −log Φ over a softmax-normalized Φ — i.e. conventional classification *is* this principle, already.
- **Teacher labels are not privileged.** The paper stresses that labels are themselves products of a prior (human) learning process; there is no mathematical necessity to their correctness. Supervised learning inherits whatever probability structure the labeling process encoded.
- The distinction between "supervised" and "unsupervised" is only in the **normalization condition**, not in the loss or the underlying objective. Both are probability estimation.
