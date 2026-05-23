# SPDX-License-Identifier: Apache-2.0
"""Tests for release_snapshot.py release gates."""
from __future__ import annotations

import pathlib
import tempfile
import unittest

import release_snapshot


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


if __name__ == "__main__":
    unittest.main()
