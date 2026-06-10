# SPDX-License-Identifier: Apache-2.0
"""Tests for calibration_resolver.py.

These lock the resolution + scoring contract: the resolver must (a) early-lock a
YES when a new in-window confirmation appears in a target zone, (b) score it with
the Brier midpoint convention, (c) never resolve NO before resolves_at, (d) treat
a missing feed entry as unscoreable (not NO), (e) reject out-of-window
confirmations, and (f) never mutate the immutable ledger.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import tempfile
import unittest

import calibration_resolver as cr

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _point(corridor, source, target, lo, hi, pinned, resolves, hid):
    return {
        "hypothesis_id": hid,
        "corridor": corridor,
        "source": source,
        "target": target,
        "horizon_days": 30,
        "risk_adj_50": [lo, hi],
        "pinned_at": pinned,
        "resolves_at": resolves,
    }


def _ledger(points):
    return {
        "_meta": {"outbreak_id": "bdbv-uga-cod-2026", "scope_id": "epi:bdbv-uga-cod-2026"},
        "blocks": [
            {
                "block_id": "calibration-block:test:2026-05-20",
                "pinned_at": "2026-05-20",
                "resolves_at": "2026-06-19T23:59:59Z",
                "horizon_days": 30,
                "status": "active",
                "points": points,
            }
        ],
    }


def _evidence(entries):
    return {"_meta": {"as_of": "2026-05-24"}, "evidence": entries}


def _index(entries):
    return {e["target_zone"]: e for e in entries}


KAMPALA_YES = {
    "target_zone": "kampala-uga",
    "confirmed_in_window": True,
    "first_in_window_confirmation_date": "2026-05-23",
    "source_id": "uga-moh-2026-05-23-kampala-three-new",
    "source_url": "https://example.org/uga-moh-23may",
    "classification": "resolution_eligible",
}


class ResolvePointTests(unittest.TestCase):
    def setUp(self):
        self.as_of = dt.date(2026, 5, 24)

    def test_kampala_resolves_yes_with_midpoint_brier(self):
        point = _point(
            "bunia -> kampala-uga", "bunia", "kampala-uga",
            0.229, 0.523, "2026-05-20", "2026-06-19T23:59:59Z", "h-kampala",
        )
        res = cr.resolve_point(point, _index([KAMPALA_YES]), self.as_of)
        self.assertEqual(res["status"], cr.STATUS_RESOLVED_YES)
        self.assertEqual(res["outcome"], 1)
        self.assertEqual(res["p_point"], 0.376)
        self.assertEqual(res["brier"], 0.389376)        # (0.376 - 1)**2
        self.assertEqual(res["brier_lo"], 0.594441)     # (0.229 - 1)**2
        self.assertEqual(res["brier_hi"], 0.227529)     # (0.523 - 1)**2
        self.assertEqual(res["evidence"]["source_id"], KAMPALA_YES["source_id"])

    def test_pending_when_confirmed_false_and_window_open(self):
        point = _point(
            "rwampara -> kasese-uga", "rwampara", "kasese-uga",
            0.209, 0.515, "2026-05-20", "2026-06-19T23:59:59Z", "h-kasese",
        )
        entry = {
            "target_zone": "kasese-uga", "confirmed_in_window": False,
            "first_in_window_confirmation_date": None, "source_id": "uga-moh",
            "source_url": "https://example.org", "classification": "resolution_eligible",
        }
        res = cr.resolve_point(point, _index([entry]), self.as_of)
        self.assertEqual(res["status"], cr.STATUS_PENDING)
        self.assertNotIn("brier", res)

    def test_unscoreable_when_no_feed_entry(self):
        point = _point(
            "mongbwalu -> nebbi-uga", "mongbwalu", "nebbi-uga",
            0.222, 0.51, "2026-05-21", "2026-06-20T23:59:59Z", "h-nebbi",
        )
        res = cr.resolve_point(point, _index([]), self.as_of)
        self.assertEqual(res["status"], cr.STATUS_UNSCOREABLE)
        self.assertNotIn("brier", res)

    def test_pre_window_confirmation_is_excluded(self):
        # Confirmation dated 2026-05-15, before the 2026-05-20 pin -> must NOT resolve YES.
        point = _point(
            "bunia -> kampala-uga", "bunia", "kampala-uga",
            0.229, 0.523, "2026-05-20", "2026-06-19T23:59:59Z", "h-kampala",
        )
        entry = dict(KAMPALA_YES, first_in_window_confirmation_date="2026-05-15")
        res = cr.resolve_point(point, _index([entry]), self.as_of)
        self.assertEqual(res["status"], cr.STATUS_PENDING)
        self.assertIn("outside the point window", res["reason"])

    def test_confirmed_flag_without_date_is_unscoreable_not_no(self):
        # Malformed entry: YES flag, no date. Even after resolves_at it must NOT
        # score resolved_no (which would penalize the model for a data error).
        point = _point(
            "bunia -> kampala-uga", "bunia", "kampala-uga",
            0.229, 0.523, "2026-05-20", "2026-06-19T23:59:59Z", "h-kampala",
        )
        entry = dict(KAMPALA_YES, first_in_window_confirmation_date=None)
        res = cr.resolve_point(point, _index([entry]), dt.date(2026, 6, 20))
        self.assertEqual(res["status"], cr.STATUS_UNSCOREABLE)
        self.assertNotIn("brier", res)
        self.assertIn("malformed", res["reason"])

    def test_resolved_no_after_resolves_at(self):
        point = _point(
            "rwampara -> kasese-uga", "rwampara", "kasese-uga",
            0.4, 0.6, "2026-05-20", "2026-06-19T23:59:59Z", "h-kasese",
        )
        entry = {
            "target_zone": "kasese-uga", "confirmed_in_window": False,
            "first_in_window_confirmation_date": None, "source_id": "uga-moh",
            "source_url": "https://example.org", "classification": "resolution_eligible",
        }
        res = cr.resolve_point(point, _index([entry]), dt.date(2026, 6, 20))
        self.assertEqual(res["status"], cr.STATUS_RESOLVED_NO)
        self.assertEqual(res["outcome"], 0)
        self.assertEqual(res["brier"], 0.25)  # (0.5 - 0)**2


class BuildReportTests(unittest.TestCase):
    def test_report_summary_counts_and_mean_brier(self):
        points = [
            _point("bunia -> kampala-uga", "bunia", "kampala-uga", 0.229, 0.523,
                   "2026-05-20", "2026-06-19T23:59:59Z", "h1"),
            _point("rwampara -> kasese-uga", "rwampara", "kasese-uga", 0.209, 0.515,
                   "2026-05-20", "2026-06-19T23:59:59Z", "h2"),
            _point("mongbwalu -> nebbi-uga", "mongbwalu", "nebbi-uga", 0.222, 0.51,
                   "2026-05-20", "2026-06-19T23:59:59Z", "h3"),
        ]
        entries = [
            KAMPALA_YES,
            {"target_zone": "kasese-uga", "confirmed_in_window": False,
             "first_in_window_confirmation_date": None, "source_id": "x",
             "source_url": "y", "classification": "resolution_eligible"},
            # nebbi-uga intentionally omitted -> unscoreable
        ]
        report = cr.build_report(_ledger(points), _index(entries), dt.date(2026, 5, 24),
                                 _evidence(entries))
        self.assertEqual(report["summary"]["by_status"][cr.STATUS_RESOLVED_YES], 1)
        self.assertEqual(report["summary"]["by_status"][cr.STATUS_PENDING], 1)
        self.assertEqual(report["summary"]["by_status"][cr.STATUS_UNSCOREABLE], 1)
        self.assertEqual(report["summary"]["mean_brier_resolved"], 0.389376)
        self.assertFalse(report["ledger_mutated"])
        self.assertTrue(report["proposed_ledger_outcomes"]["advisory_not_written"])


class RealRepoTests(unittest.TestCase):
    """Run against the actual committed ledger + evidence feed."""

    def test_two_kampala_corridors_resolve_yes(self):
        ledger = cr.load_ledger()
        _, index = cr.load_evidence()
        report = cr.build_report(ledger, index, dt.date(2026, 5, 24))
        # 4 + 8 + 3 + 4 = 19 points across May-20, May-21, May-26 (Goma), and
        # 2026-06-04 (west/SSD) blocks. Blocks pinned after the 2026-05-24 cycle date
        # (May-26 Goma and June-04 yei-ssd/kisangani-cod) are counted but PENDING in
        # the report, consistent with the resolver counting all pinned points.
        # See data/calibration-ledger.json.
        self.assertEqual(report["summary"]["total_points"], 19)
        self.assertEqual(report["summary"]["by_status"][cr.STATUS_RESOLVED_YES], 2)

    def test_write_report_does_not_mutate_ledger(self):
        before = hashlib.sha256(cr.LEDGER_PATH.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "report.json"
            rc = cr.main(["--as-of", "2026-05-24", "--write-report", "--report-path", str(out)])
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())
            written = json.loads(out.read_text())
            self.assertEqual(written["schema_version"], cr.REPORT_SCHEMA_VERSION)
        after = hashlib.sha256(cr.LEDGER_PATH.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_atomic_write_refuses_ledger_path(self):
        with self.assertRaises(RuntimeError):
            cr._atomic_write_text(cr.LEDGER_PATH, "{}")

    def test_default_run_writes_no_file(self):
        # Without --write-report the resolver prints only; no report file appears.
        if cr.REPORT_PATH.exists():
            self.skipTest("report artifact already present; skip default-no-write check")
        rc = cr.main(["--as-of", "2026-05-24"])
        self.assertEqual(rc, 0)
        self.assertFalse(cr.REPORT_PATH.exists())


if __name__ == "__main__":
    unittest.main()
