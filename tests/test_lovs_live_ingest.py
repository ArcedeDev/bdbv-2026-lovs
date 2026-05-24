"""Tests for lovs/lovs_live_ingest.py.

Network is mocked via injectable ``fetch_fn`` to keep the test suite
hermetic and deterministic.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from lovs import lovs_archive
from lovs import lovs_live_ingest


_SAMPLE_HTML = b"""
<html><head><title>WHO DON sample</title></head>
<body>
<article>
<h1>Ebola disease caused by Bundibugyo virus</h1>
<p>On 15 May 2026 the Government declared the outbreak.</p>
<p>As of 15 May 2026, 246 suspected cases and 80 deaths.</p>
<p>Four deaths among confirmed cases identified.</p>
<p>Affected: Rwampara Health Zone, Mongbwalu Health Zone, Bunia City. Kampala District (imported cases).</p>
<p>The strain is Bundibugyo virus (BDBV).</p>
</article>
</body></html>
"""

_CDC_HTML = b"""
<html><body>
<h1>Ebola Disease: Current Situation</h1>
<p>May 20, 2026</p>
<ul>
<li>As of May 19, the DRC and Uganda Ministries of Health report the following:</li>
<li>A total of 536 suspected cases, 105 probable cases, 34 confirmed cases, and 134 suspected deaths</li>
<li>In the last 24 to 48 hours, 26 new confirmed cases and 143 new suspected cases were identified.</li>
<li>These numbers include 2 confirmed cases including 1 death in Uganda in people who traveled from DRC.</li>
</ul>
<p>As of May 20, 2026, the Ebola Bundibugyo outbreak in DRC has been reported in 11 health zones in Ituri Province and in Nord-Kivu Province.</p>
<p>To date, no cases of Ebola disease have been confirmed in the United States because of this outbreak.</p>
</body></html>
"""

_CDC_HTML_MAY21 = b"""
<html><body>
<h1>Ebola Disease: Current Situation</h1>
<p>May 21, 2026</p>
<ul>
<li>As of May 21, the DRC and Uganda Ministries of Health report the following:</li>
<li>A total of 575 suspected cases, 51 confirmed cases, and 148 suspected deaths.</li>
<li>These numbers include 2 confirmed cases including 1 death in Uganda in people who traveled from DRC.</li>
</ul>
<p>As of May 20, 2026, the Ebola Bundibugyo outbreak in DRC has been reported in 11 health zones in Ituri Province and in Nord-Kivu Province.</p>
<p>To date, no cases of Ebola disease have been confirmed in the United States because of this outbreak.</p>
</body></html>
"""

_CDC_HTML_MAY23 = b"""
<html><body>
<h1>Ebola Disease: Current Situation</h1>
<p>May 23, 2026</p>
<ul>
<li>As of May 23, the DRC and Uganda Ministries of Health report the following:</li>
<li>A new confirmed case in Sud-Kivu Province; previously, cases had been confirmed in Ituri and Nord-Kivu provinces only.</li>
<li>DRC : A total of 746 suspected cases, 83 confirmed cases, 176 suspected deaths, and 9 confirmed deaths .</li>
<li>Uganda : A total of 5 confirmed cases and 1 confirmed death .</li>
<li>On May 23, Uganda announced 3 additional cases, all with clear links to the previously announced cases in people who traveled from DRC.</li>
</ul>
<p>As of May 23, 2026, the Ebola Bundibugyo outbreak in DRC has been confirmed in Ituri, Nord-Kivu, and Sud-Kivu provinces. Five cases related to the DRC outbreak also have been reported in Uganda's capital of Kampala.</p>
<p>To date, no cases of Ebola disease have been confirmed in the United States because of this outbreak.</p>
</body></html>
"""


def _mock_fetch(url: str) -> bytes:
    return _SAMPLE_HTML


def _fixed_now() -> str:
    return "2026-05-20T15:00:00Z"


class TestIngestOne(unittest.TestCase):

    def test_ingest_one_fetches_and_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            snap = lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=_fixed_now,
            )
            self.assertIsNotNone(snap)
            self.assertEqual(snap.outbreak_id, "bdbv-uga-cod-2026")
            self.assertEqual(snap.pathogen, "BDBV")
            self.assertEqual(snap.provenance.retrieved_at, "2026-05-20T15:00:00Z")
            self.assertTrue((root / "manifest.json").exists())

    def test_ingest_one_extracts_case_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            snap = lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=_fixed_now,
            )
            self.assertEqual(snap.normalized_content.get("cases_suspected"), 246)
            self.assertEqual(snap.normalized_content.get("deaths"), 80)

    def test_ingest_one_idempotent_on_same_bytes(self):
        """Second call with the same fetched bytes returns None (no new snapshot)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            first = lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=_fixed_now,
            )
            self.assertIsNotNone(first)
            second = lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=lambda: "2026-05-21T15:00:00Z",
            )
            self.assertIsNone(second)

    def test_ingest_one_raises_on_empty_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            with self.assertRaises(lovs_live_ingest.LiveIngestError):
                lovs_live_ingest.ingest_one(
                    lovs_live_ingest.WHO_DON_602_TARGET,
                    root,
                    fetch_fn=lambda url: b"",
                    now_fn=_fixed_now,
                )

    def test_ingest_one_raises_on_unknown_parser(self):
        bad_target = lovs_live_ingest.IngestTarget(
            source_id="bad",
            source_tier="official_who",
            publisher="WHO",
            url="https://example.com",
            license="CC-BY-NC-SA-3.0-IGO",
            outbreak_id="x",
            pathogen="BDBV",
            country_scope=("COD",),
            geography_id="x",
            parser_name="nonexistent_parser",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            with self.assertRaises(lovs_live_ingest.LiveIngestError):
                lovs_live_ingest.ingest_one(
                    bad_target, root, fetch_fn=_mock_fetch, now_fn=_fixed_now
                )

    def test_ingest_one_writes_raw_bytes_by_content_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            snap = lovs_live_ingest.ingest_one(
                lovs_live_ingest.WHO_DON_602_TARGET,
                root,
                fetch_fn=_mock_fetch,
                now_fn=_fixed_now,
            )
            raw_path = root / snap.raw_bytes_relpath
            self.assertTrue(raw_path.exists())
            self.assertEqual(raw_path.read_bytes(), _SAMPLE_HTML)


class TestWhoDonParser(unittest.TestCase):

    def test_parses_zones(self):
        normalized = lovs_live_ingest._parse_who_don_html(_SAMPLE_HTML)
        zones = normalized.get("affected_zones", [])
        for zone in ("rwampara", "mongbwalu", "bunia", "kampala"):
            self.assertIn(zone, zones)

    def test_parses_declaration_date(self):
        normalized = lovs_live_ingest._parse_who_don_html(_SAMPLE_HTML)
        self.assertIn("declaration_date_raw", normalized)
        self.assertIn("2026", normalized["declaration_date_raw"])

    def test_parses_short_narrative_excerpt(self):
        normalized = lovs_live_ingest._parse_who_don_html(_SAMPLE_HTML)
        excerpt = normalized.get("narrative_excerpt", "")
        self.assertGreater(len(excerpt), 0)
        # narrative_excerpt must be bounded (the parser caps at 60 words)
        self.assertLessEqual(len(excerpt.split()), 60)

    def test_html_parse_ignores_scripts(self):
        with_script = (
            b"<html><body>"
            b"<script>alert(\"This should not affect parsing 999 suspected cases\")</script>"
            b"<p>3 suspected cases observed.</p></body></html>"
        )
        normalized = lovs_live_ingest._parse_who_don_html(with_script)
        self.assertEqual(normalized.get("cases_suspected"), 3)

    def test_declaration_date_prefers_canonical_ministry_sentence(self):
        """Regression: an antecedent 'declared on <date>' for a PREVIOUS outbreak
        must not beat the canonical 'On <date>, the Ministry ... declared' shape.

        Round 1 fix for Stage Two Important finding: the original regex captured
        '5 May 2026' (an alerted-WHO date) or '4 September 2025' (a previous-outbreak
        reference) rather than the canonical 15 May 2026 declaration. The tightened
        priority order in _DECLARATION_DATE_PATTERNS resolves this.
        """
        html = (
            b"<html><body>"
            b"<p>On 5 May 2026, WHO was alerted of an unknown illness.</p>"
            b"<p>The previous outbreak was declared on 4 September 2025.</p>"
            b"<p>On 15 May 2026, the Ministry of Public Health, Hygiene and Social Welfare, DRC, officially declared the 17th Ebola outbreak.</p>"
            b"</body></html>"
        )
        normalized = lovs_live_ingest._parse_who_don_html(html)
        self.assertEqual(normalized.get("declaration_date_raw"), "15 May 2026")
        self.assertIn("Ministry", normalized.get("declaration_text", ""))


class TestCdcCurrentSituationParser(unittest.TestCase):

    def test_parses_cdc_current_situation_tuple(self):
        normalized = lovs_live_ingest._parse_cdc_ebola_html(_CDC_HTML)
        self.assertEqual(normalized["publication_date"], "2026-05-20")
        self.assertEqual(normalized["data_as_of"], "2026-05-19")
        self.assertEqual(normalized["cases_suspected"], 536)
        self.assertEqual(normalized["cases_probable"], 105)
        self.assertEqual(normalized["cases_confirmed"], 34)
        self.assertEqual(normalized["deaths_suspected"], 134)
        self.assertEqual(normalized["new_confirmed_cases_24_to_48h"], 26)
        self.assertEqual(normalized["new_suspected_cases_24_to_48h"], 143)
        self.assertEqual(normalized["cases_confirmed_uganda"], 2)
        self.assertEqual(normalized["deaths_uganda"], 1)
        self.assertEqual(normalized["affected_health_zones_count"], 11)
        self.assertEqual(normalized["affected_provinces"], ["Ituri", "Nord-Kivu"])
        self.assertEqual(normalized["cases_confirmed_united_states"], 0)

    def test_parses_cdc_current_situation_tuple_without_probable(self):
        normalized = lovs_live_ingest._parse_cdc_ebola_html(_CDC_HTML_MAY21)
        self.assertEqual(normalized["publication_date"], "2026-05-21")
        self.assertEqual(normalized["data_as_of"], "2026-05-21")
        self.assertEqual(normalized["cases_suspected"], 575)
        self.assertNotIn("cases_probable", normalized)
        self.assertEqual(normalized["cases_confirmed"], 51)
        self.assertEqual(normalized["deaths_suspected"], 148)
        self.assertEqual(normalized["cases_confirmed_uganda"], 2)
        self.assertEqual(normalized["deaths_uganda"], 1)

    def test_parses_cdc_current_situation_country_split(self):
        normalized = lovs_live_ingest._parse_cdc_ebola_html(_CDC_HTML_MAY23)
        self.assertEqual(normalized["publication_date"], "2026-05-23")
        self.assertEqual(normalized["data_as_of"], "2026-05-23")
        self.assertEqual(normalized["cases_suspected"], 746)
        self.assertEqual(normalized["cases_suspected_drc"], 746)
        self.assertEqual(normalized["cases_confirmed_drc"], 83)
        self.assertEqual(normalized["cases_confirmed_uganda"], 5)
        self.assertEqual(normalized["cases_confirmed_total"], 88)
        self.assertEqual(normalized["cases_confirmed"], 88)
        self.assertEqual(normalized["deaths_suspected"], 176)
        self.assertEqual(normalized["deaths_confirmed_drc"], 9)
        self.assertEqual(normalized["deaths_uganda"], 1)
        self.assertEqual(normalized["new_confirmed_cases_uganda"], 3)

    def test_cdc_target_is_available_for_archive_ingest(self):
        self.assertEqual(
            lovs_live_ingest.CDC_CURRENT_SITUATION_TARGET.parser_name,
            "cdc_ebola_html",
        )
        self.assertEqual(
            lovs_live_ingest.CDC_CURRENT_SITUATION_TARGET.source_tier,
            "official_cdc",
        )

    def test_cdc_target_can_be_dated_without_reusing_source_id(self):
        target = lovs_live_ingest.cdc_current_situation_target("2026-05-21")
        self.assertEqual(target.source_id, "cdc-current-situation-2026-05-21")
        self.assertEqual(target.url, lovs_live_ingest.CDC_CURRENT_SITUATION_TARGET.url)


class TestIngestBdbv2026Convenience(unittest.TestCase):

    def test_returns_only_new_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            first = lovs_live_ingest.ingest_bdbv_2026(
                root, fetch_fn=_mock_fetch, now_fn=_fixed_now
            )
            self.assertEqual(len(first), 1)
            second = lovs_live_ingest.ingest_bdbv_2026(
                root, fetch_fn=_mock_fetch, now_fn=_fixed_now
            )
            self.assertEqual(len(second), 0)

    def test_archive_loadable_after_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "bundibugyo-2026"
            lovs_live_ingest.ingest_bdbv_2026(
                root, fetch_fn=_mock_fetch, now_fn=_fixed_now
            )
            archive = lovs_archive.load_archive(root)
            self.assertEqual(len(archive.snapshots), 1)
            self.assertEqual(archive.snapshots[0].pathogen, "BDBV")


class TestArchiveRestrictedBytes(unittest.TestCase):

    def test_load_archive_allows_private_restricted_hash_only_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "archive"
            root.mkdir()
            manifest = {
                "manifest_version": lovs_archive.MANIFEST_VERSION,
                "entries": [
                    {
                        "source_id": "restricted-source",
                        "source_tier": "official_continental_body",
                        "publisher": "Publisher",
                        "url": "https://example.com/source",
                        "retrieved_at": "2026-05-20T00:00:00Z",
                        "content_hash": "0" * 64,
                        "license": "publisher-terms-not-confirmed",
                        "extraction_status": "partial",
                        "root_provenance_chain": [],
                        "outbreak_id": "x",
                        "pathogen": "BDBV",
                        "country_scope": ["COD"],
                        "geography_id": "x",
                        "raw_archive_status": "private_restricted_bytes",
                        "raw_bytes_relpath": None,
                        "normalized_content": {"cases": 1},
                    }
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            archive = lovs_archive.load_archive(root)
            self.assertEqual(len(archive.snapshots), 1)
            self.assertIsNone(archive.snapshots[0].raw_bytes_relpath)
            self.assertEqual(
                archive.snapshots[0].raw_archive_status,
                "private_restricted_bytes",
            )


class TestNowFnDefault(unittest.TestCase):

    def test_now_iso_z_format(self):
        s = lovs_live_ingest._now_utc_iso_z()
        self.assertTrue(s.endswith("Z"))
        self.assertGreaterEqual(len(s), 20)


if __name__ == "__main__":
    unittest.main()
