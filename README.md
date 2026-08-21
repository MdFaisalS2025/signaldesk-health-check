# SignalDesk Weekly Health Check

Track A. MS student (AI + Business Analytics), picked the messy-data
track over picking my own dataset.

`health_check.py` reads `product_usage_events.csv` and prints what's
working, suspicious, and worth checking next. Built for whoever owns
SignalDesk, run weekly. Data: their 41-row export, unmodified.

## Why no pandas

At 41 rows a dataframe is pure overhead. I checked, and pandas'
`.drop_duplicates()` misses the planted duplicate row, because its
`notes` text differs between the two copies even though every other
column matches. Used plain `csv.DictReader` instead, which forces you to
look at each row rather than trust a method name. Every trap (that
duplicate, `"n/a"` in a numeric column, `team` spelled two ways, a blank
rating, a partial day) is caught by name and printed in a ledger, not
quietly dropped.

## What I'd lead with in a meeting

`Support / Reply draft / queue`: confidence hit its weekly high on 08-07,
the exact day acceptance, rating, and minutes-saved hit weekly lows and
flags tripled. Dataset-wide, confidence tracks rating decently (Spearman
around +0.72). Inside that series it flips to roughly 0 (n=7, small, say
it loosely): the metric agrees with people right until it stops
mattering. It's a rule (confidence up, rating down, flag spike), not a
note about one date, so it catches a repeat and stays quiet otherwise.

## Assumptions

- 08-07 is missing 2 of 6 series, excluded from totals, not read as a
  real drop.
- No before/after read on the Aug-4 prompt change: the "after" window
  overlaps a fake demo spike and a policy change, so the tool says so.
- Daily rows under 20 sessions get flagged thin, not trusted at face
  value.

## With more time

Real columns for prompt-version/policy events; rules as config;
divergence check as a dbt test.
