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


if __name__ == "__main__":
    unittest.main()
