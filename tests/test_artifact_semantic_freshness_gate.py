# SPDX-License-Identifier: Apache-2.0
"""Tests for the semantic-freshness release gate (lovs.semantic_freshness_gate).

The gate proves the CONTENT rendered into published artifacts is current, not
merely byte-identical to a stale committed copy. Every assertion is anchored to
the snapshot's own clocks; no test reads a wall clock.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
import zipfile

import export_public_health_dataset
from lovs import lovs_evidence
from lovs import semantic_freshness_gate as gate


def _with_generated_headline_provenance(snapshot: dict) -> dict:
    """Return a copy with the headline provenance the refresh pipeline now emits.

    Derives the backing chain for each headline metric from the snapshot's own
    ``reported_counts.confirmed`` / ``reported_deaths.confirmed``
    ``primary_source_id`` (exactly as ``refresh_pipeline`` does), so a test can
    validate the WIRED snapshot shape against the on-disk committed snapshot
    without rewriting that committed artifact.
    """
    confirmed = (snapshot.get("reported_counts") or {}).get("confirmed") or {}
    deaths = (snapshot.get("reported_deaths") or {}).get("confirmed") or {}
    out = dict(snapshot)
    out["headline_evidence_chain_ids"] = lovs_evidence.headline_evidence_provenance(
        lovs_evidence.load_registry(),
        confirmed_primary_source_id=confirmed.get("primary_source_id"),
        confirmed_deaths_primary_source_id=deaths.get("primary_source_id"),
    )
    return out


# A June-2 snapshot fixture: headline 2026-06-02, per-zone block 2026-05-29,
# confirmed 370 primary, confirmed-deaths 63 primary. Mirrors the live shape.
JUNE2_SNAPSHOT = {
    "as_of": "2026-06-02T23:59:59Z",
    "data_as_of": "2026-06-02",
    "outbreak_id": "bdbv-uga-cod-2026",
    "reported_counts": {
        "confirmed": {
            "primary": 370,
            "min": 355,
            "max": 370,
            "primary_source_id": "inrb-sitrep-019-2026-06-02",
            "conflicting_source_ids": [],
        },
    },
    "reported_deaths": {
        "confirmed": {
            "primary": 63,
            "min": 61,
            "max": 63,
            "primary_source_id": "inrb-sitrep-019-2026-06-02",
            "conflicting_source_ids": [],
        },
    },
    # Headline provenance wired (Blocker 1): each headline metric's
    # primary_source_id is backed by the embedded chain whose chain_source
    # matches it. Without this, the chain-to-source gate FAILs (which is the
    # whole point); these tests exercise the OTHER gate checks, so the snapshot
    # is correctly wired here.
    "headline_evidence_chain_ids": [
        {
            "metric": "confirmed",
            "primary_source_id": "inrb-sitrep-019-2026-06-02",
            "chain_source": "inrb-sitrep-019-2026-06-02",
            "evidence_chain_id": "ec:lovs:data:inrb-sitrep-019-visual-promotion:2026-06-02",
            "backed": True,
        },
        {
            "metric": "confirmed_deaths",
            "primary_source_id": "inrb-sitrep-019-2026-06-02",
            "chain_source": "inrb-sitrep-019-2026-06-02",
            "evidence_chain_id": "ec:lovs:data:inrb-sitrep-019-visual-promotion:2026-06-02",
            "backed": True,
        },
    ],
    "insp_per_zone_block": {
        "as_of_data_date": "2026-05-29",
        "source_id": "inrb-umie-ebola-drc-2026-build-2026-06-01-b4cafc9",
    },
}

# Source manifest declaring 2026-05-29 and 2026-05-30 as legitimate source dates.
SOURCE_MANIFEST = {
    "entries": [
        {"source_id": "a", "published_at": "2026-05-29T00:00:00Z"},
        {"source_id": "b", "data_as_of": "2026-05-30"},
        {"source_id": "c", "published_at": "2026-06-02T00:00:00Z"},
    ]
}


def _minimal_workbook(path: pathlib.Path, reported_counts_rows: list[list[str]], extra_text_rows=None):
    """Write a tiny workbook with a 'Reported Counts' sheet + an optional text row.

    Exercises the gate's real xlsx parser, including a deliberately empty cell
    in the `location` column to lock in the column-alignment behaviour.
    """
    header = [
        "row_id", "row_type", "metric", "location", "as_of_date", "value",
        "value_min", "value_max",
    ]

    def cell(ref: str, value: str) -> str:
        if value == "":
            return f'<c r="{ref}"/>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'

    def row(idx: int, values: list[str]) -> str:
        letters = "ABCDEFGHIJKL"
        cells = "".join(cell(f"{letters[i]}{idx}", v) for i, v in enumerate(values))
        return f'<row r="{idx}">{cells}</row>'

    rows_xml = [row(1, header)]
    r = 2
    for data in reported_counts_rows:
        rows_xml.append(row(r, data))
        r += 1
    for data in (extra_text_rows or []):
        rows_xml.append(row(r, data))
        r += 1
    sheet1 = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(rows_xml)}</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheets><sheet name="Reported Counts" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)


class TestSemanticFreshnessGate(unittest.TestCase):

    # 1. SVG date extraction.
    def test_parse_svg_dates_extracts_as_of(self):
        svg = '<svg><text>as_of 2026-05-29</text><text>as of 2026-06-02</text>' \
              '<text>updated: 2026-01-01</text></svg>'
        self.assertEqual({"2026-05-29", "2026-06-02"}, gate.parse_svg_dates(svg))
        # A free-floating date not tagged as as_of is ignored.
        self.assertNotIn("2026-01-01", gate.parse_svg_dates(svg))

    # 2. SVG date-mismatch FAIL.
    def test_svg_stale_as_of_date_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = pathlib.Path(tmp)
            # 2026-05-29 is an allowed source date -> OK.
            (brief / "ok.svg").write_text("<svg><text>as_of 2026-05-29</text></svg>")
            # 2026-04-01 is neither the snapshot date nor a source date -> FAIL.
            (brief / "stale.svg").write_text("<svg><text>as_of 2026-04-01</text></svg>")
            result = gate.check_artifact_semantic_freshness(
                snapshot=JUNE2_SNAPSHOT,
                manifest=SOURCE_MANIFEST,
                brief_dir=brief,
                workbook=pathlib.Path(tmp) / "missing.xlsx",
                output_dir=pathlib.Path(tmp),
            )
        self.assertEqual("fail", result["status"])
        self.assertTrue(any("stale.svg" in f and "2026-04-01" in f for f in result["findings"]))
        self.assertFalse(any("ok.svg" in f for f in result["findings"]))

    # 3. XLSX rendered-count validation.
    def test_xlsx_rendered_count_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            # Stale workbook: renders 355 confirmed / 61 deaths (the min band, not
            # the 370/63 primary). The empty 'location' cell must not shift columns.
            wb = out / "stale.xlsx"
            _minimal_workbook(
                wb,
                [
                    ["snapshot:reported_counts:confirmed", "snapshot_reconciled_metric",
                     "confirmed_cases", "", "2026-06-02T23:59:59Z", "355", "355", "370"],
                    ["snapshot:reported_deaths:confirmed", "snapshot_reconciled_metric",
                     "deaths_confirmed", "", "2026-06-02T23:59:59Z", "61", "61", "63"],
                ],
            )
            parsed = gate.parse_xlsx_context_text(wb)
            self.assertEqual({"355"}, parsed["confirmed"])
            self.assertEqual({"61"}, parsed["deaths_confirmed"])
            result = gate.check_artifact_semantic_freshness(
                snapshot=JUNE2_SNAPSHOT,
                manifest=SOURCE_MANIFEST,
                brief_dir=pathlib.Path(tmp) / "no-brief",
                workbook=wb,
                output_dir=out,
            )
        self.assertEqual("fail", result["status"])
        self.assertTrue(any("confirmed cells" in f and "370" in f for f in result["findings"]))
        self.assertTrue(any("confirmed-deaths cells" in f and "63" in f for f in result["findings"]))

    def test_xlsx_rendered_count_match_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            wb = out / "fresh.xlsx"
            _minimal_workbook(
                wb,
                [
                    ["snapshot:reported_counts:confirmed", "snapshot_reconciled_metric",
                     "confirmed_cases", "", "2026-06-02T23:59:59Z", "370", "355", "370"],
                    ["snapshot:reported_deaths:confirmed", "snapshot_reconciled_metric",
                     "deaths_confirmed", "", "2026-06-02T23:59:59Z", "63", "61", "63"],
                ],
            )
            result = gate.check_artifact_semantic_freshness(
                snapshot=JUNE2_SNAPSHOT,
                manifest=SOURCE_MANIFEST,
                brief_dir=pathlib.Path(tmp) / "no-brief",
                workbook=wb,
                output_dir=out,
            )
        self.assertEqual("pass", result["status"], result["findings"])

    # 4. Per-zone CSV currency.
    def test_per_zone_csv_stale_date_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            # Block date is 2026-05-29; a per-zone row dated 2026-05-28 is stale.
            (out / "per-zone_snapshot.csv").write_text(
                "lovs_zone_id,as_of_data_date,confirmed\n"
                "bunia,2026-05-29,80\n"
                "aru,2026-05-28,2\n"
            )
            result = gate.check_artifact_semantic_freshness(
                snapshot=JUNE2_SNAPSHOT,
                manifest=SOURCE_MANIFEST,
                brief_dir=pathlib.Path(tmp) / "no-brief",
                workbook=pathlib.Path(tmp) / "missing.xlsx",
                output_dir=out,
            )
        self.assertEqual("fail", result["status"])
        self.assertTrue(
            any("per-zone" in f and "2026-05-28" in f for f in result["findings"])
        )

    def test_per_zone_csv_current_date_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            (out / "per-zone_snapshot.csv").write_text(
                "lovs_zone_id,as_of_data_date,confirmed\n"
                "bunia,2026-05-29,80\n"
                "aru,2026-05-29,2\n"
            )
            result = gate.check_artifact_semantic_freshness(
                snapshot=JUNE2_SNAPSHOT,
                manifest=SOURCE_MANIFEST,
                brief_dir=pathlib.Path(tmp) / "no-brief",
                workbook=pathlib.Path(tmp) / "missing.xlsx",
                output_dir=out,
            )
        self.assertEqual("pass", result["status"], result["findings"])

    # 5. Mixed-basis label detection (SVG + workbook).
    def test_mixed_basis_death_label_in_svg_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = pathlib.Path(tmp)
            (brief / "deaths.svg").write_text(
                '<svg><text>as_of 2026-06-02</text><text>Deaths (reported)</text></svg>'
            )
            result = gate.check_artifact_semantic_freshness(
                snapshot=JUNE2_SNAPSHOT,
                manifest=SOURCE_MANIFEST,
                brief_dir=brief,
                workbook=pathlib.Path(tmp) / "missing.xlsx",
                output_dir=pathlib.Path(tmp),
            )
        self.assertEqual("fail", result["status"])
        self.assertTrue(
            any("mixed-basis death label" in f for f in result["findings"])
        )

    def test_mixed_basis_label_allowed_before_cutoff(self):
        # A pre-cutoff snapshot (death axis still broad-register) tolerates the
        # "Deaths (reported)" label: the gate is basis-aware, not a blanket ban.
        pre_cutoff = json.loads(json.dumps(JUNE2_SNAPSHOT))
        pre_cutoff["as_of"] = "2026-05-31T23:59:59Z"
        with tempfile.TemporaryDirectory() as tmp:
            brief = pathlib.Path(tmp)
            (brief / "deaths.svg").write_text(
                '<svg><text>as_of 2026-05-31</text><text>Deaths (reported)</text></svg>'
            )
            result = gate.check_artifact_semantic_freshness(
                snapshot=pre_cutoff,
                manifest={"entries": [{"source_id": "x", "published_at": "2026-05-31T00:00:00Z"}]},
                brief_dir=brief,
                workbook=pathlib.Path(tmp) / "missing.xlsx",
                output_dir=pathlib.Path(tmp),
            )
        self.assertEqual("pass", result["status"], result["findings"])

    # 6. Stale-context detection via the per-artifact package manifest (schema v2).
    def test_stale_manifest_context_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            (out / "reported_counts.csv").write_text(
                "metric,value\nconfirmed_cases,370\ndeaths_confirmed,63\n"
            )
            # A manifest whose must_contain promises a value the artifact no longer
            # carries (a stale rendered context), plus a stale semantic_as_of.
            manifest = {
                "schema_version": 2,
                "package": "lovs-public-health-dataset",
                "outputs": [
                    {
                        "path": "reported_counts.csv",
                        "sha256": "x",
                        "semantic_as_of": "2026-04-01",
                        "source_date": "2026-04-01",
                        "source_ids": ["inrb-sitrep-019-2026-06-02"],
                        "must_contain_text": ["999"],
                        "must_not_contain_text": ["370"],
                    }
                ],
            }
            (out / gate.PACKAGE_MANIFEST_NAME).write_text(json.dumps(manifest))
            findings = gate.validate_per_artifact_manifest(
                manifest, JUNE2_SNAPSHOT, SOURCE_MANIFEST, out
            )
        joined = "\n".join(findings)
        self.assertIn("semantic_as_of", joined)  # stale date flagged
        self.assertIn("required text '999' is absent", joined)
        self.assertIn("forbidden text '370' is present", joined)

    def test_manifest_schema_v1_is_tolerated(self):
        # A pre-regen schema-v1 package manifest (path + sha256 only, no per-
        # artifact semantic block) must be TOLERATED, not hard-fail the release
        # pipeline: per-artifact text-contract enforcement is skipped until the
        # founder-gated regen emits a schema-v2 manifest. The SVG/workbook/CSV
        # content checks (run in check_artifact_semantic_freshness) still apply.
        findings = gate.validate_per_artifact_manifest(
            {"schema_version": 1, "outputs": []},
            JUNE2_SNAPSHOT,
            SOURCE_MANIFEST,
            pathlib.Path("/nonexistent"),
        )
        self.assertEqual([], findings)

    # End-to-end: a freshly-exported package + real source manifest passes, and
    # the exporter emits the schema-2 per-artifact manifest the gate validates.
    def test_fresh_export_package_passes_gate(self):
        snapshot = json.loads(
            (pathlib.Path(export_public_health_dataset.SNAPSHOT_PATH)).read_text()
        )
        source_manifest = json.loads(
            (pathlib.Path(export_public_health_dataset.MANIFEST_PATH)).read_text()
        )
        # Attach the generated headline provenance the refresh pipeline now emits
        # (derived from the snapshot's own headline primary_source_ids), so this
        # end-to-end check validates the WIRED snapshot shape without rewriting
        # the committed on-disk artifact (a production regen is out of scope).
        snapshot = _with_generated_headline_provenance(snapshot)
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            paths = export_public_health_dataset.export_package(out)
            manifest = json.loads(paths["manifest"].read_text())
            self.assertEqual(2, manifest["schema_version"])
            self.assertTrue(
                all("semantic_as_of" in o for o in manifest["outputs"]),
                "every output row must carry semantic-freshness metadata",
            )
            result = gate.check_artifact_semantic_freshness(
                snapshot=snapshot,
                manifest=source_manifest,
                brief_dir=pathlib.Path(export_public_health_dataset.REPO_ROOT) / "brief",
                workbook=paths["workbook"],
                output_dir=out,
            )
        self.assertEqual("pass", result["status"], result["findings"])


class TestHeadlineSourceClockGate(unittest.TestCase):
    """Gate (6): sourceClocks[headline_count_endpoint] == confirmed primary."""

    def _snapshot_with_clock(self, clock_source: str):
        snap = json.loads(json.dumps(JUNE2_SNAPSHOT))
        snap["date_semantics"] = {
            "source_clocks": {"headline_count_endpoint": clock_source}
        }
        return snap

    def test_clock_matches_confirmed_primary_passes(self):
        # Clock names the same source the confirmed headline (inrb-sitrep-019)
        # rides -> no finding.
        snap = self._snapshot_with_clock("inrb-sitrep-019-2026-06-02")
        self.assertEqual([], gate.check_headline_source_clock(snap))

    def test_clock_stale_sitrep018_fails(self):
        # The headline rides #019 but the clock still names #018 -> FAIL.
        snap = self._snapshot_with_clock("inrb-sitrep-018-2026-06-01")
        findings = gate.check_headline_source_clock(snap)
        self.assertTrue(findings)
        self.assertTrue(any("headline_count_endpoint" in f for f in findings))

    def test_clock_absent_is_not_a_finding(self):
        # An internal snapshot without the website clock has nothing to bind.
        self.assertEqual([], gate.check_headline_source_clock(JUNE2_SNAPSHOT))

    def test_clock_camel_website_shape(self):
        # The camelCased website shape (dateSemantics.sourceClocks) is read too.
        snap = json.loads(json.dumps(JUNE2_SNAPSHOT))
        snap["reportedCounts"] = {
            "confirmed": {"primarySourceId": "inrb-sitrep-019-2026-06-02"}
        }
        snap["dateSemantics"] = {
            "sourceClocks": {"headline_count_endpoint": "inrb-sitrep-018-2026-06-01"}
        }
        findings = gate.check_headline_source_clock(snap)
        self.assertTrue(findings)

    def test_clock_strips_live_suffix_match(self):
        snap = self._snapshot_with_clock("inrb-sitrep-019-2026-06-02-live")
        self.assertEqual([], gate.check_headline_source_clock(snap))

    def test_clock_check_runs_in_top_level_gate(self):
        # A stale clock fails the whole gate, end-to-end.
        snap = self._snapshot_with_clock("inrb-sitrep-018-2026-06-01")
        with tempfile.TemporaryDirectory() as tmp:
            result = gate.check_artifact_semantic_freshness(
                snapshot=snap,
                manifest=SOURCE_MANIFEST,
                brief_dir=pathlib.Path(tmp) / "no-brief",
                workbook=pathlib.Path(tmp) / "missing.xlsx",
                output_dir=pathlib.Path(tmp),
            )
        self.assertEqual("fail", result["status"])
        self.assertTrue(any("headline_count_endpoint" in f for f in result["findings"]))


# The structured methodology twins the prose check enforces.
METHODOLOGY_CONSTANTS = {
    "imperial_reference": [400, 900],
    "cfr": [0.26, 0.33, 0.40],
    "central_doubling_time_days": 7.0,
    "observed_doubling_times_days": [5.0, 7.0, 11.0],
}


class TestProseStructuredTwinGate(unittest.TestCase):
    """Gate (7): a prose number with a structured twin must match it."""

    def test_matching_cfr_passes(self):
        text = "reproduces Method 2 (CFR 26/33/40, at the borrowed 14-day central)"
        self.assertEqual(
            [], gate.check_prose_structured_twins(text, METHODOLOGY_CONSTANTS)
        )

    def test_stale_cfr_fails(self):
        # The old 24/30/40 set is stale against the structured 26/33/40.
        text = "reproduces Method 2 (CFR 24/30/40, at the borrowed 14-day central)"
        findings = gate.check_prose_structured_twins(text, METHODOLOGY_CONSTANTS)
        self.assertTrue(findings)
        self.assertTrue(any("CFR" in f and "26/33/40" in f for f in findings))

    def test_matching_imperial_band_passes(self):
        text = "estimating 400-900 total cases in DRC (values over 1,000 not excluded)"
        self.assertEqual(
            [], gate.check_prose_structured_twins(text, METHODOLOGY_CONSTANTS)
        )

    def test_stale_imperial_band_fails(self):
        # The old 400-800 band is stale against the structured 400-900.
        text = "estimating 400-800 total cases in DRC"
        findings = gate.check_prose_structured_twins(text, METHODOLOGY_CONSTANTS)
        self.assertTrue(findings)
        self.assertTrue(any("Imperial reference band" in f for f in findings))

    def test_matching_source_zone_count_passes(self):
        text = "across 25 source zones carrying confirmed cases"
        self.assertEqual(
            [],
            gate.check_prose_structured_twins(
                text, METHODOLOGY_CONSTANTS, source_zone_count=25
            ),
        )

    def test_stale_source_zone_count_fails(self):
        text = "across 18 source zones carrying confirmed cases"
        findings = gate.check_prose_structured_twins(
            text, METHODOLOGY_CONSTANTS, source_zone_count=25
        )
        self.assertTrue(findings)
        self.assertTrue(any("source-zone count" in f for f in findings))

    def test_unrelated_numbers_are_not_twins(self):
        # A free number that is not a twinned quantity is never flagged.
        text = "the snapshot carries 370 confirmed cases and 63 confirmed deaths"
        self.assertEqual(
            [],
            gate.check_prose_structured_twins(
                text, METHODOLOGY_CONSTANTS, source_zone_count=25
            ),
        )

    def test_no_constants_is_noop(self):
        text = "CFR 24/30/40 and 400-800 total cases in DRC"
        self.assertEqual([], gate.check_prose_structured_twins(text, None))

    def test_prose_twin_runs_in_top_level_gate_via_svg(self):
        # A stale CFR literal in a shipped SVG fails the whole gate when the
        # structured twin is supplied.
        with tempfile.TemporaryDirectory() as tmp:
            brief = pathlib.Path(tmp)
            (brief / "framing.svg").write_text(
                "<svg><text>as_of 2026-06-02</text>"
                "<text>Method 2 (CFR 24/30/40, borrowed 14-day central)</text></svg>"
            )
            result = gate.check_artifact_semantic_freshness(
                snapshot=JUNE2_SNAPSHOT,
                manifest=SOURCE_MANIFEST,
                brief_dir=brief,
                workbook=pathlib.Path(tmp) / "missing.xlsx",
                output_dir=pathlib.Path(tmp),
                methodology_constants=METHODOLOGY_CONSTANTS,
            )
        self.assertEqual("fail", result["status"])
        self.assertTrue(any("CFR" in f and "26/33/40" in f for f in result["findings"]))


if __name__ == "__main__":
    unittest.main()
