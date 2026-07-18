---
url: https://arxiv.org/abs/2311.13341
fetched: 2026-07-18
summary: Under these constructions the total loss decomposes into a sum of per-node and per-time-slice local losses, so optimization can be done locally and sequentially with no backpropagation and no notion of an output layer.
---
# Localized loss: local, sequential optimization without backprop

A striking consequence of the exact-normalization constructions: the total loss **decomposes into a sum of local terms**. Using the chain rule of the Jacobian across layers z₀…z_d, the loss becomes

> L = −Σᵢ Σⱼ log(∂c_{j;i+1} / ∂c_{j;i}),

a sum of a **localized loss per node**, Lᵢ,ⱼ = −log(∂c/∂c). In the time-evolution model it decomposes even further — into a localized loss **per node and per time slice**, Lᵢ(t) = −db_i(a_i(t))/da_i(t).

Three important implications:

- **No backpropagation.** Each parameter can be optimized using only its own local loss term and its neighbors. There is no global objective to differentiate through the whole network.
- **No special layers or times.** Because the loss is local per time slice, there is no privileged "output" or "final state" — optimization is done **sequentially**, and you can add arbitrarily many layers (or time steps) without changing the form of the local loss.
- **Physical grounding.** The paper draws the analogy to a hanging string settling into a catenary: no controller dictates the shape; each local segment minimizes its own energy and the global minimum emerges. It argues this locality is *required* by physics — real neurons are affected only by local interactions, so the brain's loss must be local, and any faithful model of it must be too.

For engineered learning systems, the transferable idea is that **online, local, per-observation updates are not a compromise — they are a principled way to minimize a globally-correct objective**, provided each local update follows the −log Φ form.
