import argparse
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import daily_snapshot_prep


class ReviewSnapshotDateTests(unittest.TestCase):
    def test_uses_latest_completed_source_publication_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_path = root / "live.json"
            manifest_path = root / "manifest.json"
            out_path.write_text(json.dumps({"as_of": "2026-05-22T23:59:59Z"}))
            manifest_path.write_text(json.dumps({
                "entries": [
                    {"published_at": "2026-05-22T12:00:00Z"},
                    {"published_at": "2026-05-23T18:36:26Z"},
                ],
            }))

            with mock.patch.object(daily_snapshot_prep.release_snapshot, "OUT_PATH", out_path), \
                mock.patch.object(daily_snapshot_prep.release_snapshot, "MANIFEST_PATH", manifest_path):
                resolved = daily_snapshot_prep.resolve_review_snapshot_date("")

        self.assertEqual("2026-05-23", resolved["snapshot_date"])
        self.assertEqual("latest_completed_source_publication_date", resolved["basis"])
        self.assertTrue(resolved["ready"])

    def test_falls_back_to_analytic_as_of_when_no_new_publication_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_path = root / "live.json"
            manifest_path = root / "manifest.json"
            out_path.write_text(json.dumps({"as_of": "2026-05-22T23:59:59Z"}))
            manifest_path.write_text(json.dumps({
                "entries": [{"published_at": "2026-05-22T12:00:00Z"}],
            }))

            with mock.patch.object(daily_snapshot_prep.release_snapshot, "OUT_PATH", out_path), \
                mock.patch.object(daily_snapshot_prep.release_snapshot, "MANIFEST_PATH", manifest_path):
                resolved = daily_snapshot_prep.resolve_review_snapshot_date("")

        self.assertEqual("2026-05-22", resolved["snapshot_date"])
        self.assertEqual("analytic_as_of_no_new_completed_source_publication", resolved["basis"])
        self.assertFalse(resolved["ready"])

    def test_explicit_override_is_preserved(self):
        resolved = daily_snapshot_prep.resolve_review_snapshot_date("2026-05-24")

        self.assertEqual("2026-05-24", resolved["snapshot_date"])
        self.assertEqual("explicit_override", resolved["basis"])

    def test_reviewed_sitrep_release_is_completed_publication_state_source(self):
        resolved = daily_snapshot_prep.resolve_review_snapshot_date(
            "",
            reviewed_release_target={
                "release_as_of": "2026-06-01",
                "source_id": "inrb-sitrep-018-2026-06-01",
                "sitrep_number": 18,
                "published_at": "2026-06-02T00:00:00Z",
            },
        )

        self.assertEqual("2026-06-01", resolved["snapshot_date"])
        self.assertEqual("reviewed_sitrep_promotion", resolved["basis"])
        self.assertTrue(resolved["ready"])
        self.assertEqual(18, resolved["sitrep_number"])

    def test_syncs_only_new_completed_publication_snapshots(self):
        self.assertTrue(daily_snapshot_prep.should_sync_review_website(
            {"basis": "latest_completed_source_publication_date"},
            "",
        ))
        self.assertTrue(daily_snapshot_prep.should_sync_review_website(
            {"basis": "reviewed_sitrep_promotion"},
            "",
        ))
        self.assertFalse(daily_snapshot_prep.should_sync_review_website(
            {"basis": "analytic_as_of_no_new_completed_source_publication"},
            "",
        ))
        self.assertTrue(daily_snapshot_prep.should_sync_review_website(
            {"basis": "analytic_as_of_no_new_completed_source_publication"},
            "2026-05-24",
        ))


