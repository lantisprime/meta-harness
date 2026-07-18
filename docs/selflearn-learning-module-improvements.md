# selflearn learning module — improvement design from recent learning-theory papers

**Status:** design / proposal (2026-07-18). No code changed by this document.
**Scope:** the learning module only — `selflearn/src/selflearn/learning/`
(`marks.py`, `gaps.py`), `evidence.py`, `advisor.py`, and the retrieval
ranking in `retrieval/retriever.py`.

**Sources.** Distilled into version-controlled packs under `knowledge-packs/`:

| Pack | Paper | One-line thesis |
|---|---|---|
| `mathematical-learning` | Katayose, *Learning principle…* ([arXiv:2311.13341](https://arxiv.org/abs/2311.13341)) | All learning is estimating input probability; the honest objective is the log-loss `−log Φ` over a **normalized** estimator; optimization can be local/online. |
| `active-inference` | Ghasimi & Movarraei, *Knowledge Generation Using Active Inference* ([arXiv:2501.15105](https://arxiv.org/abs/2501.15105)) | Agents minimize **expected free energy** = pragmatic (goal) + **epistemic (information gain)**; perception updates beliefs, action changes the world; knowledge grows on **surprise**. |
| `human-learning` | Allen, Redish & Kizilcec, *Fundamental Mechanisms of Human Learning* ([arXiv:2509.17202](https://arxiv.org/abs/2509.17202)) | Distinct systems learn distinct **information types**; memory does not transfer between them; don't collapse types into one signal. |

---

## 1. Purpose

The learning module works and is well-grounded, but three recent papers
converge on a small number of concrete gaps. This document names every gap,
maps it to the paper that motivates it, proposes the narrowest mechanism
that closes it, points at the exact code, and grades it honestly
(**NEW** mechanism vs **VALIDATION** of existing design), with an effort/impact
estimate. It also records what we deliberately will **not** build.

The single most important finding: **the acquisition loop is reactive, and
the one quantity needed to make it proactive is already computed and then
discarded.**

---

## 2. Current baseline (what the module does today)

- **`evidence.py`** — one source of truth for the math: `laplace_score(h, m)
  = (h+1)/(h+m+2)`; `decay_factor` (90-day half-life recency weighting);
  `parse_iso`.
- **`StoredEntry.score` / `score_for(task_type, smoothing=2.0, now=…)`** —
  a decayed, Laplace-smoothed helpfulness score, optionally conditioned on
  `task_type` via `marks_by_task` buckets.
- **`marks.py` (fast loop)** — `apply_outcome` credits `outcome.credited`
  entries helpful/harmful online; streak-based auto-deprecation
  (`consecutive_harmful ≥ DEPRECATE_THRESHOLD (3)` **and** decayed harmful
  > helpful).
- **`gaps.py` (slow loop)** — `Learner` retains failures durably
  (`learner-state.json`) and emits three `GapSignal` kinds: **coverage**
  (≥`min_failures=2` failures in a claimed-but-uncovered topic),
  **quality** (failures where entries were retrieved but didn't work),
  **staleness** (`age > 180d` **and** `score < 0.45`), each with
  `backoff_rounds=2` suppression.
- **`advisor.py`** — turns store state into ranked operator suggestions;
  deprecation fires on `consecutive_harmful ≥ 3` **or** (`evidence ≥
  MIN_EVIDENCE=4` and `score < LOW_SCORE=0.35`).
- **`retrieval/retriever.py`** — ranks entries by `cosine × decayed
  score_for(task_type)`.

---

## 3. Gaps and improvements

Ordered by value. Each item: **what we lack → paper → mechanism → files →
grade → effort/impact.**

### 3.1 Acquisition is reactive, not uncertainty-seeking `[NEW, HIGH]`

**Lack.** The slow loop only proposes acquisition where we have *already
failed* (`gap_signals` needs `min_failures` real failures) or where an entry
already aged out. It has no notion of *"we know almost nothing about area X
— go find out."* Blind spots stay blind until they cause a failure.

**Paper.** Active inference: action is chosen to minimize **expected free
energy** `G = −pragmatic − epistemic`, where the epistemic term rewards
*expected information gain* — acting where the model is most uncertain,
before any goal payoff.

**Mechanism.** Rank acquisition candidates by `priority ∝ softmax(−G)` with
`G = pragmatic + epistemic`:
- `pragmatic` = expected reduction in task failure (our existing failure /
  coverage / staleness signals fold in here).
- `epistemic` = expected reduction in uncertainty about a topic/entry (new;
  see 3.2 for the concrete estimator).

This unifies coverage/quality/staleness/backoff under **one score** and adds
proactive gap-seeking.

**Files.** `gaps.py` (`Learner.gap_signals`/`staleness_signals` → an
`expected_free_energy` scorer), `advisor.py` (`suggest_actions` ranking).

**Effort/impact.** Medium effort, high impact. Depends on 3.2.

### 3.2 We use the posterior mean but discard its uncertainty `[NEW, HIGH — enabler for 3.1]`

**Lack.** `laplace_score` is the **mean** of a `Beta(h+1, m+1)` posterior
over an entry's helpfulness. We use only the mean (for ranking and
deprecation) and throw the rest of the distribution away — so we cannot
distinguish "0.5 because it's genuinely mixed (40 marks)" from "0.5 because
we have no evidence (0 marks)."

**Paper.** Active inference's epistemic value is exactly posterior
uncertainty; the math paper frames every score as a probability estimate
whose *distribution* matters.

**Mechanism.** Expose the posterior **variance/entropy** alongside the mean:
```
Var[Beta(h+1, m+1)] = (h+1)(m+1) / ((h+m+2)² (h+m+3))
```
High for under-observed entries/topics → directly usable as the epistemic
term in 3.1, and as a confidence badge in the advisor ("low-evidence,
treat as provisional"). The quantity is one function off numbers we
already store; nothing new to persist.

**Files.** `evidence.py` (add `laplace_variance` / `posterior_entropy`
next to `laplace_score`), consumed by `gaps.py`, `advisor.py`.

**Effort/impact.** Low effort, high impact (it is the unlock for 3.1).

### 3.3 Marks are unweighted binary; no surprise/precision weighting `[NEW, MEDIUM]`

**Lack.** `apply_outcome` adds fixed helpful/harmful increments regardless of
how *expected* the outcome was. A confirming outcome and a shocking one move
the counters equally.

**Paper.** Both papers agree the learning signal is **surprise**
`−log p(outcome)`. Active inference precision-weights prediction errors;
the math paper makes `−log Φ` the objective.

**Mechanism.** Weight each mark update by surprise — the divergence between
the outcome and the entry's current predicted helpfulness `score_for`.
Confirming outcomes teach little; surprising ones teach a lot. Converges
faster on informative evidence and damps churn from expected results.

**Files.** `marks.py` (`apply_outcome` increment weighting), `evidence.py`
(a `surprise(prediction, outcome)` helper).

**Effort/impact.** Medium effort, medium impact. Note the risk in §6
(interaction with streak-based deprecation must be preserved).

### 3.4 Retrieval scoring is not a normalized posterior `[NEW, MEDIUM]`

**Lack.** `retrieve` ranks by `cosine × decayed score_for` — a product of
two un-normalized quantities. Scores are not comparable across queries and
there is no principled cutoff beyond a fixed `k`.

**Paper.** The math paper's central technical demand: a probability
estimator **must be normalized** or estimates are not comparable.

**Mechanism.** Reframe ranking as a Bayesian posterior over candidates,
normalized with a softmax:
```
P(entry | task) ∝ P(task | entry)  ·  P(helpful | entry, task_type)
                  └ semantic likelihood ┘   └ Laplace prior (existing) ┘
```
Enables calibrated, cross-query-comparable scores and a principled cutoff
("include entries covering ≥95% of the mass") instead of a fixed `k`.

**Files.** `retrieval/retriever.py` (ranking + selection).

**Effort/impact.** Medium effort, medium impact. Purely a ranking change;
retrieval recall behavior must be regression-tested.

### 3.5 Thresholds are ad-hoc linear cutoffs, not log-loss/surprisal `[NEW, LOW]`

**Lack.** Deprecation trips on `score < LOW_SCORE (0.35)` — a linear cutoff
picked by feel.

**Paper.** The papers' native currency is self-information `−log P`.

**Mechanism.** Express the deprecation / qualification thresholds in
**surprisal** (`−log P(helpful)`), a proper scoring rule that penalizes
confident-but-wrong entries more than merely-uncertain ones.

**Files.** `marks.py`, `advisor.py`, and `verification/…qualify_model`.

**Effort/impact.** Low effort, low impact (mostly a reparameterization;
monotonic with today's behavior).

### 3.6 Surprise-triggered concept growth `[NEW, MEDIUM]`

**Lack.** New knowledge is created only when a fixed counter trips
(`min_failures = 2`). It is a scheduled trigger, not an epistemic one.

**Paper.** Active inference spawns a new concept exactly when stimuli stay
**unexplained** (surprise stays high) — growth is driven by inability to
predict, not by a count.

**Mechanism.** Trigger acquisition when *sustained* surprise on a topic
stays high — i.e. retrieved entries repeatedly fail to reduce prediction
error — rather than on a raw failure count. Cleaner, and it fires earlier
on genuinely novel gaps.

**Files.** `gaps.py` (`gap_signals` trigger condition).

**Effort/impact.** Medium effort, medium impact. Overlaps with 3.1/3.3.

### 3.7 Conditional knowledge is implicit, not first-class `[SPECULATIVE, LOW-priority]`

**Lack.** "Use knowledge X via procedure Y **under condition Z**" is spread
implicitly across `marks_by_task` buckets and workflow `check`/`depends_on`.
There is no first-class *conditional* entry.

**Paper.** All three papers name a distinct **conditional** ("when to
apply") knowledge type — active inference's third loop, human-learning's
instinctual/predictive gating, the math paper's `P(b|a)`.

**Mechanism.** Consider a first-class conditional link binding a knowledge
entry + a procedure + an applicability condition. Larger change to the
contract model; flagged for future discussion, not near-term work.

**Files.** `contracts.py`, `distillation/`, `retrieval/injection.py`.

**Effort/impact.** High effort, uncertain impact. **Deliberately deferred.**

---

## 4. What is already right (VALIDATION — no change)

The papers spend most of their force confirming existing design decisions.
Recording these so we don't "fix" what's correct:

- **`laplace_score` = probability estimation.** It is a Beta posterior mean —
  exactly the math paper's `Φ`. Our scoring is principled, not a heuristic.
- **`marks_by_task` = conditional-probability estimation.** The math paper
  shows supervised learning is just per-condition normalization `P(b|a)`;
  all three papers insist on not collapsing knowledge types into one
  counter. This is the review-finding-4 decision, vindicated three times.
- **Online per-outcome marks = localized loss.** The math paper proves the
  global objective decomposes into local, backprop-free, sequential updates
  — precisely our fast loop.
- **Entry `kind` (knowledge / skill / workflow) = declarative / procedural /
  conditional.** The same trichotomy appears in all three papers; three
  independent derivations landing on our taxonomy is strong evidence it is
  right.
- **Streak-reset-on-helpful deprecation** mirrors human-learning's
  "confounding a danger prediction with a safety experience" — a harmful
  streak is cleared by a single helpful outcome.

---

## 5. Out of scope / rejected (honesty)

Not everything in the papers is worth building here:

- **Full expected-free-energy planning with a transition (B-)matrix.** True
  active inference needs a generative model of dynamics (how one
  knowledge-state leads to the next under an action). We do not have one and
  will not fake it. We approximate EFE with the pragmatic + epistemic terms
  (3.1/3.2) only.
- **The time-evolution "brain" model** and **differential/Jacobian
  normalization** (math paper, ch. 6–7) are network-training constructions,
  not applicable to an evidence-counter learning loop.
- **The information-transfer-energy / λ≈0.41 least-effort layer** (active
  inference paper) is about concept communication, not our mark loop.

---

## 6. Phased roadmap

1. **Phase 1 — posterior uncertainty (3.2).** Add `laplace_variance` /
   `posterior_entropy` to `evidence.py`; surface as a confidence badge in
   `advisor.py`. Small, self-contained, unlocks the rest.
2. **Phase 2 — epistemic acquisition (3.1).** Add an `expected_free_energy`
   scorer combining the existing pragmatic signals with the Phase-1 epistemic
   term; rank `suggest_actions`/gap signals by it. **Highest-value phase.**
3. **Phase 3 — surprise-weighted marks (3.3) and surprise-triggered growth
   (3.6).** Introduce a `surprise` helper; weight increments and the
   acquisition trigger by it.
4. **Phase 4 — normalized retrieval posterior (3.4) and surprisal thresholds
   (3.5).** Reparameterize ranking and cutoffs.
5. **Deferred — first-class conditional knowledge (3.7).** Separate design.

---

## 7. Risks and test strategy

- **Preserve the loud, deterministic contract.** Every change stays in the
  evidence/counters model; no model calls added to the fast loop.
- **Deprecation guarantee must survive.** Surprise-weighting (3.3) must not
  weaken the streak-based auto-deprecation guarantee that holds at any
  cadence (the review-finding fix). Add regression tests pinning that a slow
  harmful cadence still deprecates.
- **Retrieval recall regression.** 3.4 changes ranking; pin current top-k
  recall on the existing fixtures before/after.
- **Backward-compatible state.** No new persisted fields in Phase 1–2
  (variance is derived); if any are added later, they must load-tolerate old
  `learner-state.json` / manifests through the existing migration paths.
- **Every phase ships with regression tests**, per the repo convention.

---

*This is a map, not a mandate. The highest-leverage single change is Phase 2
(epistemic acquisition), enabled by Phase 1 (posterior uncertainty) — turning
the acquisition loop from reactive gap-filling into proactive,
uncertainty-seeking learning.*
