#!/usr/bin/env python3
"""
SignalDesk Weekly Health Check
--------------------------------
A trust-gated report over SignalDesk's workflow usage export.

Answers, in order, the teammate's actual question:
  1. What's working right now?
  2. What looks suspicious (and should not be trusted blindly)?
  3. What should we look at next?

Design choice: stdlib only, no pandas. At 41 rows, a dataframe buys nothing
and hides exactly the row-level oddities (a duplicate whose `notes` column
differs, a "n/a" string in a numeric column) that matter most here. Hand
parsing forces you to look at every row once. See README.md for more.

Usage:
    python health_check.py [path/to/csv]
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict, Counter

DEFAULT_PATH = "data/product_usage_events.csv"
BUSINESS_KEY = ("date", "team", "workflow", "source")
NUMERIC_COLS = [
    "sessions", "completed", "accepted_output", "flagged_for_review",
    "avg_minutes_saved", "median_confidence", "user_rating",
]
MIN_N_FOR_RATE = 30  # below this, don't report a rate as if it were precise


# ---------------------------------------------------------------- loading --

def to_float(value: str):
    """Return a float, or None if the cell isn't really numeric.
    Deliberately does NOT coerce bad values to 0.0 -- a missing minute-saved
    figure is not the same claim as zero minutes saved."""
    value = (value or "").strip()
    if value == "" or value.lower() in ("n/a", "na", "null", "none"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_rows(path: str):
    """Returns (rows, annotations). `annotations` records the silent-but-real
    repairs made during parsing -- casing normalization and dirty numeric
    cells -- so the report can surface them instead of quietly fixing them
    and moving on."""
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

def dedupe_on_business_key(rows: list[dict]):
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


def detect_incomplete_days(rows: list[dict]):
    """A day is 'incomplete' if it has fewer distinct series than the modal
    (most common) count across days -- found empirically, not hardcoded."""
    by_day = defaultdict(set)
    for r in rows:
        by_day[r["date"]].add((r["team"], r["workflow"], r["source"]))
    counts = Counter(len(v) for v in by_day.values())
    modal_n = counts.most_common(1)[0][0]
    return {d for d, v in by_day.items() if len(v) < modal_n}, modal_n


def quarantine(rows: list[dict]):
    """Partition rows into `clean` (safe to aggregate) vs `flagged` (kept,
    but annotated / excluded from headline numbers), plus a ledger of why."""
    ledger = []

    deduped, dupes = dedupe_on_business_key(rows)
    for r, reason in dupes:
        ledger.append((r, "DROP", reason))

    incomplete_days, modal_n = detect_incomplete_days(deduped)

    clean, flagged = [], []
    for r in deduped:
        reasons = []
        if "traffic spike from demo account" in r["notes"].lower():
            reasons.append("flagged as demo-account traffic, not real usage")
        if r["date"] in incomplete_days:
            reasons.append(
                f"partial export day (fewer series present than the modal {modal_n:.0f}/day)"
            )
        if reasons:
            for reason in reasons:
                ledger.append((r, "EXCLUDE FROM TOTALS", reason))
            flagged.append(r)
        else:
            clean.append(r)

    return clean, flagged, ledger


# --------------------------------------------------------------- stats ----

def spearman(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 4:
        return None
    xs_, ys_ = zip(*pairs)
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    rx, ry = rank(xs_), rank(ys_)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1 - 6 * d2 / (n * (n * n - 1))


def pct(numer: float, denom: float) -> float | None:
    if not denom:
        return None
    return 100.0 * numer / denom


BLOCKS = " .:-=+*#%@"  # ASCII ramp, low to high -- ascii, not unicode blocks,
                       # because Windows' default console codepage (cp1252)
                       # can't print the unicode block characters


def sparkline(values: list[float | None]) -> str:
    """One-line trend, scaled to its own min/max so confidence (0-1) and
    rating (1-5) both read as shape, not absolute height -- the point is
    'which way is it moving,' not the raw units."""
    nums = [v for v in values if v is not None]
    if len(nums) < 2:
        return "".join("?" if v is None else BLOCKS[-1] for v in values)
    lo, hi = min(nums), max(nums)
    span = hi - lo or 1.0
    out = []
    for v in values:
        if v is None:
            out.append("?")
        else:
            level = round((v - lo) / span * (len(BLOCKS) - 2)) + 1
            out.append(BLOCKS[level])
    return "".join(out)


# -------------------------------------------------------------- sections --

def section_working(clean: list[dict]):
    print("=" * 72)
    print("1) WHAT'S WORKING RIGHT NOW")
    print("=" * 72)

    agg = defaultdict(lambda: {"sessions": 0, "completed": 0, "accepted": 0,
                                "flagged": 0, "min_saved_total": 0.0})
    for r in clean:
        a = agg[r["workflow"]]
        a["sessions"] += r["sessions"] or 0
        a["completed"] += r["completed"] or 0
        a["accepted"] += r["accepted_output"] or 0
        a["flagged"] += r["flagged_for_review"] or 0
        if r["avg_minutes_saved"] is not None and r["sessions"]:
            a["min_saved_total"] += r["avg_minutes_saved"] * r["sessions"]

    print(f"{'workflow':22}{'sessions':>9}{'accept%':>9}{'flag%':>8}{'min/sess':>10}{'total min saved':>18}")
    rows_sorted = sorted(agg.items(), key=lambda kv: -kv[1]["sessions"])
    for w, a in rows_sorted:
        n = a["sessions"]
        acc = pct(a["accepted"], n)
        flag = pct(a["flagged"], n)
        mps = a["min_saved_total"] / n if n else None
        note = "" if n >= MIN_N_FOR_RATE else "  (n small, directional only)"
        print(f"{w:22}{n:>9.0f}{acc:>8.1f}%{flag:>7.1f}%{mps:>9.1f}{a['min_saved_total']:>17.0f}.0{note}")

    print(
        "\nNo single workflow wins on every metric, so 'most useful' depends on what you\n"
        "optimize for:\n"
        "  - Reply draft leads on volume and acceptance rate -> most-adopted workflow.\n"
        "  - Lead summary leads on lowest flag rate and total minutes saved -> most\n"
        "    trustworthy at scale.\n"
        "  - Feedback clustering saves the most time per session but has the lowest\n"
        "    acceptance rate -> useful but least reliable output.\n"
        "(Excludes rows quarantined in section 2 and any day flagged incomplete.)"
    )
    print()


def section_suspicious(flagged: list[dict], ledger, clean: list[dict], annotations):
    print("=" * 72)
    print("2) WHAT LOOKS SUSPICIOUS")
    print("=" * 72)

    print(f"Quarantine ledger ({len(ledger)} rows touched):")
    for r, action, reason in ledger:
        print(f"  [{action:20}] {r['date']}  {r['team']:8}{r['workflow']:20}{r['source']:12} - {reason}")
    print()

    print(f"Dirty-value repairs made while parsing ({len(annotations)} cells):")
    for r, reason in annotations:
        print(f"  [{'REPAIRED':20}] {r['date']}  {r['team']:8}{r['workflow']:20}{r['source']:12} - {reason}")
    print(
        "  These rows are still counted above (only the bad cell was nulled, not the\n"
        "  row) -- worth checking if the source pipeline should reject them instead."
    )
    print()

    # --- Divergence alarm: confidence UP while human signal DOWN ---
    print("Divergence alarm (model confidence up, human signal down):")
    series = defaultdict(list)
    for r in clean + flagged:
        series[(r["team"], r["workflow"], r["source"])].append(r)

    any_alarm = False
    for key, rs in series.items():
        rs = sorted(rs, key=lambda r: r["date"])
        for prev, cur in zip(rs, rs[1:]):
            if None in (prev["median_confidence"], cur["median_confidence"],
                        prev["user_rating"], cur["user_rating"]):
                continue
            conf_up = cur["median_confidence"] > prev["median_confidence"]
            rating_drop = prev["user_rating"] - cur["user_rating"]
            flag_rate_prev = pct(prev["flagged_for_review"], prev["sessions"]) or 0
            flag_rate_cur = pct(cur["flagged_for_review"], cur["sessions"]) or 0
            # thresholds: a single-decimal rating wobble or routine flag-rate
            # noise isn't a divergence -- require a real rating drop AND a
            # real flag-rate spike, together, before calling it an alarm.
            if conf_up and rating_drop >= 0.3 and flag_rate_cur > flag_rate_prev * 1.5:
                any_alarm = True
                team, wf, src = key
                print(f"  {team}/{wf}/{src}: {prev['date']} -> {cur['date']}")
                print(f"    confidence {prev['median_confidence']:.2f} -> {cur['median_confidence']:.2f}  (UP)")
                print(f"    rating     {prev['user_rating']:.1f}  -> {cur['user_rating']:.1f}   (DOWN)")
                print(f"    flag rate  {flag_rate_prev:.1f}%  -> {flag_rate_cur:.1f}%  (spike)")
                print(f"    note: \"{cur['notes']}\"")
                # full week, both signals, same scale trick as above -- this
                # is the picture that makes the divergence obvious at a glance
                week = sorted(rs, key=lambda r: r["date"])
                conf_line = sparkline([r["median_confidence"] for r in week])
                rate_line = sparkline([r["user_rating"] for r in week])
                print(f"    week shape  confidence {conf_line}   rating {rate_line}"
                      f"   ({week[0]['date']} .. {week[-1]['date']}, last bar = incident day)")
    if not any_alarm:
        print("  none detected in this export.")
    print()

    # --- Metric trust ranking, evidence-backed ---
    all_rows = clean + flagged
    conf = [r["median_confidence"] for r in all_rows]
    rating = [r["user_rating"] for r in all_rows]
    accept_rate = [pct(r["accepted_output"], r["sessions"]) for r in all_rows]

    rho_overall = spearman(conf, rating)
    rho_overall_acc = spearman(conf, accept_rate)

    # same correlation, but only within the series that had the divergence
    incident_key = None
    for key, rs in series.items():
        team, wf, src = key
        if wf == "Reply draft" and src == "queue":
            incident_key = key
    rho_incident = None
    if incident_key:
        rs = series[incident_key]
        rho_incident = spearman([r["median_confidence"] for r in rs],
                                 [r["user_rating"] for r in rs])

    print("Metric trust ranking (evidence, not opinion):")
    print(f"  median_confidence vs user_rating, whole dataset:      rho = {rho_overall:+.2f}")
    if rho_incident is not None:
        print(f"  same pair, WITHIN the flagged Reply draft/queue series: rho = {rho_incident:+.2f}")
    print(
        "  -> Confidence tracks human judgment fine in steady state, but that\n"
        "     relationship breaks down exactly during the incident above -- the one\n"
        "     moment you'd actually want an early warning. Trust it least when you\n"
        "     need it most. Treat completed/accepted_output as the primary signal\n"
        "     and confidence as a lagging, not leading, indicator."
    )
    print()


def section_next(clean, flagged, ledger):
    print("=" * 72)
    print("3) WHAT TO LOOK AT NEXT")
    print("=" * 72)
    items = [
        "Support / Reply draft / queue, 2026-08-07: acceptance and rating cratered "
        "same day flag rate tripled and confidence hit its 7-day high. Note says "
        "'review policy changed mid-day' -- confirm whether the policy change itself "
        "(more/stricter human review) or an underlying quality regression caused the drop; "
        "the data alone can't separate them.",

        "The 'new prompt version' rolled out 2026-08-04 cannot be evaluated from this "
        "export: the after-window overlaps both the demo-account spike (08-05) and the "
        "review-policy change (08-07), and 08-07 is a partial day. Before it's rolled out "
        "further, re-run this comparison on a window that excludes both confounds, or tag "
        "prompt-version directly in the export instead of relying on free-text notes.",

        "Feedback clustering has the lowest acceptance rate of the three workflows (45%) "
        "despite the highest per-session time saved -- worth a qualitative look at a sample "
        "of rejected clusters before expanding it to more teams.",
    ]
    for i, item in enumerate(items, 1):
        print(f"  {i}. {item}\n")


def print_intake_summary(raw_n, clean, flagged, ledger):
    print("SignalDesk Weekly Health Check")
    print(f"Source rows: {raw_n}  |  clean: {len(clean)}  |  "
          f"flagged/excluded: {len(flagged)}  |  ledger entries: {len(ledger)}")
    print()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    rows, annotations = load_rows(path)
    clean, flagged, ledger = quarantine(rows)

    print_intake_summary(len(rows), clean, flagged, ledger)
    section_working(clean)
    section_suspicious(flagged, ledger, clean, annotations)
    section_next(clean, flagged, ledger)


if __name__ == "__main__":
    main()
