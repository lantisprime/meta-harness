---
url: https://arxiv.org/abs/2311.13341
fetched: 2026-07-18
summary: Because the loss form is fixed at -log Phi, the only design freedom is how the model function satisfies the normalization constraint; two exact methods are proposed (differential/Jacobian and time-evolution).
---
# Normalization is the only design freedom

Once the loss is fixed at L = −log Φ, the *only* thing a designer can change is **how the model function Φ is made to satisfy the normalization condition** (positive everywhere, integrates/sums to 1). Any construction that guarantees those two properties yields a valid probability estimator — Φ need not be a conventional network output at all. The paper offers two constructions that satisfy normalization **exactly**, with no assumptions or approximations:

**1. Normalization by differentiation.** Define Φ(x) as a derivative of a monotone, bounded network output y(x) — for a single variable, Φ(x) = dy/dx with y ∈ [0,1] and dy/dx ≥ 0 (achieved with positive weights + sigmoid). Then ∫ Φ dx = ∫ dy = 1 automatically. For multiple variables this becomes a **Jacobian determinant**; constraining the network to a triangular Jacobian (no edges from lower to upper nodes) makes Φ = Π ∂bᵢ/∂aᵢ — an **O(n)**, guaranteed-positive product whose factors are exactly the conditional probabilities ∂bᵢ/∂aᵢ → P(aᵢ | a₁…aᵢ₋₁). This gives genuine unsupervised density estimation on arbitrary data, and the paper shows tight fits to multimodal, flat, and skewed distributions even at small sample sizes.

**2. Normalization by time evolution.** Define Φ through the time evolution of a connected network (see the brain-model entry). This is the construction that maps onto the brain.

The takeaway is a design discipline: **don't invent ad-hoc objectives; fix the log-loss and instead engineer a construction that is provably a normalized, positive probability estimate.** Whatever satisfies those constraints is, by the principle, doing valid learning.
