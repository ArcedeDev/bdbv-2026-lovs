<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Tournament resolution drafts

`lovs.tournament_resolution` prepares pending target assessments after a frozen
round's inclusive window closes. It uses the policy embedded in the frozen round,
the existing reviewed promotion and evidence registries, and the canonical zone
index. It never changes forecasts, calibration commitments, historical snapshots,
the evidence registry, or final resolution artifacts.
Each round's result is recorded in [tournament-rounds.md](tournament-rounds.md).
The centroid file must match its hash in the frozen build receipt; each target
must use the corresponding `cod-health-zone:` geography identifier.

```sh
python3 -m lovs.tournament_resolution \
  --round data/model-tournament/rounds/ROUND_ID.json \
  --evidence-cutoff-utc YYYY-MM-DDTHH:MM:SSZ \
  --coverage-assessments /reviewed/coverage.json \
  --output .process/CHANGE_ID/resolution-draft.json
```

Omit `--coverage-assessments` when no reviewed target coverage exists. The command
still produces a useful draft, with unscoreable targets and explicit diagnostics.
The cutoff must be after the window closes. Every input publication and review
must be available by that cutoff. Files dated later than its UTC day are not
opened. Output uses the existing create-only writer: identical repeats are safe;
changed content cannot replace a prior draft. Output inside canonical `data/` is
refused.

## Three separate clocks

The event is first public authority publication. An exact source receipt
`wordpress_published_at`, then `published_at`, takes precedence over the promotion's
printed publication time. A receipt's source ID, when present, must match; its
source URL must match the anchored primary source. Older receipts may omit the
source ID. Every declared publication clock is checked against the cutoff.
The draft preserves these clocks, plus
the observation/reporting date, table date, and source review time. A source
published after the window cannot be backdated into the window by its data day.
Same-day editions remain separate observations of publication.

An ambiguous publication time cannot establish an exact event date. An unknown
zone, duplicate row or contradictory declared count stops the draft. Pending
positives, older tables without reconciliation, and missing supported source
chains produce diagnostics; printed target positives remain conflicting evidence
rather than disappearing. Explicit unventilated rows and declared negative
residuals participate only in national accounting and are never assigned to a
target. A remaining disclosed national gap makes that table unsuitable for
certifying outcomes. Carried tables cannot establish a new public detection.

The resolver verifies every baseline promotion hash frozen in the build receipt.
Missing or changed baseline files stop the draft. An unchanged baseline file
without usable outcome evidence remains a known forecast input; it need not
independently support a later outcome. Its target positives are still checked.
Missing supported evidence in newly encountered history blocks negative
certification and remains an explicit target assessment limitation. Legacy source
records and review metadata are never repaired by the resolver.

## Negative evidence

Each round freezes its resolution policy. Round 001 froze policy v1; later rounds
freeze v2.

Under v1, absence from a table, a reconciled national total and province
operational fields do not establish a target negative. Without reviewed target
coverage, an absent target is surveillance dark; missing end-of-window tables give
no-feed status. The authority reports alerts and laboratory activity by province
only, so no v1 target could show zone-level coverage. A positive came from the zone
table and a negative needed evidence the reports do not hold: the two outcomes were
measured with different instruments, and the scored set could hold only positives.

v2 reads both outcomes from the same instrument, and scores a target only when that
instrument covers it, whatever the outcome. Its `negative_table_rule`, frozen in each
round, is the authority.
- **Accounted province.** A province is accounted by the earliest reviewed promotion
  with anchored primary evidence, an exact publication time by the evidence cutoff, and
  a reconciled zone table whose data day and table date are on or after the window end.
  That table must assign every national confirmed case to a named zone, with no
  unallocated confirmed case, and print for the province a count of affected zones
  equal to the number of its named zones with a confirmed case. Every SitRep edition
  numbered after the last one published before the window, up to that table's own
  edition, must also be present with anchored primary evidence. Each province takes its
  own earliest such table, so a province count that lags by one edition is accounted by
  the next one.
- **Only accounted provinces are scored.** A target in a province no table accounts for
  is surveillance dark, whether it was listed in the window or not. A missing edition
  leaves the provinces unaccounted for listed and absent targets alike.
- **No early resolution.** A resolution draft is refused while any target's province is
  not accounted, until the evidence cutoff falls more than 14 days after the window end.
  A draft run before the certifying table or a missing edition is published therefore
  cannot leave the round with one outcome class only. After that grace an unaccounted
  province stays unscoreable.
- **Yes.** In an accounted province, a target first listed with a confirmed case in a
  reviewed, reconciled publication with an exact publication time dated in the window
  resolves yes.
- **No.** Any other target in an accounted province that was not first listed before
  the window resolves no, with assessment `reconciled_zone_table_absence`. The tables are
  cumulative, and the complete run of editions shows the target was first listed after
  the window, if at all.
