---
url: https://arxiv.org/abs/2501.15105
fetched: 2026-07-18
summary: Concepts are hidden variables inferred from observable stimuli through a generative model (a stimulus-to-concept matrix); knowledge is the Bayesian/Markov inference of concepts from the stimuli they generate.
---
# Concepts as hidden variables inferred from stimuli

The model's basic ontology: **concepts are hidden variables in the environment**, discoverable only by inferring them from the **stimuli** they generate. A concept set S = {s₁…sₙ} and a stimulus set R = {r₁…rₘ} are linked by a **generative model** — an m×n matrix A (or joint density p(s, r)) mapping stimuli to concepts. Each concept is a vector over stimuli, sᵢ = (aᵢ₁…aᵢₘ); concept similarity is the cosine between these vectors, giving an n×n similarity matrix in [0,1].

Key properties carried into the FEP model:

- **A concept is a combination of stimuli** across sensory/abstract modalities (hearing a bark + seeing a dog → the concept "dog"). Concepts can be objective (dog) or abstract (justice), or both (home).
- **Many-to-many**: one concept has many stimuli; one stimulus can evoke many concepts. Concepts sharing all stimuli are synonyms (a "synset").
- **Inference is Markovian and probabilistic**: continuous perceptual signals are converted into discrete concepts. Some concepts are known through inference; others remain hidden in the environment until the right stimuli arrive.
- **New stimuli grow the model**: a stimulus (or combination) that refers to no existing concept triggers the generation of a **new concept**, expanding the generative matrix (see the concept-growth entry).

This is a computational reformulation of **semantic networks** (nodes = concepts, edges = relations), but expressed as a generative model p(s, r) that can both *compute* similarity/organization and *show the process* by which new concepts enter the network. The limitation of classic semantic networks — they only handle declarative/semantic knowledge — is what the FEP extension is designed to overcome.
