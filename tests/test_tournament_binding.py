# SPDX-License-Identifier: Apache-2.0
import copy
import hashlib
import pathlib
import tempfile
from unittest import mock

from lovs import model_tournament as T
from tests.test_model_tournament import TournamentFixture


class BuildBindingTests(TournamentFixture):
    def freeze(self, candidate):
        return T.build_forecast_manifest(
            candidate, self.source_snapshot(), registry=self.registry(), schedule=self.schedule(),
            control=self.control(), existing_rounds=[], frozen_at="2026-08-05T10:00:00Z",
            approval_receipt=self.approval_receipt(candidate))

    def test_missing_receipt_and_policy_cannot_freeze(self):
        for key in ("build_receipt", "resolution_policy"):
            candidate = self.candidate()
            candidate.pop(key)
            with self.assertRaises(T.TournamentConfigError):
                self.freeze(candidate)

    def test_cutoff_after_real_freeze_and_future_input_are_rejected(self):
        for mutate in (
            lambda c: c["build_receipt"].update(source_availability_cutoff_utc="2026-08-05T10:00:01Z"),
            lambda c: c["build_receipt"]["inputs"][2].update(reviewed_at="2026-08-06T00:00:00Z"),
            lambda c: c["build_receipt"]["inputs"][2].pop("reviewed_at"),
        ):
            candidate = self.candidate()
            mutate(candidate)
            with self.assertRaises(T.TournamentConfigError):
                self.freeze(candidate)

    def test_substitution_and_unknown_policy_cannot_freeze(self):
        for key in ("candidate_payload_sha256", "registry_content_sha256", "schedule_content_sha256",
                    "source_snapshot_content_sha256", "source_release_id", "round_id"):
            candidate = self.candidate()
            candidate["build_receipt"][key] = "b" * 64
            with self.subTest(key=key), self.assertRaises(T.TournamentConfigError):
                self.freeze(candidate)
        candidate = self.candidate()
        candidate["resolution_policy"]["review_required"] = False
        with self.assertRaises(T.TournamentConfigError):
            self.freeze(candidate)

    def test_bound_policy_tamper_fails_even_with_rehashed_forecast(self):
        round_doc = self.round()
        round_doc["resolution_policy"]["review_required"] = False
        round_doc["freeze_receipt"]["forecast_sha256"] = T.forecast_hash(round_doc)
        with self.assertRaises(T.TournamentConfigError):
            T.validate_round(round_doc)

    def test_legacy_round_remains_readable_but_cannot_be_newly_frozen(self):
        doc = self.round()
        doc["schema_version"] = T.LEGACY_ROUND_SCHEMA_VERSION
        doc.pop("build_receipt")
        doc.pop("resolution_policy")
        candidate = {k: v for k, v in self.candidate().items() if k not in {"build_receipt", "resolution_policy"}}
        receipt = doc["freeze_receipt"]
        receipt["candidate_sha256"] = T.content_hash(candidate)
        receipt["approval"]["candidate_sha256"] = receipt["candidate_sha256"]
        receipt["forecast_sha256"] = T.forecast_hash(doc)
        T.validate_round(doc)
        with self.assertRaises(T.TournamentConfigError):
            self.freeze(candidate)

    def test_frozen_approval_reconstructs_all_bound_fields(self):
        doc = self.round()
        with mock.patch.object(T, "verify_github_pr_approval", return_value=doc["freeze_receipt"]["approval"]) as verify:
            T.verify_frozen_round_approval(doc, self.schedule())
        self.assertEqual(self.candidate(), verify.call_args.args[2])

    def test_actual_input_bytes_and_repository_boundary(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(T, "REPO_ROOT", pathlib.Path(tmp)):
            path = pathlib.Path(tmp) / "input.json"
            path.write_bytes(b"source")
            row = {"path": "input.json", "sha256": hashlib.sha256(b"source").hexdigest()}
            candidate = {"build_receipt": {"inputs": [row]}}
            T._verify_input_bytes(candidate)
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(T.TournamentConfigError, "hash mismatch"):
                T._verify_input_bytes(candidate)
            for unsafe in ("../escape", str(path)):
                row["path"] = unsafe
                with self.assertRaisesRegex(T.TournamentConfigError, "repository-relative"):
                    T._verify_input_bytes(candidate)

    def test_real_freeze_rebuild_rejects_spoofed_or_incomplete_inputs(self):
        from lovs import tournament_candidates as C
        candidate = self.candidate()
        with mock.patch.object(T, "_verify_input_bytes"), mock.patch.object(C, "build_candidate") as build:
            build.return_value = (copy.deepcopy(candidate), {})
            T.verify_build_inputs(candidate)
            self.assertIn("rounds_dir", build.call_args.kwargs)
            build.return_value[0]["build_receipt"]["inputs"][0]["sha256"] = "f" * 64
            with self.assertRaisesRegex(T.TournamentConfigError, "deterministic rebuild"):
                T.verify_build_inputs(candidate)
            candidate["build_receipt"]["inputs"].append(copy.deepcopy(candidate["build_receipt"]["inputs"][0]))
            with self.assertRaisesRegex(T.TournamentConfigError, "exactly one"):
                T.verify_build_inputs(candidate)

    def test_date_only_review_uses_conservative_bound(self):
        self.assertEqual("2026-10-05T11:59:59+00:00", T.availability_bound("2026-10-04", "review").isoformat())
