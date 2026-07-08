---
url: https://www.youtube.com/watch?v=ox1mW2N9Z_Y (video: "MiniCPM5 - Just How Good Can a 1B Model Be?"); https://github.com/openbmb/minicpm; https://github.com/thunlp/OPD
fetched: 2026-07-08
summary: MiniCPM5-1B (OpenBMB) — SOTA 1B on-device LLM trained with On-Policy Distillation (OPD); implications for the meta-harness SMALL tier.
---

# MiniCPM5-1B and On-Policy Distillation (OPD)

## The model
- **MiniCPM5-1B**: 1B dense transformer from OpenBMB, competitive with 3B–8B models.
  Ships in SFT and MLX variants (on-device: laptops/phones). HF: `openbmb/MiniCPM5-1B`.
- Post-training pipeline: **SFT → RL → OPD**. 200B tokens deep-thinking SFT + 200B
  hybrid-thinking SFT; domain-specialized RL teachers (math, code, closed-book QA,
  writing); OPD distills the teachers back into one release model.
- **Hybrid thinking**: supports deep-thinking and non-thinking modes (an effort knob
  the harness can exploit per task).

## OPD (On-Policy Distillation) — why it matters
- Unlike classic (off-policy) distillation on teacher outputs, the **student samples
  its own responses**, and the teacher grades them: reverse-KL divergence on the union
  of student/teacher top-k logits per position is used as the advantage signal
  (replacing verification-based advantage in the RL framework).
- Reuses each RL teacher's in-domain prompts as distillation data — no extra curation.
- Reported effect: RL + OPD lifts math/code/instruction-following average by ~16 points
  and cuts max-token-budget overruns by ~29pp.
- Reference implementation / paper: thunlp/OPD ("Rethinking On-Policy Distillation of
  Large Language Models").

## Implications for the meta-harness
1. **SMALL tier ≠ cloud-only.** Support OpenAI-compatible local endpoints (Ollama,
   LM Studio, vLLM, MLX servers) so OPD-class 1B models are first-class workers.
2. **Per-model evidence beats size priors.** An OPD-distilled 1B defies "1B = weak"
   priors; the capability matrix (observed pass rates per model × task type) is the
   right arbiter — priors are only the cold start.
3. **Domain spikiness.** OPD models are strong exactly where their RL teachers were
   (math/code/QA/writing) — per-task-type routing granularity matters more, not less.
4. **Effort knob.** Hybrid-thinking models take an "enable thinking" flag; the runner
   should pass task-level extra params so the router can buy accuracy with latency.

Sources: [OpenBMB/MiniCPM](https://github.com/openbmb/minicpm), [thunlp/OPD](https://github.com/thunlp/OPD), [MiniCPM5-1B on HF](https://huggingface.co/openbmb/MiniCPM5-1B), [DeepWiki benchmarks](https://deepwiki.com/OpenBMB/MiniCPM/8.1-minicpm5-benchmarks), [Kingy AI guide](https://kingy.ai/news/what-is-minicpm5-1b-a-practical-guide-to-openbmbs-1b-on-device-language-model/)
