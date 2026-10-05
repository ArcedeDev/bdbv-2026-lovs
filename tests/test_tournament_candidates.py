# SPDX-License-Identifier: Apache-2.0
"""Tests for the Round 001 candidate builder.

These run on the reviewed SitRep promotions through SitRep 140 (data day
2026-10-01) and a frozen copy of the SitRep 140 release envelope, so they do not
drift as later editions land: the builder never opens a promotion dated after the
source data day.
"""
from __future__ import annotations

import copy
import json
import math
import pathlib
import tempfile
import unittest
from unittest import mock

from lovs import model_tournament as T
from lovs import tournament_candidates as C

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The SitRep 140 release envelope, frozen as a literal. The real check
# (model_tournament._verified_source_release) needs the restricted source bytes,
# which public CI does not hold; see TestEndToEndWithArchive for the unpatched run.
_SR140_RELEASE = {
    "edition": 140,
    "publication_state": "published",
    "readiness": "reviewed",
    "release_id": "bdbv-sr140-2026-10-01-4a2e79f665110ea4",
    "review_receipt": {
        "evidence_chain_id": "ec:lovs:data:insp-sitrep-140-visual-promotion:2026-10-01",
        "reviewed_at": "2026-10-03",
        "reviewed_by": "F. Moore (user-confirmed source review)",
    },
    "schema_version": "bdbv-release/v1",
    "snapshot_date": "2026-10-01",
    "source_receipt": {
        "byte_length": 672333,
        "id_resolution_note": None,
        "media_id": 25650,
        "page_count": 9,
        "post_id": 25649,
        "published_at": "2026-10-02T22:33:43Z",
        "sha256": "4a2e79f665110ea425ff38645c37e5e2cea3f4167137e8c3e4e5fdd0bbf36aa6",
        "source_id": "inrb-sitrep-140-2026-10-01",
        "source_url": "https://insp.cd/wp-content/uploads/2026/10/SitRep_MVEBDB_140_01_10_2026.pdf",
    },
}
CUTOFF = "2026-10-04T12:00:00Z"
DISTANCE_ID = "rank.distance_only_frontier_30d"
CORRIDOR_ID = "corridor.lovs_next_zone_v0_3_0"


def _fixture_release(source_snapshot: dict) -> dict:
    release = copy.deepcopy(source_snapshot.get("release"))
    if release != _SR140_RELEASE:
        raise T.TournamentConfigError("source snapshot release does not match a byte-verified reviewed promotion")
    return release


def proposed_registry(*, promote: bool = True, demote: bool = True) -> dict:
    """The committed registry with each Round 001 proposal set explicitly on or off.

    Both models are forced into the requested state rather than read from the
    committed file, so these tests hold whether or not either registry commit lands.
    """
    doc = json.loads(T.REGISTRY_PATH.read_text(encoding="utf-8"))
    for model in doc["models"]:
        if model["model_id"] == DISTANCE_ID:
            if promote:
                model.update({
                    "version": "v1",
                    "readiness": "eligible_when_round_freezes",
                    "scoring_eligible": True,
                    "implementation_module": "lovs.distance_only_frontier_30d",
                })
            else:
                model.update({"version": "planned-v1", "readiness": "planned_review_required",
                              "scoring_eligible": False})
        if model["model_id"] == CORRIDOR_ID:
            if demote:
                model.update({"readiness": "not_eligible", "scoring_eligible": False})
            else:
                model.update({"readiness": "eligible_when_round_freezes", "scoring_eligible": True,
                              "scoring_transform": "interval_midpoint",
                              "implementation_module": "lovs.lovs_next_zone"})
    return doc


