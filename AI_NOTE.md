# AI Note

Yes, used AI (Claude) through most of this: reading the challenge repo,
poking at the CSV, drafting the script, this note. I'm not going to
pretend otherwise since the challenge literally tells you to use it.

First thing I did was have it pull `challenge.md`, `rubric.md`, and
`domain-packet.md` and actually read them instead of just working off the
Handshake job post, because the packet has the real warnings (don't
assume confidence = quality, don't average everything together) that the
posting doesn't repeat. Then before writing any code I had it run some
quick stats on the raw CSV, a duplicate check, per-day row counts, a
Spearman correlation, so I knew what was actually in the data instead of
guessing.

The most useful moment: I had a hunch that confidence "probably tracks
rating fine most of the time but breaks somewhere." I asked it to compute
the confidence-vs-rating correlation twice, once across the whole
dataset, once restricted to just the incident series. That split (strong
correlation overall, near zero inside the incident) is what the README
leads with, and I wouldn't have landed on it just eyeballing the CSV.

What I checked myself, and this is the part I'd actually walk through in
an interview: after the first working version, I asked for a second
pass, specifically to find bugs rather than polish. It found two real
ones I would not have caught on my own. First, the correlation function
ranked ties incorrectly, which mattered because `user_rating` in this
data is mostly repeated values, and it had quietly inflated the incident
correlation from about 0 to +0.11, a wrong number sitting in my
headline claim. Second, and worse, the "what's working" and "what's
next" sections had specific dates typed directly into the print
statements, so the tool would print the same story about August 7th no
matter what CSV you gave it. I confirmed that myself by running it on a
subset of the data with no incident in it and watching it still describe
one. Both are fixed now: the correlation is tie-corrected, and every
conclusion is computed from whatever rows are actually loaded, checked
by rerunning the tool on that empty-incident subset until it correctly
reported nothing.
