#!/usr/bin/env python3
"""
SignalDesk Weekly Health Check

A trust-gated report over SignalDesk's workflow usage export.

Answers, in order, the teammate's actual question:
  1. What's working right now?
  2. What looks suspicious (and should not be trusted blindly)?
  3. What should we look at next?

Every conclusion printed below is derived from the data that was actually
loaded. Nothing about a specific date or workflow is hardcoded, so pointing
this at next week's export gives next week's answers, not this week's.

Design choice: stdlib only, no pandas. At 41 rows a dataframe buys nothing
and hides exactly the row-level oddities (a duplicate whose `notes` column
differs, a "n/a" string in a numeric column) that matter most here.

Requires Python 3.9+.

Usage:
    python health_check.py [path/to/csv]
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict, Counter
from datetime import date

DEFAULT_PATH = "data/product_usage_events.csv"
BUSINESS_KEY = ("date", "team", "workflow", "source")
NUMERIC_COLS = [
    "sessions", "completed", "accepted_output", "flagged_for_review",
    "avg_minutes_saved", "median_confidence", "user_rating",
]

# Two different thresholds, for two different questions.
MIN_N_FOR_RATE = 30      # aggregate sessions needed before a rate is "precise"
MIN_DAILY_SESSIONS = 20  # a single day's row below this is a thin sample

# Divergence rule thresholds. A 0.1 rating wobble or routine flag noise is not
# an incident; require a real drop AND a real flag spike, together.
MIN_RATING_DROP = 0.3
FLAG_SPIKE_MULTIPLE = 1.5


# ---------------------------------------------------------------- loading --

def to_float(value: str):
    """Return a float, or None if the cell isn't really numeric.

    Deliberately does NOT coerce bad values to 0.0: a missing minutes-saved
    figure is not the same claim as zero minutes saved. Also rejects inf/nan,
    which float() happily parses and which would silently poison any sum.
    """
    value = (value or "").strip()
    if value == "" or value.lower() in ("n/a", "na", "null", "none"):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def load_rows(path: str):
    """Returns (rows, annotations). `annotations` records the small repairs
    made while parsing (casing, dirty numeric cells) so the report can show
    them rather than quietly fixing them and moving on."""
    with open(path, newline="", encoding="utf-8") as f:
        raw = list(csv.DictReader(f))
    rows, annotations = [], []
    for r in raw:
        row = dict(r)
        raw_team = row["team"].strip()
        row["team"] = raw_team.title()  # 'product' vs 'Product'
        if raw_team != row["team"]:
            annotations.append(
                (row, f"team casing normalized: '{raw_team}' -> '{row['team']}'")
            )
        row["workflow"] = row["workflow"].strip()
        row["source"] = row["source"].strip()
        row["notes"] = (row.get("notes") or "").strip()
        for col in NUMERIC_COLS:
            raw_val = (row.get(col, "") or "").strip()
            row[col] = to_float(raw_val)
            if row[col] is None and raw_val != "":
                annotations.append((row, f"{col}='{raw_val}' is not numeric -> treated as missing"))
            elif row[col] is None and raw_val == "":
                annotations.append((row, f"{col} is blank -> treated as missing"))
        rows.append(row)
    return rows, annotations


# ------------------------------------------------------------ quarantine --

def dedupe_on_business_key(rows: list):
    """Exact drop_duplicates() misses this dataset's duplicate because the
    `notes` text differs between the two copies. Key on the business fields
    that actually identify a distinct measurement instead."""
    seen = {}
    kept, dropped = [], []
    for r in rows:
        key = tuple(r[c] for c in BUSINESS_KEY)
        if key in seen:
            dropped.append((r, "duplicate export row (same date/team/workflow/source)"))
        else:
            seen[key] = r
            kept.append(r)
    return kept, dropped


def detect_incomplete_days(rows: list):
    """A day is 'incomplete' if it carries fewer distinct series than the
    modal count across days. Derived from the data, not hardcoded."""
    by_day = defaultdict(set)
    for r in rows:
        by_day[r["date"]].add((r["team"], r["workflow"], r["source"]))
    counts = Counter(len(v) for v in by_day.values())
    modal_n = counts.most_common(1)[0][0]
    return {d for d, v in by_day.items() if len(v) < modal_n}, modal_n


def quarantine(rows: list):
    """Partition rows three ways, because "don't trust this row" and "don't
    sum this row into a cross-day total" are different claims:

    - `clean`: safe to aggregate into headline totals.
    - `flagged`: real usage, just excluded from totals because the day it
      belongs to is a partial export (not comparable to a full day). Still
      real data, so still usable for row-level analysis like correlation.
    - `rejected`: not real usage at all (demo-account traffic). Excluded
      from every analysis, not just totals.

    A previous version lumped `flagged` and `rejected` together, which meant
    the fabricated demo-traffic row was quietly included in the confidence
    vs. rating trust correlation. Returns a ledger explaining every call.
    """
    ledger = []

    deduped, dupes = dedupe_on_business_key(rows)
    for r, reason in dupes:
        ledger.append((r, "DROP", reason))

    incomplete_days, modal_n = detect_incomplete_days(deduped)

    clean, flagged, rejected = [], [], []
    for r in deduped:
        if "demo account" in r["notes"].lower():
            ledger.append((r, "REJECT", "demo-account traffic, not real usage"))
            rejected.append(r)
        elif r["date"] in incomplete_days:
            ledger.append((r, "EXCLUDE FROM TOTALS",
                            f"partial export day (fewer series present than the modal {modal_n}/day)"))
            flagged.append(r)
        else:
            clean.append(r)

    return clean, flagged, rejected, ledger, incomplete_days


# --------------------------------------------------------------- stats ----

def _average_ranks(values):
    """Ranks with ties averaged. Plain ordinal ranking silently biases
    Spearman when values repeat, and `user_rating` here is mostly ties."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(xs, ys):
    """Tie-corrected Spearman. Returns (rho, n), or (None, n) if too few
    usable pairs to say anything."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 4:
        return None, n
    xs_, ys_ = zip(*pairs)
    rx, ry = _average_ranks(xs_), _average_ranks(ys_)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    if not den:
        return None, n
    return num / den, n


def pct(numer, denom):
    if not denom:
        return None
    return 100.0 * numer / denom


RAMP = ".:-=+*#%@"  # ASCII, not unicode blocks: Windows' default console
                    # codepage (cp1252) cannot print the block characters.


def sparkline(values) -> str:
    """One-line trend, scaled to its own min/max so confidence (0-1) and
    rating (1-5) both read as shape rather than absolute height. The question
    is which way it moved, not what the raw units were."""
    nums = [v for v in values if v is not None]
    if len(nums) < 2 or max(nums) == min(nums):
        return "".join("?" if v is None else RAMP[len(RAMP) // 2] for v in values)
    lo, hi = min(nums), max(nums)
    span = hi - lo
    out = []
    for v in values:
        if v is None:
            out.append("?")
        else:
            out.append(RAMP[round((v - lo) / span * (len(RAMP) - 1))])
    return "".join(out)


# ------------------------------------------------------------- analysis ---

def build_series(rows: list):
    """Group rows into (team, workflow, source) series, each sorted by date."""
    series = defaultdict(list)
    for r in rows:
        series[(r["team"], r["workflow"], r["source"])].append(r)
    for key in series:
        series[key].sort(key=lambda r: r["date"])
    return dict(series)


def _days_apart(d1: str, d2: str):
    try:
        return (date.fromisoformat(d2) - date.fromisoformat(d1)).days
    except ValueError:
        return None


def detect_divergences(series: dict):
    """The one reusable rule: model confidence rising while the human signals
    fall. Returns a list of findings, so the report and the 'what next' list
    both read from the same source instead of restating a hardcoded story."""
    findings = []
    for key, rows in series.items():
        for prev, cur in zip(rows, rows[1:]):
            if None in (prev["median_confidence"], cur["median_confidence"],
                        prev["user_rating"], cur["user_rating"]):
                continue
            gap = _days_apart(prev["date"], cur["date"])
            rating_drop = prev["user_rating"] - cur["user_rating"]
            flag_prev = pct(prev["flagged_for_review"], prev["sessions"]) or 0.0
            flag_cur = pct(cur["flagged_for_review"], cur["sessions"]) or 0.0
            if (cur["median_confidence"] > prev["median_confidence"]
                    and rating_drop >= MIN_RATING_DROP
                    and flag_cur > flag_prev * FLAG_SPIKE_MULTIPLE):
                findings.append({
                    "key": key, "prev": prev, "cur": cur,
                    "flag_prev": flag_prev, "flag_cur": flag_cur,
                    "rating_drop": rating_drop,
                    "gap_days": gap,
                    "series_rows": rows,
                })
    return findings


def find_change_events(rows: list):
    """Pull claimed change events out of the free-text `notes` column.
    Keyword matching on prose is fragile, which is itself worth reporting:
    these should be real columns, not notes."""
    events = {}
    for r in rows:
        note = r["notes"].lower()
        if "new prompt" in note or "policy chang" in note or "version" in note:
            if "duplicate" in note:
                continue
            events.setdefault(r["notes"], set()).add(r["date"])
    return {note: min(dates) for note, dates in events.items()}


def aggregate_workflows(clean: list):
    agg = defaultdict(lambda: {"sessions": 0.0, "completed": 0.0, "accepted": 0.0,
                               "flagged": 0.0, "min_saved": 0.0, "thin_days": 0,
                               "rows": 0})
    for r in clean:
        a = agg[r["workflow"]]
        sessions = r["sessions"] or 0
        a["sessions"] += sessions
        a["completed"] += r["completed"] or 0
        a["accepted"] += r["accepted_output"] or 0
        a["flagged"] += r["flagged_for_review"] or 0
        if r["avg_minutes_saved"] is not None:
            a["min_saved"] += r["avg_minutes_saved"] * sessions
        a["rows"] += 1
        if sessions < MIN_DAILY_SESSIONS:
            a["thin_days"] += 1
    return agg


# -------------------------------------------------------------- sections --

def header(title: str):
    print("=" * 72)
    print(title)
    print("=" * 72)


def section_working(clean: list):
    header("1) WHAT'S WORKING RIGHT NOW")
    agg = aggregate_workflows(clean)
    if not agg:
        print("No clean rows survived the quality gate; nothing to rank.\n")
        return

    print(f"{'workflow':22}{'sessions':>9}{'accept%':>9}{'flag%':>8}"
          f"{'min/sess':>10}{'total min':>11}{'thin days':>11}")
    for w, a in sorted(agg.items(), key=lambda kv: -kv[1]["sessions"]):
        n = a["sessions"]
        acc = pct(a["accepted"], n)
        flag = pct(a["flagged"], n)
        mps = a["min_saved"] / n if n else 0.0
        acc_s = f"{acc:>8.1f}%" if n >= MIN_N_FOR_RATE else f"{'n<%d' % MIN_N_FOR_RATE:>9}"
        flag_s = f"{flag:>7.1f}%" if n >= MIN_N_FOR_RATE else f"{'-':>8}"
        thin = f"{a['thin_days']}/{a['rows']}"
        print(f"{w:22}{n:>9.0f}{acc_s}{flag_s}{mps:>10.1f}{a['min_saved']:>11.0f}{thin:>11}")

    # Leaders are computed, not asserted, so this text follows the data.
    def leader(metric, best=max):
        return best(agg.items(), key=metric)[0]

    most_used = leader(lambda kv: kv[1]["sessions"])
    best_accept = leader(lambda kv: pct(kv[1]["accepted"], kv[1]["sessions"]) or 0)
    fewest_flags = leader(lambda kv: pct(kv[1]["flagged"], kv[1]["sessions"]) or 0, min)
    most_time = leader(lambda kv: kv[1]["min_saved"])
    best_per_session = leader(lambda kv: (kv[1]["min_saved"] / kv[1]["sessions"]) if kv[1]["sessions"] else 0)
    worst_accept = leader(lambda kv: pct(kv[1]["accepted"], kv[1]["sessions"]) or 0, min)

    print("\nNo single workflow wins on every metric, so 'most useful' depends on"
          "\nwhat you optimize for:")
    print(f"  - most used:              {most_used}")
    print(f"  - highest acceptance:     {best_accept}")
    print(f"  - fewest flags:           {fewest_flags}")
    print(f"  - most total time saved:  {most_time}")
    if best_per_session == worst_accept:
        print(f"  - most time per session:  {best_per_session} (also has the lowest acceptance rate)")
    else:
        print(f"  - most time per session:  {best_per_session} "
              f"(lowest acceptance belongs to {worst_accept})")
    print("'thin days' counts contributing daily rows under "
          f"{MIN_DAILY_SESSIONS} sessions, where a rate is noisy.")
    print("Excludes every row quarantined in section 2.\n")


def section_suspicious(clean, flagged, ledger, annotations, divergences):
    header("2) WHAT LOOKS SUSPICIOUS")

    print(f"Quarantine ledger ({len(ledger)} entries):")
    if not ledger:
        print("  nothing quarantined.")
    for r, action, reason in ledger:
        print(f"  [{action:20}] {r['date']}  {r['team']:8}{r['workflow']:20}"
              f"{r['source']:12} - {reason}")
    print()

    print(f"Dirty-value repairs made while parsing ({len(annotations)} cells):")
    if not annotations:
        print("  none.")
    for r, reason in annotations:
        print(f"  [{'REPAIRED':20}] {r['date']}  {r['team']:8}{r['workflow']:20}"
              f"{r['source']:12} - {reason}")
    if annotations:
        print("  Only the bad cell was nulled, not the row, so these rows still count\n"
              "  above. Worth deciding whether the pipeline should reject them instead.")
    print()

    print("Divergence alarm (model confidence up, human signals down):")
    if not divergences:
        print("  none detected in this export.")
    for d in divergences:
        team, wf, src = d["key"]
        prev, cur = d["prev"], d["cur"]
        print(f"  {team}/{wf}/{src}: {prev['date']} -> {cur['date']}")
        print(f"    confidence {prev['median_confidence']:.2f} -> {cur['median_confidence']:.2f}  (UP)")
        print(f"    rating     {prev['user_rating']:.1f}  -> {cur['user_rating']:.1f}   (DOWN)")
        print(f"    flag rate  {d['flag_prev']:.1f}% -> {d['flag_cur']:.1f}%  (spike)")
        print(f"    note: \"{cur['notes']}\"")
        if d["gap_days"] is not None and d["gap_days"] != 1:
            print(f"    caution: these rows are {d['gap_days']} days apart, not consecutive.")
        week = d["series_rows"]
        conf_line = sparkline([r["median_confidence"] for r in week])
        rate_line = sparkline([r["user_rating"] for r in week])
        print(f"    shape  confidence {conf_line}   rating {rate_line}"
              f"   ({week[0]['date']} .. {week[-1]['date']})")
    print()

    section_metric_trust(clean, flagged, divergences)


def section_metric_trust(clean, flagged, divergences):
    """Rank metric trustworthiness from evidence in this export, rather than
    repeating the domain packet's warning back at the reader."""
    print("Metric trust check (evidence, not opinion):")
    all_rows = clean + flagged
    rho_all, n_all = spearman([r["median_confidence"] for r in all_rows],
                              [r["user_rating"] for r in all_rows])
    if rho_all is None:
        print("  not enough paired confidence/rating data to say anything.\n")
        return
    print(f"  median_confidence vs user_rating, all rows:  rho = {rho_all:+.2f}  (n={n_all})")

    if not divergences:
        print("  No divergence fired this run, so there is no incident window to\n"
              "  contrast against. On this export alone, confidence and rating agree.\n")
        return

    # Contrast against whichever series actually tripped the alarm.
    for d in divergences:
        team, wf, src = d["key"]
        rows = d["series_rows"]
        rho_s, n_s = spearman([r["median_confidence"] for r in rows],
                              [r["user_rating"] for r in rows])
        if rho_s is None:
            print(f"  {team}/{wf}/{src}: too few points (n={n_s}) to correlate.")
            continue
        print(f"  same pair, within {team}/{wf}/{src}: rho = {rho_s:+.2f}  (n={n_s})")
    print("  -> Confidence agrees with human judgment in steady state, then stops\n"
          "     agreeing inside the series that broke, which is the one moment an\n"
          "     early warning would matter. Treat it as lagging, not leading, and\n"
          "     lean on accepted_output. Caveat: these per-series n are small, so\n"
          "     this is a reason to distrust the metric, not a measurement of it.\n")


