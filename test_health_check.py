"""
Lightweight tests for health_check.py -- stdlib unittest, no dependencies.

Not meant to be exhaustive. These pin down the exact traps in the challenge
dataset that a naive analysis (pandas drop_duplicates(), astype(float), a
plain groupby) would get wrong, so a future edit can't silently reintroduce
one of them.
"""
import unittest

from health_check import (
    to_float, load_rows, dedupe_on_business_key, detect_incomplete_days,
    quarantine, spearman, DEFAULT_PATH,
)


class TestToFloat(unittest.TestCase):
    def test_normal_number(self):
        self.assertEqual(to_float("4.2"), 4.2)

    def test_na_text_is_missing_not_zero(self):
        # This is the trap: naive float("n/a") raises, and a bare except that
        # returns 0.0 would silently turn "unknown confidence" into "zero
        # confidence" -- a very different claim.
        self.assertIsNone(to_float("n/a"))

    def test_blank_is_missing(self):
        self.assertIsNone(to_float(""))
        self.assertIsNone(to_float("   "))


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
        # a third day with one series missing
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
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [1, 2, 3, 4]), 1.0)

    def test_perfect_disagreement(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)

    def test_ignores_none_pairs(self):
        # 4 clean pairs remain after dropping the two None-containing ones;
        # that's exactly the function's minimum, so this also pins the floor.
        rho = spearman([1, 2, None, 4, 5, 6], [1, 2, 3, None, 5, 6])
        self.assertIsNotNone(rho)


class TestEndToEnd(unittest.TestCase):
    def test_full_dataset_quarantine_counts(self):
        rows, _ = load_rows(DEFAULT_PATH)
        self.assertEqual(len(rows), 41)
        clean, flagged, ledger = quarantine(rows)
        # 1 exact duplicate dropped, leaving 40; of those, 1 demo-spike row +
        # 4 rows from the partial 08-07 day are excluded from totals = 5 flagged.
        self.assertEqual(len(clean) + len(flagged), 40)
        self.assertEqual(len(flagged), 5)
        self.assertEqual(len(ledger), 6)


if __name__ == "__main__":
    unittest.main()
