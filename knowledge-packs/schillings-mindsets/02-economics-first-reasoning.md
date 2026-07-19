---
url: https://www.youtube.com/watch?v=1P1hJ36rxM0&t=690s
fetched: 2026-07-19
summary: Find the cost assumption an entire culture is built on, then reason forward from its collapse — writing code at ~$0 marginal cost implies exploding volume, unread code, and system pressure shifting to validation and specification.
---
# Economics-first reasoning — find the priced-in assumption

"We developed a whole software engineering culture and infrastructure and set
of companies **based on the assumption that writing code was the hard part**.
That this was the expensive part. We're now in a world where writing code is
free or nearly free."

The move has three beats:

1. **Name the assumption a system's economics are built on** — not its stated
   principles, but the cost structure everything silently prices in.
2. **Flip it** — set that cost to ~zero and reason forward mechanically:
   volume explodes ("the amount of code produced is going to explode"),
   attention becomes the scarce good, artifacts stop being read ("in one year
   we'll let models generate the code and nobody will actually look at it —
   who still checks the assembly output of their compiler?").
3. **Locate the new pressure point** — his slide states it: "the system
   pressure shifts to **validation and specification**. The core disciplines
   of the future engineer will focus on defining system correctness, auditing
   security boundaries, and designing precise constraints."

The compiler analogy is the calibration tool: find the last time a layer's
output stopped being inspected by humans, and expect the same social
transition — suspicion, spot-checking, then trust — to replay one layer up.
