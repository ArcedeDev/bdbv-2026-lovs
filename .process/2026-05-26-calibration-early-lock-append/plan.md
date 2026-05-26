# Plan: first early-lock outcome-append to the calibration ledger

**Change-id:** 2026-05-26-calibration-early-lock-append
**Date:** 2026-05-26
**Size classification:** Medium

## Intent
Execute the first-ever outcome-append into `data/calibration-ledger.json`. Two of the twelve active calibration points have empirically resolved YES per the read-only resolver: `bunia -> kampala-uga` (Block-1) and `rwampara -> kampala-uga` (Block-2). Both were triggered by the same in-window evidence (3 new lab-confirmed Kampala cases on 2026-05-23, source `uga-moh-2026-05-23-kampala-three-new`), and both are monotonic-safe early-locks per the resolver doctrine. The ledger's own `_meta.doctrine` already authorizes this as the fourth listed rule ("Resolve by appending"), so this change is the doctrine's first invocation rather than a doctrine extension. The append closes the open question raised by the user: the ledger should not stay silent on facts the resolver has been recognizing since 2026-05-24.

## Success Criteria
- [ ] `data/calibration-ledger.json` has `outcome: 1`, `resolved_as_of: "2026-05-23"`, and an `outcome_evidence` block added to exactly the two early-locked points (`hypothesis_id` `e77003de63` and `92b62f4c0c`).
- [ ] No other ledger fields change. The original `risk_adj_50`, `pinned_at`, `resolves_at`, `source`, `target`, `horizon_days`, and `hypothesis_id` are byte-identical before and after.
- [ ] The other ten points have no `outcome`, `resolved_as_of`, or `outcome_evidence` fields added. The ledger keeps these as bare prediction rows until their respective resolution.
- [ ] A new monotonic-guard test (`tests/test_ledger_outcome_monotonic.py`) asserts: (a) for every point with an appended outcome, the ledger's outcome equals the resolver's computed outcome for that point byte-for-byte; (b) once a point has an `outcome`, no later commit may change it (the test fails closed if a future diff mutates an existing outcome).
- [ ] `release_snapshot.py --check` stays green (all existing gates pass, none of them depend on `outcome` field presence).
- [ ] `python3 -m unittest discover -s tests` is 329/329 + new monotonic-guard test (so 330+/330+ pass).
- [ ] 3 of 4 mirrored publisher artifacts (`brief.pdf`, `lovs-public-health-dataset.xlsx`, `lovs-public-health-dataset.schema.json`) are byte-identical before and after. The 4th (`lovs-public-health-dataset.manifest.json`) updates by exactly one hash field (the ledger sha256 in `inputs[]`) and is mirrored to the website in the same change to keep cross-surface byte-parity green.
- [ ] CI on the pushed branch passes the `public-release-gates` workflow.

