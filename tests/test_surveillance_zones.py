# SPDX-License-Identifier: Apache-2.0
"""Surveillance overlay: suspected-only zones off the reconciled model (2026-06-05).

A zone carrying suspected cases on the RETIRED per-zone cumulative-suspected tier
(national-only since 2026-06-02), with no laboratory-confirmed cases and no
LOVS-bridge mapping (Jiba), is emitted as a SurveillanceZone. It is NEVER reconciled
and NEVER summed into any national. This gate pins that contract end to end
(loader + assembler), independent of the live INRB-UMIE artifact.
"""
from __future__ import annotations

import pathlib
from datetime import date

from lovs import insp_block_assembler
from lovs.insp_per_zone_loader import load_per_zone_snapshot


def _write(dir_path: pathlib.Path, stem: str, metric: str, rows: list[tuple[str, str, int]]) -> None:
    path = dir_path / "build" / "long" / f"{stem}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "nom,date," + metric + "\n"
    for nom, dt, value in rows:
        body += f"{nom},{dt},{value}\n"
    path.write_text(body)


def _write_national(dir_path: pathlib.Path, stem: str, metric: str, value: int) -> None:
    path = dir_path / "build" / "long" / f"{stem}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "nom,date," + metric + "\n"
    for nom in ("ZoneA", "Bunia"):
        body += f"{nom},2026-05-26,{value}\n"
    path.write_text(body)


def _aliases(dir_path: pathlib.Path) -> None:
    (dir_path / "data").mkdir(parents=True, exist_ok=True)
    (dir_path / "data" / "aliases.csv").write_text(
        "observed_name,canonical_nom,source_dataset,notes\n"
    )


def _fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """Fixture with one suspected-only unmapped zone (Jiba), one mapped zone that ALSO
    carries suspected (Bunia), and one unmapped zone that carries confirmed (ConfirmedExtra).
    Only Jiba is a surveillance zone."""
    d = tmp_path / "surv"
    _write(
        d,
        "insp_sitrep__cumulative_confirmed_cases",
        "cumulative_confirmed_cases",
        [
            ("Bunia", "2026-05-26", 36),
            ("ConfirmedExtra", "2026-05-26", 5),  # unmapped but has confirmed -> NOT surveillance
        ],
    )
    _write(
        d,
        "insp_sitrep__cumulative_confirmed_deaths",
        "cumulative_confirmed_deaths",
        [("Bunia", "2026-05-26", 2)],
    )
    _write(
        d,
        "insp_sitrep__cumulative_suspected_cases",
        "cumulative_suspected_cases",
        [
            ("Jiba", "2026-05-26", 2),            # suspected-only, unmapped -> surveillance
            ("Bunia", "2026-05-26", 7),           # mapped into the bridge -> NOT surveillance
            ("ConfirmedExtra", "2026-05-26", 3),  # carries confirmed -> NOT surveillance
        ],
    )
    _write_national(d, "insp_sitrep__national_cumulative_confirmed_cases", "national_cumulative_confirmed_cases", 109)
    _write_national(d, "insp_sitrep__national_cumulative_confirmed_deaths", "national_cumulative_confirmed_deaths", 16)
    _aliases(d)
    return d


def test_loader_emits_suspected_only_unmapped_zone(tmp_path: pathlib.Path) -> None:
    snap = load_per_zone_snapshot(_fixture(tmp_path), date(2026, 5, 26))
    names = {sz.inrb_nom for sz in snap.surveillance_zones}
    assert names == {"Jiba"}
    jiba = next(sz for sz in snap.surveillance_zones if sz.inrb_nom == "Jiba")
    assert jiba.suspected == 2
    assert jiba.confirmed == 0


def test_surveillance_excludes_mapped_and_confirmed_zones(tmp_path: pathlib.Path) -> None:
    snap = load_per_zone_snapshot(_fixture(tmp_path), date(2026, 5, 26))
    names = {sz.inrb_nom for sz in snap.surveillance_zones}
    assert "Bunia" not in names           # mapped into the LOVS bridge
    assert "ConfirmedExtra" not in names  # carries confirmed cases


def test_surveillance_never_enters_reconciliation(tmp_path: pathlib.Path) -> None:
    snap = load_per_zone_snapshot(_fixture(tmp_path), date(2026, 5, 26))
    # Only the laboratory-confirmed metrics are reconciled; the suspected tier stays out.
    assert set(snap.unallocated_residual) == {"confirmed", "confirmed_deaths"}
    assert snap.national.confirmed == 109


def test_assembler_serializes_surveillance_overlay(tmp_path: pathlib.Path) -> None:
    res = insp_block_assembler.assemble_insp_artifacts(
        _fixture(tmp_path), date(2026, 5, 26), source_id="test-src"
    )
    overlay = res["surveillance_zones"]
    assert overlay is not None
    assert [z["zone_id"] for z in overlay["zones"]] == ["jiba"]
    assert overlay["zones"][0]["suspected"] == 2
    assert overlay["zones"][0]["confirmed"] == 0
    # The basis caveat names the retired tier + the never-summed-into-national contract.
    assert "retired" in overlay["basis"].lower()
    assert "national" in overlay["basis"].lower()
    # The reconciled block is untouched: its national confirmed is the same 109.
    assert res["insp_per_zone_block"]["national_at_data_date"]["confirmed"] == 109


def test_assembler_emits_none_when_no_surveillance_zone(tmp_path: pathlib.Path) -> None:
    d = tmp_path / "clean"
    _write(d, "insp_sitrep__cumulative_confirmed_cases", "cumulative_confirmed_cases", [("Bunia", "2026-05-26", 36)])
    _write(d, "insp_sitrep__cumulative_confirmed_deaths", "cumulative_confirmed_deaths", [("Bunia", "2026-05-26", 2)])
    _write_national(d, "insp_sitrep__national_cumulative_confirmed_cases", "national_cumulative_confirmed_cases", 109)
    _write_national(d, "insp_sitrep__national_cumulative_confirmed_deaths", "national_cumulative_confirmed_deaths", 16)
    _aliases(d)
    res = insp_block_assembler.assemble_insp_artifacts(d, date(2026, 5, 26), source_id="test-src")
    assert res["surveillance_zones"] is None
