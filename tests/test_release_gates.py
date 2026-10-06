# SPDX-License-Identifier: Apache-2.0
"""Tests for release_snapshot.py release gates."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import release_snapshot

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
    def check(self, section: dict, rounds_dir: pathlib.Path | None = None) -> list[str]:
        kwargs = {} if rounds_dir is None else {"rounds_dir": rounds_dir}
        return release_snapshot.check_model_tournament_status(
            {"model_tournament": section}, **kwargs
        )

    def rounds(self, tmp: str, *frozen_at: str) -> pathlib.Path:
        rounds_dir = pathlib.Path(tmp) / "rounds"
        rounds_dir.mkdir()
        for ordinal, stamp in enumerate(frozen_at, start=1):
            round_id = f"test-round-{ordinal:03d}"
            (rounds_dir / f"{round_id}.json").write_text(
                json.dumps({"round_id": round_id, "freeze_receipt": {"frozen_at": stamp}}),
                encoding="utf-8",
            )
        return rounds_dir

    def test_refuses_the_released_invalid_section_with_its_diagnostic(self):
        # Real rounds directory: Round 001 froze on 5 October, before the evaluation day.
        problems = self.check(RELEASED_INVALID_SECTION)

        self.assertIn("bdbv-2026-tournament-round-001", problems[0])
        self.assertIn("2026-10-06", problems[0])
        self.assertIn(RELEASED_INVALID_SECTION["diagnostics"][0]["message"], "\n".join(problems))

    def test_counts_rounds_from_the_evaluation_day_not_the_snapshot_date(self):
        # Round 001 froze a day after the 4 October snapshot it first appeared in.
        summary = {"as_of": "2026-10-04T23:59:59Z", "model_tournament": RELEASED_INVALID_SECTION}

        problems = release_snapshot.check_model_tournament_status(summary)
        self.assertIn("froze on or before", problems[0])

    def test_round_frozen_on_the_evaluation_day_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            rounds_dir = self.rounds(tmp, "2026-10-06T23:59:59Z")
            self.assertIn("test-round-001", self.check(RELEASED_INVALID_SECTION, rounds_dir)[0])

    def test_invalid_before_any_round_froze_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            later = self.rounds(tmp, "2026-10-07T00:00:00Z")
            self.assertEqual([], self.check(RELEASED_INVALID_SECTION, later))
            self.assertEqual([], self.check(RELEASED_INVALID_SECTION, pathlib.Path(tmp) / "absent"))

    def test_other_statuses_and_an_absent_section_pass(self):
        for status in ("active", "frozen", "scheduled", "ready_for_freeze_review", "disabled"):
            with self.subTest(status=status):
                self.assertEqual([], self.check({"evaluated_as_of": "2026-10-06", "status": status}))
        self.assertEqual([], release_snapshot.check_model_tournament_status({}))

    def test_unreadable_evaluation_day_fails_closed(self):
        for value in (None, "", "6 October 2026"):
            with self.subTest(evaluated_as_of=value):
                section = {**RELEASED_INVALID_SECTION, "evaluated_as_of": value}
                problems = self.check(section)
                self.assertIn("cannot rule out a frozen round", problems[0])
                self.assertIn("CERTIFICATE_VERIFY_FAILED", "\n".join(problems))

    def test_unreadable_round_fails_closed(self):
        for body in ("{", "[]", json.dumps({"freeze_receipt": {}}),
                     json.dumps({"freeze_receipt": {"frozen_at": "2026-10-05"}})):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                rounds_dir = self.rounds(tmp)
                (rounds_dir / "broken-round.json").write_text(body, encoding="utf-8")
                problems = self.check(RELEASED_INVALID_SECTION, rounds_dir)
                self.assertIn("broken-round.json is unreadable", problems[0])

    def test_missing_diagnostics_are_named(self):
        section = {key: value for key, value in RELEASED_INVALID_SECTION.items() if key != "diagnostics"}

        self.assertIn("no diagnostic recorded", self.check(section)[-1])

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
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            passed = release_snapshot.run_release_gates(summary, website_public=pathlib.Path(tmp))

        self.assertFalse(passed)
        run.assert_not_called()
        self.assertIn("[FAIL] model-tournament status gate", stderr.getvalue())
        self.assertIn("CERTIFICATE_VERIFY_FAILED", stderr.getvalue())

    def test_committed_snapshot_has_no_invalid_tournament_after_a_freeze(self):
        summary = json.loads(release_snapshot.OUT_PATH.read_text(encoding="utf-8"))

        # An absent section would pass the gate, so pin that the build output carries one.
        self.assertIn("status", summary["model_tournament"])
        self.assertEqual([], release_snapshot.check_model_tournament_status(summary))


if __name__ == "__main__":
    unittest.main()
