# SPDX-License-Identifier: Apache-2.0
"""Tests for source_ingest.py live source freshness checks."""
from __future__ import annotations

import unittest
import pathlib
import tempfile

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

_DRC_MOH_GRAPHQL = b"""
{
  "data": {
    "epidemie": {
      "name": "Ebola bundibugyo 2026",
      "epidemiesFields": {
        "codeOms": "A98.4",
        "dateDebut": "2026-05-12T00:00:00+00:00",
        "statut": ["active"],
        "souche": "bundibugyo"
      },
      "rapportsHebdomandaires": {
        "edges": [
          {
            "node": {
              "slug": "sitrep-008",
              "title": "Sitrep/008",
              "reportsFields": {
                "dateRapportage": "2026-05-22T00:00:00+00:00",
                "datePublication": "2026-05-23T00:00:00+00:00",
                "pdfOfficiel": null,
                "situationProvince": [
                  {
                    "province": {
                      "nom": ["Sud-Kivu"],
                      "zoneSante": [
                        {"nom": "Miti Murhesa", "casConfirmes": 1, "casSuspects": 1, "deces": 1}
                      ]
                    }
                  }
                ]
              }
            }
          },
          {
            "node": {
              "slug": "sitrep-mve-n-007-mvb_17-2026",
              "title": "SitRep MVE N\\u00b0 007/MVB_17/2026",
              "reportsFields": {
                "dateRapportage": "2026-05-21T00:00:00+00:00",
                "datePublication": "2026-05-22T00:00:00+00:00",
                "pdfOfficiel": {
                  "node": {
                    "mediaItemUrl": "https://administration.sante.gouv.cd/wp-content/uploads/2026/05/SitRep_MVE_RDC_20260512-FDv2_IM.pdf"
                  }
                },
                "situationProvince": [
                  {
                    "province": {
                      "nom": ["Ituri"],
                      "zoneSante": [
                        {"nom": "Bunia", "casConfirmes": 15, "casSuspects": 166, "deces": 34}
                      ]
                    }
                  }
                ]
              }
            }
          }
        ]
      }
    }
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

    def test_drc_moh_dashboard_check_extracts_reports_and_pdf_assets(self):
        source = {
            "registry_id": "drc-moh-epidemie-dashboard",
            "title": "DRC MoH dashboard",
            "publisher": "DRC Ministry of Health",
            "source_tier": "national_moh",
            "landing_url": "https://sante.gouv.cd/epidemie/ebola-bundibugyo-2026",
            "api_request": {
                "type": "graphql",
                "response_kind": "drc_moh_epidemie_dashboard",
                "url": "https://administration.sante.gouv.cd/graphql",
                "query": "query { epidemie(id: \"ebola-bundibugyo-2026\", idType: SLUG) { name } }",
            },
            "archive_target": "outbreak_manifest",
            "manifest_source_prefix": "drc-moh-epidemie-dashboard",
            "latest_known": {"data_as_of": "2026-05-22"},
        }

        row = source_ingest.live_source_check(
            source,
            {"entries": []},
            "2026-05-23",
            fetch_fn=lambda url, **kwargs: (_DRC_MOH_GRAPHQL, 200, "application/json"),
        )

        self.assertEqual(row["status"], "fetched")
        self.assertEqual(row["api_url"], "https://administration.sante.gouv.cd/graphql")
        self.assertEqual(row["latest_detected_date"], "2026-05-23")
        self.assertEqual(row["drc_moh_dashboard"]["report_count"], 2)
        self.assertEqual(
            row["drc_moh_dashboard"]["latest_report"]["reported_rows"][0]["province"],
            "Sud-Kivu",
        )
        self.assertEqual(row["extracted_counts"]["dashboard_zone_rows_confirmed_total"], 1)
        self.assertTrue(row["needs_review"])
        self.assertIn("drc_moh_table_semantics_source_review", row["review_reasons"])
        self.assertIn("latest_report_pdf_missing", row["review_reasons"])
        self.assertIn("bytes_not_in_manifest", row["review_reasons"])
        self.assertEqual(
            row["drc_moh_dashboard"]["official_pdf_assets"][0]["url"],
            "https://administration.sante.gouv.cd/wp-content/uploads/2026/05/SitRep_MVE_RDC_20260512-FDv2_IM.pdf",
        )


class TestDropboxScan(unittest.TestCase):

    def test_scan_dropbox_uses_sidecar_registry_id_for_json_and_pdf_payloads(self):
        registry = {
            "sources": [
                {
                    "registry_id": "afro-weekly-sitrep",
                    "filename_hints": ["SitRep"],
                    "archive_target": "outbreak_manifest",
                },
                {
                    "registry_id": "drc-moh-epidemie-dashboard",
                    "filename_hints": ["drc-moh-epidemie-dashboard"],
                    "archive_target": "outbreak_manifest",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            original = source_ingest.DROPBOX
            try:
                source_ingest.DROPBOX = pathlib.Path(tmpdir)
                pdf = source_ingest.DROPBOX / "drc-moh-epidemie-dashboard-SitRep.pdf"
                payload = source_ingest.DROPBOX / "drc-moh-epidemie-dashboard-sitrep-008.json"
                for path in (pdf, payload):
                    path.write_bytes(b"payload")
                    path.with_name(path.name + ".meta.json").write_text(
                        '{"registry_id":"drc-moh-epidemie-dashboard"}',
                        encoding="utf-8",
                    )
                rows = source_ingest.scan_dropbox(registry, {"entries": []})
            finally:
                source_ingest.DROPBOX = original

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["registry_id"] for row in rows},
            {"drc-moh-epidemie-dashboard"},
        )


if __name__ == "__main__":
    unittest.main()
