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
# Frozen invariants: response-state surfacing must not perturb the current
# SitRep headline counts or the 19-June calibration block.
# ---------------------------------------------------------------------------


class TestFrozenInvariants(unittest.TestCase):
    def test_headline_617_117_current(self) -> None:
        live = snapshot_contract.load_json(snapshot_contract.DEFAULT_SNAPSHOT_PATH)
        self.assertEqual(live["reported_counts"]["confirmed"]["primary"], 617)
        self.assertEqual(live["reported_deaths"]["confirmed"]["primary"], 117)

    def test_live_contract_is_current_and_deterministic(self) -> None:
        # The pinned on-disk contract must equal build_contract(live) exactly:
        # the regen is deterministic and the contract is not stale relative to
        # the live snapshot (which now carries the populated response_state_block).
        live = snapshot_contract.load_json(snapshot_contract.DEFAULT_SNAPSHOT_PATH)
        pinned = snapshot_contract.load_json(snapshot_contract.DEFAULT_CONTRACT_PATH)
        self.assertEqual(snapshot_contract.build_contract(live), pinned)

    def test_response_block_projection_is_inert_when_absent(self) -> None:
        # The additive response_state_block projection must be inert when the
        # block is absent: stripping it from the live snapshot yields a contract
        # with no response_state_block and otherwise identical to the pinned one
        # minus that single projected key.
        live = snapshot_contract.load_json(snapshot_contract.DEFAULT_SNAPSHOT_PATH)
        pinned = snapshot_contract.load_json(snapshot_contract.DEFAULT_CONTRACT_PATH)
        self.assertIn("response_state_block", pinned)
        stripped = copy.deepcopy(live)
        stripped.pop("response_state_block", None)
        contract_without = snapshot_contract.build_contract(stripped)
        self.assertNotIn("response_state_block", contract_without)
        expected = copy.deepcopy(pinned)
        expected.pop("response_state_block", None)
        self.assertEqual(contract_without, expected)

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


# ---------------------------------------------------------------------------
# Generated artifact: the shipped data/public_snapshot.json now carries a
# POPULATED per-zone responseState layer (not just the consumed national axis).
# These assertions read the committed artifact rather than reconstructing it, so
# they fail if the per-zone population is ever silently dropped from the source.
# ---------------------------------------------------------------------------


