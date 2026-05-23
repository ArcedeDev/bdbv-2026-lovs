# SPDX-License-Identifier: Apache-2.0
"""Tests for source_ingest.py live source freshness checks."""
from __future__ import annotations

import unittest

import source_ingest


_CDC_MAY_21 = b"""
<html><body>
<h1>Ebola Disease: Current Situation</h1>
<p>Bundibugyo virus outbreak.</p>
<p>May 21, 2026</p>
<ul>
<li>As of May 21, the DRC and Uganda Ministries of Health report the following:</li>
<li>A total of 575 suspected cases, 51 confirmed cases, and 148 suspected deaths.</li>
</ul>
</body></html>
"""

_HDX_PACKAGE = b"""
{
  "success": true,
  "result": {
    "title": "Democratic Republic of Congo: Population and Mobility Estimates",
    "license_id": "cc-by",
    "license_title": "Creative Commons Attribution International (CC BY)",
    "metadata_modified": "2026-05-22T12:06:10.971226",
    "dataset_date": "[2020-03-01T00:00:00 TO 2026-03-31T23:59:59]",
    "resources": [
      {
        "id": "a29c006b-e958-4f2f-be13-bd4f12ef9318",
        "name": "DRC estimated residents",
        "format": "CSV",
        "last_modified": "2026-05-21T18:17:00.614392",
        "url": "https://data.humdata.org/example.csv"
      }
    ]
  }
}
"""


class TestFreshnessExtraction(unittest.TestCase):

    def test_extract_dates_handles_publisher_formats(self):
        text = "Updated 2026-05-21. Data as of May 20, 2026 and 18 May 2026."
        self.assertEqual(
            source_ingest.extract_dates(text),
            ["2026-05-18", "2026-05-20", "2026-05-21"],
        )

    def test_extract_count_tuple_handles_cdc_shape(self):
        text = "A total of 575 suspected cases, 51 confirmed cases, and 148 suspected deaths."
        self.assertEqual(
            source_ingest.extract_count_tuple(text),
            {
                "cases_suspected": 575,
                "cases_confirmed": 51,
                "deaths_suspected": 148,
            },
        )

    def test_extract_count_tuple_does_not_read_year_as_confirmed_count(self):
        text = "First reported 15 May 2026 Confirmed cases 51 Suspected cases 653 Deaths 144"
        self.assertEqual(
            source_ingest.extract_count_tuple(text),
            {
                "cases_confirmed": 51,
                "cases_suspected": 653,
                "deaths": 144,
            },
        )


class TestLiveSourceCheck(unittest.TestCase):

    def test_flags_newer_live_counts_against_archive(self):
        source = {
            "registry_id": "cdc-situation-summary",
            "title": "CDC",
            "publisher": "CDC",
            "source_tier": "official_cdc",
            "landing_url": "https://example.test/cdc",
            "archive_target": "outbreak_manifest",
            "manifest_source_prefix": "cdc-current-situation",
            "latest_known": {"data_as_of": "2026-05-20"},
        }
        manifest = {
            "entries": [
                {
                    "source_id": "cdc-current-situation-2026-05-20",
                    "published_at": "2026-05-20T00:00:00Z",
                    "content_hash": "0" * 64,
                    "normalized_content": {
                        "cases_suspected": 536,
                        "cases_confirmed": 34,
                        "deaths_suspected": 134,
                    },
                }
            ]
        }

        row = source_ingest.live_source_check(
            source,
            manifest,
            "2026-05-21",
            fetch_fn=lambda url: (_CDC_MAY_21, 200, "text/html"),
        )

        self.assertEqual(row["status"], "fetched")
        self.assertEqual(row["latest_detected_date"], "2026-05-21")
        self.assertEqual(row["extracted_counts"]["cases_confirmed"], 51)
        self.assertTrue(row["needs_review"])
        self.assertIn("detected_date_newer_than_archive", row["review_reasons"])
        self.assertIn("count_tuple_differs_from_latest_archive", row["review_reasons"])

    def test_hdx_package_check_records_resource_metadata(self):
        source = {
            "registry_id": "flowminder-drc-health-zone-popmob",
            "title": "Flowminder",
            "publisher": "Flowminder",
            "source_tier": "open_covariate",
            "landing_url": "https://data.humdata.org/dataset/example",
            "hdx_package_id": "bd5781f3-9c6a-427a-955b-ce2b59def8c3",
            "archive_target": "external_covariate_metadata",
            "manifest_source_prefix": None,
            "latest_known": {"data_as_of": "2026-03-31"},
        }

        row = source_ingest.live_source_check(
            source,
            {"entries": []},
            "2026-05-23",
            fetch_fn=lambda url: (_HDX_PACKAGE, 200, "application/json"),
        )

        self.assertEqual(row["status"], "fetched")
        self.assertEqual(row["latest_detected_date"], "2026-05-22")
        self.assertEqual(row["hdx_package"]["license_id"], "cc-by")
        self.assertEqual(
            row["hdx_package"]["resources"][0]["resource_id"],
            "a29c006b-e958-4f2f-be13-bd4f12ef9318",
        )
        self.assertFalse(row["extracted_counts"])


if __name__ == "__main__":
    unittest.main()
