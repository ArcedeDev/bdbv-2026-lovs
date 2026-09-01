"""Deadline gate: the resolution-evidence feed must stay ahead of what is due.

Two of the three 2026 calibration stalls were input starvation, not design
failure. data/calibration-resolution-evidence.json stopped being fed on
2026-06-20; Blocks 3 and 4 then sat unresolved past their dates for months
while every other test stayed green, because nothing in the suite asserted
that the feed still covered the commitments the ledger had made.

This is that assertion. It starts failing the day a pinned point falls due
without evidence coverage, and keeps failing until the feed is refreshed. A
failure here is a DATA alarm, not a code regression: the fix is to feed
data/calibration-resolution-evidence.json, not to change this file.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
LEDGER = REPO / "data" / "calibration-ledger.json"
EVIDENCE = REPO / "data" / "calibration-resolution-evidence.json"


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value[:10])


class FeedLivenessGate(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.feed_as_of = _date(self.evidence["_meta"]["as_of"])
        self.today = dt.date.today()

    def _due_unresolved(self):
        """Points whose resolution date has passed and that carry no outcome."""
        due = []
        for block in self.ledger["blocks"]:
            if block.get("status") != "active":
                continue
            resolves = _date(block["resolves_at"])
            if resolves > self.today:
                continue
            for point in block["points"]:
                if "outcome" not in point:
                    due.append((block["block_id"], point["corridor"], resolves))
        return due

    def test_feed_covers_every_point_that_has_fallen_due(self):
        due = self._due_unresolved()
        if not due:
            return
        latest = max(r for _, _, r in due)
        self.assertGreaterEqual(
            self.feed_as_of,
            latest,
            "\n\nRESOLUTION-EVIDENCE FEED IS STALE.\n"
            f"  feed as_of:        {self.feed_as_of}\n"
            f"  latest due date:   {latest}\n"
            f"  points due, unresolved: {len(due)}\n"
            + "".join(f"    {c}  (due {r})\n" for _, c, r in sorted(due, key=lambda x: x[2]))
            + "\nThe resolver will return unscoreable_stale_feed for these rather than\n"
            "inventing false negatives, so nothing is silently mis-scored -- but the\n"
            "window is lost until the feed is refreshed. Feed\n"
            "data/calibration-resolution-evidence.json for the targets above and set\n"
            "_meta.as_of to the retrieval date. Do not edit this test to make it pass.",
        )

    def test_feed_is_not_drifting_far_behind_an_open_block(self):
        """Warn well before the deadline, not on it.

        An open block whose resolution date is inside two weeks and whose feed
        has not been touched since before the block was pinned is the shape the
        June stall had. Catching it here leaves time to gather public evidence,
        which for six cross-border targets is a day of work, not an afternoon.
        """
        soon = self.today + dt.timedelta(days=14)
        at_risk = []
        for block in self.ledger["blocks"]:
            if block.get("status") != "active":
                continue
            resolves = _date(block["resolves_at"])
            pinned = _date(block["pinned_at"])
            open_points = [p for p in block["points"] if "outcome" not in p]
            if open_points and self.today <= resolves <= soon and self.feed_as_of < pinned:
                at_risk.append((block["block_id"], resolves, len(open_points)))
        self.assertEqual(
            at_risk,
            [],
            "\n\nFEED HAS NOT BEEN TOUCHED SINCE THESE BLOCKS WERE PINNED, AND THEY\n"
            "RESOLVE WITHIN 14 DAYS:\n"
            + "".join(f"    {b}  resolves {r}  ({n} open points)\n" for b, r, n in at_risk)
            + f"  feed as_of: {self.feed_as_of}\n"
            "\nStart gathering resolution evidence now. Refresh the feed and set\n"
            "_meta.as_of. Do not edit this test to make it pass.",
        )

    def test_every_open_target_has_a_reachable_evidence_shape(self):
        """A target with no entry at all fails loudly; verify that stays true."""
        entries = {e["target_zone"] for e in self.evidence["evidence"]}
        open_targets = {
            p["target"]
            for b in self.ledger["blocks"]
            if b.get("status") == "active"
            for p in b["points"]
            if "outcome" not in p
        }
        missing = sorted(open_targets - entries)
        self.assertEqual(
            missing,
            [],
            f"\n\nOpen calibration points target zones with no evidence entry: {missing}\n"
            "These score unscoreable_no_feed (loudly, by design). Add an entry per\n"
            "target before the resolution date.",
        )


if __name__ == "__main__":
    unittest.main()
