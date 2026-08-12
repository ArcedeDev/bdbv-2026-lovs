# SPDX-License-Identifier: Apache-2.0
"""Guards for the publisher-availability record behind every cited SitRep."""
from __future__ import annotations

import json
import pathlib
import unittest
import urllib.error

from lovs.sitrep_promotions import load_reviewed_promotions
from tools import probe_source_availability as P

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestSourceAvailabilityRecord(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(P.OUTPUT_PATH.read_text(encoding="utf-8"))

    def test_every_cited_sitrep_has_been_probed(self) -> None:
        # A new edition must not reach the public surface without a verdict on
        # whether its own citation still resolves.
        cited = {
            str(p["source_id"]) for p in load_reviewed_promotions() if p.get("source_url")
        }
        missing = sorted(cited - set(self.payload["sources"]))
        self.assertEqual([], missing, f"unprobed cited sources: {missing}")

    def test_withdrawn_sources_keep_their_audit_anchor(self) -> None:
        # Once the publisher copy is gone the retained hash and byte length are
        # the only things a reader can still check the figures against, so a
        # withdrawn row without them is worse than no row at all.
        for source_id, row in self.payload["sources"].items():
            if row.get("state") != "withdrawn":
                continue
            with self.subTest(source=source_id):
                self.assertRegex(str(row.get("retained_sha256", "")), r"^[0-9a-f]{64}$")
                self.assertIsInstance(row.get("retained_byte_length"), int)
                self.assertGreater(row["retained_byte_length"], 0)

    def test_states_are_from_the_closed_vocabulary(self) -> None:
        for source_id, row in self.payload["sources"].items():
            with self.subTest(source=source_id):
                self.assertIn(row.get("state"), {"available", "withdrawn", "unverified"})


class TestProbeClassification(unittest.TestCase):
    def test_http_404_is_withdrawn(self) -> None:
        def fetch(url, **_kwargs):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        self.assertEqual(
            {"state": "withdrawn", "http_status": 404}, P.probe("https://x/y.pdf", fetch_fn=fetch)
        )

    def test_transport_failure_is_unverified_not_withdrawn(self) -> None:
        # A flaky network must never be allowed to report a live document as
        # withdrawn: that would put a false retraction notice on the brief.
        def fetch(url, **_kwargs):
            raise TimeoutError("connection timed out")

        verdict = P.probe("https://x/y.pdf", fetch_fn=fetch)
        self.assertEqual("unverified", verdict["state"])
        self.assertIn("TimeoutError", str(verdict["error"]))

    def test_success_records_the_served_length(self) -> None:
        def fetch(url, **_kwargs):
            return b"%PDF-1.7\n", 200, "application/pdf"

        self.assertEqual(
            {"state": "available", "http_status": 200, "byte_length": 9},
            P.probe("https://x/y.pdf", fetch_fn=fetch),
        )


if __name__ == "__main__":
    unittest.main()