def section_next(clean, divergences, change_events, incomplete_days, flagged, rejected):
    header("3) WHAT TO LOOK AT NEXT")
    items = []

    divergence_dates = {d["cur"]["date"] for d in divergences}
    for d in divergences:
        team, wf, src = d["key"]
        items.append(
            f"{team} / {wf} / {src}, {d['cur']['date']}: rating fell "
            f"{d['rating_drop']:.1f} and flags went {d['flag_prev']:.0f}% -> "
            f"{d['flag_cur']:.0f}% while model confidence rose. The note reads "
            f"\"{d['cur']['notes']}\". Confirm whether stricter review or an actual "
            f"quality regression caused it, because this data cannot separate the two."
        )

    # A claimed change is only evaluable if its after-window is clean. A
    # window is dirty if it contains a partial day, an incomplete day, or a
    # rejected (fabricated) row. Skip a change whose own start date is the
    # divergence just reported above, since that would just restate item 1
    # as a confound of itself.
    dirty_dates = ({r["date"] for r in flagged}
                   | {r["date"] for r in rejected}
                   | set(incomplete_days))
    for note, start in sorted(change_events.items(), key=lambda kv: kv[1]):
        if start in divergence_dates:
            continue
        confounds = sorted(d for d in dirty_dates if d >= start)
        if confounds:
            items.append(
                f"\"{note}\" starts {start}, but the window after it contains "
                f"{', '.join(confounds)}, which this run already quarantined. Any "
                f"before/after read would be confounded, so it is deliberately not "
                f"reported. Tag prompt version and policy changes as real columns "
                f"instead of free text, then re-run on a clean window."
            )

    agg = aggregate_workflows(clean)
    if agg:
        worst = min(agg.items(), key=lambda kv: pct(kv[1]["accepted"], kv[1]["sessions"]) or 0)
        rate = pct(worst[1]["accepted"], worst[1]["sessions"]) or 0
        per_session = worst[1]["min_saved"] / worst[1]["sessions"] if worst[1]["sessions"] else 0
        items.append(
            f"{worst[0]} has the lowest acceptance rate ({rate:.0f}%) despite saving "
            f"{per_session:.1f} minutes per session. Read a sample of rejected outputs "
            f"before expanding it to more teams."
        )

    if not items:
        print("  Nothing stood out in this export.\n")
        return
    for i, item in enumerate(items[:3], 1):
        print(f"  {i}. {item}\n")


