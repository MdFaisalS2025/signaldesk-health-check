"""
Lightweight tests for health_check.py -- stdlib unittest, no dependencies.

Not meant to be exhaustive. These pin down the exact traps in the challenge
dataset that a naive analysis (pandas drop_duplicates(), astype(float), a
plain groupby, ordinal-rank Spearman) would get wrong, plus the one property
that matters most for a "weekly health check": its conclusions must come
from whatever data it's given, not from this specific week's dates.
"""
import unittest

from health_check import (
    to_float, load_rows, dedupe_on_business_key, detect_incomplete_days,
    quarantine, spearman, sparkline, build_series, detect_divergences,
    find_change_events, DEFAULT_PATH,
)


class TestToFloat(unittest.TestCase):
    def test_normal_number(self):
        self.assertEqual(to_float("4.2"), 4.2)

    def test_na_text_is_missing_not_zero(self):
        # naive float("n/a") raises, and a bare except returning 0.0 would
        # silently turn "unknown confidence" into "zero confidence" -- a very
        # different claim than what the source row actually says.
        self.assertIsNone(to_float("n/a"))

    def test_blank_is_missing(self):
        self.assertIsNone(to_float(""))
        self.assertIsNone(to_float("   "))

    def test_inf_and_nan_are_rejected(self):
        # float("inf") and float("nan") both parse without raising, and
        # would silently poison any sum() downstream if let through.
        self.assertIsNone(to_float("inf"))
        self.assertIsNone(to_float("nan"))


class TestDedupe(unittest.TestCase):
    def test_business_key_catches_dupe_even_with_different_notes(self):
        rows = [
            {"date": "2026-08-05", "team": "Sales", "workflow": "Lead summary",
             "source": "email", "notes": "traffic spike from demo account"},
            {"date": "2026-08-05", "team": "Sales", "workflow": "Lead summary",
             "source": "email", "notes": "duplicate export row"},
        ]
        kept, dropped = dedupe_on_business_key(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)


class TestIncompleteDay(unittest.TestCase):
    def test_detects_short_day_without_hardcoded_date(self):
        rows = []
        for d in ["2026-08-01", "2026-08-02"]:
            for combo in [("A", "w1", "s1"), ("A", "w2", "s1"), ("B", "w1", "s1")]:
                rows.append({"date": d, "team": combo[0], "workflow": combo[1], "source": combo[2]})
        rows.append({"date": "2026-08-03", "team": "A", "workflow": "w1", "source": "s1"})
        rows.append({"date": "2026-08-03", "team": "A", "workflow": "w2", "source": "s1"})
        incomplete, modal_n = detect_incomplete_days(rows)
        self.assertEqual(incomplete, {"2026-08-03"})
        self.assertEqual(modal_n, 3)


class TestCasingNormalization(unittest.TestCase):
    def test_load_rows_normalizes_team_casing(self):
        rows, annotations = load_rows(DEFAULT_PATH)
        teams = {r["team"] for r in rows}
        self.assertNotIn("product", teams)
        self.assertIn("Product", teams)
        self.assertTrue(any("casing" in a[1] for a in annotations))


class TestSpearman(unittest.TestCase):
    def test_perfect_agreement(self):
        rho, n = spearman([1, 2, 3, 4], [1, 2, 3, 4])
        self.assertAlmostEqual(rho, 1.0)
        self.assertEqual(n, 4)

    def test_perfect_disagreement(self):
        rho, _ = spearman([1, 2, 3, 4], [4, 3, 2, 1])
        self.assertAlmostEqual(rho, -1.0)

    def test_ties_are_averaged_not_ordinal(self):
        # x is strictly increasing; y has two tied pairs that happen to
        # appear in the same order as x. A naive ordinal-rank implementation
        # (rank ties by order of appearance) gives ranks_y == ranks_x here
        # and reports a spurious rho of exactly 1.0. The correct tie-averaged
        # Spearman is ~0.894. This dataset's user_rating column is mostly
        # ties, so this exact bug would have inflated the report's headline
        # correlation.
        xs = [1, 2, 3, 4]
        ys = [1, 1, 2, 2]
        rho, _ = spearman(xs, ys)
        self.assertAlmostEqual(rho, 0.8944271909999159, places=6)
        self.assertNotAlmostEqual(rho, 1.0, places=3)

    def test_ignores_none_pairs(self):
        rho, n = spearman([1, 2, None, 4, 5, 6], [1, 2, 3, None, 5, 6])
        self.assertIsNotNone(rho)
        self.assertEqual(n, 4)

    def test_too_few_points_returns_none(self):
        rho, n = spearman([1, 2], [1, 2])
        self.assertIsNone(rho)
        self.assertEqual(n, 2)


