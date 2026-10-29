# Plan - prospective cross-border validation
**Change-id:** 2026-10-29-prospective-cross-border
**Date:** 2026-10-29
**Size classification:** Medium

## Intent
Prepare a working, review-only prospective cross-border validation setup that compares future country-level spatial rankings against geography and genuine travel-hub connectivity, using the existing daily prep cadence. Make launch prerequisites explicit so review cannot be mistaken for a registered forecast.

## Success Criteria
- [x] Draft protocol fixes endpoint, target rule, clocks, baseline definitions and comparable metrics, and identifies remaining scientific review gates.
- [x] Deterministic baseline preparation uses existing distance geometry and complete reviewed travel inputs, rejecting missing, future, stale or malformed evidence.
- [x] Existing prep packets and journal summaries include read-only validation readiness; no new scheduler or automatic freeze exists.
- [x] Meaningful baseline, provenance, failure and cadence integration tests pass.
- [x] Independent review clears material findings and frozen historical/tournament/November artifacts remain unchanged.
- [x] User receives a concrete protocol, generated readiness example and review branch with launch remaining blocked.

## Non-Goals
- Activate/register/freeze a prediction or deploy production surfaces before user review.
- Revive the failed corridor model, invent a challenger, fabricate traffic data or replace current connectivity with airport counts/2014 routes.
- Duplicate scoring, outcome adjudication or scheduling infrastructure.
- Alter current DRC tournament geography or the sealed November operational tests.

## Constraints
- Existing 30-day UTC tournament policy and twice-daily prep cadence are source contracts.
- Use current reviewed aggregate international scheduled arriving seats over 28 complete days for hub rank; no personal itineraries.
- Complete UN-member-country universe and source-point tables must be reviewed and hash-bound before baseline construction; missing entries block the entire preview.
- First-country confirmation excludes countries already confirmed before cutoff, travel-only transit and planned medical evacuation.
- All candidate inputs must be available and reviewed no later than cutoff, with conservative handling of date-only source clocks.
- No new dependencies or background services; heavy test commands use mac-storage.
- Draft code can prepare rankings but has no activation/freeze/resolve command. Missing approvals never become green automatically.

## Approach
Thesis: a controlled prospective comparison can identify added spatial ranking information. Antithesis: country evidence and current travel inputs are not yet complete, and the failed model cannot re-enter by renaming historical outputs. Synthesis object: a draft protocol plus a small read-only preparation adapter in the existing daily packet, reusing distance_only_frontier_30d, tournament hashing/date policy and country event attribution. Existing scoring remains authoritative; no performance or public-priority claim is emitted. Remaining independent-witness, adjudication and inferential-design choices are review gates, not silently chosen defaults.

## Decomposition
1. Ground the country, cadence, geometry, provenance and scoring seams. Check: validation.md has concrete citations and explicit unknowns.
2. Add draft protocol and pure reviewed-input baseline preparation with fail-closed readiness. Check: complete fixture builds both ranks and incomplete/future/stale inputs fail.
3. Add readiness to the existing prep packet/journal and write review guide. Check: actual prep integration tests retain existing behavior and expose draft state.
4. Run focused tests and frozen-artifact integrity checks, independently review and simplify. Check: verification, review and polish artifacts show green required checks and no material open finding.
5. Exercise malformed/path/clock/data failures and document trust boundaries. Check: stress and threat artifacts cite tested mitigations and review results.

## Risks
- Weak proxy masquerades as travel connectivity: require seat units and full country coverage; public airport inventory/stale route data fail suitability review.
- Metadata can lie: hashes prove bytes, not source truth; manual source review and later independent public freeze witness remain mandatory.
- Sparse first-country events: descriptive rank metrics only until power/stopping/inference is independently reviewed.
- Production cadence disruption: readiness is isolated observational metadata, never an implicit deployment/freeze trigger.
- Reviewer decisions unavailable now: preserve explicit blocked gates and deliver the requested reviewable setup.

## Skip Decision
Skip Phase 8 production staging: no deployment/activation is authorized before the requested user review. Stage the code as a review branch with a deterministic blocked readiness artifact instead. Phases 0-7 apply, scaled to a read-only adapter.
