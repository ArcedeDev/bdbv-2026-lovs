import json
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

    def test_calibration_commitment_guard_requires_15_hash_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / daily_snapshot_prep.public_exports.PUBLIC_CALIBRATION_LEDGER_PATH
            path.parent.mkdir(parents=True)
            path.write_text("ledger_id,commitment_hash\none,bad\n", encoding="utf-8")

            findings = daily_snapshot_prep._calibration_commitment_findings(root)

        self.assertTrue(any("expected 15 rows" in finding for finding in findings))
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

            with mock.patch.object(daily_snapshot_prep.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                result = daily_snapshot_prep.sync_review_website(site, "2026-06-01", dry_run=True)

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["dry_run"])
        self.assertIn("--dry-run", run.call_args.args[0])

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
