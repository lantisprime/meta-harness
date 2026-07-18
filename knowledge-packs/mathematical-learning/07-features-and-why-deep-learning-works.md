---
url: https://arxiv.org/abs/2311.13341
fetched: 2026-07-18
summary: Features are just high-probability regions of the input distribution; deep learning works by approximating the true probability, and model architecture only changes convergence speed, not the final solution.
---
# Features, and why deep learning works

The learning principle reinterprets several folk explanations of deep learning.

**Features are an afterthought, not a cause.** Since all information lives in the input probability distribution, a "feature" is simply **a region of input space where the probability is concentrated**. Example: a normally-drawn "3" appears frequently, while the enormous number of distorted "3"s each appear rarely — so the recognizable shape is exactly the high-probability pattern. Features are what *emerge* once the probability distribution is successfully estimated; deep learning is not successful *because* it captures features — capturing features is a symptom of estimating probabilities well.

**Why deep learning works.** As long as the model function satisfies the normalization condition, it automatically approaches the true probability. With universal approximation plus enough data and compute, it can get arbitrarily close. So the success of deep learning is: depth buys universal-approximation capacity, and modern compute makes minimizing the (log-)loss feasible.

**What "improving a model" actually means.** Because the true-minimum solution depends only on the data and loss — *not* the architecture — a better architecture does not change *what* is ultimately learned. It only changes the **speed of convergence**. Baking in structure suited to the data (CNNs for images, attention for language) makes convergence dramatically faster *for that data type*. Since real training always stops early (finite data/compute), faster-converging models win in practice — which creates the illusion that the "essence" of learning lives in the architecture. It does not; **improving a model = specializing it to a particular input for faster convergence.**

Practical corollary for anyone building learning systems: invest architecture/structure to accelerate convergence on your actual data distribution, but remember the target is always a faithful, normalized probability estimate — keep the objective honest (log-loss, normalized) rather than chasing ad-hoc metrics.