class FullCyclePrepTests(unittest.TestCase):
    def live_args(self, root: Path) -> argparse.Namespace:
        return argparse.Namespace(
            as_of="2026-10-07", slot=None, earth_awake=False, auto_pull=False,
            full_cycle_release=False, build_review_snapshot=False,
            full_release_check=False, interim_public_precycle_dry_run=False,
            release_as_of="", website_gates=False, website_sync_dry_run=False,
            publish_live=True, deploy_command="echo deploy", skip_health_report=True,
            snapshot_date="2026-10-07", website_root=root, earth_agent_id="",
        )

    def test_live_publish_runs_projection_then_full_release_after_site_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "live.json"
            output.write_text('{"model_tournament":{}}', encoding="utf-8")
            args = self.live_args(root)
            ordered = mock.Mock()
            with (
                mock.patch.object(daily_snapshot_prep.source_ingest, "live_check", return_value=0),
                mock.patch.object(daily_snapshot_prep, "_freshness_path", return_value=root / "absent.json"),
                mock.patch.object(daily_snapshot_prep, "review_rows", return_value=[]),
                mock.patch.object(daily_snapshot_prep, "resolve_release_target", return_value={
                    "status": "ok", "release_as_of": "2026-10-07",
                }),
                mock.patch.object(daily_snapshot_prep, "run_release_check", side_effect=[
                    {"mode": "fast_private_preview", "returncode": 0},
                    {"mode": "full_public_release_check", "returncode": 0},
                ]) as release,
                mock.patch.object(daily_snapshot_prep.release_snapshot, "OUT_PATH", output),
                mock.patch.object(daily_snapshot_prep.release_snapshot, "check_model_tournament_status", return_value=[]) as tournament,
                mock.patch.object(daily_snapshot_prep, "resolve_review_snapshot_date", return_value={
                    "snapshot_date": "2026-10-07", "basis": "latest_completed_source_publication_date",
                }),
                mock.patch.object(daily_snapshot_prep, "sync_review_website", return_value={
                    "status": "ok", "dry_run": False, "source_commit": "a" * 40,
                }) as sync,
                mock.patch.object(daily_snapshot_prep, "run_website_gates", return_value={"status": "ok"}) as site_gates,
                mock.patch.object(daily_snapshot_prep, "verify_tournament_website_publication", return_value={"status": "ok"}) as verify,
                mock.patch.object(daily_snapshot_prep, "run_live_publish", return_value={"status": "ok"}) as publish,
                mock.patch.object(daily_snapshot_prep, "write_prep_packet", return_value=root / "prep.json"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                for name, stage in (
                    ("release", release), ("tournament", tournament), ("sync", sync),
                    ("site_gates", site_gates), ("verify", verify), ("publish", publish),
                ):
                    ordered.attach_mock(stage, name)
                result = daily_snapshot_prep.run_prep(args)

        self.assertEqual(0, result)
        self.assertTrue(args.build_review_snapshot)
        self.assertTrue(args.website_gates)
        self.assertEqual([False, True], [call.kwargs["full_release_check"] for call in release.call_args_list])
        self.assertEqual(root, release.call_args_list[1].kwargs["website_root"])
        tournament.assert_called_once()
        sync.assert_called_once()
        site_gates.assert_called_once()
        self.assertTrue(site_gates.call_args.kwargs["require_bundle"])
        verify.assert_called_once_with(root, "a" * 40)
        publish.assert_called_once()
        self.assertEqual(
            ["release", "tournament", "sync", "site_gates", "release", "verify", "publish"],
            [call[0] for call in ordered.mock_calls],
        )

    def test_live_check_failure_blocks_deploy_after_other_gates_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "live.json"
            output.write_text('{"model_tournament":{}}', encoding="utf-8")
            with (
                mock.patch.object(daily_snapshot_prep.source_ingest, "live_check", return_value=1),
                mock.patch.object(daily_snapshot_prep, "_freshness_path", return_value=root / "absent.json"),
                mock.patch.object(daily_snapshot_prep, "review_rows", return_value=[]),
                mock.patch.object(daily_snapshot_prep, "resolve_release_target", return_value={
                    "status": "ok", "release_as_of": "2026-10-07",
                }),
                mock.patch.object(daily_snapshot_prep, "run_release_check", side_effect=[
                    {"mode": "fast_private_preview", "returncode": 0},
                    {"mode": "full_public_release_check", "returncode": 0},
                ]),
                mock.patch.object(daily_snapshot_prep.release_snapshot, "OUT_PATH", output),
                mock.patch.object(daily_snapshot_prep.release_snapshot, "check_model_tournament_status", return_value=[]),
                mock.patch.object(daily_snapshot_prep, "resolve_review_snapshot_date", return_value={
                    "snapshot_date": "2026-10-07", "basis": "latest_completed_source_publication_date",
                }),
                mock.patch.object(daily_snapshot_prep, "sync_review_website", return_value={
                    "status": "ok", "dry_run": False, "source_commit": "a" * 40,
                }),
                mock.patch.object(daily_snapshot_prep, "run_website_gates", return_value={"status": "ok"}),
                mock.patch.object(daily_snapshot_prep, "verify_tournament_website_publication") as verify,
                mock.patch.object(daily_snapshot_prep, "run_live_publish") as publish,
                mock.patch.object(daily_snapshot_prep, "write_prep_packet", return_value=root / "prep.json"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                result = daily_snapshot_prep.run_prep(self.live_args(root))

        self.assertEqual(1, result)
        verify.assert_not_called()
        publish.assert_not_called()

    def test_independent_site_readback_catches_false_status_even_if_script_approves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "site"
            script = root / "lib" / "scripts" / "sync-bdbv-lovs.py"
            script.parent.mkdir(parents=True)
            script.write_text("# test verifier\n", encoding="utf-8")
            public_path = root / "app" / "bdbv-2026" / "_data" / "model-tournament-status.json"
            public_path.parent.mkdir(parents=True)
            receipt_path = root / "lib" / "generated" / "bdbv-model-tournament-source-receipt.json"
            receipt_path.parent.mkdir(parents=True)
            source_path = Path(tmp) / "source.json"
            source = {
                "schema_version": 1, "outbreak_id": "bdbv", "evaluated_as_of": "2026-10-07",
                "status": "active", "control": {"state": "active"},
                "cadence": {"cadence_days": 30},
                "model_registry": {"model_count": 1, "eligible_model_count": 1,
                                   "by_readiness": {}, "models": [{"model_id": "m1", "readiness": "eligible"}]},
                "next_eligible_round": {"round_id": "r2", "status": "scheduled"},
                "rounds": {"count": 1, "frozen": [], "active": [{"round_id": "r1", "status": "active",
                    "window_start": "2026-10-06", "window_end": "2026-11-04"}],
                           "awaiting_resolution": [], "resolved": [], "evaluated": []},
                "honesty_notes": [],
            }
            source_path.write_text(json.dumps({"model_tournament": source}), encoding="utf-8")
            public_path.write_text(json.dumps(source), encoding="utf-8")
            source_commit = "a" * 40
            receipt_path.write_text(json.dumps({
                "schema_version": "bdbv-model-tournament-source-receipt/v1",
                "source_repository": "https://github.com/ArcedeDev/bdbv-2026-lovs.git",
                "source_commit": source_commit,
                "source_contract_sha256": daily_snapshot_prep.hashlib.sha256(json.dumps(
                    source, sort_keys=True, separators=(",", ":")
                ).encode()).hexdigest(),
                "public_contract_sha256": daily_snapshot_prep.hashlib.sha256(public_path.read_bytes()).hexdigest(),
            }), encoding="utf-8")
            with (
                mock.patch.object(daily_snapshot_prep.release_snapshot, "OUT_PATH", source_path),
                mock.patch.object(daily_snapshot_prep, "_today_utc", return_value="2026-10-07"),
                mock.patch.object(daily_snapshot_prep.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")),
            ):
                self.assertEqual("ok", daily_snapshot_prep.verify_tournament_website_publication(
                    root, source_commit
                )["status"])
                false_public = {**source, "status": "disabled"}
                public_path.write_text(json.dumps(false_public), encoding="utf-8")
                self.assertEqual("failed", daily_snapshot_prep.verify_tournament_website_publication(
                    root, source_commit
                )["status"])

    def test_false_tournament_projection_blocks_site_sync_and_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "live.json"
            output.write_text('{"model_tournament":{}}', encoding="utf-8")
            with (
                mock.patch.object(daily_snapshot_prep.source_ingest, "live_check", return_value=0),
                mock.patch.object(daily_snapshot_prep, "_freshness_path", return_value=root / "absent.json"),
                mock.patch.object(daily_snapshot_prep, "review_rows", return_value=[]),
                mock.patch.object(daily_snapshot_prep, "resolve_release_target", return_value={
                    "status": "ok", "release_as_of": "2026-10-07",
                }),
                mock.patch.object(daily_snapshot_prep, "run_release_check", return_value={
                    "mode": "fast_private_preview", "returncode": 0,
                }),
                mock.patch.object(daily_snapshot_prep.release_snapshot, "OUT_PATH", output),
                mock.patch.object(daily_snapshot_prep.release_snapshot, "check_model_tournament_status", return_value=[
                    "model_tournament.status differs from canonical projection",
                ]),
                mock.patch.object(daily_snapshot_prep, "sync_review_website") as sync,
                mock.patch.object(daily_snapshot_prep, "run_live_publish") as publish,
                mock.patch.object(daily_snapshot_prep, "write_prep_packet", return_value=root / "prep.json"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                result = daily_snapshot_prep.run_prep(self.live_args(root))

        self.assertEqual(1, result)
        sync.assert_not_called()
        publish.assert_not_called()

    def test_auto_pull_includes_insp_wordpress_hot_path(self):
        rows = [
            {"registry_id": "insp-wordpress-sitrep-feed"},
            {"registry_id": "drc-moh-epidemie-dashboard"},
            {"registry_id": "cdc-situation-summary"},
        ]
        with mock.patch.object(daily_snapshot_prep.source_ingest, "pull_source", return_value=0) as pull:
            pulled = daily_snapshot_prep.auto_pull_candidates(rows, "2026-06-03")

        self.assertEqual(
            [call.args[0] for call in pull.mock_calls],
            ["insp-wordpress-sitrep-feed", "drc-moh-epidemie-dashboard"],
        )
        self.assertEqual([row["status"] for row in pulled], ["pulled_to_private_dropbox", "pulled_to_private_dropbox"])

    def test_full_release_check_uses_release_snapshot_as_of(self):
        with mock.patch.object(daily_snapshot_prep, "_run_stage", return_value={"returncode": 0}) as run_stage:
            result = daily_snapshot_prep.run_release_check("2026-06-01", full_release_check=True)

        self.assertEqual("full_public_release_check", result["mode"])
        self.assertEqual(
            [daily_snapshot_prep.PY, "release_snapshot.py", "--check", "--as-of", "2026-06-01"],
            run_stage.call_args.args[1],
        )

    def test_fast_review_stages_do_not_regenerate_public_artifacts(self):
        commands = [
            " ".join(command)
            for _, command in daily_snapshot_prep.FAST_REVIEW_STAGES
        ]

        self.assertFalse(any("lovs.public_exports" in command for command in commands))

    def test_fast_review_fails_if_public_artifact_changes(self):
        ok_stage = {
            "label": "stage",
            "command": [],
            "returncode": 0,
            "stdout_tail": "",
            "stderr_tail": "",
        }
        with mock.patch.object(daily_snapshot_prep, "verify_public_precycle_guards", return_value=[]), \
            mock.patch.object(
                daily_snapshot_prep,
                "_public_artifact_hashes",
                side_effect=[
                    {"data/public_snapshot.json": "before"},
                    {"data/public_snapshot.json": "after"},
                ],
            ), \
            mock.patch.object(daily_snapshot_prep, "_run_stage", return_value=ok_stage):
            result = daily_snapshot_prep.run_fast_review_check("2026-06-10")

        self.assertEqual(1, result["returncode"])
        self.assertIn("changed during website review cycle", result["stderr_tail"])

    def test_precycle_guard_runs_public_head_stability_by_default(self):
        with mock.patch.object(
            daily_snapshot_prep,
            "_public_artifact_hashes",
            return_value={"data/public_snapshot.json": "current"},
        ) as hashes, \
            mock.patch.object(
                daily_snapshot_prep,
                "_public_head_stability_findings",
                return_value=["head"],
            ) as head, \
            mock.patch.object(
                daily_snapshot_prep,
                "_calibration_commitment_findings",
                return_value=["calibration"],
            ) as calibration, \
            mock.patch.object(
                daily_snapshot_prep,
                "_public_snapshot_orphan_findings",
                return_value=["orphan"],
            ) as orphan:
            findings = daily_snapshot_prep.verify_public_precycle_guards()

        self.assertEqual(["head", "calibration", "orphan"], findings)
        hashes.assert_called_once()
        head.assert_called_once()
        calibration.assert_called_once()
        orphan.assert_called_once()

    def test_interim_precycle_dry_run_skips_only_public_head_stability(self):
        with mock.patch.object(
            daily_snapshot_prep,
            "_public_artifact_hashes",
            return_value={"data/public_snapshot.json": "current"},
        ), \
            mock.patch.object(
                daily_snapshot_prep,
                "_public_head_stability_findings",
                return_value=["head"],
            ) as head, \
            mock.patch.object(
                daily_snapshot_prep,
                "_calibration_commitment_findings",
                return_value=["calibration"],
            ) as calibration, \
            mock.patch.object(
                daily_snapshot_prep,
                "_public_snapshot_orphan_findings",
                return_value=["orphan"],
            ) as orphan:
            findings = daily_snapshot_prep.verify_public_precycle_guards(
                skip_public_head_stability=True,
            )

        self.assertEqual(["calibration", "orphan"], findings)
        head.assert_not_called()
        calibration.assert_called_once()
        orphan.assert_called_once()

    def test_interim_precycle_dry_run_keeps_post_stage_mutation_guard(self):
        ok_stage = {
            "label": "stage",
            "command": [],
            "returncode": 0,
            "stdout_tail": "",
            "stderr_tail": "",
        }
        with mock.patch.object(
            daily_snapshot_prep,
            "_public_artifact_hashes",
            side_effect=[
                {"data/public_snapshot.json": "dirty_current"},
                {"data/public_snapshot.json": "before"},
                {"data/public_snapshot.json": "after"},
            ],
        ), \
            mock.patch.object(daily_snapshot_prep, "_public_head_stability_findings") as head, \
            mock.patch.object(
                daily_snapshot_prep,
                "_calibration_commitment_findings",
                return_value=[],
            ), \
            mock.patch.object(
                daily_snapshot_prep,
                "_public_snapshot_orphan_findings",
                return_value=[],
            ), \
            mock.patch.object(daily_snapshot_prep, "_run_stage", return_value=ok_stage):
            result = daily_snapshot_prep.run_fast_review_check(
                "2026-06-10",
                skip_public_head_stability=True,
            )

        self.assertEqual(1, result["returncode"])
        self.assertIn("changed during website review cycle", result["stderr_tail"])
        head.assert_not_called()

    def test_interim_precycle_dry_run_flag_is_opt_in_cli(self):
        with mock.patch.object(daily_snapshot_prep, "run_prep", return_value=0) as run_prep:
            result = daily_snapshot_prep.main(["--interim-public-precycle-dry-run"])

        self.assertEqual(0, result)
        args = run_prep.call_args.args[0]
        self.assertTrue(args.interim_public_precycle_dry_run)

    def test_release_check_threads_interim_precycle_flag_to_fast_review_only(self):
        with mock.patch.object(daily_snapshot_prep, "run_fast_review_check", return_value={"returncode": 0}) as fast:
            daily_snapshot_prep.run_release_check(
                "2026-06-10",
                skip_public_head_stability=True,
            )

        fast.assert_called_once_with("2026-06-10", skip_public_head_stability=True)

    def test_calibration_commitment_guard_requires_at_least_15_hash_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / daily_snapshot_prep.public_exports.PUBLIC_CALIBRATION_LEDGER_PATH
            path.parent.mkdir(parents=True)
            path.write_text("ledger_id,commitment_hash\none,bad\n", encoding="utf-8")

            findings = daily_snapshot_prep._calibration_commitment_findings(root)

        self.assertTrue(any("expected at least 15 rows" in finding for finding in findings))
        self.assertTrue(any("commitment_hash does not match" in finding for finding in findings))

    def test_latest_reviewed_sitrep_sets_full_cycle_release_target(self):
        rows = [
            {"sitrep_number": 17, "data_as_of": "2026-05-31", "source_id": "s17", "published_at": "2026-06-01T00:00:00Z"},
            {"sitrep_number": 18, "data_as_of": "2026-06-01", "source_id": "s18", "published_at": "2026-06-02T00:00:00Z"},
        ]
        with mock.patch.object(daily_snapshot_prep.sitrep_promotions, "load_reviewed_promotions", return_value=rows):
            target = daily_snapshot_prep.resolve_release_target(
                "2026-06-03",
                "",
                prefer_latest_reviewed_sitrep=True,
            )

        self.assertEqual("2026-06-01", target["release_as_of"])
        self.assertEqual(18, target["sitrep_number"])
        self.assertEqual("latest_reviewed_sitrep_promotion", target["basis"])

    def test_explicit_release_target_keeps_reviewed_sitrep_metadata(self):
        rows = [
            {"sitrep_number": 18, "data_as_of": "2026-06-01", "source_id": "s18", "published_at": "2026-06-02T00:00:00Z"},
        ]
        with mock.patch.object(daily_snapshot_prep.sitrep_promotions, "load_reviewed_promotions", return_value=rows):
            target = daily_snapshot_prep.resolve_release_target(
                "2026-06-03",
                "2026-06-01",
                prefer_latest_reviewed_sitrep=True,
            )

        self.assertEqual("explicit_release_as_of", target["basis"])
        self.assertEqual(18, target["sitrep_number"])
        self.assertEqual("s18", target["source_id"])

    def test_website_sync_dry_run_flag_is_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "checkout" / "apps" / "site"
            script = site / "lib" / "scripts" / "sync-bdbv-lovs.py"
            script.parent.mkdir(parents=True)
            script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

            source_head = "a" * 40
            with mock.patch.object(daily_snapshot_prep.subprocess, "run") as run:
                run.side_effect = [
                    mock.Mock(returncode=0, stdout=source_head + "\n", stderr=""),
                    mock.Mock(returncode=0, stdout="", stderr=""),
                ]
                result = daily_snapshot_prep.sync_review_website(site, "2026-06-01", dry_run=True)

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(["git", "rev-parse", "--verify", "HEAD^{commit}"], run.call_args_list[0].args[0])
        sync_command = run.call_args_list[1].args[0]
        self.assertIn("--dry-run", sync_command)
        self.assertEqual(source_head, sync_command[sync_command.index("--expected-lovs-commit") + 1])

    def test_website_sync_refuses_unresolved_source_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "checkout" / "apps" / "site"
            script = site / "lib" / "scripts" / "sync-bdbv-lovs.py"
            script.parent.mkdir(parents=True)
            script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            with mock.patch.object(daily_snapshot_prep.subprocess, "run", side_effect=
                            subprocess.CalledProcessError(128, "git")) as run:
                result = daily_snapshot_prep.sync_review_website(site, "2026-06-01")

        self.assertEqual("failed", result["status"])
        self.assertIn("cannot resolve", result["reason"])
        self.assertEqual(1, run.call_count)

    def test_live_publish_requires_explicit_environment_gate(self):
        with mock.patch.dict(daily_snapshot_prep.os.environ, {}, clear=True):
            result = daily_snapshot_prep.run_live_publish(
                website_root=Path("/tmp/checkout/apps/site"),
                deploy_command="echo publish",
                enabled=True,
            )

        self.assertEqual("blocked", result["status"])
        self.assertIn("LOVS_ALLOW_LIVE_PUBLISH", result["reason"])


if __name__ == "__main__":
    unittest.main()
