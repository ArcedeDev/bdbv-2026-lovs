"""Count-reconciliation gate: does the cumulative move by what was notified?

Regression cover for the two real defects this gate was built to catch:
  * SitRep 69 restated its cumulative (+369 confirmed against 97 notified) after INSP
    integrated harmonized provincial databases. Differencing across that boundary inflated
    the floated doubling time from ~23d to 11.5d and published the regime as "growing".
  * SitReps 51-53 carried stale day-columns copied from SitRep 51 (33/354 where the sources
    printed 63/84 and 135/237). Corrected 2026-07-25; they must now reconcile exactly.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lovs import lovs_count_reconciliation as R  # noqa: E402
from lovs.sitrep_promotions import load_reviewed_promotions  # noqa: E402


def _prom(sr, date, confirmed, deaths, notified_c=None, notified_d=None, *, top=False,
          fig_notified_c=None, harmonized=False):
    """Build a minimal promotion payload."""
    figures = {"cumul_cas_confirmes_drc": confirmed, "cumul_deces_parmi_confirmes_drc": deaths}
    payload = {"sitrep_number": sr, "data_as_of": date, "figures": figures}
    if top:
        if notified_c is not None:
            payload["new_confirmed_24h"] = notified_c
        if notified_d is not None:
            payload["new_confirmed_deaths_24h"] = notified_d
        if fig_notified_c is not None:
            figures["new_confirmed_24h"] = fig_notified_c
    else:
        if notified_c is not None:
            figures["new_confirmed_24h"] = notified_c
        if notified_d is not None:
            figures["new_confirmed_deaths_24h"] = notified_d
    if harmonized:
        figures["health_zone_table"] = {"reconciliation": {"harmonization_declared": True}}
    return payload


class TestStatusTaxonomy(unittest.TestCase):
    def test_reconciled_when_delta_equals_notified(self):
        a = _prom(1, "2026-07-01", 100, 10)
        b = _prom(2, "2026-07-02", 110, 12, notified_c=10, notified_d=2)
        rec = R.reconcile_pair(a, b)
        self.assertEqual("reconciled", rec["confirmed"]["status"])
        self.assertEqual(0, rec["confirmed"]["gap"])
        self.assertTrue(rec["confirmed"]["differenceable"])
        self.assertEqual("reconciled", rec["deaths"]["status"])

    def test_restated_when_delta_exceeds_notified(self):
        a = _prom(1, "2026-07-01", 100, 10)
        b = _prom(2, "2026-07-02", 200, 40, notified_c=10, notified_d=2, harmonized=True)
        rec = R.reconcile_pair(a, b)
        self.assertEqual("restated", rec["confirmed"]["status"])
        self.assertEqual(90, rec["confirmed"]["gap"])
        self.assertFalse(rec["confirmed"]["differenceable"])
        self.assertTrue(rec["source_declared_harmonization"])

    def test_negative_gap_is_still_restated(self):
        """A downward move is a restatement too; a pure backfill cannot produce one."""
        a = _prom(1, "2026-07-01", 100, 10)
        b = _prom(2, "2026-07-02", 105, 11, notified_c=10, notified_d=1)
        rec = R.reconcile_pair(a, b)
        self.assertEqual("restated", rec["confirmed"]["status"])
        self.assertEqual(-5, rec["confirmed"]["gap"])

    def test_missing_notified_is_unknown_never_zero(self):
        """The 12 early promotions carry no notified value. Coercing to 0 would report the
        entire cumulative delta as a restatement."""
        a = _prom(1, "2026-07-01", 100, 10)
        b = _prom(2, "2026-07-02", 150, 20)  # no notified at all
        rec = R.reconcile_pair(a, b)
        self.assertEqual("unknown_notified", rec["confirmed"]["status"])
        self.assertIsNone(rec["confirmed"]["gap"])
        self.assertFalse(rec["confirmed"]["differenceable"])

    def test_multi_day_span_is_undetermined(self):
        """SR28->30, SR42->44, SR44->46 span two days because those SitReps do not exist."""
        a = _prom(1, "2026-07-01", 100, 10)
        b = _prom(2, "2026-07-03", 150, 20, notified_c=25, notified_d=5)
        rec = R.reconcile_pair(a, b)
        self.assertEqual(2, rec["day_span"])
        self.assertEqual("undetermined_multi_day", rec["confirmed"]["status"])
        self.assertFalse(rec["confirmed"]["differenceable"])

    def test_series_start_is_emitted_not_omitted(self):
        recs = R.reconcile_series([_prom(1, "2026-07-01", 100, 10)])
        self.assertEqual(1, len(recs))
        self.assertEqual("series_start", recs[0]["confirmed"]["status"])
        self.assertFalse(recs[0]["confirmed"]["differenceable"])


class TestNotifiedFieldResolution(unittest.TestCase):
    def test_top_level_wins_and_conflict_is_surfaced(self):
        """The SitRep 51-53 signature: top level correct, figures stale."""
        a = _prom(1, "2026-07-01", 100, 10)
        b = _prom(2, "2026-07-02", 163, 25, notified_c=63, notified_d=15,
                  top=True, fig_notified_c=33)
        rec = R.reconcile_pair(a, b)
        self.assertEqual(63, rec["confirmed"]["notified"])
        self.assertTrue(rec["confirmed"]["notified_field_conflict"])
        self.assertEqual("reconciled", rec["confirmed"]["status"])

    def test_no_conflict_when_both_agree(self):
        a = _prom(1, "2026-07-01", 100, 10)
        b = _prom(2, "2026-07-02", 110, 12, notified_c=10, notified_d=2,
                  top=True, fig_notified_c=10)
        rec = R.reconcile_pair(a, b)
        self.assertFalse(rec["confirmed"]["notified_field_conflict"])


class TestIncidenceCorrection(unittest.TestCase):
    def test_raw_when_no_reconciliation_supplied(self):
        val, basis = R.correct_incidence_increment(
            end_date="2026-07-02", raw_increment=10.0, day_span=1, reconciliation=None
        )
        self.assertEqual(10.0, val)
        self.assertTrue(basis.startswith("raw"))

    def test_notified_substituted_on_restated_boundary(self):
        recs = R.reconcile_series([
            _prom(1, "2026-07-01", 100, 10),
            _prom(2, "2026-07-02", 200, 40, notified_c=10, notified_d=2),
        ])
        idx = R.index_by_date(recs)
        val, basis = R.correct_incidence_increment(
            end_date="2026-07-02", raw_increment=100.0, day_span=1, reconciliation=idx
        )
        self.assertEqual(10.0, val, "must use the notified count, not the 100-case jump")
        self.assertIn("notified-substituted", basis)

    def test_dropped_when_restated_and_notified_unknown(self):
        recs = R.reconcile_series([
            _prom(1, "2026-07-01", 100, 10),
            _prom(2, "2026-07-02", 200, 40),  # no notified -> unknown
        ])
        idx = R.index_by_date(recs)
        val, basis = R.correct_incidence_increment(
            end_date="2026-07-02", raw_increment=100.0, day_span=1, reconciliation=idx
        )
        self.assertIsNone(val, "an unusable increment must be dropped, never fed through")
        self.assertIn("dropped", basis)

    def test_multi_day_increment_is_dropped(self):
        recs = R.reconcile_series([
            _prom(1, "2026-07-01", 100, 10),
            _prom(2, "2026-07-03", 150, 20, notified_c=25, notified_d=5),
        ])
        val, _ = R.correct_incidence_increment(
            end_date="2026-07-03", raw_increment=50.0, day_span=2,
            reconciliation=R.index_by_date(recs),
        )
        self.assertIsNone(val)


class TestAssertDifferenceable(unittest.TestCase):
    def test_raises_on_restated_boundary(self):
        recs = R.reconcile_series([
            _prom(1, "2026-07-01", 100, 10),
            _prom(2, "2026-07-02", 200, 40, notified_c=10, notified_d=2),
        ])
        with self.assertRaises(R.CountReconciliationError) as ctx:
            R.assert_differenceable(recs, since="2026-07-02")
        self.assertIn("2026-07-02", str(ctx.exception))

    def test_passes_on_clean_window(self):
        recs = R.reconcile_series([
            _prom(1, "2026-07-01", 100, 10),
            _prom(2, "2026-07-02", 110, 12, notified_c=10, notified_d=2),
        ])
        R.assert_differenceable(recs, since="2026-07-02")  # must not raise


class TestLiveSeriesRegression(unittest.TestCase):
    """Against the real corpus, so a future promotion defect trips these."""

    @classmethod
    def setUpClass(cls):
        cls.recs = R.reconcile_series(load_reviewed_promotions())
        cls.by_date = R.index_by_date(cls.recs)

    def test_sitrep69_is_flagged_restated_and_source_declared(self):
        rec = self.by_date["2026-07-22"]
        self.assertEqual("restated", rec["confirmed"]["status"])
        self.assertEqual(369, rec["confirmed"]["cumulative_delta"])
        self.assertEqual(97, rec["confirmed"]["notified"])
        self.assertEqual(272, rec["confirmed"]["gap"])
        self.assertEqual(236, rec["deaths"]["cumulative_delta"])
        self.assertEqual(62, rec["deaths"]["notified"])
        self.assertEqual(174, rec["deaths"]["gap"])
        self.assertTrue(rec["source_declared_harmonization"])

    def test_sitreps_51_to_53_reconcile_after_the_2026_07_25_correction(self):
        for d in ("2026-07-04", "2026-07-05", "2026-07-06"):
            with self.subTest(date=d):
                rec = self.by_date[d]
                self.assertEqual("reconciled", rec["confirmed"]["status"])
                self.assertEqual(0, rec["confirmed"]["gap"])
                self.assertEqual("reconciled", rec["deaths"]["status"])
                self.assertEqual(0, rec["deaths"]["gap"])

    def test_early_window_notified_is_unknown_not_zero(self):
        """SitRep 15-26 publish no notified count; they must never read as restated."""
        for d in ("2026-05-30", "2026-06-05", "2026-06-09"):
            with self.subTest(date=d):
                self.assertEqual("unknown_notified", self.by_date[d]["confirmed"]["status"])
                self.assertIsNone(self.by_date[d]["confirmed"]["gap"])

    def test_known_multi_day_transitions_are_undetermined(self):
        for d in ("2026-06-13", "2026-06-27", "2026-06-29"):
            with self.subTest(date=d):
                self.assertEqual(
                    "undetermined_multi_day", self.by_date[d]["confirmed"]["status"]
                )

    def test_deaths_restate_more_often_than_cases(self):
        """The substantive finding: death records arrive late far more often than cases,
        consistent with community deaths confirmed at post-mortem reaching the national
        database after the fact. Guards against a regression that silently equalizes them."""
        conf = R.summarize(self.recs, "confirmed")["by_status"]
        deaths = R.summarize(self.recs, "deaths")["by_status"]
        self.assertGreater(deaths.get("restated", 0), conf.get("restated", 0))

    def test_no_promotion_has_a_notified_field_conflict(self):
        """Top-level and figures notified values must agree. A conflict is the stale
        day-column signature that produced the SitRep 51-53 defect."""
        conflicts = R.summarize(self.recs, "confirmed")["field_conflict_cycles"]
        self.assertEqual([], conflicts, f"stale day-column suspected at {conflicts}")


if __name__ == "__main__":
    unittest.main()
