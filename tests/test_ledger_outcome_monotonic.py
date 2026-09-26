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

One mutation is admissible: a correction under a founder ruling (the
ledger's "Correct by superseding" doctrine). The point must keep the outcome
fields it had on origin/main, verbatim, as the newest entry of its
`superseded_outcomes` list, dated by an ISO `superseded_at` that is not in the
future, and an amendment appended to
data/calibration-ledger.pinned-block-hashes.json on that date must name the
point. Duplicate block or point ids are refused outright, since every reader
of the ledger would otherwise pick its own copy. The evidence feed behind the
outcomes is held to the same rule: an entry on origin/main is superseded,
never edited or removed. Anything short of that is refused.

stdlib-only.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "data" / "calibration-ledger.json"
EVIDENCE_PATH = REPO_ROOT / "data" / "calibration-resolution-evidence.json"
PINNED_HASHES_PATH = REPO_ROOT / "data" / "calibration-ledger.pinned-block-hashes.json"

OUTCOME_FIELDS = ("outcome", "resolved_as_of", "outcome_evidence", "resolution_provenance")
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


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


def _iso_day(text: object) -> dt.date | None:
    """The day an ISO ``YYYY-MM-DD`` string names; None for anything else."""
    if not isinstance(text, str) or not _ISO_DAY.fullmatch(text):
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _index_points_by_hid(ledger_doc: dict) -> dict[str, dict]:
    """Points by hypothesis_id. A duplicate block or point id raises: gates that keep
    the first copy and gates that keep the last would otherwise read different ledgers."""
    out: dict[str, dict] = {}
    block_ids: set[str] = set()
    for block in ledger_doc.get("blocks", []):
        if block.get("block_id") in block_ids:
            raise ValueError(f"the ledger carries two blocks with id {block.get('block_id')!r}")
        block_ids.add(block.get("block_id"))
        for point in block.get("points", []):
            if point["hypothesis_id"] in out:
                raise ValueError(f"the ledger carries two points with id {point['hypothesis_id']!r}")
            out[point["hypothesis_id"]] = point
    return out


def outcome_mutation_problems(
    prior_doc: dict,
    working_doc: dict,
    prior_amendments: list[str],
    amendments: list[str],
    today: dt.date | None = None,
) -> list[str]:
    """Every way the working ledger breaks the frozen-outcome contract against a prior one.

    An outcome field present on a prior point must survive unchanged, unless the point
    records a correction: exactly one new `superseded_outcomes` entry that holds the
    prior outcome fields verbatim, carries an ISO `superseded_at` later than any earlier
    entry's and not in the future, and is named by an amendment appended since the prior
    state on that date. The amendments log is append-only, and each new amendment opens
    with an ISO date no earlier than the one before it and not in the future. Earlier
    `superseded_outcomes` entries are history and must survive unchanged, and a point
    with no prior outcome may carry no history at all. ``today`` defaults to the UTC
    date; a date one day ahead is allowed for a ruling dated in a zone ahead of UTC.
    """
    latest = (today or dt.datetime.now(dt.timezone.utc).date()) + dt.timedelta(days=1)
    problems: list[str] = []
    if amendments[: len(prior_amendments)] != prior_amendments:
        problems.append("the amendments log was rewritten; earlier amendments must survive unchanged")
    prior_doctrine = prior_doc.get("_meta", {}).get("doctrine", [])
    if working_doc.get("_meta", {}).get("doctrine", [])[: len(prior_doctrine)] != prior_doctrine:
        problems.append("the ledger doctrine was rewritten; earlier lines must survive unchanged")
    new_amendments = amendments[len(prior_amendments):]
    previous = max(filter(None, (_iso_day(a.split(":", 1)[0]) for a in prior_amendments)), default=dt.date.min)
    for amendment in new_amendments:
        day = _iso_day(amendment.split(":", 1)[0])
        if day is None or day < previous or day > latest:
            problems.append(
                f"amendment {amendment[:40]!r} must open with an ISO date that is not before "
                f"the previous amendment's and not in the future"
            )
            continue
        previous = day
    try:
        working_points = _index_points_by_hid(working_doc)
        prior_points = _index_points_by_hid(prior_doc)
    except ValueError as exc:
        return problems + [str(exc)]
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
        dated = entry.get("superseded_at")
        day = _iso_day(dated)
        if day is None or day > latest:
            problems.append(f"the new superseded_outcomes entry on {hid} needs an ISO superseded_at date not in the future")
            continue
        if prior_history and day <= (_iso_day(prior_history[-1].get("superseded_at")) or dt.date.min):
            problems.append(f"the new superseded_outcomes entry on {hid} is not dated after the previous one")
        if not any(a.startswith(f"{dated}:") and hid in a for a in new_amendments):
            problems.append(
                f"no amendment appended since origin/main authorizes the correction of {hid}; "
                f"a correction needs a founder ruling recorded as a dated amendment"
            )
    return problems


def _without_outcomes(block: dict) -> dict:
    """The block as pinned: every point without its outcome fields or their history."""
    stripped = json.loads(json.dumps(block))
    for point in stripped.get("points", []):
        for field in (*OUTCOME_FIELDS, "superseded_outcomes"):
            point.pop(field, None)
    return stripped


def repin_problems(prior_pins: dict, pins: dict, prior_ledger: dict, ledger: dict) -> list[str]:
    """Every pinned block hash dropped, or changed other than by an authorized outcome write.

    ``prior_pins`` and ``pins`` are the pinned-hash documents, ``prior_ledger`` and
    ``ledger`` the ledgers they pin. A re-pin is how a block legitimately changes (an
    outcome appended or corrected), so the block-hash gate accepts whatever hash is
    pinned. This check gives each re-pin its authority and its limit: an amendment
    appended since the prior state must name the block's id, and the block must equal
    its prior copy once outcome fields are set aside, so no band, point, id or date of a
    pinned block can be rewritten by re-pinning it.
    """
    prior_amendments = prior_pins.get("_meta", {}).get("amendments", [])
    new_amendments = pins.get("_meta", {}).get("amendments", [])[len(prior_amendments):]
    hashes = pins.get("block_hashes", {})
    prior_blocks = {b.get("block_id"): b for b in prior_ledger.get("blocks", [])}
    blocks = {b.get("block_id"): b for b in ledger.get("blocks", [])}
    problems: list[str] = []
    for block_id, digest in prior_pins.get("block_hashes", {}).items():
        if block_id not in hashes:
            problems.append(f"pinned block {block_id!r} was dropped from block_hashes")
            continue
        if hashes[block_id] == digest:
            continue
        if not any(block_id in a for a in new_amendments):
            problems.append(
                f"the pinned hash of {block_id!r} changed, but no amendment appended since "
                f"origin/main names that block id"
            )
        if block_id not in blocks or block_id not in prior_blocks:
            problems.append(f"re-pinned block {block_id!r} is missing from the ledger")
        elif _without_outcomes(blocks[block_id]) != _without_outcomes(prior_blocks[block_id]):
            problems.append(
                f"re-pinned block {block_id!r} changed beyond its outcome fields; a pinned "
                f"block's points, bands, ids and dates never change"
            )
    return problems


def feed_edit_problems(prior_doc: dict, working_doc: dict) -> list[str]:
    """Every way the working evidence feed edits or removes, rather than supersedes, a prior entry.

    Entries are matched by (target_zone, source_id), which must be unique. The doctrine
    list is append-only, the purpose (which states the resolution rule) is fixed, and the
    retrieval note keeps its earlier text as a prefix. as_of may move.
    """
    problems: list[str] = []
    prior_meta, working_meta = prior_doc.get("_meta", {}), working_doc.get("_meta", {})
    prior_doctrine = prior_meta.get("doctrine", [])
    if working_meta.get("doctrine", [])[: len(prior_doctrine)] != prior_doctrine:
        problems.append("the feed doctrine was rewritten; earlier lines must survive unchanged")
    if working_meta.get("purpose") != prior_meta.get("purpose"):
        problems.append("the feed purpose, which states the resolution rule, was changed")
    if not str(working_meta.get("retrieval_note", "")).startswith(str(prior_meta.get("retrieval_note", ""))):
        problems.append("the feed retrieval_note was rewritten; append to it instead")
    working: dict[tuple, dict] = {}
    for entry in working_doc.get("evidence", []):
        key = (entry.get("target_zone"), entry.get("source_id"))
        if key in working:
            problems.append(f"two feed entries share target zone and source id {key!r}")
        working[key] = entry
    for entry in prior_doc.get("evidence", []):
        key = (entry.get("target_zone"), entry.get("source_id"))
        if key not in working:
            problems.append(f"feed entry {key!r} was removed; supersede it with a new dated entry instead")
        elif working[key] != entry:
            problems.append(f"feed entry {key!r} was edited; supersede it with a new dated entry instead")
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

    def test_every_repin_since_origin_main_is_named_in_an_amendment(self) -> None:
        prior_raw = _git_show("origin/main:data/calibration-ledger.pinned-block-hashes.json")
        if prior_raw is None:
            self.skipTest("origin/main:data/calibration-ledger.pinned-block-hashes.json unreachable")
        prior_ledger_raw = _git_show("origin/main:data/calibration-ledger.json")
        self.assertIsNotNone(prior_ledger_raw, "origin/main carries the pinned-hash file but not the ledger")
        pins = json.loads(PINNED_HASHES_PATH.read_text(encoding="utf-8"))
        self.assertEqual([], repin_problems(json.loads(prior_raw), pins, json.loads(prior_ledger_raw), self.working))


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
    TODAY = dt.date(2026, 9, 27)

    def _problems(self, prior, working, prior_amendments, amendments):
        return outcome_mutation_problems(prior, working, prior_amendments, amendments, today=self.TODAY)

    def test_a_documented_correction_is_admitted(self):
        self.assertEqual([], self._problems(self._prior(), self._corrected(), [], self.AMENDMENTS))

    def test_a_bare_mutation_is_refused(self):
        bare = self._doc(outcome=0, resolved_as_of="2026-07-04", outcome_evidence={"source_id": "new"},
                         resolution_provenance="corrected")
        self.assertTrue(self._problems(self._prior(), bare, [], self.AMENDMENTS))

    def test_a_correction_that_alters_the_prior_is_refused(self):
        altered = self._corrected(outcome=0)
        self.assertIn("verbatim", " ".join(self._problems(self._prior(), altered, [], self.AMENDMENTS)))

    def test_a_correction_without_a_matching_amendment_is_refused(self):
        for amendments in ([], ["2026-09-25: names " + self.HID], ["2026-09-26: names another point"]):
            problems = self._problems(self._prior(), self._corrected(), [], amendments)
            self.assertIn("no amendment appended", " ".join(problems), amendments)

    def test_rewriting_or_padding_history_is_refused(self):
        corrected = self._corrected()
        # A later commit may not drop or alter the recorded history...
        rewritten = self._doc(outcome=0, resolved_as_of="2026-07-04", outcome_evidence={"source_id": "new"},
                              resolution_provenance="corrected 2026-09-26", superseded_outcomes=[])
        self.assertTrue(self._problems(corrected, rewritten, self.AMENDMENTS, self.AMENDMENTS))
        # ...nor add a history entry with no change behind it.
        padded = json.loads(json.dumps(corrected))
        padded["blocks"][0]["points"][0]["superseded_outcomes"].append({"outcome": 0, "superseded_at": "2026-09-26"})
        self.assertTrue(self._problems(corrected, padded, self.AMENDMENTS, self.AMENDMENTS))

    def test_a_deleted_outcome_field_is_refused(self):
        deleted = self._doc(outcome=1, resolved_as_of="2026-07-04", outcome_evidence={"source_id": "old"})
        self.assertIn("deleted", " ".join(self._problems(self._prior(), deleted, [], self.AMENDMENTS)))

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
        problems = self._problems(corrected, flipped, self.AMENDMENTS + stale, self.AMENDMENTS + stale)
        self.assertIn("no amendment appended", " ".join(problems))
        self.assertEqual([], self._problems(corrected, flipped, self.AMENDMENTS, self.AMENDMENTS + stale))

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
        problems = self._problems(corrected, backdated, self.AMENDMENTS, amendments)
        self.assertIn("not dated after", " ".join(problems))

    def test_an_edited_amendment_log_is_refused(self):
        edited = ["2026-09-26: authorized outcome correction naming another point."]
        problems = self._problems(self._prior(), self._prior(), edited, self.AMENDMENTS)
        self.assertIn("amendments log was rewritten", " ".join(problems))

    def test_a_rewritten_ledger_doctrine_is_refused(self):
        prior, working = self._prior(), self._prior()
        prior["_meta"] = {"doctrine": ["Pin once.", "Correct by superseding."]}
        working["_meta"] = {"doctrine": ["Pin once.", "Correct freely."]}
        self.assertIn("ledger doctrine was rewritten", " ".join(self._problems(prior, working, [], [])))
        working["_meta"] = {"doctrine": ["Pin once.", "Correct by superseding.", "A new line."]}
        self.assertEqual([], self._problems(prior, working, [], []))

    def test_history_on_a_point_without_a_prior_outcome_is_refused(self):
        problems = self._problems(self._doc(), self._corrected(), [], self.AMENDMENTS)
        self.assertIn("had no outcome to supersede", " ".join(problems))

    def test_a_malformed_or_future_correction_date_is_refused(self):
        # "2026-09-3" sorts after "2026-09-26" as text; it and the rest are not ISO days.
        for dated in ("2026-09-3", "zzzz", "2026-09-26T00:00:00Z", "2099-12-31"):
            problems = self._problems(self._prior(), self._corrected(superseded_at=dated), [],
                                      [f"{dated}: correction naming {self.HID}"])
            self.assertIn("ISO superseded_at", " ".join(problems), dated)

    def test_an_amendment_out_of_date_order_or_undated_is_refused(self):
        for late in ("2026-09-20: out of order", "undated amendment", "2099-01-01: in the future"):
            problems = self._problems(self._prior(), self._prior(), self.AMENDMENTS, self.AMENDMENTS + [late])
            self.assertIn("must open with an ISO date", " ".join(problems), late)

    def test_a_duplicate_point_or_block_id_is_refused(self):
        # A second copy of a point, in its own block or in a block of the same id, would let
        # one gate read the untouched copy while another reads the altered one.
        corrected = self._corrected()
        shadow = json.loads(json.dumps(corrected))
        shadow["blocks"].append({"block_id": "calibration-block:shadow", "points": [self._prior()["blocks"][0]["points"][0]]})
        self.assertIn("two points with id", " ".join(self._problems(self._prior(), shadow, [], self.AMENDMENTS)))
        twin = json.loads(json.dumps(corrected))
        twin["blocks"].append(json.loads(json.dumps(corrected["blocks"][0])))
        self.assertIn("two blocks with id", " ".join(self._problems(self._prior(), twin, [], self.AMENDMENTS)))


class TestRepinNeedsANamedAmendment(unittest.TestCase):
    """A pinned block hash changes only by an outcome write, with an amendment since origin/main naming the block."""

    BLOCK = "calibration-block:test:2026-06-04"

    def _pins(self, digest: str, *amendments: str) -> dict:
        return {"_meta": {"amendments": ["2026-06-09: pinned."] + list(amendments)},
                "block_hashes": {self.BLOCK: digest, "calibration-block:test:2026-05-20": "b"}}

    def _ledger(self, **point_fields) -> dict:
        point = {"hypothesis_id": "p1", "risk_adj_50": [0.3, 0.7], **point_fields}
        return {"blocks": [{"block_id": self.BLOCK, "pinned_at": "2026-06-04", "points": [point]}]}

    def test_a_named_outcome_repin_is_admitted(self):
        resolved = self._ledger(outcome=0, resolved_as_of="2026-07-04", superseded_outcomes=[{"outcome": 1}])
        self.assertEqual([], repin_problems(self._pins("a"), self._pins("c", f"2026-09-26: re-pins {self.BLOCK}."),
                                            self._ledger(), resolved))

    def test_a_silent_or_unnamed_repin_is_refused(self):
        prior, resolved = self._pins("a"), self._ledger(outcome=0)
        for amendments in ((), ("2026-09-26: re-pins the June block.",)):
            problems = repin_problems(prior, self._pins("c", *amendments), self._ledger(), resolved)
            self.assertIn("names that block id", " ".join(problems))
        # An amendment already on origin/main cannot authorize a later re-pin.
        named = self._pins("c", f"2026-09-26: re-pins {self.BLOCK}.")
        again = self._pins("d", f"2026-09-26: re-pins {self.BLOCK}.")
        self.assertIn("names that block id", " ".join(repin_problems(named, again, resolved, self._ledger(outcome=1))))

    def test_a_named_repin_that_moves_a_band_is_refused(self):
        # Naming the block authorizes an outcome write, not a rewrite of what was pinned.
        moved = self._ledger(outcome=0, risk_adj_50=[0.01, 0.02])
        problems = repin_problems(self._pins("a"), self._pins("c", f"2026-10-01: re-pins {self.BLOCK}."),
                                  self._ledger(), moved)
        self.assertIn("changed beyond its outcome fields", " ".join(problems))

    def test_a_dropped_pin_is_refused(self):
        pins = self._pins("a")
        del pins["block_hashes"][self.BLOCK]
        self.assertIn("was dropped", " ".join(repin_problems(self._pins("a"), pins, self._ledger(), self._ledger())))


class TestEvidenceFeedIsSupersededNeverEdited(unittest.TestCase):
    """A feed entry on origin/main is superseded by a new entry, never edited or removed."""

    OLD = {"target_zone": "x-cod", "source_id": "old", "confirmed_in_window": True, "residual_uncertainty": "weak"}
    NEW = {"target_zone": "x-cod", "source_id": "new", "confirmed_in_window": False, "supersedes": "old"}

    def _feed(self, *entries, doctrine=("a",)) -> dict:
        return {"_meta": {"doctrine": list(doctrine), "as_of": "2026-09-15"}, "evidence": [dict(e) for e in entries]}

    def test_superseding_and_moving_meta_are_admitted(self):
        working = self._feed(self.OLD, self.NEW, doctrine=("a", "b"))
        working["_meta"]["as_of"] = "2026-10-01"
        self.assertEqual([], feed_edit_problems(self._feed(self.OLD), working))

    def test_an_edited_or_removed_entry_is_refused(self):
        edited = {k: v for k, v in self.OLD.items() if k != "residual_uncertainty"}
        self.assertIn("was edited", " ".join(feed_edit_problems(self._feed(self.OLD), self._feed(edited, self.NEW))))
        self.assertIn("was removed", " ".join(feed_edit_problems(self._feed(self.OLD), self._feed(self.NEW))))

    def test_a_rewritten_doctrine_or_a_duplicate_key_is_refused(self):
        self.assertIn("doctrine was rewritten",
                      " ".join(feed_edit_problems(self._feed(self.OLD), self._feed(self.OLD, doctrine=("b",)))))
        prior = self._feed(self.OLD)
        prior["_meta"].update(purpose="rule", retrieval_note="first")
        for field, value, needle in (("purpose", "another rule", "purpose"), ("retrieval_note", "rewritten", "retrieval_note")):
            working = json.loads(json.dumps(prior))
            working["_meta"][field] = value
            self.assertIn(needle, " ".join(feed_edit_problems(prior, working)), field)
        appended = json.loads(json.dumps(prior))
        appended["_meta"]["retrieval_note"] = "first | second"
        self.assertEqual([], feed_edit_problems(prior, appended))
        self.assertIn("share target zone and source id",
                      " ".join(feed_edit_problems(self._feed(self.OLD), self._feed(self.OLD, self.OLD))))

    def test_feed_entries_against_origin_main(self):
        prior_raw = _git_show("origin/main:data/calibration-resolution-evidence.json")
        if prior_raw is None:
            self.skipTest("origin/main:data/calibration-resolution-evidence.json unreachable")
        working = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        self.assertEqual([], feed_edit_problems(json.loads(prior_raw), working))



if __name__ == "__main__":
    unittest.main()
