import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from lovs import cross_border_validation as C


class CrossBorderPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.protocol = json.loads((C.ROOT / C.PROTOCOL).read_text())
        self.protocol["country_universe"]["expected_member_count"] = 4
        (self.root / C.PROTOCOL).parent.mkdir(parents=True)
        (self.root / C.PROTOCOL).write_text(json.dumps(self.protocol))
        schedule = self.root / self.protocol["cadence_schedule"]
        schedule.parent.mkdir(parents=True)
        schedule.write_bytes((C.ROOT / self.protocol["cadence_schedule"]).read_bytes())
        self.payloads = {
            "country_universe": ["COD", "KEN", "RWA", "TZA"],
            "country_points": {"COD": [0, 0], "KEN": [0, 20], "RWA": [0, 1], "TZA": [0, 10]},
            "source_frontier": {"points": {"bunia": [0, 0]}, "observations": [
                {"date": "2026-09-20", "counts": {"bunia": 1}},
                {"date": "2026-10-28", "counts": {"bunia": 2}}]},
            "travel_hub": {"unit": "international_scheduled_arriving_seats",
                "coverage": "complete_country_universe", "window_start": "2026-10-01",
                "window_end": "2026-10-28", "reviewed_zero_countries": [],
                "countries": {"COD": 10, "KEN": 20, "RWA": 10, "TZA": 100}},
            "international_registry": {"reports": [{"report_id": "fixture-ken",
                "country": "KEN", "reported_on": "2026-10-26", "new_confirmed": 1, "cumulative_confirmed": 1,
                "sources": ["https://example.org/fixture-authority"]}]},
        }
        self.bundle = {"schema_version": "bdbv-cross-border-feature-bundle/v1",
                       "cutoff_utc": "2026-10-29T00:00:00Z", "inputs": []}
        for role in sorted(C.ROLES):
            self.bundle["inputs"].append({"role": role, "path": role + ".json",
                "sha256": "", "source_url": "https://example.org/review-fixture",
                "published_at": "2026-10-28T10:00:00Z", "reviewed_at": "2026-10-28T11:00:00Z",
                "reviewed_by": "fixture-reviewer"})
        self.write_inputs()

    def write_inputs(self):
        for receipt in self.bundle["inputs"]:
            raw = json.dumps(self.payloads[receipt["role"]], sort_keys=True).encode()
            (self.root / receipt["path"]).write_bytes(raw)
            receipt["sha256"] = hashlib.sha256(raw).hexdigest()
        (self.root / "bundle.json").write_text(json.dumps(self.bundle))

    def status(self):
        return C.prep_status("2026-10-29", root=self.root, bundle_path="bundle.json")

    def test_shared_country_matrix_and_meaningfully_different_baseline_order(self):
        status = self.status()
        preview = status["baseline_preview"]
        self.assertEqual(["RWA", "TZA"], preview["target_ids"])
        self.assertEqual(["COD", "KEN"], preview["excluded_pre_cutoff_countries"])
        a, b = preview["rows"]
        self.assertGreater(a["geography_rank"], b["geography_rank"])
        self.assertLess(a["travel_hub_rank"], b["travel_hub_rank"])
        self.assertFalse(status["launch_ready"])
        self.assertFalse(preview["registered"])
        self.assertEqual(status, self.status())

    def test_missing_country_is_blocked_even_if_it_would_be_excluded(self):
        del self.payloads["travel_hub"]["countries"]["KEN"]
        self.write_inputs()
        result = self.status()
        self.assertEqual("invalid_review_inputs", result["state"])
        self.assertIsNone(result["baseline_preview"])
        self.assertIn("unknown is not zero", str(result["blockers"]))

    def test_zero_is_not_missing_and_requires_explicit_review(self):
        self.payloads["travel_hub"]["countries"]["RWA"] = 0
        self.write_inputs()
        self.assertEqual("invalid_review_inputs", self.status()["state"])
        self.payloads["travel_hub"]["reviewed_zero_countries"] = ["RWA"]
        self.write_inputs()
        self.assertEqual(0, self.status()["baseline_preview"]["rows"][0]["travel_hub_rank"])

    def test_hashed_input_edit_and_duplicate_or_missing_role_are_rejected(self):
        (self.root / "country_points.json").write_text("{}")
        self.assertIn("hash mismatch", str(self.status()["blockers"]))
        self.write_inputs()
        self.bundle["inputs"].append(copy.deepcopy(self.bundle["inputs"][0]))
        (self.root / "bundle.json").write_text(json.dumps(self.bundle))
        self.assertEqual("invalid_review_inputs", self.status()["state"])
        self.bundle["inputs"].pop()
        self.bundle["inputs"].pop()
        (self.root / "bundle.json").write_text(json.dumps(self.bundle))
        self.assertIn("all five", str(self.status()["blockers"]))

    def test_future_and_ambiguous_review_clocks_fail_closed(self):
        for field, value in (("published_at", "2026-10-30T00:00:00Z"),
                             ("reviewed_at", "2026-10-29")):
            with self.subTest(field=field):
                receipt = self.bundle["inputs"][0]
                old = receipt[field]
                receipt[field] = value
                self.write_inputs()
                self.assertEqual("invalid_review_inputs", self.status()["state"])
                receipt[field] = old

    def test_stale_frontier_or_wrong_travel_period_and_unit_rejected(self):
        for role, field, value in (("travel_hub", "window_end", "2026-10-27"),
                                   ("travel_hub", "unit", "airport_count")):
            with self.subTest(field=field):
                old = self.payloads[role][field]
                self.payloads[role][field] = value
                self.write_inputs()
                self.assertEqual("invalid_review_inputs", self.status()["state"])
                self.payloads[role][field] = old
        self.payloads["source_frontier"]["observations"][-1]["date"] = "2026-10-10"
        self.write_inputs()
        self.assertIn("stale", str(self.status()["blockers"]))

    def test_numeric_edges_are_not_converted_into_valid_predictions(self):
        for value in (float("nan"), float("inf"), True, 91):
            with self.subTest(value=value):
                self.payloads["country_points"]["RWA"] = [value, 0]
                self.write_inputs()
                self.assertEqual("invalid_review_inputs", self.status()["state"])

    def test_later_country_report_is_not_used_and_medical_evacuation_does_not_exclude(self):
        registry = self.payloads["international_registry"]
        registry["reports"].append({"report_id": "future-rwa", "country": "RWA",
            "reported_on": "2026-10-30", "new_confirmed": 1, "cumulative_confirmed": 1,
            "sources": ["https://example.org/future"]})
        registry["medical_evacuations"] = [{"country": "TZA", "confirmed_on": "2026-10-25"}]
        self.write_inputs()
        self.assertEqual(["RWA", "TZA"], self.status()["baseline_preview"]["target_ids"])

    def test_same_day_and_future_source_observations_and_non_midnight_cutoff_blocked(self):
        self.payloads["source_frontier"]["observations"][-1]["date"] = "2026-10-29"
        self.write_inputs()
        self.assertIn("look-ahead", str(self.status()["blockers"]))
        self.bundle["cutoff_utc"] = "2026-10-29T12:00:00Z"
        self.write_inputs()
        self.assertIn("UTC midnight", str(self.status()["blockers"]))

    def test_no_missing_or_filled_review_fields_can_activate_this_adapter(self):
        for key in self.protocol["launch_reviews"]:
            self.protocol["launch_reviews"][key] = {"approved": True}
        self.protocol["candidate_model"] = {"model_id": "unverified-example"}
        (self.root / C.PROTOCOL).write_text(json.dumps(self.protocol))
        self.assertFalse(self.status()["launch_ready"])
        self.assertIn("draft_only_no_activation_path", self.status()["blockers"])
        self.protocol["state"] = "active"
        (self.root / C.PROTOCOL).write_text(json.dumps(self.protocol))
        self.assertEqual("invalid_review_inputs", self.status()["state"])

    def test_path_traversal_external_symlink_and_oversized_input_blocked(self):
        receipt = self.bundle["inputs"][0]
        original = receipt["path"]
        for path in ("../outside.json", "/etc/passwd"):
            receipt["path"] = path
            (self.root / "bundle.json").write_text(json.dumps(self.bundle))
            self.assertIn("inside the repository", str(self.status()["blockers"]))
        outside = self.root.parent / (self.root.name + "-outside")
        outside.write_text("{}")
        self.addCleanup(outside.unlink)
        (self.root / "link.json").symlink_to(outside)
        receipt["path"] = "link.json"
        (self.root / "bundle.json").write_text(json.dumps(self.bundle))
        self.assertIn("inside the repository", str(self.status()["blockers"]))
        receipt["path"] = original
        self.write_inputs()
        (self.root / original).write_bytes(b" " * (C.MAX_BYTES + 1))
        self.assertIn("4 MiB", str(self.status()["blockers"]))

    def test_real_default_remains_blocked_and_cannot_be_backdated(self):
        status = C.prep_status("2026-10-29")
        self.assertEqual("draft_review_required", status["state"])
        self.assertIsNone(status["baseline_preview"])
        self.assertIn("reviewed_complete_feature_bundle_required", status["blockers"])
        self.assertEqual("not_available_as_of", C.prep_status("2026-10-28")["state"])

    def test_malformed_missing_unicode_and_numeric_overflow_inputs_are_explicit(self):
        for raw in (b"null", b"{}", b"{", b"\xff", ("[" * 2000 + "]" * 2000).encode()):
            with self.subTest(raw=raw[:12]):
                (self.root / "bundle.json").write_bytes(raw)
                self.assertEqual("invalid_review_inputs", self.status()["state"])
        self.payloads["country_points"]["RWA"] = [10 ** 400, 0]
        self.write_inputs()
        self.assertEqual("invalid_review_inputs", self.status()["state"])
        self.payloads["country_universe"][2] = "RW\u200dA"
        self.write_inputs()
        self.assertEqual("invalid_review_inputs", self.status()["state"])

    def test_concurrent_readers_are_identical_and_do_not_write(self):
        before = {p.name: p.read_bytes() for p in self.root.glob("*.json")}
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda _: self.status(), range(16)))
        self.assertTrue(all(value == values[0] for value in values))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.glob("*.json")})

    def test_source_size_envelope_has_explicit_boundary(self):
        f = self.payloads["source_frontier"]
        f["points"] = {f"zone-{i}": [0, 0] for i in range(C.MAX_SOURCE_POINTS + 1)}
        self.write_inputs()
        self.assertIn("bounded review envelope", str(self.status()["blockers"]))

    def test_each_input_boundary_rejects_partial_json_missing_and_denied_access(self):
        for receipt in self.bundle["inputs"]:
            with self.subTest(role=receipt["role"]):
                path = self.root / receipt["path"]
                raw = path.read_bytes()
                path.write_bytes(b"{")
                old_hash = receipt["sha256"]
                receipt["sha256"] = hashlib.sha256(b"{").hexdigest()
                (self.root / "bundle.json").write_text(json.dumps(self.bundle))
                self.assertEqual("invalid_review_inputs", self.status()["state"])
                path.unlink()
                self.assertEqual("invalid_review_inputs", self.status()["state"])
                path.write_bytes(raw)
                receipt["sha256"] = old_hash
        with mock.patch.object(os, "open", side_effect=PermissionError("fixture denied")):
            self.assertEqual("invalid_review_inputs", self.status()["state"])

    def test_protocol_cannot_drop_review_gates_or_mislabel_the_baseline(self):
        original = copy.deepcopy(self.protocol)
        for field, value in (("maximum_frontier_age_days", float("nan")),
                             ("launch_reviews", {}),
                             ("baseline_models", [{"model_id": "other", "output_kind": "probability"}])):
            self.protocol = {**original, field: value}
            (self.root / C.PROTOCOL).write_text(json.dumps(self.protocol))
            self.assertEqual("invalid_review_inputs", self.status()["state"])

    def test_semantically_invalid_country_registry_never_crashes_prep(self):
        report = self.payloads["international_registry"]["reports"][0]
        for field, value in (("sources", []), ("cumulative_confirmed", 0)):
            with self.subTest(field=field):
                old = report[field]
                report[field] = value
                self.write_inputs()
                result = self.status()
                self.assertEqual("invalid_review_inputs", result["state"])
                self.assertIsNone(result["baseline_preview"])
                self.assertIn("RegistrationError", str(result["blockers"]))
                report[field] = old

    def test_reviewer_identity_cannot_be_null_or_coerced(self):
        for value in (None, False, 0, " "):
            with self.subTest(value=value):
                self.bundle["inputs"][0]["reviewed_by"] = value
                (self.root / "bundle.json").write_text(json.dumps(self.bundle))
                self.assertIn("source URL and reviewer required", str(self.status()["blockers"]))

    def test_fifo_is_rejected_without_blocking_daily_prep(self):
        os.mkfifo(self.root / "fifo.json")
        self.bundle["inputs"][0]["path"] = "fifo.json"
        (self.root / "bundle.json").write_text(json.dumps(self.bundle))
        code = ("import json,sys;from pathlib import Path;from lovs import cross_border_validation as c;"
                "print(json.dumps(c.prep_status('2026-10-29',root=Path(sys.argv[1]),bundle_path='bundle.json')))" )
        run = subprocess.run([sys.executable, "-c", code, str(self.root)], cwd=C.ROOT,
                             capture_output=True, text=True, timeout=2, check=True)
        self.assertIn("regular file", str(json.loads(run.stdout)["blockers"]))

    def test_error_metadata_does_not_disclose_paths_or_report_ids(self):
        missing = self.root / self.bundle["inputs"][0]["path"]
        missing.unlink()
        self.assertNotIn(str(self.root), str(self.status()))
        self.write_inputs()
        report = self.payloads["international_registry"]["reports"][0]
        report.update(report_id="PRIVATE-ID-DO-NOT-EMIT", sources=[])
        self.write_inputs()
        self.assertNotIn("PRIVATE-ID-DO-NOT-EMIT", str(self.status()))


if __name__ == "__main__":
    unittest.main()
