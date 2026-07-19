---
url: http://birdhouse.org/beos/bible/bos/int_schillings.html
fetched: 2026-07-19
summary: "Repertoire: three lived BeOS-era cases with rationales from the Henry Bortman BeOS Bible interview — the thread-per-window architecture bet, bare-metal bring-up ordering, and the App Server handoff against tunnel vision."
---
# Repertoire — BeOS cases (Bortman interview, BeOS Bible)

Lived cases with situation → decision → rationale, from the published
BeOS Bible interview. These are the precedents behind mindsets he states
abstractly 25 years later.

**Case 1 — the thread-per-window bet.** Situation: designing BeOS's UI
framework with no precedent; "every windowing system had one thread
managing all windows." Decision: one client-side and one server-side
thread *per window*. Rationale and risk owned explicitly: "we were not
certain it could be done... it took a while to fix all the issues of
deadlock and synchronization." The payoff was his real objective (see
case 3): "always a thread ready to respond to a user interaction, even
when heavily loaded." Precedent for: contrarian architecture bets priced
by the bottleneck that actually matters, accepted difficulty as the cost
of terminating a structural problem.

**Case 2 — bare-metal bring-up ordering.** Situation: 20 MHz Hobbit
hardware, no video, serial link only. Decision: keyboard driver first,
then a crude filesystem, then graphics. Rationale: remove the immediate
usability barrier blocking every next iteration — sequence work by what
unblocks the feedback loop, not by architectural grandeur.

**Case 3 — the App Server handoff.** Situation: years deep in the
graphics system he wrote, facing burnout. Decision: hand his own
subsystem to other engineers (Pierre, George). Rationale: "when you work
on a project too long, you start to get tunnel vision... giving the
project to someone else made it much better than what I would have
done." A lived instance of his epistemics: treating his own perspective
as a degrading instrument and acting on it — at personal cost.

Cross-source corroboration: "speed is mostly a perception thing... the
key was always a thread ready to respond" is anchor-C-style analysis
(optimize the constraint users actually price, not the benchmark)
applied in 1998 — independent, 25-years-earlier support for the anchor
extracted from the 2026 keynote.
