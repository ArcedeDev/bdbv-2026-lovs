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

One mutation is admissible: a correction under a dated, documented review
(the ledger's "Correct by superseding" doctrine). The point must keep the
outcome fields it had on origin/main, verbatim, as the newest entry of its
`superseded_outcomes` list, and an amendment in
data/calibration-ledger.pinned-block-hashes.json dated that entry's
`superseded_at` must name the point. Anything short of that is refused.

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
PINNED_HASHES_PATH = REPO_ROOT / "data" / "calibration-ledger.pinned-block-hashes.json"

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


def outcome_mutation_problems(
    prior_doc: dict, working_doc: dict, prior_amendments: list[str], amendments: list[str]
) -> list[str]:
    """Every way the working ledger breaks the frozen-outcome contract against a prior one.

    An outcome field present on a prior point must survive unchanged, unless the point
    records a correction: exactly one new `superseded_outcomes` entry that holds the
    prior outcome fields verbatim, dated later than any earlier entry, and named by an
    amendment appended since the prior state. The amendments log itself is append-only,
    earlier `superseded_outcomes` entries are history and must survive unchanged, and a
    point with no prior outcome may carry no history at all.
    """
    problems: list[str] = []
    if amendments[: len(prior_amendments)] != prior_amendments:
        problems.append("the amendments log was rewritten; earlier amendments must survive unchanged")
    new_amendments = amendments[len(prior_amendments):]
    working_points = _index_points_by_hid(working_doc)
    prior_points = _index_points_by_hid(prior_doc)
    for hid, working_point in working_points.items():
        if "outcome" not in prior_points.get(hid, {}) and working_point.get("superseded_outcomes"):
            problems.append(f"{hid} carries superseded_outcomes but had no outcome to supersede")
    for hid, prior_point in prior_points.items():
        if "outcome" not in prior_point:
            continue
        working_point = working_points.get(hid)
        if working_point is None:
            problems.append(f"point {hid} had outcome on origin/main but is missing from working ledger")
            continue
        prior_history = prior_point.get("superseded_outcomes", [])
        history = working_point.get("superseded_outcomes", [])
        if history[: len(prior_history)] != prior_history:
            problems.append(f"superseded_outcomes history on {hid} was rewritten")
            continue
        prior_fields = {field: prior_point[field] for field in OUTCOME_FIELDS if field in prior_point}
        for field in prior_fields:
            if field not in working_point:
                problems.append(f"existing outcome field {field} on {hid} was deleted")
        changed = [f for f, value in prior_fields.items() if f in working_point and working_point[f] != value]
        added = history[len(prior_history):]
        if not changed:
            if added:
                problems.append(f"{hid} gained a superseded_outcomes entry but its outcome did not change")
            continue
        if len(added) != 1:
            problems.append(
                f"existing outcome field(s) {changed} on {hid} were mutated without exactly one new "
                f"superseded_outcomes entry"
            )
            continue
        entry = added[0]
        if {field: entry.get(field) for field in prior_fields} != prior_fields:
            problems.append(f"the new superseded_outcomes entry on {hid} does not keep the prior outcome verbatim")
        dated = str(entry.get("superseded_at", ""))
        if prior_history and dated <= str(prior_history[-1].get("superseded_at", "")):
            problems.append(f"the new superseded_outcomes entry on {hid} is not dated after the previous one")
        if not dated or not any(a.startswith(f"{dated}:") and hid in a for a in new_amendments):
            problems.append(f"no amendment appended since origin/main, dated {dated!r}, names the corrected point {hid}")
    return problems


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
        prior_hashes_raw = _git_show("origin/main:data/calibration-ledger.pinned-block-hashes.json")
        # A ledger without its amendments log would make every current amendment look new.
        self.assertIsNotNone(prior_hashes_raw, "origin/main carries the ledger but not its pinned-hash file")
        prior_amendments = json.loads(prior_hashes_raw)["_meta"]["amendments"]
        amendments = json.loads(PINNED_HASHES_PATH.read_text(encoding="utf-8"))["_meta"]["amendments"]
        self.assertEqual(
            [], outcome_mutation_problems(json.loads(prior_raw), self.working, prior_amendments, amendments)
        )


class TestOutcomeCorrectionGuard(unittest.TestCase):
    """The one admissible mutation is a dated, documented correction; everything else is refused."""

    HID = "calibration-point:bdbv-uga-cod-2026:30d:test"

    def _doc(self, **point_fields) -> dict:
        point = {"hypothesis_id": self.HID, "corridor": "a -> b", **point_fields}
        return {"blocks": [{"block_id": "calibration-block:test", "points": [point]}]}

    def _prior(self) -> dict:
        return self._doc(outcome=1, resolved_as_of="2026-07-04", outcome_evidence={"source_id": "old"},
                         resolution_provenance="appended 2026-09-01")

    def _corrected(self, **entry_overrides) -> dict:
        prior_fields = {k: v for k, v in self._prior()["blocks"][0]["points"][0].items() if k in OUTCOME_FIELDS}
        entry = prior_fields | {"superseded_at": "2026-09-26", "superseded_by": "ruling"} | entry_overrides
        return self._doc(outcome=0, resolved_as_of="2026-07-04", outcome_evidence={"source_id": "new"},
                         resolution_provenance="corrected 2026-09-26", superseded_outcomes=[entry])

    AMENDMENTS = [f"2026-09-26: authorized outcome correction naming {HID}."]

    def test_a_documented_correction_is_admitted(self):
        self.assertEqual([], outcome_mutation_problems(self._prior(), self._corrected(), [], self.AMENDMENTS))

    def test_a_bare_mutation_is_refused(self):
        bare = self._doc(outcome=0, resolved_as_of="2026-07-04", outcome_evidence={"source_id": "new"},
                         resolution_provenance="corrected")
        self.assertTrue(outcome_mutation_problems(self._prior(), bare, [], self.AMENDMENTS))

    def test_a_correction_that_alters_the_prior_is_refused(self):
        altered = self._corrected(outcome=0)
        self.assertIn("verbatim", " ".join(outcome_mutation_problems(self._prior(), altered, [], self.AMENDMENTS)))

    def test_a_correction_without_a_matching_amendment_is_refused(self):
        for amendments in ([], ["2026-09-25: names " + self.HID], ["2026-09-26: names another point"]):
            problems = outcome_mutation_problems(self._prior(), self._corrected(), [], amendments)
            self.assertIn("no amendment appended", " ".join(problems), amendments)

    def test_rewriting_or_padding_history_is_refused(self):
        corrected = self._corrected()
        # A later commit may not drop or alter the recorded history...
        rewritten = self._doc(outcome=0, resolved_as_of="2026-07-04", outcome_evidence={"source_id": "new"},
                              resolution_provenance="corrected 2026-09-26", superseded_outcomes=[])
        self.assertTrue(outcome_mutation_problems(corrected, rewritten, self.AMENDMENTS, self.AMENDMENTS))
        # ...nor add a history entry with no change behind it.
        padded = json.loads(json.dumps(corrected))
        padded["blocks"][0]["points"][0]["superseded_outcomes"].append({"outcome": 0, "superseded_at": "2026-09-26"})
        self.assertTrue(outcome_mutation_problems(corrected, padded, self.AMENDMENTS, self.AMENDMENTS))

    def test_a_deleted_outcome_field_is_refused(self):
        deleted = self._doc(outcome=1, resolved_as_of="2026-07-04", outcome_evidence={"source_id": "old"})
        self.assertIn("deleted", " ".join(outcome_mutation_problems(self._prior(), deleted, [], self.AMENDMENTS)))

    def test_a_second_correction_cannot_reuse_an_old_amendment(self):
        # The corrected state is now the prior; changing the outcome again needs its own
        # newly appended amendment, not one already on record.
        corrected = self._corrected()
        point = corrected["blocks"][0]["points"][0]
        flipped = json.loads(json.dumps(corrected))
        again = flipped["blocks"][0]["points"][0]
        again["superseded_outcomes"].append(
            {field: point[field] for field in OUTCOME_FIELDS} | {"superseded_at": "2026-09-27", "superseded_by": "x"}
        )
        again.update(outcome=1, outcome_evidence={"source_id": "newer"}, resolution_provenance="flipped")
        stale = ["2026-09-27: names " + self.HID]
        problems = outcome_mutation_problems(corrected, flipped, self.AMENDMENTS + stale, self.AMENDMENTS + stale)
        self.assertIn("no amendment appended", " ".join(problems))
        self.assertEqual([], outcome_mutation_problems(corrected, flipped, self.AMENDMENTS, self.AMENDMENTS + stale))

    def test_a_correction_dated_before_the_last_one_is_refused(self):
        corrected = self._corrected()
        point = corrected["blocks"][0]["points"][0]
        backdated = json.loads(json.dumps(corrected))
        again = backdated["blocks"][0]["points"][0]
        again["superseded_outcomes"].append(
            {field: point[field] for field in OUTCOME_FIELDS} | {"superseded_at": "2026-09-20", "superseded_by": "x"}
        )
        again.update(outcome=1)
        amendments = self.AMENDMENTS + ["2026-09-20: names " + self.HID]
        problems = outcome_mutation_problems(corrected, backdated, self.AMENDMENTS, amendments)
        self.assertIn("not dated after", " ".join(problems))

    def test_an_edited_amendment_log_is_refused(self):
        edited = ["2026-09-26: authorized outcome correction naming another point."]
        problems = outcome_mutation_problems(self._prior(), self._prior(), edited, self.AMENDMENTS)
        self.assertIn("amendments log was rewritten", " ".join(problems))

    def test_history_on_a_point_without_a_prior_outcome_is_refused(self):
        problems = outcome_mutation_problems(self._doc(), self._corrected(), [], self.AMENDMENTS)
        self.assertIn("had no outcome to supersede", " ".join(problems))



if __name__ == "__main__":
    unittest.main()