## Non-Goals
- No change to the resolver's logic. `calibration_resolver.py` continues to derive outcomes from the evidence feed and never reads the ledger's outcome fields. The append is a record-keeping operation, not a state-driver.
- No change to the 10 still-pending points. Their resolution stays gated on their respective `resolves_at` (Block-1 2026-06-19, Block-2 2026-06-20).
- No new tool or CLI for outcome-append. Two-row hand-edit through `Edit` is the right size; if future appends become repetitive, a `ledger_outcome_append.py` with the same atomic-write + monotonic-guard contract can be built then.
- No change to the EXISTING `_meta.doctrine` wording. (Round-2 amendment after fresh-context review: a NEW doctrine line is added, `doctrine[4]`, explicitly authorizing early-lock YES appends inside the window. The existing lines are byte-identical. This makes the authorization visible to a cold reader of the ledger document alone, rather than requiring them to also read plan.md and glossary.md.)
- No public-surface SEMANTIC change. The brief, the dataset, and the schema are byte-identical before and after. The dataset manifest changes by one hash field (the ledger's sha256 is listed in `inputs[]` for provenance), so the manifest is republished and the website mirror is updated in the same PR; no other website changes.
- No backfill of historical outcomes. The 2 early-locks are the only outcomes currently appended; the remaining 10 land at their `resolves_at` dates.

## Constraints
- The ledger is the pre-commitment artifact. The only ledger writes ever permitted are append-only field additions per the existing doctrine; no mutation of existing fields, no removal of points or blocks, no edits to `risk_adj_50` / `pinned_at` / `resolves_at` / `hypothesis_id`.
- The atomic-write contract from `_atomic_write_text` at `calibration_resolver.py:57-66` is bypass-free: that function refuses to write `LEDGER_PATH` by path identity. The hand-edit through `Edit` is the correct channel for the founder-gated step, and the resolver's guard correctly stays in place for the unattended path.
- stdlib-only (repo convention).
- Brief/dataset bytes must stay deterministic. The release-check `Verifying byte-determinism (second run)` step proves this.
- No em dashes in any prose (this plan, glossary, validation, review, journal). No automation provenance markers in commit messages or files (the banned tokens are enumerated in lovs/public_repo_hygiene.py PROVENANCE_PATTERNS; the hygiene scan enforces them at release-check time).
- The change must land as a single PR (not split). Cross-surface byte-parity is unaffected because the ledger is not in the publisher mirror set.

## Approach
The change is mechanically simple but doctrinally meaningful, so it goes through the full pipeline. The work is a four-field append per point (`outcome`, `resolved_as_of`, `outcome_evidence`, and a brief `resolution_provenance` note tying back to the resolver report), executed by hand-editing `data/calibration-ledger.json` through `Edit`. The append shape is derived 1:1 from the resolver's already-emitted `proposed_ledger_outcomes` block, which is the canonical patch. A new monotonic-guard test locks the contract: once a point has an outcome in the ledger, no later commit may change it (the test parses both the merge-base and the working ledger, and fails closed on any field-level mutation of an existing outcome). The release-check confirms no existing gate regresses, and the full test suite stays green.

## Decomposition
1. Read the canonical ledger and the resolver's `proposed_ledger_outcomes` from `/tmp/lovs-canonical-data/resolution-report.json`. Cross-reference each proposed outcome's `hypothesis_id` against the ledger to confirm the two target points exist exactly once and have no existing `outcome` field. Check: both `hypothesis_id` strings (`e77003de63` and `92b62f4c0c`) are present, and `outcome` does not appear anywhere in the ledger file before this commit.
2. Edit `data/calibration-ledger.json` to add four fields to each of the two target points, immediately after the existing `risk_adj_50` field, preserving JSON formatting (indent=2, sort_keys=False per file convention). Fields added: `outcome` (int 1), `resolved_as_of` (string "2026-05-23"), `outcome_evidence` (object with `source_id`, `source_url`, `classification`, `confirmed_in_window`, `first_in_window_confirmation_date`), `resolution_provenance` (string: "calibration_resolver.py report 2026-05-26; early-lock monotonic-safe"). Check: `python3 -c "import json; d=json.load(open('data/calibration-ledger.json'))"` parses without error and a hand-read confirms only the 2 expected points changed.
3. Add `tests/test_ledger_outcome_monotonic.py`. Three test methods: (a) `test_appended_outcomes_match_resolver` runs the resolver against the canonical ledger + evidence and asserts every ledger point with an `outcome` field has that outcome equal to the resolver's derivation; (b) `test_no_outcome_mutation_against_merge_base` walks `git show HEAD~1:data/calibration-ledger.json` (when on the append branch) and asserts no existing outcome was changed, only new ones added; (c) `test_only_resolved_yes_points_have_outcome_fields` asserts the 10 pending points have no `outcome`, `resolved_as_of`, or `outcome_evidence` fields. Check: the 3 tests pass on the post-edit ledger.
4. Run `python3 release_snapshot.py --as-of 2026-05-25 --check` and confirm `Check passed.` with every existing gate green (publication-clock, reconciliation-invariant, hygiene, leak, CDC fidelity, cross-surface parity, process-health). Check: exit 0, no FAIL lines.
5. Run `python3 -m unittest discover -s tests` and confirm 330+/330+ pass (329 existing + 3 new). Check: `OK` with no failures or errors.
6. Run `python3 calibration_resolver.py --as-of 2026-05-26 --write-report` and confirm the report's `points[*].status` for the 2 early-locks is unchanged (still `resolved_yes`) and the `proposed_ledger_outcomes.outcomes[*].outcome` matches the new ledger fields. Check: deterministic resolver output, no semantic shift.
7. Fresh-context review of the diff against this plan + glossary + validation. The reviewer must see the diff cold and confirm the append is monotonic-safe, the doctrine pre-authorized it, and no existing fields were mutated. Check: `review_complete` gate passes with `Verdict: ready to proceed`.
8. Commit + push the branch, open PR, confirm CI green, squash-merge. Check: PR shows `mergeStateStatus: CLEAN`, the `public-release-gates` check passes, and after squash-merge the canonical ledger on origin/main carries the 2 outcomes.

## Risks
- R1: A typo in the appended JSON syntactically corrupts the ledger and breaks the resolver and every downstream consumer. Mitigation: `python3 -c "json.load(...)"` parse check between Step 2 and Step 3; the resolver run in Step 6 also fails loudly on any structural defect.
- R2: A field-order or whitespace drift in the hand-edit alters the ledger's byte hash in a way that confuses downstream consumers that hash-pin it. Mitigation: the resolver's report records the ledger path as a reference, not the hash; no downstream consumer hash-pins the ledger today. The cross-surface parity gate hashes the publisher set, which does not include the ledger.
- R3: The monotonic-guard test could over-fit to the merge-base diff and reject legitimate future appends. Mitigation: the test compares field-by-field, asserts only that EXISTING outcomes do not change, and explicitly allows new points to gain outcomes. New tests will be added when block-level Brier scoring lands (June 19+), but those are additive, not mutational.
- R4: The doctrine line "Resolve by appending" is interpreted by some future reader as authorizing edits to existing outcomes ("re-resolve"). Mitigation: this plan and the monotonic-guard test together make the append-only semantic unambiguous; the test fails closed on any attempted mutation, so the doctrine is enforced in code.

## Skip Decision
Do not skip. Medium classification: the first-ever ledger write is doctrine-significant and warrants Plan, Validate, Implement, Review (full pipeline phases 1-5). Phase 6 (stage) is skipped because the ledger is not in the publisher set; the release-check in Decomposition step 4 covers staging-equivalent verification. Phases 7-8 (red team, stress) are skipped: the change is two-row data, no public-facing semantic, no new code paths beyond the monotonic-guard test (which IS the stress).