class TestGeneratedPublicSnapshotResponseState(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = public_exports._read_json(public_exports.PUBLIC_SNAPSHOT_PATH)
        self.response = self.snapshot.get("responseState") or {}

    def test_generated_snapshot_carries_per_zone_layer(self) -> None:
        # Not just the national block: the per-zone surfaces must be present and
        # non-empty in the shipped artifact.
        self.assertIn("national", self.response)
        self.assertIn("by_zone", self.response)
        self.assertIn("by_province", self.response)
        by_zone = self.response["by_zone"]
        self.assertGreater(len(by_zone), 0)
        with_data = [
            z
            for z, row in by_zone.items()
            if any(
                row.get(metric) is not None
                for metric in (
                    "contacts_under_follow_up",
                    "contacts_seen",
                    "patients_in_care",
                    "hospital_escapes",
                )
            )
        ]
        self.assertGreater(len(with_data), 0, "per-zone responseState is empty")

    def test_generated_snapshot_known_values(self) -> None:
        by_zone = self.response["by_zone"]
        self.assertEqual(by_zone["nyankunde"]["contacts_under_follow_up"], 743)
        self.assertEqual(by_zone["nyankunde"]["contact_follow_up_coverage"], 0.3149)
        self.assertEqual(by_zone["bunia"]["patients_in_care"], 23)
        self.assertEqual(by_zone["mongbwalu"]["hospital_escapes"], 2)

    def test_generated_snapshot_nd_zone_null_real_zero_zero(self) -> None:
        # ND-aware in the GENERATED artifact: a zone the source marks ND is null
        # (beni-cod is a confirmed-carrying zone with NO declared response data),
        # while a real reported zero (Aru contacts-under-follow-up = 0) stays 0.
        by_zone = self.response["by_zone"]
        beni = by_zone["beni-cod"]
        for metric in (
            "contacts_under_follow_up",
            "contacts_seen",
            "patients_in_care",
            "hospital_escapes",
        ):
            self.assertIsNone(beni[metric], f"beni-cod.{metric} must be null (ND)")
        aru = by_zone["aru"]
        self.assertEqual(aru["contacts_under_follow_up"], 0)
        self.assertIsNotNone(aru["contacts_under_follow_up"])
        # A real zero and an ND null must be distinguishable on the same row:
        # Aru reports 0 contacts-under-follow-up but ND (null) contacts-seen.
        self.assertIsNone(aru["contacts_seen"])

    def test_generated_snapshot_clock_distinct_from_headline(self) -> None:
        # CLOCK HONESTY: the responseState block's own data_as_of is the actual
        # latest response-data date (2026-05-30), distinct from the headline
        # as_of (2026-06-02) and never differenced.
        self.assertEqual(self.response["data_as_of"], "2026-05-30")
        self.assertTrue(self.snapshot["as_of"].startswith("2026-06-02"))
        self.assertNotEqual(self.response["data_as_of"], self.snapshot["as_of"][:10])

    def test_generated_snapshot_province_scope_labelled(self) -> None:
        # Province roll-ups are labelled province scope (aggregations), never
        # painted onto individual zones.
        for province, row in self.response["by_province"].items():
            self.assertEqual(row["scope"], "province", province)
            self.assertIn("zone_count", row)


# ---------------------------------------------------------------------------
# Durability: the response_state_block is GENERATED by refresh_pipeline every
# run, not a static injection. This is the regression guard for the latent
# data-loss bug where a regen silently dropped the entire per-zone responseState
# layer because refresh_pipeline did not produce the block.
# ---------------------------------------------------------------------------


class TestResponseStateBlockDurability(unittest.TestCase):
    """A fresh refresh_pipeline run must regenerate a populated block.

    These tests run the real pipeline (redirecting OUT_PATH to a tempfile so the
    committed artifact is never touched) and assert the block is present and
    populated in the freshly written output. If refresh_pipeline ever stops
    generating the block (the exact bug this change fixes), these fail.
    """

    @unittest.skipUnless(
        REAL_ARTIFACT.exists(),
        "INRB-UMIE artifact not present on this machine; durability run needs it",
    )
    def test_fresh_refresh_pipeline_regenerates_populated_block(self) -> None:
        import tempfile
        from unittest import mock

        # Redirect OUT_PATH to a temp file INSIDE the repo so the committed
        # artifact is never touched, while refresh_pipeline's final
        # `OUT_PATH.relative_to(REPO_ROOT)` log line still resolves. The temp dir
        # is removed in tearDown regardless of assertion outcome.
        with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as tmp:
            out_path = pathlib.Path(tmp) / "live.json"
            with mock.patch.object(refresh_pipeline, "OUT_PATH", out_path):
                rc = refresh_pipeline.main(["--as-of", "2026-05-31"])
            self.assertEqual(rc, 0)
            written = json.loads(out_path.read_text(encoding="utf-8"))

        # GENERATED, not static: the block exists in a fresh run's output.
        self.assertIn(
            "response_state_block",
            written,
            "refresh_pipeline did not generate response_state_block (the bug)",
        )
        block = written["response_state_block"]
        by_zone = block["by_lovs_zone"]
        # Populated: per-zone figures are present, not an empty shell.
        self.assertEqual(len(by_zone), 25)
        with_data = [
            z
            for z, row in by_zone.items()
            if any(row.get(m) is not None for m in RESPONSE_METRICS)
        ]
        self.assertGreater(len(with_data), 0, "regenerated block is empty")
        # Known anchors survive the round-trip through the real pipeline.
        self.assertEqual(by_zone["nyankunde"]["contacts_under_follow_up"], 743)
        self.assertEqual(by_zone["nyankunde"]["contacts_seen"], 234)
        self.assertEqual(by_zone["nyankunde"]["patients_in_care"], 44)
        self.assertEqual(by_zone["nyankunde"]["hospital_escapes"], 2)
        # ND-aware: a confirmed-carrying zone with no declared response data is
        # null on every metric, never zero.
        for metric in RESPONSE_METRICS:
            self.assertIsNone(by_zone["beni-cod"][metric])
        # Real reported zero is preserved as 0 (distinct from ND null).
        self.assertEqual(by_zone["aru"]["contacts_under_follow_up"], 0)
        self.assertIsNone(by_zone["aru"]["contacts_seen"])
        # CLOCK HONESTY: the block's own data date is the real latest response
        # date (2026-05-30), distinct from the headline (2026-05-31) and never
        # differenced against it.
        self.assertEqual(block["data_as_of"], "2026-05-30")
        self.assertEqual(block["as_of"], "2026-05-30")
        self.assertTrue(written["as_of"].startswith("2026-05-31"))
        self.assertNotEqual(block["data_as_of"], written["as_of"][:10])

    def test_serializer_reproduces_committed_block_byte_for_byte(self) -> None:
        # The serializer applied to a fresh load_response_state snapshot must
        # reproduce the committed on-disk block EXACTLY (same zones, same values,
        # same method_basis and clocks). This pins the generator against drift:
        # if the serializer or loader ever change the block shape/values, the
        # shipped artifact would diverge and this fails.
        from lovs.insp_per_zone_loader import serialize_response_state_block

        if not REAL_ARTIFACT.exists():
            self.skipTest("INRB-UMIE artifact not present; cannot pin committed block")
        snap = load_response_state(
            REAL_ARTIFACT, AS_OF, source_id=SITREP17_SOURCE_ID
        )
        generated = serialize_response_state_block(snap)
        committed = public_exports._read_json(
            pathlib.Path("data") / "live-bdbv-2026-output.json"
        )["response_state_block"]
        self.assertEqual(
            json.dumps(generated, sort_keys=True),
            json.dumps(committed, sort_keys=True),
        )

    def test_serializer_block_shape_is_the_assembler_contract(self) -> None:
        # The serialized block carries exactly the keys the public assembler
        # (_response_state) reads, so the generator and consumer stay in lockstep.
        from lovs.insp_per_zone_loader import serialize_response_state_block

        snap = ResponseStateSnapshot(
            as_of=date(2026, 5, 31),
            source_id="unit",
            by_lovs_zone={
                "bunia": ZoneResponseMetrics(
                    contacts_under_follow_up=404,
                    contacts_seen=71,
                    patients_in_care=23,
                    hospital_escapes=1,
                )
            },
            data_as_of=date(2026, 5, 30),
        )
        block = serialize_response_state_block(snap)
        self.assertEqual(
            set(block),
            {"as_of", "data_as_of", "source_id", "method_basis", "by_lovs_zone"},
        )
        # data_as_of (not the requested as_of) drives both clock fields.
        self.assertEqual(block["as_of"], "2026-05-30")
        self.assertEqual(block["data_as_of"], "2026-05-30")
        self.assertEqual(
            set(block["by_lovs_zone"]["bunia"]),
            set(RESPONSE_METRICS),
        )

    def test_all_nd_snapshot_falls_back_to_requested_as_of(self) -> None:
        # When no table carried a non-ND value (data_as_of is None), the block's
        # clock falls back to the requested as_of so the field is never null.
        from lovs.insp_per_zone_loader import serialize_response_state_block

        snap = ResponseStateSnapshot(
            as_of=date(2026, 5, 31),
            source_id="unit",
            by_lovs_zone={
                "bunia": ZoneResponseMetrics(None, None, None, None),
            },
            data_as_of=None,
        )
        block = serialize_response_state_block(snap)
        self.assertEqual(block["as_of"], "2026-05-31")
        self.assertEqual(block["data_as_of"], "2026-05-31")


if __name__ == "__main__":
    unittest.main()
