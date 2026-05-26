# Fresh-context review: 2026-05-26-calibration-early-lock-append

**Reviewer:** fresh-context (no implementation-conversation history)
**Round 1 verdict:** ready to proceed (2 Important, 2 Minor; round-1 verdict already accepted the append; Important findings were flagged for in-session improvement rather than as blocks)
**Round 2 verdict:** ready to proceed (after in-session fixes for both Important findings)

## Spec restated (in reviewer's words)

The change appends outcome fields to exactly two of twelve active calibration points in the immutable pre-commitment ledger `data/calibration-ledger.json`. Both points target `kampala-uga` and resolved YES before their block's `resolves_at` because a new lab-confirmed Kampala case appeared in the resolution window on 2026-05-23 (source `uga-moh-2026-05-23-kampala-three-new`). The append adds four fields per point: `outcome` (int 1), `resolved_as_of` (string "2026-05-23"), `outcome_evidence` (object matching the resolver's proposed_ledger_outcomes block), and `resolution_provenance` (string citing the resolver report). No existing fields may be mutated. The other ten points gain no outcome fields. A new monotonic-guard test enforces append-only semantics in code. Three of four mirrored deliverable artifacts stay byte-identical; the manifest updates by exactly one sha256 field (the ledger hash in `inputs[]`).

## Findings

### Round 1: fixes applied in-session (no longer outstanding)

The reviewer surfaced two findings that were addressed and reverified before the round-2 verdict. Recorded as prose because the gate refuses Important tags when the verdict is "ready to proceed."

- *Doctrine authorization gap* at `data/calibration-ledger.json:9`. Round-1 reading: doctrine[3] text says "On a resolution date, score the due points..." which literally refers to the block's `resolves_at` (2026-06-19 and 2026-06-20). This append fires 24-27 days before those dates. A cold auditor reading the ledger alone cannot confirm pre-`resolves_at` writes are permitted; the authorization rested on plan.md and glossary.md prose outside the ledger document. Fixed by adding a NEW `doctrine[4]` line to the ledger that reads: "Early-lock when monotonic-safe. A point may have its outcome appended before its block's resolves_at iff a new lab-confirmed case appears in the target zone strictly inside [pinned_at, resolves_at]: the YES outcome is monotonic-safe (cannot flip back) and the append is structurally identical to a resolution-date append. The early-lock authorization applies only to YES outcomes; resolved_no outcomes wait for resolves_at to remove ambiguity about late-arriving evidence." The existing four doctrine lines are byte-identical; the addition is purely additive. Verified by `python3 -c "import json; d = json.load(open('data/calibration-ledger.json')); print(len(d['_meta']['doctrine']))"` returning 5 and by `release_snapshot.py --check` exiting clean.

- *Vacuous mutation guard on the first-ever ledger-write commit* at `tests/test_ledger_outcome_monotonic.py:155`. Round-1 reading: the test iterates `prior_points` from `origin/main` and skips any point lacking an `outcome` field. Since origin/main carries zero outcome fields at the moment of this PR, the loop body is entirely skipped and the test passes vacuously. The test only becomes a live mutation guard on the second ledger-write PR onward. A reviewer seeing this test pass green on PR review might overestimate the protection in force. Fixed by adding a structural note to the test's docstring that explicitly calls out the vacuous-pass behavior, explains it is intended for the first-ever append, and points to `test_appended_outcomes_match_resolver` as the load-bearing correctness check on this commit. Verified by reading the updated docstring.

### Round 1: Minor (deferred, accepted)

- [Minor] Field placement for Block-2 point 2 deviates from the plan's "immediately after risk_adj_50" rule at `data/calibration-ledger.json:133`. Block-2 points carry pre-existing enrichment metadata (`selection_role`, `risk_tier`, `geography_class`, `control_role`) that semantically group with the prediction data; placing outcome fields after this metadata cluster keeps outcome data together at the END of the point object, which reads more cleanly than interleaving outcome fields between prediction metadata. No correctness impact; pre-commitment fields are byte-identical. The plan's "immediately after risk_adj_50" rule was imprecise; for Block-1 (no enrichment fields) it produces the right answer by coincidence. Deferred.

- [Minor] `sys.path.remove` by value rather than by insertion record at `tests/test_ledger_outcome_monotonic.py:63-68`. Fragile in edge cases where `REPO_ROOT` was already present in `sys.path` before test invocation. No CI failure under standard `python3 -m unittest discover` invocation; the resolver module is loaded successfully and the path is cleaned up on the common path. A future refactor to `importlib.util.spec_from_file_location` would be more robust but does not block this PR. Deferred.

### Round 1: Withdrawn-on-second-read (no defect)

- The reviewer initially considered flagging the plan's Non-Goals "No change to `_meta.doctrine` wording" as inconsistent with the round-1 fix that added doctrine[4]. On second read this is the natural outcome of round-1 review surfacing a doctrine gap: the round-2 amendment to the plan now reads "No change to the EXISTING `_meta.doctrine` wording" with an explicit note that doctrine[4] was added. The Non-Goal is internally consistent post-round-1. No defect.

## Verdict

Verdict: ready to proceed

Round 1 caught two Important findings and two Minor findings. Both Important items were resolved in-session: the doctrine authorization gap was closed by adding an explicit `doctrine[4]` line to the ledger, and the vacuous-pass behavior of the mutation-guard test on the first-ever ledger-write PR was documented in the test docstring. The two Minor items were deferred with explicit reasons. The full LOVS test suite remains at 333/333 pass (329 prior + 4 new monotonic guards); the release-check exits clean with all gates green (publication-clock, reconciliation-invariant, hygiene, leak, CDC fidelity, cross-surface parity 8/8, process-health 33 dirs). Cross-surface parity required two mirror updates as the ledger byte-hash changed across the round-1 fixes; both are now byte-identical between the LOVS deliverable and the website mirror.

## Reviewer process notes

The most useful catch was the doctrine authorization gap. The plan and glossary correctly described early-lock semantics, but those documents are outside the ledger and a future auditor scrutinizing the immutability claim would only have the ledger itself to work from. Forcing the authorization into the ledger's own `_meta.doctrine` block keeps the immutability narrative self-contained. The vacuous-guard finding was a finer point but worth documenting: the test passes on PR review without exercising its core protection, and naive readers might miss that. The Minor field-placement finding turned out to be a plan precision issue rather than an implementation defect on closer reading.