class CandidateFixture(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(T, "_verified_source_release", side_effect=_fixture_release)
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.rounds_dir = self.tmp / "rounds"
        self.snapshot_path = self.write("source-snapshot.json", {
            "as_of": "2026-10-01T23:59:59Z",
            "data_as_of": "2026-10-01",
            "outbreak_id": "bdbv-uga-cod-2026",
            "release": _SR140_RELEASE,
        })
        self.registry_path = self.write("model-registry.json", proposed_registry())
        self.control_path = self.write("control.json", {
            "schema_version": T.CONTROL_SCHEMA_VERSION,
            "state": "enabled",
            "updated_at": "2026-07-13T12:40:00Z",
            "updated_by": "founder",
            "reason": "test control",
        })

    def write(self, name: str, doc: dict) -> pathlib.Path:
        path = self.tmp / name
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        return path

    def build(self, cutoff: str = CUTOFF, registry_path: pathlib.Path | None = None,
              promotions_dir: pathlib.Path = C.sitrep_promotions.PROMOTIONS_DIR):
        return C.build_candidate(
            self.snapshot_path, cutoff,
            registry_path=registry_path or self.registry_path,
            rounds_dir=self.rounds_dir,
            promotions_dir=promotions_dir,
        )

    def promotions_through_sr140(self) -> pathlib.Path:
        """A private promotions folder: links to every reviewed file dated on or before 2026-10-01."""
        folder = self.tmp / "promotions"
        folder.mkdir()
        for path in sorted(C.sitrep_promotions.PROMOTIONS_DIR.glob("sitrep-*.json")):
            if path.name[11:21] <= "2026-10-01":
                (folder / path.name).symlink_to(path)
        return folder


class TestUniverse(CandidateFixture):
    def test_sitrep_140_universe_is_104_unaffected_zones_in_seven_provinces(self):
        candidate, receipt = self.build()
        universe = receipt["universe"]
        self.assertEqual(104, universe["target_count"])
        self.assertEqual(104, len(candidate["target_events"]))
        self.assertEqual(63, len(universe["affected_zones"]))
        self.assertEqual(167, universe["scope_zone_count"])
        self.assertEqual(
            ["Bas-Uele", "Haut-Uele", "Ituri", "Nord-Kivu", "Sud-Kivu", "Sud-Ubangi", "Tshopo"],
            universe["affected_provinces"],
        )
        self.assertEqual(
            {"affected": 63, "total_in_affected_provinces": 167, "not_affected_in_affected_provinces": 104},
            universe["printed_footprint"],
        )
        targets = {t["target_id"] for t in candidate["target_events"]}
        self.assertFalse(targets & set(universe["affected_zones"]))
        self.assertTrue(all(t["province"] in universe["affected_provinces"] for t in candidate["target_events"]))

    def test_target_ids_can_carry_a_resolution_claim_id(self):
        candidate, _ = self.build()
        for target in candidate["target_events"]:
            self.assertRegex(
                f"claim:lovs:model-tournament:{candidate['expected_round_id']}:{target['target_id']}",
                r"^claim:[a-z0-9][a-z0-9_.:-]*$",
            )

    def test_a_footprint_the_tables_do_not_reproduce_fails_closed(self):
        _, receipt = self.build()
        tables = [{"date": "2026-10-01", "counts": {z: 1 for z in receipt["universe"]["affected_zones"]}}]
        index = C.hzc.CentroidIndex.load_default()
        wrong = {"figures": {"affected_health_zone_footprint": {"affected": 62, "total_in_affected_provinces": 167}}}
        with self.assertRaises(C.CandidateBuildError):
            C._universe(tables, index, wrong)
        with self.assertRaises(C.CandidateBuildError):
            C._universe(tables, index, {"figures": {}})


class TestPendingRows(unittest.TestCase):
    def test_a_confirmed_row_pending_integration_in_the_latest_table_stops_the_build(self):
        index = C.hzc.CentroidIndex.load_default()
        promotion = {
            "sitrep_number": 999,
            "data_as_of": "2026-10-01",
            "figures": {"health_zone_table": {"date": "2026-10-01", "rows": [
                {"province": "Ituri", "zone": "Bunia", "confirmed": 5},
                {"province": "Tshopo", "zone": "Yakusu", "confirmed": 1, "officially_integrated": False},
            ]}},
        }
        with self.assertRaises(C.CandidateBuildError) as ctx:
            C._zone_tables([promotion], index)
        self.assertIn("Yakusu", str(ctx.exception))

    def test_an_older_pending_row_does_not_stop_the_build(self):
        index = C.hzc.CentroidIndex.load_default()
        older = {
            "sitrep_number": 998, "data_as_of": "2026-09-30",
            "figures": {"health_zone_table": {"date": "2026-09-30", "rows": [
                {"province": "Tshopo", "zone": "Yakusu", "confirmed": 1, "officially_integrated": False},
            ]}},
        }
        latest = {
            "sitrep_number": 999, "data_as_of": "2026-10-01",
            "figures": {"health_zone_table": {"date": "2026-10-01", "rows": [
                {"province": "Tshopo", "zone": "Yakusu", "confirmed": 1},
            ]}},
        }
        tables = C._zone_tables([older, latest], index)
        self.assertEqual({"yakusu": 1}, tables[-1]["counts"])


class TestMatrix(CandidateFixture):
    def test_complete_matrix_for_every_eligible_model(self):
        candidate, receipt = self.build()
        self.assertEqual(["benchmark.base_rate_30d", DISTANCE_ID], candidate["eligible_model_ids"])
        expected = {(m, t["target_id"]) for m in candidate["eligible_model_ids"] for t in candidate["target_events"]}
        observed = {(p["model_id"], p["target_id"]) for p in candidate["predictions"]}
        self.assertEqual(expected, observed)
        self.assertEqual(len(expected), len(candidate["predictions"]))
        self.assertEqual({"models": 2, "targets": 104, "predictions": 208}, receipt["matrix"])
        T._candidate_contract(candidate)

    def test_base_rate_uses_the_pool_that_contains_its_own_history(self):
        candidate, receipt = self.build()
        base = receipt["models"]["benchmark.base_rate_30d"]
        self.assertEqual(167, base["target_universe_size"])
        self.assertEqual("2026-10-02", base["cutoff"])
        values = {p["probability"] for p in candidate["predictions"] if p["model_id"] == "benchmark.base_rate_30d"}
        self.assertEqual({base["probability"]}, values)

    def test_distance_scores_are_finite_non_positive_ranks(self):
        candidate, receipt = self.build()
        scores = [p["rank_score"] for p in candidate["predictions"] if p["model_id"] == DISTANCE_ID]
        self.assertTrue(all(math.isfinite(s) and s <= 0 for s in scores))
        frontier = receipt["models"][DISTANCE_ID]
        self.assertEqual("2026-10-01", frontier["frontier_latest_observation"])
        self.assertEqual("2026-09-01", frontier["frontier_baseline_observation"])


class TestEligibility(CandidateFixture):
    def test_an_eligible_model_without_an_adapter_stops_the_build(self):
        registry = self.write("registry-corridor-eligible.json", proposed_registry(demote=False))
        with self.assertRaises(C.CandidateBuildError) as ctx:
            self.build(registry_path=registry)
        self.assertIn(CORRIDOR_ID, str(ctx.exception))

    def test_an_eligible_corridor_model_is_refused_with_the_distance_model_planned(self):
        registry = self.write("registry-committed.json", proposed_registry(promote=False, demote=False))
        with self.assertRaises(C.CandidateBuildError) as ctx:
            self.build(registry_path=registry)
        self.assertIn(CORRIDOR_ID, str(ctx.exception))

    def test_registry_version_must_match_the_adapter(self):
        doc = proposed_registry()
        for model in doc["models"]:
            if model["model_id"] == DISTANCE_ID:
                model["version"] = "v2"
        with self.assertRaises(C.CandidateBuildError):
            self.build(registry_path=self.write("registry-v2.json", doc))


class TestCommandLine(CandidateFixture):
    def test_a_refused_build_exits_2_and_writes_nothing(self):
        registry = self.write("registry-corridor-eligible.json", proposed_registry(demote=False))
        out_dir = self.tmp / "candidates"
        with mock.patch("builtins.print") as printed:
            code = C.main([
                "--source-snapshot", str(self.snapshot_path), "--source-cutoff-utc", CUTOFF,
                "--registry", str(registry), "--out-dir", str(out_dir),
            ])
        self.assertEqual(2, code)
        self.assertFalse(out_dir.exists())
        self.assertIn("candidate build refused", printed.call_args.args[0])

    def test_a_failing_dry_run_writes_nothing(self):
        out_dir = self.tmp / "candidates"
        with (
            mock.patch("builtins.print") as printed,
            mock.patch.object(T, "load_rounds", return_value=[]),
        ):
            code = C.main([
                "--source-snapshot", str(self.snapshot_path), "--source-cutoff-utc", CUTOFF,
                "--registry", str(self.registry_path), "--out-dir", str(out_dir),
                "--dry-run-freeze-at", "2026-10-04T11:00:00Z",
            ])
        self.assertEqual(2, code)
        self.assertFalse(out_dir.exists())
        self.assertIn("cannot precede", printed.call_args.args[0])


class TestSourceAvailabilityCutoff(CandidateFixture):
    def test_exact_publication_receipts_cannot_bypass_cutoff(self):
        folder = self.promotions_through_sr140()
        path = next(folder.glob("sitrep-015-*.json"))
        original = json.loads(path.read_text())
        path.unlink()
        for field in ("published_at", "wordpress_published_at"):
            promotion = copy.deepcopy(original)
            promotion["source_receipt"] = {field: "2026-10-10T12:00:00Z"}
            path.write_text(json.dumps(promotion))
            with self.subTest(field=field), self.assertRaisesRegex(C.CandidateBuildError, "later than"):
                self.build(promotions_dir=folder)
        promotion["source_receipt"] = {"source_id": "wrong", "published_at": original["published_at"]}
        path.write_text(json.dumps(promotion))
        with self.assertRaisesRegex(C.CandidateBuildError, "identity mismatch"):
            self.build(promotions_dir=folder)

    def test_receipt_hashes_every_input_and_records_the_cutoff(self):
        _, receipt = self.build()
        self.assertEqual(CUTOFF, receipt["source_availability_cutoff_utc"])
        self.assertEqual("2026-10-01", receipt["source_data_day"])
        roles = [row["role"] for row in receipt["inputs"]]
        self.assertEqual(["model_registry", "tournament_schedule", "health_zone_centroids", "source_snapshot"], roles[:4])
        promotions = [row for row in receipt["inputs"] if row["role"] == "reviewed_sitrep_promotion"]
        self.assertTrue(promotions)
        self.assertTrue(all(row["data_day"] <= "2026-10-01" for row in promotions))
        self.assertEqual("data/sitrep_promotions/sitrep-140-2026-10-01.json", promotions[-1]["path"])
        for row in receipt["inputs"]:
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")

    def test_review_time_after_the_cutoff_fails_closed(self):
        # SitRep 140 was reviewed on 2026-10-03, a bare date read as late as
        # 2026-10-04T11:59:59Z.
        with self.assertRaises(C.CandidateBuildError) as ctx:
            self.build(cutoff="2026-10-04T00:00:00Z")
        self.assertIn("later than the declared source-availability cutoff", str(ctx.exception))

    def test_publication_after_the_cutoff_fails_closed(self):
        with self.assertRaises(C.CandidateBuildError) as ctx:
            self.build(cutoff="2026-10-02T12:00:00Z")
        self.assertIn("published_at", str(ctx.exception))

    def test_data_day_after_the_cutoff_fails_closed(self):
        with self.assertRaises(C.CandidateBuildError) as ctx:
            self.build(cutoff="2026-09-30T23:59:59Z")
        self.assertIn("snapshot_date", str(ctx.exception))

    def test_promotions_dated_after_the_data_day_are_never_opened(self):
        folder = self.promotions_through_sr140()
        baseline = [C.render(doc) for doc in self.build(promotions_dir=folder)]
        # A later edition that would fail validation, or change the universe, if it were read.
        (folder / "sitrep-141-2026-10-02.json").write_text("{not json", encoding="utf-8")
        poisoned = [C.render(doc) for doc in self.build(promotions_dir=folder)]
        self.assertEqual(baseline, poisoned)
        self.assertNotIn("sitrep-141", poisoned[1])

    def test_an_earlier_promotion_reviewed_after_the_cutoff_fails_closed(self):
        folder = self.promotions_through_sr140()
        name = "sitrep-139-2026-09-30.json"
        doc = json.loads((folder / name).read_text(encoding="utf-8"))
        doc["review"]["reviewed_at"] = "2026-10-05T09:00:00Z"
        (folder / name).unlink()
        (folder / name).write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(C.CandidateBuildError) as ctx:
            self.build(promotions_dir=folder)
        self.assertIn(name, str(ctx.exception))
        self.assertIn("review.reviewed_at", str(ctx.exception))

    def test_receipt_carries_the_snapshot_content_hash_the_freeze_receipt_uses(self):
        _, receipt = self.build()
        snapshot = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual(T.content_hash(snapshot), receipt["source_snapshot_content_sha256"])

    def test_times_without_an_offset_are_read_at_their_latest_utc_instant(self):
        self.assertEqual(
            T._utc_datetime("2026-06-05T06:30:21Z", "x"), C._instant("2026-06-04T18:30:21", "x")
        )
        self.assertEqual(T._utc_datetime("2026-10-04T11:59:59Z", "x"), C._instant("2026-10-03", "x"))
        self.assertEqual(T._utc_datetime("2026-10-02T22:33:43Z", "x"), C._instant("2026-10-02T22:33:43Z", "x"))


class TestDeterminismAndDryRun(CandidateFixture):
    def test_same_inputs_give_byte_identical_candidate_and_receipt(self):
        first = [C.render(doc) for doc in self.build()]
        second = [C.render(doc) for doc in self.build()]
        self.assertEqual(first, second)

    def test_zone_tables_do_not_depend_on_promotion_order(self):
        promotions, _ = C._used_promotions(
            C.sitrep_promotions.PROMOTIONS_DIR, C.dt.date(2026, 10, 1), T._utc_datetime(CUTOFF, "c")
        )
        index = C.hzc.CentroidIndex.load_default()
        self.assertEqual(C._zone_tables(promotions, index), C._zone_tables(list(reversed(promotions)), index))

    def test_dry_run_passes_freeze_validation_and_writes_nothing(self):
        candidate, receipt = self.build()
        rounds_before = sorted(T.ROUNDS_DIR.glob("*.json")) if T.ROUNDS_DIR.exists() else []
        manifest = C.dry_run_freeze(
            candidate, self.snapshot_path,
            frozen_at="2026-10-04T13:00:00Z", source_cutoff_utc=CUTOFF,
            candidate_path=f"data/model-tournament/candidates/{candidate['expected_round_id']}.json",
            registry_path=self.registry_path, rounds_dir=self.rounds_dir,
            control_path=self.control_path,
        )
        T.validate_round(manifest, registry_doc=proposed_registry())
        self.assertEqual("bdbv-2026-tournament-round-001", manifest["round_id"])
        self.assertEqual("2026-10-05", manifest["window_start"])
        self.assertEqual("2026-11-03", manifest["window_end"])
        self.assertEqual(receipt["candidate_sha256"], manifest["freeze_receipt"]["candidate_sha256"])
        self.assertEqual("dry-run-not-an-approval", manifest["freeze_receipt"]["approval"]["merged_by"])
        self.assertFalse(self.rounds_dir.exists())
        rounds_after = sorted(T.ROUNDS_DIR.glob("*.json")) if T.ROUNDS_DIR.exists() else []
        self.assertEqual(rounds_before, rounds_after)

    def test_a_freeze_before_the_cutoff_is_refused(self):
        candidate, _ = self.build()
        with self.assertRaises(C.CandidateBuildError):
            C.dry_run_freeze(
                candidate, self.snapshot_path, frozen_at="2026-10-04T11:00:00Z",
                source_cutoff_utc=CUTOFF, candidate_path="data/model-tournament/candidates/x.json",
                registry_path=self.registry_path, rounds_dir=self.rounds_dir,
                control_path=self.control_path,
            )


_PRIVATE_SR140 = (
    REPO_ROOT / "data" / "bundibugyo-2026" / "private" / "raw" / _SR140_RELEASE["source_receipt"]["sha256"]
)


@unittest.skipUnless(_PRIVATE_SR140.is_file(), "restricted SitRep 140 source bytes are not present")
class TestEndToEndWithArchive(unittest.TestCase):
    """The unpatched path: the release envelope is byte-verified against the archive."""

    def test_release_verification_accepts_the_frozen_envelope(self):
        self.assertEqual(_SR140_RELEASE, T._verified_source_release({"release": _SR140_RELEASE}))


if __name__ == "__main__":
    unittest.main()
