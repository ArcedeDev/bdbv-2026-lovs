# SPDX-License-Identifier: Apache-2.0
"""Tests for release-time README snapshot-date currency guard."""
from __future__ import annotations

import json
import pathlib
import unittest

import release_snapshot

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestReadmeCurrencyGuard(unittest.TestCase):
    def test_format_snapshot_date(self):
        self.assertEqual("24 May 2026", release_snapshot._format_snapshot_date("2026-05-24"))
        self.assertEqual("1 June 2026", release_snapshot._format_snapshot_date("2026-06-01"))

    def test_stale_phrase_is_flagged(self):
        text = "The 23 May 2026 snapshot indicates the picture."
        self.assertEqual(
            ["23 May 2026"],
            release_snapshot.find_stale_readme_snapshot_dates(text, "2026-05-24"),
        )

    def test_current_phrase_is_clean(self):
        text = "The 24 May 2026 snapshot indicates the picture."
        self.assertEqual(
            [],
            release_snapshot.find_stale_readme_snapshot_dates(text, "2026-05-24"),
        )

    def test_non_snapshot_dates_are_ignored(self):
        text = "Imperial published a 20 May 2026 update; ECDC reported on 19 May 2026."
        self.assertEqual(
            [],
            release_snapshot.find_stale_readme_snapshot_dates(text, "2026-05-24"),
        )

    def test_live_readme_matches_built_snapshot(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        out = json.loads(
            (REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text(encoding="utf-8")
        )
        as_of = str(out["as_of"])[:10]
        self.assertEqual([], release_snapshot.find_stale_readme_snapshot_dates(readme, as_of))


if __name__ == "__main__":
    unittest.main()