class TestSparkline(unittest.TestCase):
    def test_flat_series_does_not_crash(self):
        # min == max would divide by zero in a naive implementation.
        self.assertEqual(len(sparkline([3, 3, 3])), 3)

    def test_missing_values_render_as_question_mark(self):
        self.assertIn("?", sparkline([1, None, 3]))


class TestDivergenceIsDataDriven(unittest.TestCase):
    """The core claim of the tool: it reacts to whatever data it's given,
    not to this week's specific dates. These build synthetic series so a
    change to the real CSV, or removing the incident from it, can't make
    this test pass by accident."""

    def _row(self, date, conf, rating, sessions, flagged):
        return {"date": date, "team": "T", "workflow": "W", "source": "S",
                "median_confidence": conf, "user_rating": rating,
                "sessions": sessions, "flagged_for_review": flagged,
                "notes": "synthetic"}

    def test_fires_on_a_synthetic_incident(self):
        rows = [
            self._row("2026-01-01", 0.80, 4.0, 100, 10),
            self._row("2026-01-02", 0.90, 2.0, 100, 40),  # confidence up, rating down, flags up
        ]
        series = build_series(rows)
        findings = detect_divergences(series)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["cur"]["date"], "2026-01-02")

    def test_silent_on_data_with_no_incident(self):
        rows = [
            self._row("2026-01-01", 0.80, 4.0, 100, 10),
            self._row("2026-01-02", 0.81, 4.1, 100, 11),  # nothing wrong
        ]
        series = build_series(rows)
        self.assertEqual(detect_divergences(series), [])

    def test_small_rating_wobble_alone_does_not_fire(self):
        # Guards the noise threshold: a 0.1-point rating dip with no flag
        # spike should not read as an incident.
        rows = [
            self._row("2026-01-01", 0.80, 4.0, 100, 10),
            self._row("2026-01-02", 0.81, 3.9, 100, 11),
        ]
        series = build_series(rows)
        self.assertEqual(detect_divergences(series), [])


class TestChangeEventsAreFreeText(unittest.TestCase):
    def test_finds_prompt_and_policy_notes_by_keyword(self):
        rows = [
            {"date": "2026-01-04", "notes": "new prompt version started"},
            {"date": "2026-01-07", "notes": "review policy changed mid-day"},
            {"date": "2026-01-01", "notes": "normal day"},
        ]
        events = find_change_events(rows)
        self.assertIn("new prompt version started", events)
        self.assertEqual(events["new prompt version started"], "2026-01-04")


class TestEndToEnd(unittest.TestCase):
    def test_full_dataset_quarantine_counts(self):
        rows, _ = load_rows(DEFAULT_PATH)
        self.assertEqual(len(rows), 41)
        clean, flagged, ledger, incomplete_days = quarantine(rows)
        # 1 exact duplicate dropped, leaving 40; of those, 1 demo-spike row +
        # 4 rows from the partial 08-07 day are excluded from totals = 5 flagged.
        self.assertEqual(len(clean) + len(flagged), 40)
        self.assertEqual(len(flagged), 5)
        self.assertEqual(len(ledger), 6)
        self.assertEqual(incomplete_days, {"2026-08-07"})

    def test_real_dataset_still_trips_the_known_incident(self):
        # Confirms the general rule actually catches the specific incident
        # this submission leads with, using the real CSV end to end.
        rows, _ = load_rows(DEFAULT_PATH)
        clean, flagged, _, _ = quarantine(rows)
        series = build_series(clean + flagged)
        findings = detect_divergences(series)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["key"], ("Support", "Reply draft", "queue"))
        self.assertEqual(findings[0]["cur"]["date"], "2026-08-07")


if __name__ == "__main__":
    unittest.main()
