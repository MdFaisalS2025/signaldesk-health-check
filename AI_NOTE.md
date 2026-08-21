# AI Note

Yes, used AI (Claude) through most of this — reading the challenge repo,
poking at the CSV, drafting the script, this note. I'm not going to
pretend otherwise since the challenge literally tells you to use it.

First thing I did was have it pull `challenge.md`, `rubric.md`, and
`domain-packet.md` and actually read them instead of just working off the
Handshake job post, because the packet has the real warnings (don't
assume confidence = quality, don't average everything together) that the
posting doesn't repeat. Then before writing any code I had it run some
quick stats on the raw CSV — duplicate check, per-day row counts, a
Spearman correlation — so I knew what was actually in the data instead of
guessing.

The most useful moment: I had a hunch that confidence "probably tracks
rating fine most of the time but breaks somewhere." I asked it to compute
the confidence-vs-rating correlation twice — once across the whole
dataset, once restricted to just the Reply draft/queue series where the
policy change happened. +0.74 overall vs. +0.11 in that one series. That
gap is what the whole README leads with, and I wouldn't have landed on it
just eyeballing the CSV.

What I checked myself: the first version of the "divergence alarm" it
wrote fired on a boring day where the rating dipped by 0.1 point — noise,
not a real signal. I only caught that because I read the actual printed
output line by line instead of trusting that the logic was right because
it ran without errors. Tightened the thresholds myself (rating has to
drop by at least 0.3, flag rate has to jump by 50%+) until it only fired
on the real incident. Also hand-checked the numbers in that one alarm
line against the raw CSV rows myself before calling it done.
