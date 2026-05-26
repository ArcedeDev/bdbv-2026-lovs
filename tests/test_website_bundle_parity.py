from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lovs.website_bundle_parity import check_website_bundle_parity


CANONICAL_AS_OF = "2026-05-25T23:59:59Z"
CANONICAL_SOURCE = "cdc-current-situation-2026-05-25"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: str) -> None:
    _write(path, payload.strip() + "\n")


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _counts(source_id: str = CANONICAL_SOURCE, confirmed: int = 112) -> str:
    return f"""{{
      "confirmed": {{"min": {confirmed}, "max": {confirmed}, "primary": {confirmed}, "primarySourceId": "{source_id}"}},
      "suspected": {{"min": 906, "max": 906, "primary": 906, "primarySourceId": "{source_id}"}},
      "deaths": {{"min": 223, "max": 223, "primary": 223, "primarySourceId": "{source_id}"}}
    }}"""


def _make_bundle(root: Path, *, stale_confirmed: bool = False) -> tuple[Path, Path]:
    lovs = root / "lovs"
    website = root / "website" / "apps" / "site"
    _write_json(
        lovs / "data" / "live-bdbv-2026-output.json",
        f"""{{
          "as_of": "{CANONICAL_AS_OF}",
          "reported_counts": {{
            "confirmed": {{"min": 112, "max": 112, "primary": 112, "primary_source_id": "{CANONICAL_SOURCE}"}},
            "suspected": {{"min": 906, "max": 906, "primary": 906, "primary_source_id": "{CANONICAL_SOURCE}"}},
            "deaths": {{"min": 223, "max": 223, "primary": 223, "primary_source_id": "{CANONICAL_SOURCE}"}}
          }},
          "analysis_dependency_audit": [
            {{"surface": "corridor_watchlist", "status": "ok", "inputs": {{"headline_confirmed": 112}}, "clock_basis": "source_publication"}}
          ]
        }}""",
    )
    _write_json(
        lovs / "data" / "bundibugyo-2026" / "manifest.json",
        f"""{{
          "entries": [
            {{"source_id": "{CANONICAL_SOURCE}"}}
          ]
        }}""",
    )

    source = "who-dg-remarks-bdbv-2026-05-25" if stale_confirmed else CANONICAL_SOURCE
    confirmed = 106 if stale_confirmed else 112
    _write_json(
        website / "app" / "bdbv-2026" / "_data" / "snapshots" / "2026-05-26.json",
        f"""{{
          "date": "2026-05-26",
          "asOf": "{CANONICAL_AS_OF}",
          "dateSemantics": {{
            "snapshotDate": "2026-05-26",
            "analyticAsOf": "{CANONICAL_AS_OF}"
          }},
          "reportedCounts": {_counts(source, confirmed)},
          "sources": [
            {{"id": "{source}"}}
          ],
          "analysisDependencyAudit": [
            {{"surface": "corridor_watchlist", "status": "ok", "inputs": {{"headline_confirmed": 112}}, "clockBasis": "source_publication"}}
          ]
        }}""",
    )
    _write(
        website / "app" / "bdbv-2026" / "_data" / "snapshots" / "index.ts",
        """export const AVAILABLE_SNAPSHOT_DATES = [
  /* SNAPSHOT_DATES_BEGIN, sync-bdbv-lovs.py manages this block */
  '2026-05-26',
  /* SNAPSHOT_DATES_END */
] as const;
""",
    )

    assets = {
        "deliverables/brief.pdf": "brief.pdf",
        "deliverables/public-health-dataset/lovs-public-health-dataset.xlsx": "lovs-public-health-dataset.xlsx",
        "deliverables/public-health-dataset/lovs-public-health-dataset.schema.json": "lovs-public-health-dataset.schema.json",
        "deliverables/public-health-dataset/lovs-public-health-dataset.manifest.json": "lovs-public-health-dataset.manifest.json",
        "brief/visuals/corridor_risk.svg": "visuals/corridor_risk.svg",
    }
    for lovs_rel, web_rel in assets.items():
        payload = f"asset:{web_rel}".encode()
        _write_bytes(lovs / lovs_rel, payload)
        _write_bytes(website / "public" / "bdbv-2026" / web_rel, payload)
    return lovs, website


class WebsiteBundleParityTests(unittest.TestCase):
    def test_clean_bundle_allows_route_date_to_differ_from_as_of(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lovs, website = _make_bundle(Path(tmp))
            result = check_website_bundle_parity(lovs, website)
        self.assertEqual(result["status"], "ok", result["findings"])
        self.assertEqual(result["latest_snapshot_date"], "2026-05-26")
        self.assertGreater(result["checked"]["source_refs"], 0)

    def test_stale_count_and_unknown_source_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lovs, website = _make_bundle(Path(tmp), stale_confirmed=True)
            result = check_website_bundle_parity(lovs, website)
        self.assertEqual(result["status"], "failed")
        joined = "\n".join(result["findings"])
        self.assertIn("reportedCounts.confirmed.primary", joined)
        self.assertIn("sources[] id not in canonical source manifest", joined)
        self.assertIn("who-dg-remarks-bdbv-2026-05-25", joined)


if __name__ == "__main__":
    unittest.main()