- **Doubtful listings.** Every edition after a zone's first case lists it again. A
  listing in an unreconciled or unanchored publication, or one without an exact
  publication time, therefore makes a target unscoreable only when it is dated before
  the window, or in the window for a target with no certain listing there. A doubtful
  edition after a certain first listing changes nothing.
- **Reviewed coverage is not read under v2.** Both outcomes come only from the table, so
  no reviewer judgement can add or remove one outcome class.

Mere absence from an unreconciled table never establishes a negative under either
policy. Every round is validated under the policy it froze. Only round 001 may carry v1,
and a new freeze must use the current policy.

**Known limitations of v2.**
- The universe is fixed at the source snapshot's data day, and the window opens the day
  after the freeze. A zone first listed in an edition published between the two is a
  pre-window detection and is not scored. These are the zones nearest to the epidemic,
  so the scored set omits them; the gap is a few days.
- Selection happens at province level. A province where a new zone appears is the one
  most likely to print an affected-zone count that lags its named zones. If no table
  accounts for it by the end of the grace, its yes and its no both drop, and the scored
  set under-represents provinces where the epidemic moved.
- The event clock is publication. Cases in the window that the authority first leaves
  unallocated and names after the window end are not in-window detections.
- A zone removed from a cumulative table resolves no. No zone has been removed from one
  in this outbreak.

Pre-window public detections use conflicting-evidence status with reason
`pre_window_detection`. A positive report contradicting negative coverage is also
conflicting evidence. The existing six resolution states are preserved.

Coverage input has this shape:

```json
{
  "schema_version": "bdbv-model-tournament-coverage/v1",
  "round_id": "ROUND_ID",
  "forecast_sha256": "FROZEN_FORECAST_HASH",
  "resolution_policy_sha256": "FROZEN_POLICY_HASH",
  "assessments": [{
    "target_id": "TARGET_ID",
    "window_start": "YYYY-MM-DD",
    "window_end": "YYYY-MM-DD",
    "assessment": "explicit_negative",
    "evidence_chain_ids": ["ec:REVIEWED_COVERAGE_CHAIN:YYYY-MM-DD"]
  }]
}
```

The alternative accepted assessment is `complete_target_coverage_no_detection`.
Every listed chain must already be reviewed, supported or derived-supported,
and include anchored primary evidence. Its claim value must equal the assessment.
Its `coverage` object must contain exactly `round_id`, `forecast_sha256`,
`resolution_policy_sha256`, `target_id`, `window_start`, `window_end`, and
`assessment`, matching the input and frozen round. Both window boundaries must
match. The coverage review requires an explicit UTC timestamp after the full
window closes and no later than the evidence cutoff. Date-only coverage reviews
must be clarified through the review workflow, never assigned an invented time.

These checks bind a reviewed assessment to its target and window. They do not
automatically establish the truth of a free-text surveillance claim. Review must
verify the cited authority actually supports the declared coverage and negative
finding.

## Review and finalization

The bundle contains `resolution_candidate`, one proposed evidence chain per
target, source clocks, input hashes and diagnostics. New chains have verdict
`pending`, and both reviewer and reviewed-at are null. Source-promotion review
does not imply review of a newly derived outcome. These draft objects deliberately
cannot pass the final evidence validator.

An independent reviewer verifies every proposed outcome and coverage finding,
adds genuine reviewer and review time, and changes a supported chain's verdict
through the established evidence registry workflow. Targets with missing sources
or blockers require actual evidence before finalization; do not fill in metadata
to make validation pass. Generated derivations are labeled derivations, not
invented quotations.

After the reviewed chains are present in `data/evidence-chains.json`, extract the
bundle's `resolution_candidate` to a reviewed input file and use the existing
`lovs.model_tournament resolve` and `score` commands. `build_resolution` regenerates
and validates receipts from the canonical registry. New-round negative receipts
must also carry the exact frozen coverage binding and an explicit review timestamp
after window closure, even for manually prepared resolution candidates. Generated
negative chains copy the reviewed coverage object and retain the original coverage
chain IDs. Legacy v2 receipts without this optional object remain unchanged.
No alternate resolution,
scoring implementation, or final writer is introduced.

## Verification

```sh
python3 -m unittest discover -s tests -p test_tournament_resolution.py
```

Synthetic tests cover clocks, full-window coverage bindings, source corruption,
pending-review rejection, determinism and immutable output. Compact unit fixtures
isolate the frozen-round boundary; a complete integration test additionally builds
and validates a real v3 envelope, generates its draft, applies synthetic review,
and runs the existing resolution and scoring functions without mocking round
validation. Separate tournament tests cover approval and freeze input substitution.
A compatibility test copies the committed history through SR141 without altering
its bytes, reads all 122 promotions, checks declared residuals and missing-source
diagnostics, and generates an unscoreable draft against unchanged baseline hashes.
