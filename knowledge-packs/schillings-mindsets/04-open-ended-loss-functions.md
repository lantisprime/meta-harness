---
url: https://www.youtube.com/watch?v=1P1hJ36rxM0&t=804s
fetched: 2026-07-19
summary: Binary pass/fail evals plateau; design never-ending objectives whose loss includes solution complexity (compression: compressed size + source size) to force genuine novelty instead of benchmark saturation.
---
# Open-ended loss functions — evals that never saturate

Schillings' critique of pass/fail evaluation: "SWE-bench verifies if a piece
of code runs and produces the right output. That's only a small part of code
engineering." His slide names the transition: "from strict binary pass/fail
unit tests to **nuanced, continuous performance and logic scores**."

His worked example is text compression:

> "Take 10 megabytes of code and tell the model: write the best lossless
> compressor you can. The loss function is **the size of the compressed file
> plus the size of the source code**. That's never-ending."

Design features worth copying:

- **Continuous, unbounded metric** — there is no 100%; progress is a trend,
  not a saturation point, so the objective cannot be gamed to completion.
- **Complexity is inside the loss** — counting the source size penalizes
  memorization and bloat; elegance is scored, not admired.
- **Trivially checkable, unboundedly hard** — verification stays cheap
  (decompress, diff, measure) while improvement requires "creating totally
  new algorithmics."

The mindset: when a benchmark saturates, the benchmark was the ceiling, not
the skill. Experts pick objectives where the honest score can always get
better, then treat metric *slope* as the progress signal.
