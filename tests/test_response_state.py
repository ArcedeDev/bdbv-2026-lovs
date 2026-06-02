# SPDX-License-Identifier: Apache-2.0
"""Tests for the 2026-06-02 response-state surfacing.

Covers the three layers landed by this change:

1. Loader (`lovs.insp_per_zone_loader.load_response_state`): ND-aware per-zone
   readers for the four INRB-UMIE response tables (contacts under follow-up,
   contacts seen, patients in care, hospital escapes), reusing `_read_long_csv`,
   with latest-non-ND-on-or-before-as_of semantics.
2. Assembler (`lovs.public_exports._response_state`): the `responseState` block
   that CONSUMES the national operational axis from `operational_status` (never
   recomputes it) and adds the ND-aware per-zone figures plus province roll-ups.
3. Contract (`lovs.snapshot_contract`): the permissive ND-aware projection and
   the care-census label guard.

The SitRep-17 coverage assertions (Nyankunde 0.315 / Mongbalu 0.207 /
Bunia 0.176) run against the real INRB-UMIE artifact when it is present on this
machine, and against an inline directory fixture that mirrors the exact ND
shape otherwise, so the arithmetic is pinned either way.

Frozen invariants are asserted last: the headline 328 confirmed / 49 confirmed
deaths and the 19-June `mode_b_hypotheses` calibration block are untouched by
this additive change.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import pathlib
import unittest
from datetime import date

import refresh_pipeline
from lovs import public_exports, snapshot_contract
from lovs.insp_per_zone_loader import (
    RESPONSE_METRICS,
    ResponseStateSnapshot,
    ZoneResponseMetrics,
    load_response_state,
)
from lovs.zone_alias_bridge import ZoneAliasBridge


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REAL_ARTIFACT = pathlib.Path("/tmp/build-0601-b4cafc9.tar.gz")
SITREP17_SOURCE_ID = "inrb-umie-ebola-drc-2026-build-2026-06-01-b4cafc9"
AS_OF = date(2026, 5, 31)


# ---------------------------------------------------------------------------
# Inline directory fixture: mirrors the real SitRep-17 ND shape for the zones
# the coverage assertions touch (Nyankunde via the Nyakunde canonical Nom,
# Mongbalu, Bunia) plus an explicit ND case (Katwa contacts_seen) and a real
# reported zero (Aru contacts_under_follow_up). The numbers match the validated
# real-artifact probe so the same assertions hold with or without the tarball.
# ---------------------------------------------------------------------------


def _write_long(dir_path: pathlib.Path, stem: str, column: str, rows: list[tuple[str, str, str]]) -> None:
    path = dir_path / "build" / "long" / f"{stem}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"nom,date,{column}\n"
    for nom, dt, value in rows:
        body += f"{nom},{dt},{value}\n"
    path.write_text(body, encoding="utf-8")


def _build_fixture(dir_path: pathlib.Path) -> None:
    # contacts under follow-up (cumulative_contacts_traced): latest non-ND wins.
    _write_long(
        dir_path,
        "insp_sitrep__cumulative_contacts_traced",
        "cumulative_contacts_traced",
        [
            ("Nyakunde", "2026-05-29", "700"),
            ("Nyakunde", "2026-05-30", "743"),
            ("Mongbalu", "2026-05-30", "222"),
            ("Bunia", "2026-05-30", "404"),
            ("Katwa", "2026-05-30", "221"),
            ("Aru", "2026-05-30", "0"),       # real reported zero, not ND
            ("Bambu", "2026-05-30", "345"),
        ],
    )
    # contacts seen: Nyakunde latest row (05-24) is ND, so the 05-22 value (234)
    # must win; Katwa is ND throughout (-> null); Aru/Bambu absent (-> null).
    _write_long(
        dir_path,
        "insp_sitrep__contacts_seen",
        "contacts_seen",
        [
            ("Nyakunde", "2026-05-22", "234"),
            ("Nyakunde", "2026-05-23", "ND"),
            ("Nyakunde", "2026-05-24", "ND"),
            ("Mongbalu", "2026-05-22", "46"),
            ("Bunia", "2026-05-22", "71"),
            ("Katwa", "2026-05-22", "ND"),
        ],
    )
    # patients in care (hospitalised).
    _write_long(
        dir_path,
        "insp_sitrep__hospitalised",
        "hospitalised",
        [
            ("Nyakunde", "2026-05-30", "44"),
            ("Mongbalu", "2026-05-30", "22"),
            ("Bunia", "2026-05-30", "23"),
            ("Aru", "2026-05-30", "5"),
            ("Bambu", "2026-05-30", "10"),
        ],
    )
    # hospital escapes.
    _write_long(
        dir_path,
        "insp_sitrep__hosp_escaped",
        "hosp_escaped",
        [
            ("Nyakunde", "2026-05-30", "2"),
            ("Mongbalu", "2026-05-30", "2"),
            ("Bunia", "2026-05-30", "1"),
            ("Aru", "2026-05-30", "0"),
        ],
    )
    # Upstream aliases so the raw `Nyankunde` spelling (if any) collapses; the
    # fixture already uses the canonical `Nyakunde`, this exercises the path.
    (dir_path / "data").mkdir(parents=True, exist_ok=True)
    (dir_path / "data" / "aliases.csv").write_text(
        "observed_name,canonical_nom,source_dataset,notes\n"
        "Nyankunde,Nyakunde,flowminder,Spelling variant\n",
        encoding="utf-8",
    )


def _load_fixture_snapshot(tmp: pathlib.Path) -> ResponseStateSnapshot:
    if REAL_ARTIFACT.exists():
        return load_response_state(REAL_ARTIFACT, AS_OF, source_id=SITREP17_SOURCE_ID)
    fixture = tmp / "fixture"
    _build_fixture(fixture)
    return load_response_state(fixture, AS_OF, source_id=SITREP17_SOURCE_ID)


def _block_from_snapshot(snap: ResponseStateSnapshot) -> dict:
    return {
        "as_of": snap.as_of.isoformat(),
        "source_id": snap.source_id,
        "method_basis": snap.method_basis,
        "by_lovs_zone": {
            zone_id: dataclasses.asdict(m) for zone_id, m in snap.by_lovs_zone.items()
        },
    }


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestResponseLoaderCoverage(unittest.TestCase):
    """SitRep-17 per-zone contact follow-up coverage (seen / under-follow-up)."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.snap = _load_fixture_snapshot(pathlib.Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _coverage(self, zone_id: str) -> float:
        zm = self.snap.by_lovs_zone[zone_id]
        return round(zm.contacts_seen / zm.contacts_under_follow_up, 4)

    def test_nyankunde_coverage_31_5_pct(self) -> None:
        zm = self.snap.by_lovs_zone["nyankunde"]
        self.assertEqual(zm.contacts_under_follow_up, 743)
        self.assertEqual(zm.contacts_seen, 234)
        self.assertEqual(self._coverage("nyankunde"), 0.3149)

    def test_mongbalu_coverage_20_7_pct(self) -> None:
        zm = self.snap.by_lovs_zone["mongbwalu"]
        self.assertEqual(zm.contacts_under_follow_up, 222)
        self.assertEqual(zm.contacts_seen, 46)
        self.assertEqual(self._coverage("mongbwalu"), 0.2072)

    def test_bunia_coverage_17_6_pct(self) -> None:
        zm = self.snap.by_lovs_zone["bunia"]
        self.assertEqual(zm.contacts_under_follow_up, 404)
        self.assertEqual(zm.contacts_seen, 71)
        self.assertEqual(self._coverage("bunia"), 0.1757)