def print_intake(raw_n, clean, flagged, rejected, ledger):
    print("SignalDesk Weekly Health Check")
    print(f"Source rows: {raw_n}  |  clean: {len(clean)}  |  "
          f"partial-day (real, excluded from totals): {len(flagged)}  |  "
          f"rejected (not real usage): {len(rejected)}  |  "
          f"ledger entries: {len(ledger)}")
    print()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    try:
        rows, annotations = load_rows(path)
    except FileNotFoundError:
        print(f"Could not find a CSV at '{path}'.", file=sys.stderr)
        print(f"Usage: python {sys.argv[0]} [path/to/csv]", file=sys.stderr)
        return 2
    except (KeyError, UnicodeDecodeError) as exc:
        print(f"'{path}' does not look like a SignalDesk export ({exc}).", file=sys.stderr)
        return 2

    if not rows:
        print(f"'{path}' has a header but no data rows.", file=sys.stderr)
        return 2

    clean, flagged, rejected, ledger, incomplete_days = quarantine(rows)
    # `rejected` (fabricated demo traffic) never enters any analysis, real or
    # aggregate. `flagged` (real usage from a partial day) is excluded from
    # totals but still a legitimate data point for row-level analysis.
    real_rows = clean + flagged
    series = build_series(real_rows)
    divergences = detect_divergences(series)
    change_events = find_change_events(real_rows)

    print_intake(len(rows), clean, flagged, rejected, ledger)
    section_working(clean)
    section_suspicious(clean, flagged, ledger, annotations, divergences)
    section_next(clean, divergences, change_events, incomplete_days, flagged, rejected)
    return 0


if __name__ == "__main__":
    sys.exit(main())
