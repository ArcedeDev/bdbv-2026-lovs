# SPDX-License-Identifier: Apache-2.0
"""Tests for release_snapshot.py release gates."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import release_snapshot
from lovs import release_contract
from lovs import model_tournament

# The model_tournament section released in PR #96 for the 4 October snapshot. The
# build ran where Python had no CA bundle, so the frozen Round 001 approval lookup
# failed and every other gate passed it.
RELEASED_INVALID_SECTION = {
    "schema_version": "bdbv-model-tournament-status/v2",
    "evaluated_as_of": "2026-10-06",
    "status": "invalid",
    "diagnostics": [{
        "severity": "error",
        "code": "model_tournament_artifact_invalid",
        "message": (
            "GitHub approval lookup failed: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] "
            "certificate verify failed: unable to get local issuer certificate (_ssl.c:1081)>"
        ),
    }],
}


class TestReleaseGates(unittest.TestCase):
    def test_public_artifact_leak_scan_is_clean(self):
        self.assertEqual([], release_snapshot.scan_public_artifacts_for_leaks())

    def test_website_source_gate_rejects_promoted_pdf_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            component = root / "app" / "bdbv-2026" / "_components" / "Sidebar.tsx"
            component.parent.mkdir(parents=True)
            component.write_text(
                "export const link = <a href=\"/bdbv-2026/brief.pdf\">Download brief</a>;\n",
                encoding="utf-8",
            )

            self.assertEqual(
                ["app/bdbv-2026/_components/Sidebar.tsx: links or promotes brief.pdf"],
                release_snapshot.scan_website_source_for_release_hazards(root),
            )

    def test_website_asset_gate_covers_generated_visuals(self):
        gated_sources = {source for source, _ in release_snapshot.WEBSITE_ASSETS}
        expected = {
            str(path.relative_to(release_snapshot.REPO_ROOT))
            for path in (release_snapshot.REPO_ROOT / "brief" / "visuals").glob("*.svg")
        }

        self.assertLessEqual(expected, gated_sources)


class TestModelTournamentStatusGate(unittest.TestCase):
    def setUp(self):
        self.summary = json.loads(release_snapshot.OUT_PATH.read_text(encoding="utf-8"))
        round_path = model_tournament.ROUNDS_DIR / "bdbv-2026-tournament-round-001.json"
        source_receipt = json.loads(round_path.read_text(encoding="utf-8"))[
            "freeze_receipt"
        ]["source_release"]["source_receipt"]
        # The public CI checkout omits the private raw source archive. Keep the
        # reviewed promotion envelope, but substitute its already frozen receipt.
        archive_boundary = mock.patch.object(
            release_contract, "_validated_receipt", return_value=source_receipt
        )
        archive_boundary.start()
        self.addCleanup(archive_boundary.stop)

    def check(self, section: dict, **kwargs) -> list[str]:
        with mock.patch.object(model_tournament, "verify_frozen_round_approval"):
            return release_snapshot.check_model_tournament_status(
                {"model_tournament": section}, **kwargs
            )

    def test_refuses_the_released_invalid_section_with_its_diagnostic(self):
        problems = self.check(RELEASED_INVALID_SECTION)

        self.assertIn("status is invalid", problems[0])
        self.assertIn("2026-10-06", problems[0])
        self.assertIn(RELEASED_INVALID_SECTION["diagnostics"][0]["message"], "\n".join(problems))

    def test_current_section_matches_canonical_projection(self):
        self.assertEqual([], self.check(self.summary["model_tournament"]))

    def test_evaluation_day_is_independent_of_data_day(self):
        summary = {**self.summary, "as_of": "2026-10-04", "data_as_of": "2026-10-04"}
        release_day = date.fromisoformat(summary["model_tournament"]["evaluated_as_of"])
        with mock.patch.object(model_tournament, "verify_frozen_round_approval"):
            self.assertEqual([], release_snapshot.check_model_tournament_status(
                summary, release_day=release_day
            ))

    def test_historical_prefreeze_projection_is_valid(self):
        with mock.patch.object(model_tournament, "verify_frozen_round_approval"):
            section = model_tournament.snapshot_status("2026-10-04")
        self.assertEqual("ready_for_freeze_review", section["status"])
        self.assertEqual(0, section["rounds"]["count"])
        self.assertEqual([], self.check(section, release_day=date(2026, 10, 4)))

    def test_missing_or_malformed_section_is_refused(self):
        self.assertIn("missing or malformed", release_snapshot.check_model_tournament_status({})[0])
        for value in (None, [], "active"):
            with self.subTest(value=value):
                self.assertIn("missing or malformed", release_snapshot.check_model_tournament_status(
                    {"model_tournament": value}
                )[0])

    def test_false_lifecycle_fields_are_refused(self):
        original = self.summary["model_tournament"]
        groups = original["rounds"]
        group = next(name for name in (
            "frozen", "active", "awaiting_resolution", "resolved", "evaluated"
        ) if groups[name])
        round_path = f"rounds.{group}[0]"
        next_status = original["next_eligible_round"]["status"]
        mutations = (
            ("status", lambda row: row.__setitem__(
                "status", "disabled" if row["status"] != "disabled" else "active"
            )),
            ("rounds.count", lambda row: row["rounds"].__setitem__(
                "count", row["rounds"]["count"] + 1
            )),
            (f"{round_path}.forecast_sha256", lambda row: row["rounds"][group][0].__setitem__(
                "forecast_sha256", "0" * 64
            )),
            (f"{round_path}.prediction_count", lambda row: row["rounds"][group][0].__setitem__(
                "prediction_count", row["rounds"][group][0]["prediction_count"] + 1
            )),
            (f"{round_path}.window_end", lambda row: row["rounds"][group][0].__setitem__(
                "window_end", "2099-01-01"
            )),
            (f"rounds.{group}.length", lambda row: row["rounds"].__setitem__(group, [])),
            ("next_eligible_round.status", lambda row: row["next_eligible_round"].__setitem__(
                "status", "scheduled" if next_status != "scheduled" else "ready_for_freeze_review"
            )),
        )
        for field, mutate in mutations:
            with self.subTest(field=field):
                section = copy.deepcopy(self.summary["model_tournament"])
                mutate(section)
                self.assertIn(field, self.check(section)[0])

    def test_stale_evaluation_day_is_refused_on_release_day(self):
        section = copy.deepcopy(self.summary["model_tournament"])
        release_day = date.fromisoformat(section["evaluated_as_of"]) + timedelta(days=1)
        self.assertIn("release UTC day", self.check(section, release_day=release_day)[0])

    def test_unreadable_evaluation_day_fails_closed(self):
        for value in (None, "", "6 October 2026", "20261007"):
            with self.subTest(evaluated_as_of=value):
                section = {**self.summary["model_tournament"], "evaluated_as_of": value}
                problems = self.check(section)
                self.assertIn("evaluated_as_of is unreadable", problems[0])

    def test_missing_round_history_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            section = self.summary["model_tournament"]
            self.assertIn("directory is missing", self.check(section, rounds_dir=root / "absent")[0])
            self.assertIn("round-001 artifact is missing", self.check(section, rounds_dir=root)[0])

    def test_git_history_detects_missing_later_round_and_rewritten_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rounds_dir = root / "data" / "model-tournament" / "rounds"
            rounds_dir.mkdir(parents=True)

            def git(*args):
                subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

            git("init", "-q")
            git("config", "user.name", "Tournament Test")
            git("config", "user.email", "tournament@example.invalid")
            first = rounds_dir / "bdbv-test-round-001.json"
            second = rounds_dir / "bdbv-test-round-002.json"
            first.write_text('{"round_id":"bdbv-test-round-001"}', encoding="utf-8")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "first freeze")
            second.write_text('{"round_id":"bdbv-test-round-002"}', encoding="utf-8")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "second freeze")

            self.assertEqual([], release_snapshot.check_frozen_round_history(rounds_dir, root))
            uncommitted = rounds_dir / "bdbv-test-round-uncommitted.json"
            uncommitted.write_text('{"round_id":"uncommitted"}', encoding="utf-8")
            self.assertIn("no committed history", release_snapshot.check_frozen_round_history(rounds_dir, root)[0])
            uncommitted.unlink()
            second.unlink()
            self.assertIn("round-002", release_snapshot.check_frozen_round_history(rounds_dir, root)[0])
            second.write_text('{"round_id":"bdbv-test-round-002"}', encoding="utf-8")
            first.write_text('{"round_id":"rewritten"}', encoding="utf-8")
            self.assertIn("has changed", release_snapshot.check_frozen_round_history(rounds_dir, root)[0])

            first.write_text('{"round_id":"bdbv-test-round-001"}', encoding="utf-8")
            base_branch = subprocess.run(
                ["git", "-C", str(root), "branch", "--show-current"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            git("checkout", "-qb", "new-round")
            third = rounds_dir / "bdbv-test-round-003.json"
            third.write_text('{"round_id":"first-frozen-bytes"}', encoding="utf-8")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "third freeze")
            third.write_text('{"round_id":"rewritten-before-merge"}', encoding="utf-8")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "rewrite frozen round")
            git("checkout", base_branch)
            git("-c", "commit.gpgsign=false", "merge", "--no-ff", "new-round", "-m", "register third round")
            self.assertIn("has changed", release_snapshot.check_frozen_round_history(rounds_dir, root)[0])

    def test_git_replace_cannot_rewrite_frozen_round_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rounds_dir = root / "data" / "model-tournament" / "rounds"
            rounds_dir.mkdir(parents=True)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(root), *args], check=True,
                    capture_output=True, text=True,
                ).stdout.strip()

            git("init", "-q")
            git("config", "user.name", "Tournament Test")
            git("config", "user.email", "tournament@example.invalid")
            round_path = rounds_dir / "bdbv-test-round-001.json"
            round_path.write_text('{"prediction":"original"}', encoding="utf-8")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "freeze")
            first = git("rev-parse", "HEAD")
            (root / "note.txt").write_text("later", encoding="utf-8")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "later")
            head = git("rev-parse", "HEAD")
            round_path.write_text('{"prediction":"rewritten"}', encoding="utf-8")
            git("add", ".")
            tree = git("write-tree")
            replacement_first = git("commit-tree", tree, "-m", "forged freeze")
            replacement_head = git("commit-tree", tree, "-p", first, "-m", "forged head")
            git("reset", "--hard", "HEAD")
            git("replace", first, replacement_first)
            git("replace", head, replacement_head)
            git("reset", "--hard", "HEAD")
            self.assertEqual("", git("status", "--porcelain"))
            self.assertEqual(head, git("rev-parse", "HEAD"))
            self.assertEqual('{"prediction":"rewritten"}', round_path.read_text())
            self.assertIn("replacement references", release_snapshot.check_frozen_round_history(rounds_dir, root)[0])

    def test_git_grafts_cannot_hide_original_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rounds_dir = root / "data" / "model-tournament" / "rounds"
            rounds_dir.mkdir(parents=True)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(root), *args], check=True,
                    capture_output=True, text=True,
                ).stdout.strip()

            git("init", "-q")
            git("config", "user.name", "Tournament Test")
            git("config", "user.email", "tournament@example.invalid")
            round_path = rounds_dir / "bdbv-test-round-001.json"
            round_path.write_text('{"prediction":"original"}', encoding="utf-8")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "freeze")
            round_path.write_text('{"prediction":"rewritten"}', encoding="utf-8")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "rewrite")
            self.assertIn("has changed", release_snapshot.check_frozen_round_history(rounds_dir, root)[0])
            (root / ".git" / "info" / "grafts").write_text(git("rev-parse", "HEAD") + "\n")
            self.assertEqual("", git("status", "--porcelain"))
            self.assertIn("grafts", release_snapshot.check_frozen_round_history(rounds_dir, root)[0])

    def test_unreadable_round_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / "bdbv-2026-tournament-round-001.json"
            path.write_text("{", encoding="utf-8")
            problems = self.check(self.summary["model_tournament"], rounds_dir=root)
        self.assertIn("canonical model-tournament projection is invalid", problems[0])
        self.assertIn("invalid JSON", "\n".join(problems))

    def test_missing_diagnostics_are_named(self):
        section = {key: value for key, value in RELEASED_INVALID_SECTION.items() if key != "diagnostics"}

        self.assertIn("no diagnostic recorded", self.check(section)[-1])

    def test_malformed_diagnostics_return_a_refusal(self):
        for value in (1, "bad", {"code": "bad"}):
            with self.subTest(value=value):
                section = {**RELEASED_INVALID_SECTION, "diagnostics": value}
                self.assertIn("diagnostics are malformed", self.check(section)[-1])

    def test_failed_approval_lookup_fails_closed(self):
        with mock.patch.object(
            model_tournament, "verify_frozen_round_approval", side_effect=RuntimeError("approval lookup unavailable")
        ):
            problems = release_snapshot.check_model_tournament_status(self.summary)
        self.assertIn("canonical model-tournament projection is invalid", problems[0])
        self.assertIn("approval lookup unavailable", "\n".join(problems))

    def test_release_gates_refuse_before_any_subprocess_gate(self):
        summary = {
            "as_of": "2026-10-04T23:59:59Z",
            "data_as_of": "2026-10-04",
            "model_tournament": RELEASED_INVALID_SECTION,
        }
        stderr = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(release_snapshot, "_run") as run,
            mock.patch.object(release_snapshot, "datetime") as clock,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            clock.now.return_value = datetime(2026, 10, 7, tzinfo=timezone.utc)
            passed = release_snapshot.run_release_gates(summary, website_public=pathlib.Path(tmp))

        self.assertFalse(passed)
        run.assert_not_called()
        self.assertIn("[FAIL] model-tournament projection gate", stderr.getvalue())
        self.assertIn("release UTC day", stderr.getvalue())

if __name__ == "__main__":
    unittest.main()
