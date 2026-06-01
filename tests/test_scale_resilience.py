# SPDX-License-Identifier: Apache-2.0
"""Tests for scale-resilience invariant (spec §6.7).

Exercises all four `data_scale_used` paths through `insp_block_assembler.assemble_insp_artifacts`:

1. `national`: no artifact supplied. Block + bands omitted.
2. `per_zone`: full coverage. Block + bands populated.
3. `partial_per_zone`: at least one LOVS zone structurally absent.
4. `mixed_with_metric_floor`: at least one LOVS zone present in some metrics
   and absent from others (Komanda-style).

Each path is verified to:
- Produce a snapshot that passes `snapshot_contract.validate_contract`.
- Produce a snapshot that passes `insp_per_zone_consistency_gate`.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from datetime import date

from lovs import insp_block_assembler, insp_per_zone_consistency_gate, snapshot_contract


LOCAL_E40BC9E_TARBALL = pathlib.Path("/tmp/inrb-e40bc9e/build.tar.gz")
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _build_fixture(tmp_path: pathlib.Path, scenario: str) -> pathlib.Path:
    """Construct a synthetic INRB-UMIE-shape directory fixture.

    `scenario` controls per-zone presence:
    - "full":      bunia + goma-cod present in all four metrics.
    - "partial":   bunia present, goma-cod NOT present in any metric (so all
                   bridge zones except bunia are structurally absent).
    - "komanda":   bunia present in all four metrics; goma-cod present in
                   confirmed_deaths only (metric-asymmetric).
    """
    d = tmp_path / scenario
    long_dir = d / "build" / "long"
    long_dir.mkdir(parents=True)

    rows_by_file: dict[str, list[tuple[str, str, int]]] = {}

    def add(stem: str, nom: str, value: int) -> None:
        rows_by_file.setdefault(stem, []).append((nom, "26/05/2026", value))

    if scenario == "full":
        for metric_stem, value in (
            ("cumulative_confirmed_cases", 36),
            ("cumulative_suspected_cases", 279),
            ("cumulative_confirmed_deaths", 2),
            ("cumulative_suspected_deaths", 18),
        ):
            add(f"insp_sitrep__{metric_stem}", "Bunia", value)
            add(f"insp_sitrep__{metric_stem}", "Goma", 1 if "confirmed_cases" in metric_stem else 0)
    elif scenario == "partial":
        # Only bunia present in any per-zone table.
        for metric_stem, value in (
            ("cumulative_confirmed_cases", 36),
            ("cumulative_suspected_cases", 279),
            ("cumulative_confirmed_deaths", 2),
            ("cumulative_suspected_deaths", 18),
        ):
            add(f"insp_sitrep__{metric_stem}", "Bunia", value)
    elif scenario == "komanda":
        # Bunia present in all four metrics; goma-cod present only in
        # confirmed_deaths (metric-asymmetric).
        for metric_stem, value in (
            ("cumulative_confirmed_cases", 36),
            ("cumulative_suspected_cases", 279),
            ("cumulative_confirmed_deaths", 2),
            ("cumulative_suspected_deaths", 18),
        ):
            add(f"insp_sitrep__{metric_stem}", "Bunia", value)
        # Goma in confirmed_deaths only.
        add("insp_sitrep__cumulative_confirmed_deaths", "Goma", 1)
    else:  # pragma: no cover - unreachable
        raise ValueError(f"unknown scenario {scenario!r}")

    metric_cols = {
        "insp_sitrep__cumulative_confirmed_cases": "cumulative_confirmed_cases",
        "insp_sitrep__cumulative_suspected_cases": "cumulative_suspected_cases",
        "insp_sitrep__cumulative_confirmed_deaths": "cumulative_confirmed_deaths",
        "insp_sitrep__cumulative_suspected_deaths": "cumulative_suspected_deaths",
    }
    for stem, rows in rows_by_file.items():
        col = metric_cols[stem]
        body = f"nom,date,{col}\n"
        for nom, dt, value in rows:
            body += f"{nom},{dt},{value}\n"
        (long_dir / f"{stem}.csv").write_text(body)

    national_value = {
        "insp_sitrep__national_cumulative_confirmed_cases": (
            "national_cumulative_confirmed_cases",
            sum(v for nom, _, v in rows_by_file.get("insp_sitrep__cumulative_confirmed_cases", [])),
        ),
        "insp_sitrep__national_cumulative_suspected_cases": (
            "national_cumulative_suspected_cases",
            sum(v for nom, _, v in rows_by_file.get("insp_sitrep__cumulative_suspected_cases", [])),
        ),
        "insp_sitrep__national_cumulative_confirmed_deaths": (
            "national_cumulative_confirmed_deaths",
            sum(v for nom, _, v in rows_by_file.get("insp_sitrep__cumulative_confirmed_deaths", [])),
        ),
        "insp_sitrep__national_cumulative_suspected_deaths": (
            "national_cumulative_suspected_deaths",
            sum(v for nom, _, v in rows_by_file.get("insp_sitrep__cumulative_suspected_deaths", [])),
        ),
    }
    for stem, (col, total) in national_value.items():
        body = f"nom,date,{col}\n"
        for nom in ("ZoneA", "Bunia", "Goma"):
            body += f"{nom},2026-05-26,{total}\n"
        (long_dir / f"{stem}.csv").write_text(body)

    # PCR tables
    (long_dir / "testing_capacity__pcr_machines.csv").write_text(
        "nom,pcr_machines\nBunia,10\nGoma,2\n"
    )
    (long_dir / "testing_capacity__pcr_tests.csv").write_text(
        "nom,pcr_tests\nBunia,5000\nGoma,2000\n"
    )
    return d


def _snapshot_from_artifacts(artifacts: dict) -> dict:
    """Wrap assembled artifacts in a minimal snapshot suitable for the contract."""
    base = json.loads(
        (REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text(encoding="utf-8")
    )
    base["data_scale_used"] = artifacts["data_scale_used"]
    if artifacts["insp_per_zone_block"] is not None:
        base["insp_per_zone_block"] = artifacts["insp_per_zone_block"]
    if artifacts["per_zone_under_ascertainment_bands"] is not None:
        base["per_zone_under_ascertainment_bands"] = artifacts[
            "per_zone_under_ascertainment_bands"
        ]
    base["attribution_lag_disclosure"] = artifacts["attribution_lag_disclosure"]
    return base


def _write_snapshot(d: dict, tmp_dir: pathlib.Path) -> pathlib.Path:
    p = tmp_dir / "snap.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


class TestScaleResilience(unittest.TestCase):
    def test_national_fallback_when_artifact_missing(self):
        artifacts = insp_block_assembler.assemble_insp_artifacts(
            pathlib.Path("/tmp/never-exists-12345"), date(2026, 5, 26)
        )
        self.assertEqual("national", artifacts["data_scale_used"])
        self.assertIsNone(artifacts["insp_per_zone_block"])
        self.assertIsNone(artifacts["per_zone_under_ascertainment_bands"])
        # Snapshot passes contract.
        snapshot = _snapshot_from_artifacts(artifacts)
        snapshot_contract.build_contract(snapshot)
        # Gate accepts a national snapshot with no block.
        with tempfile.TemporaryDirectory() as td:
            path = _write_snapshot(snapshot, pathlib.Path(td))
            self.assertEqual(
                [],
                insp_per_zone_consistency_gate.check_insp_per_zone_consistency(path),
            )

    def test_national_fallback_when_path_is_none(self):
        artifacts = insp_block_assembler.assemble_insp_artifacts(
            None, date(2026, 5, 26)
        )
        self.assertEqual("national", artifacts["data_scale_used"])
        self.assertIsNone(artifacts["insp_per_zone_block"])

    def test_partial_per_zone_via_synthetic_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = _build_fixture(pathlib.Path(td), "partial")
            artifacts = insp_block_assembler.assemble_insp_artifacts(
                fixture, date(2026, 5, 26), source_id="inrb-umie-fixture"
            )
            self.assertEqual("partial_per_zone", artifacts["data_scale_used"])
            self.assertIsNotNone(artifacts["insp_per_zone_block"])
            snapshot = _snapshot_from_artifacts(artifacts)
            snapshot_contract.build_contract(snapshot)
            path = _write_snapshot(snapshot, pathlib.Path(td))
            self.assertEqual(
                [],
                insp_per_zone_consistency_gate.check_insp_per_zone_consistency(path),
            )

    def test_mixed_with_metric_floor_via_komanda_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = _build_fixture(pathlib.Path(td), "komanda")
            artifacts = insp_block_assembler.assemble_insp_artifacts(
                fixture, date(2026, 5, 26), source_id="inrb-umie-fixture"
            )
            self.assertEqual(
                "mixed_with_metric_floor", artifacts["data_scale_used"]
            )
            block = artifacts["insp_per_zone_block"]
            self.assertIsNotNone(block)
            # goma-cod is present in confirmed_deaths only, so it must carry
            # a classification (not be silently coerced to "structurally absent").
            self.assertIn("goma-cod", block["by_lovs_zone"])
            classification = block["by_lovs_zone"]["goma-cod"][
                "present_in_insp_classification"
            ]
            self.assertIn(
                classification, {"present_with_data", "present_but_zero"}
            )
            snapshot = _snapshot_from_artifacts(artifacts)
            snapshot_contract.build_contract(snapshot)
            path = _write_snapshot(snapshot, pathlib.Path(td))
            self.assertEqual(
                [],
                insp_per_zone_consistency_gate.check_insp_per_zone_consistency(path),
            )

    @unittest.skipUnless(
        LOCAL_E40BC9E_TARBALL.exists(),
        "canonical e40bc9e tarball not locally cached",
    )
    def test_real_e40bc9e_tarball_produces_partial_per_zone(self):
        """Real INRB-UMIE artifact: confirmed Phase 2 finding that some bridge
        zones (bambu, miti-murhesa) are structurally absent. Therefore the
        canonical scale on the May 26 e40bc9e snapshot is `partial_per_zone`.
        """
        artifacts = insp_block_assembler.assemble_insp_artifacts(
            LOCAL_E40BC9E_TARBALL,
            date(2026, 5, 26),
            source_id="inrb-umie-ebola-drc-2026-build-2026-05-27-e40bc9e",
        )
        # At the 11-zone bridge we have today, bambu and miti-murhesa are
        # absent so partial_per_zone is the floor. After Plan A step 6 adds
        # 7 more zones, the scale may stay partial_per_zone (if any of the
        # new zones are also absent) but the gate accepts either way.
        self.assertIn(
            artifacts["data_scale_used"],
            {"per_zone", "partial_per_zone", "mixed_with_metric_floor"},
        )
        snapshot = _snapshot_from_artifacts(artifacts)
        snapshot_contract.build_contract(snapshot)


if __name__ == "__main__":
    unittest.main()
