---
url: https://arxiv.org/abs/2311.13341
fetched: 2026-07-18
summary: The brain is modeled as a fully/partially connected homogeneous network with oriented edges whose time evolution replaces network layers; a Boltzmann-form model function normalizes automatically and learns causal/temporal structure from sequential input.
---
# The brain model: normalization by time evolution

To model the brain, the paper requires two things a layered network cannot give: the model must be **general-purpose** (unsupervised, no prior knowledge, learning rules behind sensory data) and **homogeneous/isotropic** (no special nodes or edges, since cortical neurons form fully or partially connected networks, possibly with loops).

The construction: a network of n homogeneous nodes a(t), edges with weights w_ij (oriented, like synapses; w_ii = 0), evolving in time by

> a(t+dt) − a(t) = (W·a(t) + b)·dt.

Input is written into some nodes at t = 0; the rest are seeded randomly. **Time evolution plays the role that layers play in ordinary networks.** The model function is defined in a **Boltzmann form**, Φ(a(0)) = exp(−E(a(T))) / ∫ exp(−E(a)) da, which the paper proves satisfies the normalization condition exactly (the denominator's Jacobian gives exp(tr[WT]) = 1 because the diagonal is zero).

The linear version lacks universal approximation, so **nonlinearity** is added by bounding each node to (0,1) with a per-node function b_i(a_i) that diverges at the edges (keeping a_i in range) — the *shape* of b_i(a_i) is what gets optimized. Then Φ(a(0)) = ∂a(T)/∂a(0), and the loss localizes per node **and per time slice** (see the localized-loss entry).

Why it reads as a brain model:

- **Locality is forced by physics** — neurons interact only locally, so the loss must be local; this model's is.
- **Sequential processing learns causal/temporal structure.** Because optimization happens at each time slice on temporally continuous input, the model naturally acquires concepts like "A happened because B happened." Feeding it video (images ordered in time) would let it acquire such structure automatically.

Open problems the author flags: mapping the abstract variables to real neural substrates, and practical questions (how many nodes, what time resolution, efficient local optimization). If solved, the author frames this as a route to general-purpose, human-like learning.
