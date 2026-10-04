<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Tournament resolution drafts

`lovs.tournament_resolution` prepares pending target assessments after a frozen
round's inclusive window closes. It uses the policy embedded in the frozen round,
the existing reviewed promotion and evidence registries, and the canonical zone
index. It never changes forecasts, calibration commitments, historical snapshots,
the evidence registry, or final resolution artifacts.
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

Absence from a table, a reconciled national total and province operational fields
do not establish a target negative. Without reviewed target coverage, an absent
target is surveillance dark; missing end-of-window tables give no-feed status.
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
