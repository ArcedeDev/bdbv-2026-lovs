# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from lovs import model_tournament as T


class TournamentFixture(unittest.TestCase):
    def registry(self) -> dict:
        return {
            "schema_version": T.REGISTRY_SCHEMA_VERSION,
            "outbreak_id": "bdbv-uga-cod-2026",
            "scoring_policy": {
                "primary_probability_metric": "brier",
                "calibration_min_n": 20,
                "undefined_metric_policy": "null_with_reason",
            },
            "models": [
                {
                    "model_id": "m.interval",
                    "label": "Interval",
                    "version": "v1",
                    "output_kind": "probability_interval",
                    "readiness": "eligible_when_round_freezes",
                    "scoring_eligible": True,
                    "scoring_transform": "interval_midpoint",
                    "implementation_module": "lovs.interval_model",
                },
                {
                    "model_id": "m.rank",
                    "label": "Rank",
                    "version": "v1",
                    "output_kind": "rank_score",
                    "readiness": "active",
                    "scoring_eligible": True,
                    "score_direction": "higher_is_more_likely",
                    "implementation_module": "lovs.rank_model",
                },
                {
                    "model_id": "m.planned",
                    "label": "Planned",
                    "version": "v1",
                    "output_kind": "probability",
                    "readiness": "planned_review_required",
                    "scoring_eligible": False,
                    "scoring_transform": "identity",
                },
            ],
            "honesty_notes": ["Prospective only."],
        }

    def schedule(self) -> dict:
        return {
            "schema_version": T.SCHEDULE_SCHEMA_VERSION,
            "outbreak_id": "bdbv-uga-cod-2026",
            "cadence_days": 30,
            "timezone": "UTC",
            "first_eligible_freeze_date": "2026-08-05",
            "round_id_prefix": "bdbv-test-round",
            "minimum_competitors": 2,
            "window_start_policy": "next_utc_calendar_day_after_freeze",
            "release_gate": "founder_approved_freeze_required",
            "approval_repository": "ArcedeDev/bdbv-2026-lovs",
            "approval_merge_actors": ["FransDevelopment"],
            "approval_candidate_path_prefix": "data/model-tournament/candidates/",
            "next_round_template": {
                "horizon_days": 30,
                "target_universe_policy": "fixed for all models",
                "event_definition": "first confirmed detection",
            },
        }

    def control(self, state: str = "enabled") -> dict:
        return {
            "schema_version": T.CONTROL_SCHEMA_VERSION,
            "state": state,
            "updated_at": "2026-07-13T12:40:00Z",
            "updated_by": "founder",
            "reason": "test control",
        }

    def source_snapshot(self) -> dict:
        return json.loads((T.REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text())

    def candidate(self) -> dict:
        return {
            "expected_round_id": "bdbv-test-round-001",
            "source_release_id": self.source_snapshot()["release"]["release_id"],
            "eligible_model_ids": ["m.interval", "m.rank"],
            "target_events": [
                {"target_id": "a", "geography_id": "geo:a", "event_definition": "confirmed detection"},
                {"target_id": "b", "geography_id": "geo:b", "event_definition": "confirmed detection"},
                {"target_id": "c", "geography_id": "geo:c", "event_definition": "confirmed detection"},
            ],
            "predictions": [
                {"model_id": "m.interval", "target_id": "a", "output_kind": "probability_interval", "probability_interval": [0.6, 0.8]},
                {"model_id": "m.interval", "target_id": "b", "output_kind": "probability_interval", "probability_interval": [0.2, 0.4]},
                {"model_id": "m.interval", "target_id": "c", "output_kind": "probability_interval", "probability_interval": [0.1, 0.3]},
                {"model_id": "m.rank", "target_id": "a", "output_kind": "rank_score", "rank_score": 10},
                {"model_id": "m.rank", "target_id": "b", "output_kind": "rank_score", "rank_score": 5},
                {"model_id": "m.rank", "target_id": "c", "output_kind": "rank_score", "rank_score": 1},
            ],
        }

    def round(self) -> dict:
        candidate = self.candidate()
        return T.build_forecast_manifest(
            candidate,
            self.source_snapshot(),
            registry=self.registry(),
            schedule=self.schedule(),
            control=self.control(),
            existing_rounds=[],
            frozen_at="2026-08-05T10:00:00Z",
            approval_receipt=self.approval_receipt(candidate),
        )

    def approval_receipt(self, candidate: dict | None = None) -> dict:
        candidate = candidate or self.candidate()
        return {
            "approval_api_url": "https://api.github.com/repos/ArcedeDev/bdbv-2026-lovs/pulls/123",
            "merged_at": "2026-08-05T09:00:00Z",
            "merged_by": "FransDevelopment",
            "merge_commit_sha": "a" * 40,
            "candidate_path": "data/model-tournament/candidates/bdbv-test-round-001.json",
            "candidate_sha256": T.content_hash(T._candidate_contract(candidate)),
        }

    def evidence_registry(self) -> dict:
        chains = []
        values = {
            "a": "resolved_yes",
            "b": "resolved_no",
            "c": "unscoreable_surveillance_dark",
        }
        for target_id, value in values.items():
            chains.append({
                "chain_id": f"ec:lovs:model-tournament:{target_id}:2026-09-05",
                "claim": {
                    "claim_id": f"claim:lovs:model-tournament:bdbv-test-round-001:{target_id}",
                    "artifact": "data/model-tournament/resolutions/bdbv-test-round-001.json",
                    "locator": f"target_outcomes[target_id={target_id}].resolution_status",
                    "statement": f"Target {target_id} resolution is {value}.",
                    "value": value,
                },
                "verdict": "supported",
                "reviewed_at": "2026-09-05T00:00:00Z",
                "reviewer": "test-reviewer",
                "sources": [{
                    "source_id": f"src:model-tournament-{target_id}",
                    "tier": "T1_PRIMARY",
                    "citation": f"Authoritative test source for target {target_id}.",
                    "url": f"https://example.org/model-tournament/{target_id}",
                    "finding": f"The authority supports {value}.",
                }],
                "steps": [{
                    "step_id": f"step:model-tournament-{target_id}",
                    "kind": "source_quote",
                    "source_id": f"src:model-tournament-{target_id}",
                    "finding": f"Reviewed evidence supports {value}.",
                }],
                "next_action": "Resolution review complete.",
            })
        return {"schema_version": 1, "chains": chains}

    def resolution_candidate(self) -> dict:
        return {
            "resolved_at": "2026-09-05T00:00:00Z",
            "target_outcomes": [
                {"target_id": "a", "resolution_status": "resolved_yes", "outcome": 1, "evidence_as_of": "2026-09-04", "evidence_chain_ids": ["ec:lovs:model-tournament:a:2026-09-05"]},
                {"target_id": "b", "resolution_status": "resolved_no", "outcome": 0, "evidence_as_of": "2026-09-04", "evidence_chain_ids": ["ec:lovs:model-tournament:b:2026-09-05"]},
                {"target_id": "c", "resolution_status": "unscoreable_surveillance_dark", "evidence_as_of": "2026-09-04", "evidence_chain_ids": ["ec:lovs:model-tournament:c:2026-09-05"]},
            ],
        }

    def resolution(self) -> dict:
        return T.build_resolution(
            self.resolution_candidate(), self.round(), self.evidence_registry()
        )

    def write_root(self, root: pathlib.Path) -> None:
        (root / "rounds").mkdir(parents=True)
        (root / "resolutions").mkdir()
        (root / "scores").mkdir()
        for name, doc in (
            ("model-registry.json", self.registry()),
            ("schedule.json", self.schedule()),
            ("control.json", self.control()),
            ("evidence-chains.json", self.evidence_registry()),
        ):
            (root / name).write_text(json.dumps(doc), encoding="utf-8")

    def snapshot(self, as_of: str, root: pathlib.Path) -> dict:
        with (
            mock.patch.object(T, "load_evidence_registry", return_value=self.evidence_registry()),
            mock.patch.object(T, "verify_frozen_round_approval"),
        ):
            return T.snapshot_status(
                as_of, registry_path=root / "model-registry.json",
                schedule_path=root / "schedule.json", control_path=root / "control.json",
                rounds_dir=root / "rounds", resolutions_dir=root / "resolutions",
                scores_dir=root / "scores",
            )


class ContractValidationTests(TournamentFixture):
    def test_forecast_manifest_freezes_exact_window_and_hashes(self):
        round_doc = self.round()
        self.assertEqual(round_doc["round_id"], "bdbv-test-round-001")
        self.assertEqual(round_doc["window_start"], "2026-08-06")
        self.assertEqual(round_doc["window_end"], "2026-09-04")
        self.assertEqual(len(round_doc["predictions"]), 6)
        self.assertEqual(round_doc["freeze_receipt"]["forecast_sha256"], T.forecast_hash(round_doc))
        T.validate_round(round_doc, registry_doc=self.registry())

    def test_round_rejects_tamper_and_incomplete_matrix(self):
        round_doc = self.round()
        round_doc["predictions"][0]["probability_interval"] = [0.0, 0.1]
        with self.assertRaises(T.TournamentConfigError):
            T.validate_round(round_doc)

        candidate = self.candidate()
        candidate["predictions"].pop()
        with self.assertRaisesRegex(T.TournamentConfigError, "incomplete common-target"):
            T.build_forecast_manifest(
                candidate, self.source_snapshot(), registry=self.registry(),
                schedule=self.schedule(), control=self.control(), existing_rounds=[],
                frozen_at="2026-08-05T10:00:00Z",
                approval_receipt=self.approval_receipt(candidate),
            )

    def test_round_rejects_early_or_single_model_freeze(self):
        with self.assertRaisesRegex(T.TournamentConfigError, "earlier than"):
            candidate = self.candidate()
            T.build_forecast_manifest(
                candidate, self.source_snapshot(), registry=self.registry(),
                schedule=self.schedule(), control=self.control(), existing_rounds=[],
                frozen_at="2026-08-04T10:00:00Z",
                approval_receipt=self.approval_receipt(candidate),
            )
        candidate = self.candidate()
        candidate["eligible_model_ids"] = ["m.interval"]
        candidate["predictions"] = [row for row in candidate["predictions"] if row["model_id"] == "m.interval"]
        with self.assertRaisesRegex(T.TournamentConfigError, "minimum_competitors"):
            T.build_forecast_manifest(
                candidate, self.source_snapshot(), registry=self.registry(),
                schedule=self.schedule(), control=self.control(), existing_rounds=[],
                frozen_at="2026-08-05T10:00:00Z",
                approval_receipt=self.approval_receipt(candidate),
            )

    def test_registry_rejects_every_readiness_bypass_and_labs_path(self):
        for readiness in ("planned_review_required", "research_only", "not_eligible"):
            registry = self.registry()
            registry["models"][2].update({
                "readiness": readiness,
                "scoring_eligible": True,
                "implementation_module": "lovs.planned",
            })
            with self.subTest(readiness=readiness), self.assertRaises(T.TournamentConfigError):
                T.validate_registry(registry)
        registry = self.registry()
        registry["models"][0]["implementation_module"] = r"labs\bad.py"
        with self.assertRaises(T.TournamentConfigError):
            T.validate_registry(registry)

    def test_strict_dates_reject_suffixes_and_invalid_days(self):
        schedule = self.schedule()
        for invalid in ("2026-08-05garbage", "2026-02-30"):
            schedule["first_eligible_freeze_date"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(T.TournamentConfigError):
                T.validate_schedule(schedule)

    def test_schedule_and_artifact_paths_reject_traversal(self):
        schedule = self.schedule()
        schedule["round_id_prefix"] = "../../escaped"
        with self.assertRaises(T.TournamentConfigError):
            T.validate_schedule(schedule)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(T.TournamentConfigError):
                T._artifact_path(pathlib.Path(tmp), "../escaped")

    def test_source_release_must_match_byte_verified_reviewed_receipt(self):
        source = self.source_snapshot()
        source["release"]["release_id"] = "fabricated"
        candidate = self.candidate()
        with self.assertRaisesRegex(T.TournamentConfigError, "byte-verified"):
            T.build_forecast_manifest(
                candidate, source, registry=self.registry(), schedule=self.schedule(),
                control=self.control(), existing_rounds=[], frozen_at="2026-08-05T10:00:00Z",
                approval_receipt=self.approval_receipt(candidate),
            )

    def test_resolution_requires_complete_controlled_evidence(self):
        resolution = self.resolution()
        T.validate_resolution(resolution, self.round())
        broken = copy.deepcopy(self.resolution_candidate())
        broken["target_outcomes"][2]["resolution_status"] = "typo_negative"
        with self.assertRaises(T.TournamentConfigError):
            T.build_resolution(broken, self.round(), self.evidence_registry())
        broken = copy.deepcopy(self.resolution_candidate())
        broken["target_outcomes"].pop()
        with self.assertRaisesRegex(T.TournamentConfigError, "complete target universe"):
            T.build_resolution(broken, self.round(), self.evidence_registry())
        broken = copy.deepcopy(self.resolution_candidate())
        broken["resolved_at"] = "2026-09-03T23:59:59Z"
        with self.assertRaisesRegex(T.TournamentConfigError, "before the window closes"):
            T.build_resolution(broken, self.round(), self.evidence_registry())
        broken = copy.deepcopy(self.resolution_candidate())
        broken["target_outcomes"][0]["evidence_chain_ids"] = ["ec:fabricated"]
        with self.assertRaisesRegex(T.TournamentConfigError, "unknown evidence"):
            T.build_resolution(broken, self.round(), self.evidence_registry())

    def test_negative_resolution_requires_end_of_window_review(self):
        registry = self.evidence_registry()
        registry["chains"][1]["reviewed_at"] = "2026-08-07T00:00:00Z"
        with self.assertRaisesRegex(T.TournamentConfigError, "predates window end"):
            T.build_resolution(self.resolution_candidate(), self.round(), registry)

    def test_evidence_claim_direction_cannot_be_reused_for_opposite_outcome(self):
        candidate = copy.deepcopy(self.resolution_candidate())
        candidate["target_outcomes"][0].update({
            "resolution_status": "resolved_no",
            "outcome": 0,
        })
        with self.assertRaisesRegex(T.TournamentConfigError, "claim value"):
            T.build_resolution(candidate, self.round(), self.evidence_registry())

    def test_github_pr_approval_binds_exact_merged_candidate(self):
        candidate = self.candidate()
        approval_url = self.approval_receipt(candidate)["approval_api_url"]
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / "data" / "model-tournament" / "candidates" / "bdbv-test-round-001.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(candidate), encoding="utf-8")
            pr = {
                "merged_at": "2026-08-05T09:00:00Z",
                "merge_commit_sha": "a" * 40,
                "merged_by": {"login": "FransDevelopment"},
                "base": {"repo": {"full_name": "ArcedeDev/bdbv-2026-lovs"}},
            }
            remote = {
                "encoding": "base64",
                "content": __import__("base64").b64encode(json.dumps(candidate).encode()).decode(),
            }
            with (
                mock.patch.object(T, "REPO_ROOT", root),
                mock.patch.object(T, "_github_json", side_effect=[pr, remote]),
                mock.patch.object(T, "_github_list", return_value=[{
                    "filename": "data/model-tournament/candidates/bdbv-test-round-001.json",
                    "status": "added",
                }]),
            ):
                receipt = T.verify_github_pr_approval(
                    approval_url, path, candidate,
                    self.schedule(), T._utc_datetime("2026-08-05T10:00:00Z", "now"),
                )
            self.assertEqual(receipt["candidate_sha256"], T.content_hash(T._candidate_contract(candidate)))

            with (
                mock.patch.object(T, "REPO_ROOT", root),
                mock.patch.object(T, "_github_json", return_value=pr),
                mock.patch.object(T, "_github_list", return_value=[{
                    "filename": "README.md", "status": "modified",
                }]),
            ):
                with self.assertRaisesRegex(T.TournamentConfigError, "did not change"):
                    T.verify_github_pr_approval(
                        approval_url, path, candidate, self.schedule(),
                        T._utc_datetime("2026-08-05T10:00:00Z", "now"),
                    )

    def test_github_approval_dependency_fails_closed(self):
        with mock.patch.object(T.urllib.request, "urlopen", side_effect=TimeoutError("timeout")):
            with self.assertRaisesRegex(T.TournamentConfigError, "lookup failed"):
                T._github_json(self.approval_receipt()["approval_api_url"])

        candidate = self.candidate()
        pr = {
            "merged_at": "2026-08-05T09:00:00Z",
            "merge_commit_sha": "a" * 40,
            "merged_by": {"login": "unapproved-user"},
            "base": {"repo": {"full_name": "ArcedeDev/bdbv-2026-lovs"}},
        }
        with mock.patch.object(T, "_github_json", return_value=pr):
            with self.assertRaisesRegex(T.TournamentConfigError, "approved actor"):
                T.verify_github_pr_approval(
                    self.approval_receipt()["approval_api_url"], pathlib.Path(__file__),
                    candidate, self.schedule(), T._utc_datetime("2026-08-05T10:00:00Z", "now"),
                )

    def test_create_only_write_is_idempotent_and_rejects_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "round.json"
            round_doc = self.round()
            self.assertEqual(T._write_create_only(path, round_doc), "created")
            self.assertEqual(T._write_create_only(path, round_doc), "unchanged")
            changed = copy.deepcopy(round_doc)
            changed["predictions"][0]["probability_interval"] = [0.1, 0.2]
            with self.assertRaises(T.TournamentImmutabilityError):
                T._write_create_only(path, changed)


class ScoringTests(TournamentFixture):
    def test_score_uses_common_targets_and_preregistered_transforms(self):
        score = T.score_round(self.round(), self.resolution())
        self.assertEqual(score["scored_target_ids"], ["a", "b"])
        self.assertEqual(score["scored_target_count"], 2)
        by_model = {row["model_id"]: row for row in score["model_scores"]}
        self.assertEqual(by_model["m.interval"]["n_scored"], 2)
        self.assertEqual(by_model["m.rank"]["n_scored"], 2)
        self.assertEqual(by_model["m.interval"]["brier"]["value"], 0.09)
        self.assertEqual(by_model["m.interval"]["roc_auc"]["value"], 1.0)
        self.assertEqual(by_model["m.rank"]["roc_auc"]["value"], 1.0)
        self.assertIsNone(by_model["m.interval"]["expected_calibration_error"]["value"])
        self.assertIn("calibration_min_n=20", by_model["m.interval"]["expected_calibration_error"]["reason"])
        self.assertEqual(score["score_sha256"], T._self_hash(score, "score_sha256"))

    def test_score_is_deterministic_and_rejects_resolution_tamper(self):
        first = T.score_round(self.round(), self.resolution())
        second = T.score_round(self.round(), self.resolution())
        self.assertEqual(T.canonical_json(first), T.canonical_json(second))
        resolution = self.resolution()
        resolution["target_outcomes"][0]["outcome"] = 0
        with self.assertRaises(T.TournamentConfigError):
            T.score_round(self.round(), resolution)


class LifecycleAndCliTests(TournamentFixture):
    def test_historical_projection_excludes_future_round_resolution_and_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.write_root(root)
            round_doc = self.round()
            resolution = self.resolution()
            score = T.score_round(round_doc, resolution)
            for directory, doc in (("rounds", round_doc), ("resolutions", resolution), ("scores", score)):
                (root / directory / f"{round_doc['round_id']}.json").write_text(json.dumps(doc))
            projected = self.snapshot("2026-07-15", root)
            self.assertEqual(projected["status"], "scheduled")
            self.assertEqual(projected["rounds"]["count"], 0)
    def test_status_projects_schedule_due_disable_and_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.write_root(root)
            status = self.snapshot("2026-07-13", root)
            self.assertEqual(status["status"], "scheduled")
            self.assertEqual(status["next_eligible_round"]["round_id"], "bdbv-test-round-001")
            due = self.snapshot("2026-08-05", root)
            self.assertEqual(due["status"], "ready_for_freeze_review")
            (root / "control.json").write_text(json.dumps(self.control("disabled")), encoding="utf-8")
            disabled = self.snapshot("2026-08-05", root)
            self.assertEqual(disabled["status"], "disabled")
            (root / "schedule.json").write_text("{}", encoding="utf-8")
            invalid = self.snapshot("2026-08-05", root)
            self.assertEqual(invalid["status"], "invalid")
            self.assertNotIn(tmp, invalid["diagnostics"][0]["message"])

    def test_recurring_lifecycle_advances_round_id_and_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.write_root(root)
            round_doc = self.round()
            (root / "rounds" / f"{round_doc['round_id']}.json").write_text(json.dumps(round_doc), encoding="utf-8")
            def status(day: str) -> dict:
                return self.snapshot(day, root)
            self.assertEqual(status("2026-08-05")["status"], "frozen")
            self.assertEqual(status("2026-08-06")["status"], "active")
            self.assertEqual(status("2026-09-05")["status"], "ready_for_freeze_review")
            self.assertEqual(
                status("2026-09-05")["rounds"]["awaiting_resolution"][0]["round_id"],
                "bdbv-test-round-001",
            )
            projected = status("2026-08-20")
            self.assertEqual(projected["next_eligible_round"]["round_id"], "bdbv-test-round-002")
            self.assertEqual(projected["next_eligible_round"]["first_eligible_freeze_date"], "2026-09-04")
            resolution = self.resolution()
            (root / "resolutions" / f"{round_doc['round_id']}.json").write_text(json.dumps(resolution), encoding="utf-8")
            self.assertEqual(status("2026-09-05")["status"], "ready_for_freeze_review")
            score = T.score_round(round_doc, resolution)
            (root / "scores" / f"{round_doc['round_id']}.json").write_text(json.dumps(score), encoding="utf-8")
            self.assertEqual(status("2026-09-05")["status"], "ready_for_freeze_review")
            self.assertEqual(len(status("2026-09-05")["rounds"]["evaluated"]), 1)

    def test_cli_round_trip_persists_forecast_resolution_and_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.write_root(root)
            candidate_path = root / "candidate.json"
            snapshot_path = root / "snapshot.json"
            resolution_path = root / "resolution-candidate.json"
            candidate_path.write_text(json.dumps(self.candidate()), encoding="utf-8")
            snapshot_path.write_text(json.dumps(self.source_snapshot()), encoding="utf-8")
            resolution_path.write_text(json.dumps(self.resolution_candidate()), encoding="utf-8")
            base = ["--root", str(root)]
            with (
                mock.patch.object(T, "_utc_now", return_value="2026-08-05T10:00:00Z"),
                mock.patch.object(
                    T, "verify_github_pr_approval", return_value=self.approval_receipt()
                ),
                mock.patch.object(T, "verify_frozen_round_approval"),
                mock.patch.object(T, "load_evidence_registry", return_value=self.evidence_registry()),
            ):
                self.assertEqual(T.main([
                    *base, "freeze", "--candidate", str(candidate_path),
                    "--source-snapshot", str(snapshot_path),
                    "--approval-pr-api", self.approval_receipt()["approval_api_url"],
                ]), 0)
                self.assertEqual(T.main([
                    *base, "resolve", "--round-id", "bdbv-test-round-001",
                    "--candidate", str(resolution_path),
                ]), 0)
                self.assertEqual(T.main([
                    *base, "score", "--round-id", "bdbv-test-round-001",
                ]), 0)
            self.assertTrue((root / "rounds" / "bdbv-test-round-001.json").is_file())
            self.assertTrue((root / "resolutions" / "bdbv-test-round-001.json").is_file())
            self.assertTrue((root / "scores" / "bdbv-test-round-001.json").is_file())

    def test_cli_control_round_trip_disables_and_reenables_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.write_root(root)
            common = ["--root", str(root), "control", "--updated-by", "test-operator"]
            self.assertEqual(T.main([
                *common, "--state", "disabled", "--reason", "rollback drill",
            ]), 0)
            disabled = self.snapshot("2026-07-13", root)
            self.assertEqual(disabled["status"], "disabled")
            self.assertEqual(T.main([
                *common, "--state", "enabled", "--reason", "rollback drill complete",
            ]), 0)
            enabled = T.load_control(root / "control.json")
            self.assertEqual(enabled["state"], "enabled")


if __name__ == "__main__":
    unittest.main()
