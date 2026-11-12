# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import datetime as dt
import json
import pathlib
import tempfile
import unittest

from lovs import health_zone_centroids as hzc
from lovs import model_tournament as T
from lovs import tournament_geography as G


class ZoneAttributionTests(unittest.TestCase):
    def setUp(self):
        self.index = hzc.CentroidIndex.load_default()
        self.zone_id = self.index.zone_ids()[0]
        self.zone = self.index.zone(self.zone_id)
        self.other = next(z for z in self.index.zone_ids()
                          if hzc.province_key(self.index.zone(z)["province"]) != hzc.province_key(self.zone["province"]))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = pathlib.Path(temp.name) / "zone-attributions.json"

    def row(self, **changes):
        row = {"province": self.zone["province"], "sitrep_zone": "Nouvelle Zone Test", "zone_id": self.zone_id,
               "first_sitrep": 147, "basis": "Synthetic location inside the frozen polygon.",
               "limitation": "Synthetic boundary.", "sources": [
                   {"url": "https://example.org/layer", "finding": "Synthetic health area placement"}],
               "reviewed_by": "synthetic-geography-reviewer", "reviewed_at": "2027-02-01T12:00:00.250Z"}
        row.update(changes)
        return row

    def doc(self, *rows):
        return {"schema_version": G.SCHEMA_VERSION, "attributions": list(rows) or [self.row()]}

    def test_valid_attribution_resolves_only_the_unknown_name(self):
        attributed = G.AttributedIndex(self.index, G.validate(self.doc(), self.index))
        self.assertEqual(attributed.resolve_sitrep_row(self.zone["province"], "Nouvelle Zone Test"), self.zone_id)
        self.assertEqual(list(attributed.used), [f"{self.zone['province']}/Nouvelle Zone Test"])
        self.assertEqual(attributed.resolve_sitrep_row(self.zone["province"], self.zone["name"]), self.zone_id)
        with self.assertRaises(hzc.CentroidError):
            attributed.resolve_sitrep_row(self.zone["province"], "Another Unknown Zone")
        self.assertEqual(attributed.zone_ids(), self.index.zone_ids())

    def test_attribution_cannot_replace_cross_province_or_omit_review(self):
        cases = [
            (self.row(sitrep_zone=self.zone["name"]), "already a frozen zone"),
            (self.row(zone_id=self.other), "another province"),
            (self.row(first_sitrep="147"), "first_sitrep"),
            (self.row(basis=" "), "basis"),
            (self.row(sources=[{"url": "http://example.org", "finding": "x"}]), "https"),
            (self.row(sources=[]), "sources"),
            (self.row(reviewed_at="2027-02-01"), "UTC"),
            ({k: v for k, v in self.row().items() if k != "limitation"}, "exactly the fields"),
        ]
        for row, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(G.AttributionError, message):
                G.validate(self.doc(row), self.index)
        with self.assertRaisesRegex(G.AttributionError, "twice"):
            G.validate(self.doc(self.row(), self.row(sitrep_zone="nouvelle zone test")), self.index)
        with self.assertRaisesRegex(G.AttributionError, "must use"):
            G.validate({"schema_version": "other", "attributions": []}, self.index)

    def test_load_is_empty_without_file_and_refuses_review_after_cutoff(self):
        self.assertEqual(G.load(self.index, self.path), [])
        self.path.write_text(json.dumps(self.doc()))
        cutoff = dt.datetime(2027, 2, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
        with self.assertRaisesRegex(G.AttributionError, "after the evidence cutoff"):
            G.load(self.index, self.path, cutoff)
        self.assertEqual(len(G.load(self.index, self.path, cutoff + dt.timedelta(seconds=1))), 1)

    def test_committed_attributions_validate_against_frozen_index(self):
        rows = G.load(self.index)
        for row in rows:
            with self.assertRaises(hzc.CentroidError):
                self.index.resolve_sitrep_row(row["province"], row["sitrep_zone"])

    def test_utc_timestamps_accept_fractional_seconds_only_to_microseconds(self):
        self.assertEqual(T._utc_datetime("2027-02-01T12:00:00.123456Z", "t").microsecond, 123456)
        for bad in ("2027-02-01T12:00:00.1234567Z", "2027-02-01T12:00:00.Z", "2027-02-01T12:00:00+00:00"):
            with self.subTest(bad=bad), self.assertRaises(T.TournamentConfigError):
                T._utc_datetime(bad, "t")


if __name__ == "__main__":
    unittest.main()
