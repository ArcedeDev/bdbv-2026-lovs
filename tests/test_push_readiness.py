"""Unit tests for the push-readiness composer + verdict.

Cases:
  (a) clean -> READY TO PUSH, no blockers, verdict line machine-parseable
  (b) parity mismatch -> BLOCKED with parity-mismatch reason
  (c) process-health hard finding -> BLOCKED with health reason
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cycle_status import build_push_readiness


def _stage_clean_repo(root: Path) -> tuple[Path, Path, Path]:
    """Build a synthetic LOVS root + website public root + .process dir with one clean change-id."""
    lovs = root / "lovs"
    web = root / "web"
    process = lovs / ".process"
    process.mkdir(parents=True)
    web.mkdir(parents=True)
    (lovs / "deliverables").mkdir(parents=True)
    payload = b"%PDF-1.7 clean"
    (lovs / "deliverables" / "brief.pdf").write_bytes(payload)
    (web / "brief.pdf").write_bytes(payload)
    # One clean active change-id
    c = process / "2026-05-26-clean"
    c.mkdir()
    (c / "plan.md").write_text("# Plan\n", encoding="utf-8")
    (c / "plan.md.findings.json").write_text('{"schema_version":"assay-findings/v1","findings":[]}\n', encoding="utf-8")
    (c / "STATUS.md").write_text("status: active\n", encoding="utf-8")
    return lovs, web, process


class TestPushReadiness(unittest.TestCase):
    def test_clean_yields_ready_to_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            lovs, web, process = _stage_clean_repo(Path(tmp))
            state = build_push_readiness("2026-05-26", lovs, web, [process])
            self.assertEqual(state["verdict"], "READY TO PUSH")
            self.assertEqual(state["blockers"], [])
            self.assertEqual(state["parity"]["mismatches"], [])
            self.assertEqual(state["health"]["hard"], [])

    def test_parity_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            lovs, web, process = _stage_clean_repo(Path(tmp))
            # Drift the website copy
            (web / "brief.pdf").write_bytes(b"%PDF-1.7 DRIFTED")
            state = build_push_readiness("2026-05-26", lovs, web, [process])
            self.assertTrue(state["verdict"].startswith("BLOCKED"))
            self.assertTrue(any("parity mismatch" in b for b in state["blockers"]))

    def test_health_hard_finding_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            lovs, web, process = _stage_clean_repo(Path(tmp))
            # Introduce an em-dash into the active change-id
            em = "—"
            (process / "2026-05-26-clean" / "plan.md").write_text(f"# Plan {em}\n", encoding="utf-8")
            state = build_push_readiness("2026-05-26", lovs, web, [process])
            self.assertTrue(state["verdict"].startswith("BLOCKED"))
            self.assertTrue(any("process-health" in b for b in state["blockers"]))


if __name__ == "__main__":
    unittest.main()