class TestResponseLoaderNDAware(unittest.TestCase):
    """A zone the source marks ND is null, never zero, never backfilled."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.snap = _load_fixture_snapshot(pathlib.Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_nd_renders_null_not_zero(self) -> None:
        # Katwa carries a real contacts_under_follow_up but ND contacts_seen and
        # ND/absent care + escapes: those are null, never 0.
        zm = self.snap.by_lovs_zone["katwa"]
        self.assertEqual(zm.contacts_under_follow_up, 221)
        self.assertIsNone(zm.contacts_seen)
        self.assertIsNone(zm.patients_in_care)
        self.assertIsNone(zm.hospital_escapes)

    def test_real_reported_zero_is_zero_not_null(self) -> None:
        # Aru reported 0 contacts under follow-up: that is a measured zero, not
        # absence. The two must be distinguishable.
        zm = self.snap.by_lovs_zone["aru"]
        self.assertEqual(zm.contacts_under_follow_up, 0)
        self.assertIsNotNone(zm.contacts_under_follow_up)

    def test_trailing_nd_does_not_mask_earlier_value(self) -> None:
        # Nyankunde's latest contacts_seen ROW is ND (05-24); the latest non-ND
        # value (234 on 05-22) must be carried, not overwritten to null.
        self.assertEqual(self.snap.by_lovs_zone["nyankunde"].contacts_seen, 234)

    def test_absent_zone_is_all_null(self) -> None:
        # A bridged zone never appearing in any response table is fully null
        # (e.g. komanda / goma-cod for contacts_seen in the real artifact).
        bridge = ZoneAliasBridge.load_default()
        # Pick a zone guaranteed absent from the fixture tables.
        target = "komanda"
        self.assertIn(target, bridge.all_lovs_ids())
        zm = self.snap.by_lovs_zone[target]
        for metric in RESPONSE_METRICS:
            self.assertIsNone(zm.get(metric), f"{target}.{metric} should be null")


# ---------------------------------------------------------------------------
# Assembler: responseState block
# ---------------------------------------------------------------------------


def _operational_status_fixture() -> dict:
    """The landed SitRep-17 operational axis (under_investigation 116, in
    isolation 104, active total 220), shaped like the assembled block."""
    def sub(primary: int) -> dict:
        return {
            "primary": primary,
            "min": primary,
            "max": primary,
            "primary_source_id": "inrb-sitrep-017-2026-05-31",
            "conflicting_source_ids": ["inrb-sitrep-016-2026-05-30"],
        }

    return {
        "as_of": "2026-05-31",
        "basis": "point_prevalence_not_cumulative",
        "summable_into_confirmed": False,
        "note": "operational caseload",
        "suspected_under_investigation": sub(116),
        "suspected_in_isolation": sub(104),
        "active_suspected_total": sub(220),
    }


class TestResponseStateAssembler(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        snap = _load_fixture_snapshot(pathlib.Path(self._tmp.name))
        self.block = _block_from_snapshot(snap)
        self.op = _operational_status_fixture()
        source = {"response_state_block": self.block}
        self.rs = public_exports._response_state(source, self.op)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_national_axis_consumed_not_recomputed(self) -> None:
        national = self.rs["national"]
        # Provenance tag: the axis is consumed from operational_status.
        self.assertEqual(national["national_axis_source"], "operational_status")
        # Values are byte-equal to the operational_status sub-objects (no
        # recomputation, no re-derivation).
        self.assertEqual(
            national["suspected_under_investigation"],
            self.op["suspected_under_investigation"],
        )
        self.assertEqual(
            national["suspected_in_isolation"], self.op["suspected_in_isolation"]
        )
        self.assertEqual(
            national["active_suspected_total"], self.op["active_suspected_total"]
        )
        self.assertIs(national["summable_into_confirmed"], False)

    def test_national_axis_is_not_summed_or_derived_from_zones(self) -> None:
        # The national total (220) must equal the consumed axis exactly, never a
        # sum of any per-zone response figures. Perturb a per-zone count and
        # confirm the national axis does not move.
        moved_block = copy.deepcopy(self.block)
        moved_block["by_lovs_zone"]["bunia"]["contacts_under_follow_up"] = 999999
        rs2 = public_exports._response_state(
            {"response_state_block": moved_block}, self.op
        )
        self.assertEqual(
            rs2["national"]["active_suspected_total"]["primary"], 220
        )

    def test_per_zone_nd_is_null_in_assembled_block(self) -> None:
        katwa = self.rs["by_zone"]["katwa"]
        self.assertIsNone(katwa["contacts_seen"])
        self.assertIsNone(katwa["patients_in_care"])
        self.assertEqual(katwa["coverage_band"], "unknown")
        self.assertIsNone(katwa["contact_follow_up_coverage"])

    def test_per_zone_real_zero_preserved(self) -> None:
        aru = self.rs["by_zone"]["aru"]
        self.assertEqual(aru["contacts_under_follow_up"], 0)

    def test_per_zone_coverage_and_band(self) -> None:
        nyankunde = self.rs["by_zone"]["nyankunde"]
        self.assertEqual(nyankunde["contact_follow_up_coverage"], 0.3149)
        self.assertEqual(nyankunde["coverage_band"], "weak")

    def test_care_census_173_never_labelled_suspected(self) -> None:
        # The hard rule: the care/isolation census (patients_in_care) is never
        # relabelled "suspected" anywhere in the per-zone or province surfaces,
        # regardless of its value (123 here per-zone; 173 in a later SitRep).
        zone_blob = json.dumps(self.rs["by_zone"])
        prov_blob = json.dumps(self.rs["by_province"])
        self.assertNotIn("suspected", zone_blob)
        self.assertNotIn("suspected", prov_blob)
        # And the care census is carried under its own non-suspected key.
        self.assertIn("patients_in_care", self.rs["by_zone"]["nyankunde"])

    def test_province_rollup_is_aggregation_of_zones(self) -> None:
        # Ituri care census = sum of the non-null per-zone care figures
        # (Nyankunde 44 + Mongbalu 22 + Bunia 23 + Aru 5 + Bambu 10 = 104 in the
        # fixture; the real artifact rolls up to 123). The province figure is an
        # aggregation, never a per-zone value smeared across the province.
        ituri = self.rs["by_province"]["Ituri"]
        self.assertEqual(ituri["scope"], "province")
        per_zone_care = [
            self.rs["by_zone"][z]["patients_in_care"]
            for z in self.rs["by_zone"]
            if self.rs["by_zone"][z]["province"] == "Ituri"
            and self.rs["by_zone"][z]["patients_in_care"] is not None
        ]
        self.assertEqual(ituri["patients_in_care"], sum(per_zone_care))

    def test_all_nd_province_metric_is_null(self) -> None:
        # Nord-Kivu in the fixture: Katwa is the only Nord-Kivu zone and its
        # care is ND, so the province care census is null (not 0).
        nk = self.rs["by_province"].get("Nord-Kivu")
        self.assertIsNotNone(nk)
        self.assertIsNone(nk["patients_in_care"])

    def test_national_only_when_no_zone_data(self) -> None:
        # With operational_status but no response_state_block, the national axis
        # still surfaces and by_zone is omitted (graceful degradation).
        rs = public_exports._response_state({}, self.op)
        self.assertIn("national", rs)
        self.assertNotIn("by_zone", rs)

    def test_absent_when_nothing_present(self) -> None:
        self.assertIsNone(public_exports._response_state({}, None))


class TestResponseStatePublicSnapshot(unittest.TestCase):
    """End-to-end through `_public_snapshot`, including the sensitive-key guard."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        snap = _load_fixture_snapshot(pathlib.Path(self._tmp.name))
        self.source = copy.deepcopy(
            public_exports._read_json(public_exports.PUBLIC_EXPORT_SOURCE_PATH)
        )
        self.source["response_state_block"] = _block_from_snapshot(snap)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_response_state_present_and_clean(self) -> None:
        snapshot = public_exports._public_snapshot(self.source)
        self.assertIn("responseState", snapshot)
        # No sensitive model-internal keys leak via the new block.
        findings = public_exports.public_snapshot_findings(snapshot)
        self.assertEqual(findings, [])

    def test_response_state_block_is_in_source_allowlist(self) -> None:
        self.assertIn(
            "response_state_block", public_exports._PUBLIC_EXPORT_SOURCE_FIELDS
        )


