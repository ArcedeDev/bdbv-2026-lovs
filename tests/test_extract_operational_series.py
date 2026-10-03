"""The dated operational-series extractor keeps the frozen extract's row shape."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import extract_operational_series as ex  # noqa: E402


def packet(label: str, data_as_of: str, ready: bool = True) -> dict:
    return {
        "sitrep_label": label,
        "published_at": "2026-10-02",
        "data_as_of": data_as_of,
        "ready_for_model_use": ready,
        "figures": {
            "headline": {"confirmed_total": 8376, "confirmed_deaths_total": 4042, "contact_followup_percent": 81.3,
                         "hospital_isolation_total": 869, "new_confirmed_today": 76},
            "laboratory_status": [{"province": "Total", "samples_collected_analyzed": 452, "positive": 76,
                                   "positivity_percent": 16.8, "pending_results": None}],
            "alert_management_24h": [{"province": "Ituri", "alerts_reported": 1008},
                                     {"province": "Total", "alerts_reported": 2441, "alerts_investigated": None,
                                      "investigation_rate_percent": None}],
            "patient_movement": {"patients_in_isolation_end_of_day": {"Tshopo": 12, "Ituri": 394}},
            "province_split": [{"province": "Ituri", "confirmed": 6342, "confirmed_deaths": 2918},
                               {"province": "Total", "confirmed": 8376, "confirmed_deaths": 4042}],
        },
    }


class ExtractOperationalSeries(unittest.TestCase):
    def test_row_has_exactly_the_frozen_extract_fields(self) -> None:
        frozen = json.loads((REPO / "data" / "operational-series-2026-09-01.json").read_text())
        self.assertEqual(set(frozen["rows"][0]), set(ex.row_from_packet(packet("140", "2026-10-01"))))

    def test_reads_totals_rows_and_keeps_missing_values_null(self) -> None:
        row = ex.row_from_packet(packet("140", "2026-10-01"))
        self.assertEqual("140", row["sitrep"])
        self.assertEqual(2441, row["alerts_reported"])
        self.assertEqual(452, row["samples_analyzed"])
        self.assertEqual({"Ituri": 394, "Tshopo": 12}, row["isolation_by_province"])
        self.assertEqual({"Ituri": {"confirmed": 6342, "confirmed_deaths": 2918}}, row["province_split"])
        self.assertIsNone(row["cumulative_recovered"])
        self.assertIsNone(row["alerts_investigated"])

    def test_orders_by_data_date_and_drops_unready_and_later_packets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name, p in {
                "sitrep-140.json": packet("140", "2026-10-01"),
                "sitrep-139.json": packet("139", "2026-09-30"),
                "sitrep-141.json": packet("141", "2026-10-02"),
                "sitrep-138.json": packet("138", "2026-09-29", ready=False),
            }.items():
                (Path(tmp) / name).write_text(json.dumps(p))
            rows = ex.extract(tmp, "2026-10-01")
        self.assertEqual(["139", "140"], [r["sitrep"] for r in rows])


if __name__ == "__main__":
    unittest.main()
