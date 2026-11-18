# SPDX-License-Identifier: Apache-2.0
"""Weekly loop: the round-2 review fixes (review/WEEKLY-CODE-REVIEW.md R2-2 and R2-3; the v2 core digest).

R2-3. N7 is the lag from the end data day to the UTC date its SitRep was first posted, read from the
promotion's own published field. It never depends on when the promotion reached main. When main holds no
promotion for the end day at settle, the cell is censored with an anomaly flag, outside the primary
endpoint, and reported; it is never scored as a lag of 14.

Core digest. The v2 pre-registration pins a digest of the wk1 core only. Adding a candidate method changes
the full spec digest and leaves the core digest alone; a change to the core changes it.

R2-2. A receipt names the commit that was reviewed and the commit that was pushed.
"""
from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from lovs.forecast.weekly import privacy, promotion_rows, resolve, score, spec, targets, truth, usefulness

REPO = Path(__file__).resolve().parents[1]
PROMOTIONS = REPO / "data" / "sitrep_promotions"
UTC = dt.timezone.utc
D = dt.timedelta

# The reviewer's June example: the SitRep for Wednesday 2026-06-10 was posted the next day, but its
# promotion reached main more than 14 days after the week ended.
R = dt.date(2026, 6, 3)
END = dt.date(2026, 6, 10)
SETTLE = truth.settle_time(END)
NOW = dt.datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _june_docs() -> dict[str, dict]:
    out = {}
    for path in sorted(PROMOTIONS.glob("sitrep-*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        day = dt.date.fromisoformat(str(doc["data_as_of"])[:10])
        if R - D(days=1) <= day <= END + D(days=1):
            out[f"data/sitrep_promotions/{path.name}"] = doc
    return out


def _settled_n7(end_arrival: dt.datetime, published_at: str | None = None):
    """Resolve an N7 pin of R at 7 days from the real June promotions, the end day arriving at end_arrival."""
    versions, blobs = {}, {}
    for i, (key, doc) in enumerate(sorted(_june_docs().items())):
        day = dt.date.fromisoformat(str(doc["data_as_of"])[:10])
        if day == END and published_at is not None:
            doc = dict(doc, published_at=published_at)
        blobs[key] = json.dumps(doc)
        arrived = end_arrival if day == END else dt.datetime.combine(day + D(days=2), dt.time(12), tzinfo=UTC)
        versions[key] = [truth.Version(key, f"{i + 1:040x}", key, arrived, i)]
    view = truth.TruthView(versions, lambda oid: blobs[oid])
    days = {R + D(days=i) for i in range(-1, 9)}
    views, deadline_rows = resolve.views_for(view, R, NOW, days)
    start, end = targets.week_bounds(R, 1)
    pin = {"pin_id": spec.pin_id(R, 1), "registration_day": R.isoformat(), "series": "N7", "horizon_days": 7,
           "target_start": start.isoformat(), "target_end": end.isoformat(), "threshold": 3}
    for m in spec.methods_for(spec.SERIES_BY_ID["N7"]):
        pin[spec.price_column(m)] = 0.6
    rows, statuses = resolve.resolve_week([pin], R, views, NOW, deadline_rows)
    return rows, [s for s in statuses if s.series == "N7" and s.k == 1]


class N7LagTest(unittest.TestCase):
    """R2-3."""

    def test_june_promotion_late_to_main_is_censored_not_14(self) -> None:
        june = _june_docs()["data/sitrep_promotions/sitrep-027-2026-06-10.json"]
        self.assertEqual((june["data_as_of"], june["published_at"]), ("2026-06-10", "2026-06-11T15:40:17Z"))
        rows, statuses = _settled_n7(SETTLE + D(days=6))
        self.assertEqual([(r["value_kind"], r["status"], r["realised"], r["outcome"], r["anomaly"]) for r in rows],
                         [("settled", "censored", None, None, 1)])
        self.assertEqual([(s.status, s.value, s.flags) for s in statuses], [("censored", None, ("lag_censored",))])
        self.assertTrue(all(r[spec.score_column("brier", m)] is None for r in rows for m in spec.METHOD_IDS))

    def test_the_lag_never_depends_on_when_the_promotion_reached_main(self) -> None:
        for arrival in (dt.datetime(2026, 6, 12, 9, tzinfo=UTC), SETTLE - D(hours=1)):
            rows, statuses = _settled_n7(arrival)
            self.assertEqual([(r["status"], r["realised"], r["anomaly"]) for r in rows],
                             [("resolved_yes", 1.0, 0)], arrival)
            self.assertEqual([(s.status, s.value) for s in statuses], [("observed", 1.0)])

    def test_the_lag_is_to_the_utc_date_first_posted(self) -> None:
        # Posted 23:30 at UTC-3 on 06-11 is 02:30Z on 06-12: a lag of 2, not 1.
        rows, _ = _settled_n7(dt.datetime(2026, 6, 13, 9, tzinfo=UTC), published_at="2026-06-11T23:30:00-03:00")
        self.assertEqual([(r["status"], r["realised"]) for r in rows], [("resolved_yes", 2.0)])
        self.assertEqual(promotion_rows.publication_day("2026-06-11T23:30:00-03:00"), "2026-06-12")
        self.assertEqual(promotion_rows.publication_day("2026-06-11T15:40:17Z"), "2026-06-11")
        self.assertEqual(promotion_rows.publication_day("2026-06-04T18:30:21"), "2026-06-04")

    def test_censored_is_a_status_and_lag_censored_an_anomaly_code(self) -> None:
        for statuses in (spec.PIN_STATUSES, spec.TARGET_STATUSES):
            self.assertIn("censored", statuses)
        self.assertIn("lag_censored", spec.ANOMALY_CODES)
        rows, _ = _settled_n7(SETTLE + D(days=6))
        ctx = privacy.PrivacyContext(registration_days={R.isoformat()} | {d.isoformat() for d in
                                                                          targets.calendar_days(R)})
        privacy.gate(privacy.csv_rows(resolve.results_csv_bytes(rows).decode("ascii")), ctx)

    def test_the_data_usefulness_report_counts_censored_cells(self) -> None:
        rows, _ = _settled_n7(SETTLE + D(days=6))
        build = SimpleNamespace(R=dt.date(2026, 7, 1), rows=[], pins=[], floor={"floored": 0, "steps": 0},
                                forecasts={"active_series": [], "forecasts": [], "series": []})
        dur = usefulness.report(build=build, origins=[], results=rows, previous_flags=None, quantile_scores=[],
                                first_scored={}, statuses_ok=True)
        self.assertEqual(dur["measures"]["unscoreable"]["censored"], 1)


class CoreDigestTest(unittest.TestCase):
    """The v2 core digest: candidates are outside it, the core is inside it."""

    def test_core_digest_is_pinned_and_recorded_in_the_spec(self) -> None:
        self.assertEqual(spec.core_sha256(), PINNED_CORE_SHA256)
        self.assertEqual(spec.spec_document()["core"], spec.core_document())
        self.assertEqual(spec.spec_document()["candidates"], [])

    def test_adding_a_candidate_changes_the_spec_digest_and_not_the_core(self) -> None:
        core, full = spec.core_sha256(), spec.spec_sha256()
        candidate = {"id": "cand.example_method", "classes": ["flow"], "priced_beside": "the frozen set"}
        with mock.patch.object(spec, "CANDIDATES", (candidate,)):
            self.assertEqual(spec.core_sha256(), core)
            self.assertNotEqual(spec.spec_sha256(), full)

    def test_a_change_to_the_core_changes_the_core_digest(self) -> None:
        core = spec.core_sha256()
        for target, name, value in ((spec, "LAG_CAP", 15), (spec, "HORIZONS", (7, 14, 21)),
                                    (spec, "SETTLE_DAYS", 21), (spec, "ANOMALY_REL", 0.1),
                                    (score, "SCORER_VERSION", "wk1.score.2"),
                                    (privacy, "KEYS", privacy.KEYS | {"note"}),
                                    (spec, "CLASS_PAIRS", {"flow": (spec.M_LEVEL, spec.M_DRIFT),
                                                           "level": spec.CLASS_PAIRS["level"]})):
            with mock.patch.object(target, name, value):
                self.assertNotEqual(spec.core_sha256(), core, name)
        self.assertEqual(spec.core_sha256(), core)

    def test_the_core_names_the_scorer_truth_reader_and_privacy_allowlist(self) -> None:
        core = spec.core_document()
        self.assertEqual(core["scorer"]["version"], "wk1.score.1")
        self.assertIn("settled", core["truth_reader"])
        self.assertIn("combiner", core["pool"])
        self.assertEqual(core["privacy_allowlist"]["keys"], sorted(privacy.KEYS))
        self.assertEqual(core["lag_scoring"]["end_day_absent_at_settle"], "censored")


class ReceiptKeysTest(unittest.TestCase):
    """R2-2: the receipt names the reviewed and the pushed commit, and the gate admits them as commits."""

    def test_receipt_with_reviewed_and_pushed_commits_passes_the_gate(self) -> None:
        day = "2026-11-25"
        receipt = {"registration_day": day, "branch": f"bdbv-weekly-{day}", "pinning_commit": "a" * 40,
                   "reviewed_commit": "a" * 40, "pushed_commit": "a" * 40,
                   "branch_created_at": f"{day}T21:00:00Z", "deadline": f"{day}T23:59:59Z", "on_time": True,
                   "receipt_recorded_at": f"{day}T21:05:00Z"}
        ctx = privacy.PrivacyContext(registration_days={day}, build_days={day})
        privacy.gate(receipt, ctx)
        with self.assertRaises(privacy.PrivacyError):
            privacy.gate(dict(receipt, pushed_commit="not a commit"), ctx)


# The v2 pre-registration pins this value (gt/confirm_v2.py CORE_SHA256). A change ends v2. It covers the
# source of spec.CODE_MODULES too (test_weekly_round3.py), so any edit to one of those files moves it.
PINNED_CORE_SHA256 = "396de28ef2494af25e7f60b9c9fbddd7045d2464de3c48865adc7e5b6609c7c4"

if __name__ == "__main__":
    unittest.main()
