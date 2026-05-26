"""Unit tests for the process-health gate.

Cases:
  (a) clean active change-id -> no findings
  (b) active change-id missing required sidecar -> hard finding
  (c) active change-id with em-dash in .md -> hard finding
  (d) shipped change-id with em-dash -> NOT flagged (closed book)
  (e) rot change-id (no marker, plan.md old) -> soft finding + em-dash hard
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from lovs.process_health import check_process_health


def _change_dir(root: Path, name: str) -> Path:
    d = root / ".process" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _touch_old(path: Path, seconds_ago: int) -> None:
    now = time.time()
    os.utime(path, (now - seconds_ago, now - seconds_ago))


class TestProcessHealth(unittest.TestCase):
    def test_clean_active_change_id_no_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = _change_dir(root, "2026-05-26-clean")
            (d / "plan.md").write_text("# Plan\n\nClean.\n", encoding="utf-8")
            (d / "plan.md.findings.json").write_text('{"schema_version":"assay-findings/v1","findings":[]}\n', encoding="utf-8")
            (d / "STATUS.md").write_text("status: active\n", encoding="utf-8")
            result = check_process_health([root / ".process"])
            self.assertEqual(result["hard"], [])
            self.assertEqual(result["soft"], [])
            self.assertEqual(result["scanned"], 1)

    def test_active_missing_sidecar_is_hard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = _change_dir(root, "2026-05-26-no-sidecar")
            (d / "plan.md").write_text("# Plan\n\nNo sidecar.\n", encoding="utf-8")
            (d / "STATUS.md").write_text("status: active\n", encoding="utf-8")
            result = check_process_health([root / ".process"])
            self.assertEqual(len(result["hard"]), 1)
            self.assertIn("missing sidecar plan.md.findings.json", result["hard"][0])

    def test_active_em_dash_is_hard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = _change_dir(root, "2026-05-26-em-dash")
            em = "—"
            (d / "plan.md").write_text(f"# Plan\n\nText {em} more.\n", encoding="utf-8")
            (d / "plan.md.findings.json").write_text('{"schema_version":"assay-findings/v1","findings":[]}\n', encoding="utf-8")
            (d / "STATUS.md").write_text("status: active\n", encoding="utf-8")
            result = check_process_health([root / ".process"])
            self.assertTrue(any("em-dash" in h for h in result["hard"]))
            self.assertTrue(any("plan.md" in h for h in result["hard"]))

    def test_shipped_em_dash_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = _change_dir(root, "2026-05-20-shipped")
            em = "—"
            (d / "plan.md").write_text(f"# Plan {em} historical.\n", encoding="utf-8")
            (d / "SHIPPED.md").write_text("status: shipped\n", encoding="utf-8")
            result = check_process_health([root / ".process"])
            self.assertEqual(result["hard"], [])
            self.assertEqual(result["soft"], [])

    def test_rot_change_id_soft_plus_em_dash_hard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = _change_dir(root, "2026-05-15-rot")
            em = "—"
            plan = d / "plan.md"
            plan.write_text(f"# Plan {em}\n", encoding="utf-8")
            _touch_old(plan, 48 * 3600)  # 48h old
            # no STATUS marker
            result = check_process_health([root / ".process"])
            self.assertTrue(any("no STATUS marker" in s for s in result["soft"]))
            self.assertTrue(any("em-dash" in h for h in result["hard"]))


if __name__ == "__main__":
    unittest.main()
