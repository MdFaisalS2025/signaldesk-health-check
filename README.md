# SignalDesk Weekly Health Check

Track A — MS student (AI + Business Analytics), picked the messy-data
track over building my own dataset from scratch.

`health_check.py` reads `product_usage_events.csv` and prints what's
working, what's suspicious, what to check next. Built for whoever owns
SignalDesk, run weekly. Data: their 41-row export, unmodified.

## Why no pandas

First pass used pandas — at 41 rows, pure overhead, and
`.drop_duplicates()` missed the planted duplicate row because its `notes`
text differs between the two copies even though every other column
matches. Rewrote with plain `csv.DictReader` to force myself to look at
each row instead of trusting a method name. Every trap — that duplicate,
`"n/a"` in a numeric column, `team` spelled two ways, a blank rating, a
day missing 2 of 6 series — is caught by name and printed in a ledger,
not quietly dropped.

## What I'd lead with in a meeting

`Support / Reply draft / queue`: confidence hit its weekly high on 08-07,
the exact day acceptance, rating, and minutes-saved hit weekly lows and
flags tripled. Dataset-wide, confidence tracks rating decently (Spearman
≈ +0.74). Inside that one series, +0.11 — fine right up until it isn't.
Encoded as a rule (confidence up + rating down + flag spike), not a
one-off note, so it catches a repeat.

## Assumptions

- 08-07 is missing 2 of 6 series — excluded from totals, not treated as
  a real drop.
- No before/after read on the Aug-4 prompt change — the "after" window
  overlaps a fake demo spike and a policy change, so the tool says so
  instead of printing a confounded number.
- Under 30 sessions is labeled directional, not precise.

## With more time

Real columns for prompt-version/policy events, not free-text notes;
rules as config; divergence check as a dbt test.
