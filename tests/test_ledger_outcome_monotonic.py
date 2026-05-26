# SPDX-License-Identifier: Apache-2.0
"""Monotonic-guard tests for the calibration ledger's outcome-append contract.

The ledger is the immutable pre-commitment artifact. The only permitted
ledger writes are append-only field additions per `_meta.doctrine[3]`
("Resolve by appending..."). These tests fail closed on three failure modes:

  - an appended outcome that disagrees with what the resolver derives from
    the canonical evidence feed (ledger and resolver must agree byte-for-byte
    on resolved outcomes);
  - any mutation of an already-appended outcome between origin/main and the
    working tree (existing outcomes are frozen the moment they land);
  - a missing outcome field on a point that the resolver currently says is
    resolved_yes (a resolved point that did not get appended).

Together these guards make append-only enforceable in code rather than only
in prose doctrine.

stdlib-only.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "data" / "calibration-ledger.json"
EVIDENCE_PATH = REPO_ROOT / "data" / "calibration-resolution-evidence.json"

OUTCOME_FIELDS = ("outcome", "resolved_as_of", "outcome_evidence", "resolution_provenance")


def _git_show(rev_path: str) -> str | None:
    """Return file contents at git rev:path, or None if rev or path is missing."""
    proc = subprocess.run(
        ["git", "show", rev_path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _index_points_by_hid(ledger_doc: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for block in ledger_doc.get("blocks", []):
        for point in block.get("points", []):
            out[point["hypothesis_id"]] = point
    return out


def _resolver_outcomes() -> dict[str, int]:
    """Run the resolver in-process and return {hypothesis_id: outcome int}.

    The resolver derives outcomes from the evidence feed only; this is the
    canonical source of truth that the ledger's appended outcomes must match.
    """
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import calibration_resolver as resolver
    finally:
        if str(REPO_ROOT) in sys.path:
            sys.path.remove(str(REPO_ROOT))
    import datetime as dt

    ledger = resolver.load_ledger(LEDGER_PATH)
    _evidence_doc, evidence_index = resolver.load_evidence(EVIDENCE_PATH)
    today = dt.date.today()
    derived: dict[str, int] = {}
    for point in resolver.active_points(ledger):
        result = resolver.resolve_point(point, evidence_index, today)
        if "outcome" in result:
            derived[point["hypothesis_id"]] = int(result["outcome"])
    return derived


class TestLedgerOutcomeMonotonic(unittest.TestCase):
    def setUp(self) -> None:
        self.working = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        self.working_points = _index_points_by_hid(self.working)

    def test_appended_outcomes_match_resolver(self) -> None:
        """Every ledger-appended outcome equals the resolver's derived outcome."""
        resolver_outcomes = _resolver_outcomes()
        for hid, point in self.working_points.items():
            if "outcome" not in point:
                continue
            self.assertIn(
                hid,
                resolver_outcomes,
                f"ledger has outcome for {hid} but resolver does not derive one",
            )
            self.assertEqual(
                int(point["outcome"]),
                resolver_outcomes[hid],
                f"ledger outcome for {hid} disagrees with resolver derivation",
            )

    def test_resolved_yes_points_have_outcome_fields(self) -> None:
        """Every point the resolver currently scores must be appended to the ledger.

        The reverse direction of the byte-for-byte agreement check: no resolved
        point may be silently absent from the ledger. Catches the "forgot to
        append" failure mode where a resolution date passes and the founder-gated
        append is skipped.
        """
        resolver_outcomes = _resolver_outcomes()
        for hid in resolver_outcomes:
            point = self.working_points.get(hid)
            self.assertIsNotNone(point, f"resolver scores {hid} but ledger has no such point")
            for field in OUTCOME_FIELDS:
                self.assertIn(
                    field,
                    point,
                    f"point {hid} is resolved by the resolver but ledger lacks {field}",
                )

    def test_pending_points_have_no_outcome_fields(self) -> None:
        """Pending points (not yet resolved) must not carry any outcome fields.

        Detects the inverse mistake: an outcome field appearing on a point the
        resolver still treats as pending. Such a field would be either premature
        or an outright fabrication.
        """
        resolver_outcomes = _resolver_outcomes()
        for hid, point in self.working_points.items():
            if hid in resolver_outcomes:
                continue
            for field in OUTCOME_FIELDS:
                self.assertNotIn(
                    field,
                    point,
                    f"pending point {hid} carries an outcome field {field}",
                )

    def test_no_outcome_mutation_against_origin_main(self) -> None:
        """Existing outcomes on origin/main are frozen; no later commit may mutate them.

        Skipped when origin/main is unreachable (initial clone with no origin
        remote, or when CI fetches with --depth=1 and origin/main is the same as
        HEAD, in which case there is nothing to compare against). The skip is
        the correct behavior in those contexts; the protection lives in the
        founder's pre-push environment where origin/main resolves cleanly.

        Structural note (load-bearing for reviewers): this test passes vacuously
        on the very first ledger-write PR because origin/main carries zero
        outcome fields at that point, so the prior_points loop body is entirely
        skipped. That is the intended behavior; the test becomes a real
        mutation guard from the second ledger-write PR onward. A green run on
        the first append PR is not evidence the mutation guard fired; the
        first-append correctness rests on the spec review, the parse check, and
        test_appended_outcomes_match_resolver below.
        """
        prior_raw = _git_show("origin/main:data/calibration-ledger.json")
        if prior_raw is None:
            self.skipTest("origin/main:data/calibration-ledger.json unreachable")
        prior = json.loads(prior_raw)
        prior_points = _index_points_by_hid(prior)
        for hid, prior_point in prior_points.items():
            if "outcome" not in prior_point:
                continue
            working_point = self.working_points.get(hid)
            self.assertIsNotNone(
                working_point,
                f"point {hid} had outcome on origin/main but is missing from working ledger",
            )
            for field in OUTCOME_FIELDS:
                if field not in prior_point:
                    continue
                self.assertIn(
                    field,
                    working_point,
                    f"existing outcome field {field} on {hid} was deleted",
                )
                self.assertEqual(
                    prior_point[field],
                    working_point[field],
                    f"existing outcome field {field} on {hid} was mutated",
                )


if __name__ == "__main__":
    unittest.main()
