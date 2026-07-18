---
url: https://arxiv.org/abs/2501.15105
fetched: 2026-07-18
summary: Communicating concepts through stimuli minimizes an energy Omega = -lambda*I(S,R) + (1-lambda)*H(S), trading off mutual information against concept entropy; an optimal balance sits near lambda = 0.41.
---
# Information-transfer energy and least effort

Before the FEP loops, the paper grounds concept communication in an information-theoretic cost. Transmitting concepts through stimuli (environment→agent, or speaker→listener) consumes an **information-transfer energy**:

> **Ω(λ) = −λ·I(S,R) + (1−λ)·H(S)**,  λ ∈ [0,1]

where I(S,R) is the **mutual information** between stimuli and concepts (how much a stimulus tells you about the concept) and H(S) is the **entropy of concepts** (the representational cost). The mutual information is the KL divergence between the joint and the product of marginals: I(S,R) = D_KL(p(sᵢ,rⱼ) ∥ p(sᵢ)p(rⱼ)).

The two terms pull in opposite directions: **maximize information transfer** while **minimizing concept entropy (cost)**. Minimizing Ω therefore requires an optimal λ. The paper cites an optimum near **λ ≈ 0.41**, where the conflict between "few, low-entropy concepts" and "high information content" is best resolved.

Why this matters for the model:

- It is the **principle of least effort** (Zipf's law) applied to knowledge: both sender and receiver act to maximize information and minimize entropy/cost. Overusing synonyms or too-uniform concept frequencies inflates entropy and cost.
- λ is effectively a **policy choice** — a knob predicting energy consumption, trading transfer against representational economy.
- It connects directly to the FEP: minimizing free energy in the brain is the same balance in another guise — reduce prediction error (raise accuracy/information) while keeping the model simple (low complexity/entropy). Both the communication layer and the inference layer are minimizing an information-theoretic free energy.