# ---------------------------------------------------------------------------
# Contract: permissive ND-aware projection + label guard
# ---------------------------------------------------------------------------


class TestResponseStateContract(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        snap = _load_fixture_snapshot(pathlib.Path(self._tmp.name))
        self.block = _block_from_snapshot(snap)
        self.live = snapshot_contract.load_json(snapshot_contract.DEFAULT_SNAPSHOT_PATH)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_projection_nd_aware(self) -> None:
        snap = copy.deepcopy(self.live)
        snap["response_state_block"] = self.block
        contract = snapshot_contract.build_contract(snap)
        proj = contract["response_state_block"]
        self.assertIsNone(proj["by_lovs_zone"]["katwa"]["contacts_seen"])
        self.assertEqual(proj["by_lovs_zone"]["aru"]["contacts_under_follow_up"], 0)

    def test_suspected_label_guard(self) -> None:
        snap = copy.deepcopy(self.live)
        bad = copy.deepcopy(self.block)
        bad["by_lovs_zone"]["bunia"]["suspected"] = 5
        snap["response_state_block"] = bad
        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.build_contract(snap)

    def test_nd_string_rejected(self) -> None:
        snap = copy.deepcopy(self.live)
        bad = copy.deepcopy(self.block)
        bad["by_lovs_zone"]["bunia"]["contacts_seen"] = "ND"
        snap["response_state_block"] = bad
        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.build_contract(snap)


# ---------------------------------------------------------------------------
# Frozen invariants: this additive change must not perturb the headline counts
# or the 19-June calibration block.
# ---------------------------------------------------------------------------


class TestFrozenInvariants(unittest.TestCase):
    def test_headline_328_49_unchanged(self) -> None:
        live = snapshot_contract.load_json(snapshot_contract.DEFAULT_SNAPSHOT_PATH)
        self.assertEqual(live["reported_counts"]["confirmed"]["primary"], 328)
        self.assertEqual(live["reported_deaths"]["confirmed"]["primary"], 49)

    def test_existing_contract_unchanged_without_response_block(self) -> None:
        # The live snapshot (no response_state_block) must still produce exactly
        # the pinned contract: the additive projection is inert when absent.
        live = snapshot_contract.load_json(snapshot_contract.DEFAULT_SNAPSHOT_PATH)
        pinned = snapshot_contract.load_json(snapshot_contract.DEFAULT_CONTRACT_PATH)
        self.assertNotIn("response_state_block", pinned)
        self.assertEqual(snapshot_contract.build_contract(live), pinned)

    def test_19_june_mode_b_hypotheses_byte_identical_through_regen(self) -> None:
        # The 19-June calibration carry-forward is byte-identical across repeated
        # regeneration and is not touched by the response-state surfacing (which
        # never imports or mutates the calibration path).
        first = refresh_pipeline.carry_forward_calibration("2026-06-18T23:59:59Z")
        second = refresh_pipeline.carry_forward_calibration("2026-06-18T23:59:59Z")
        self.assertEqual(first["resolves_at"], "2026-06-19T23:59:59Z")
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )
        # The May-20 pinned sub-block is invariant within it.
        may20 = [
            p for p in first["mode_b_hypotheses"] if p.get("pinned_at") == "2026-05-20"
        ]
        self.assertEqual(len(may20), 4)


if __name__ == "__main__":
    unittest.main()
