---
url: https://www.youtube.com/watch?v=1P1hJ36rxM0&t=589s
fetched: 2026-07-19
summary: The locked-room thought experiment — improvement without external data is self-generated verifiable challenges plus judgment of the answers; requires a domain with strict feedback loops (compile, run, measure).
---
# Self-play curriculum — improvement is challenge generation + verification

Schillings' model of how skill grows once external training material runs
out ("80% of the new code added to GitHub today is machine generated" — the
human-knowledge mine is exhausted):

> "Take a brilliant software engineer, lock him or her in a room for two
> years, feed pizza, and give the mission: you need to become a better
> software engineer. What do you do? **You give yourself some challenges —
> challenges that you can verify — and you keep working on those
> challenges.**"

That is AlphaZero's loop restated for engineering, and his slide decomposes
it: **Continuous Verification** (compilers and execution provide strict
feedback loops), **Reinforcement Optimization** (self-play enables validating
your own work automatically), **Infinite Sandbox** (performance grows through
continuous closed loops).

The innate skill being modeled: an expert improving alone is running three
distinct capabilities — *generating* challenges slightly beyond current
ability, *attaching* a verification each challenge can be judged by, and
*judging* the attempt honestly, including its architecture, not just its
output. Remove any leg and the loop degenerates: unverifiable challenges
drift into self-delusion; unchallenging ones plateau. The limit then becomes
pure compute: "this is an issue of how much self-play time we can have."
