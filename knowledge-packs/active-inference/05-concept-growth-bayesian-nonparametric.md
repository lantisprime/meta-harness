---
url: https://arxiv.org/abs/2501.15105
fetched: 2026-07-18
summary: When stimuli match no existing concept the model spawns a new hidden concept; Bayesian-nonparametric priors (Dirichlet process) let the concept inventory grow unsupervised as observations accumulate, driven by surprise.
---
# Unsupervised concept growth via surprise

A defining feature of the model is that the concept inventory is **not fixed** — it grows as the agent encounters the world, with no supervision:

- **Surprise triggers growth.** When a stimulus (or combination of stimuli) cannot be explained by any existing concept, the agent **adds a new hidden concept** to account for it. In free-energy terms, unexplained stimuli produce high surprise; spawning a concept that predicts them lowers future free energy.
- **Entropy rises, then is reduced.** Introducing a new concept raises the concept entropy H(S); the agent then updates its generative model to bring that entropy back down, minimizing information-transmission energy. Learning is this cycle of surprise → model expansion → entropy reduction.
- **Bayesian nonparametrics make this principled.** Because the number and distribution of concepts are unknown a priori, the model uses **Bayesian nonparametric** methods — a **Dirichlet process** with **Categorical** distributions for concepts, stimuli, and policies (the Dirichlet is the conjugate prior of the multinomial, so updates stay tractable). This lets the model add concepts flexibly as observations accumulate, rather than committing to a fixed inventory.

The result is a **generative model that expands itself unsupervised**: from prior beliefs plus new stimuli it generates new, credible hidden states (concepts). Two consequences the paper highlights:

- **Abstraction enables transfer.** Mapping continuous stimulus space into discrete concepts (abstraction) is what makes knowledge easy to *generalize and transfer between agents*.
- **The trigger for creating knowledge is epistemic, not scheduled.** New knowledge is created exactly when existing knowledge fails to predict — surprise is the signal that the model must grow, not a fixed curriculum.
