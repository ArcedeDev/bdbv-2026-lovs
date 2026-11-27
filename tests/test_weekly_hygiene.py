# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 13: the loop's own files pass the public-text rules."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from lovs.forecast.weekly import spec

REPO = Path(__file__).resolve().parents[1]
# Built from fragments so this file does not itself trip the scan.
TOOL_NAMES = re.compile("|".join(("Cla" + "ude", "Anth" + "ropic", "Chat" + "GPT", "Open" + "AI", "Co" + "dex",
                                  "Co" + "pilot", r"\bG" + r"PT-?\d", "Gem" + "ini")), re.I)


def loop_files() -> list[Path]:
    files = sorted((REPO / "lovs" / "forecast" / "weekly").glob("*.py"))
    files += sorted((REPO / "tests").glob("test_weekly_*.py")) + [REPO / "tests" / "weekly_fixtures.py"]
    doc = REPO / "docs" / "weekly-forecast-loop.md"
    files.append(doc)
    store = REPO / "data" / "weekly-forecasts"
    if store.is_dir():
        files += sorted(p for p in store.rglob("*") if p.is_file())
    return files


class LoopHygieneTest(unittest.TestCase):
    def test_files_exist(self) -> None:
        for path in loop_files():
            self.assertTrue(path.is_file(), path)

    def test_no_em_dashes_and_no_tool_names(self) -> None:
        for path in loop_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), text, path)
            self.assertIsNone(TOOL_NAMES.search(text), path)

    def test_method_document_matches_the_code(self) -> None:
        # review/WEEKLY-CODE-REVIEW.md I11: N7 is the lag of the end Wednesday, scored settled only;
        # reproduction names the manifest's arrival records and what an outsider cannot rerun.
        doc = (REPO / "docs" / "weekly-forecast-loop.md").read_text(encoding="utf-8")
        self.assertNotIn("mean lag", doc)
        self.assertIn("lag of the end Wednesday", doc)
        self.assertIn("scored settled only", doc)
        self.assertIn("`arrivals`", doc)
        self.assertIn("cannot be rerun from the public repository", doc)

    def test_spec_is_pinned(self) -> None:
        # The full digest: the core (pinned in test_weekly_round2.py) and the candidate registry.
        # A core change needs a founder-approved revision with a new SPEC_VERSION; adding a
        # candidate changes only this digest.
        self.assertEqual(spec.SPEC_VERSION, "wk2")
        self.assertEqual(spec.spec_sha256(), PINNED_SPEC_SHA256)


PINNED_SPEC_SHA256 = "fdd36fac435f26e0d1466daf2c18cc7a2fd2f917cacaeaf4fc61f35735a5ab9b"

if __name__ == "__main__":
    unittest.main()
