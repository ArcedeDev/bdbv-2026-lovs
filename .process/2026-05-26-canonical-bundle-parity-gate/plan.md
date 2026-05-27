# Plan: Canonical BDBV Bundle Parity Gate

**Change-id:** 2026-05-26-canonical-bundle-parity-gate
**Date:** 2026-05-26
**Size classification:** Medium

## Intent
Prevent a website review snapshot, public-health workbook, brief, or visual set from being trusted when it was generated from a non-canonical or stale LOVS checkout. The immediate trigger is the local May 26 website branch that was synced from `bdbv-2026-lovs-tabled-2026-05-22` and therefore showed `106` confirmed cases while canonical LOVS held `112`.

## Success Criteria
- [ ] A LOVS-side hard gate compares the latest website snapshot against canonical LOVS `data/live-bdbv-2026-output.json` for headline count primaries and primary source IDs.
- [ ] The gate compares website public assets against canonical LOVS deliverables for workbook, schema, manifest, brief PDF, and generated SVG visuals.
- [ ] The gate reports source IDs present in the latest website snapshot but absent from the canonical LOVS manifest, while allowing explicitly configured frozen historical carry-forward IDs.
- [ ] `release_snapshot.py --with-website` runs this hard gate and its help text no longer claims a sync happens when only a check runs.
- [ ] `daily_snapshot_prep.py --website-gates` runs the hard gate in addition to the existing focused website tests.
- [ ] Unit tests cover at least one clean parity case and one stale-worktree count/source mismatch case.
- [ ] No public push, preview promotion, or snapshot/workbook regeneration occurs.

## Non-Goals
- Do not regenerate or repair the local website `2026-05-26.json` in this change.
- Do not ingest new source material or change outbreak counts.
- Do not change source reconciliation doctrine.
- Do not publish, push, open a PR, or promote a preview.
- Do not refactor the full website publisher.

## Constraints
- Use the canonical LOVS checkout as authority; website-only self-consistency is insufficient.
- Keep the gate stdlib-only, matching `release_snapshot.py` and current LOVS tooling.
- Do not overwrite unrelated dirty files in the current LOVS or website worktrees.
- Preserve current source-date semantics: route date, analytic as_of, source data/report date, publication date, and retrieval date remain distinct.
- Output must be deterministic and actionable: failures name the exact mismatched field or file.

## Approach
Thesis: the missing primitive is a LOVS-owned release-bundle parity gate, because the canonical methods checkout must be the authority for website review readiness. Antithesis: a website-side test can still be valuable, but it can be fooled by the same stale `--lovs-root` that generated the bad snapshot. Synthesis object: add a reusable `lovs.website_bundle_parity` gate and wire existing release/daily orchestration to it, while leaving actual sync/regeneration as a separate human-reviewed step.

## Decomposition
1. Add a stdlib LOVS module that loads canonical LOVS output, manifest, deliverables, and latest website snapshot/assets, then returns structured mismatch findings. Check: module can be called with explicit temporary roots in unit tests.
2. Add unit tests for clean parity and a stale confirmed-count/source mismatch. Check: `python3 -m unittest tests.test_website_bundle_parity` exits 0.
3. Wire `release_snapshot.py --with-website` to invoke the hard gate after website release-surface scan. Check: help text says "check website parity" rather than "run website sync", and gate failures make `--with-website` return non-zero.
4. Wire `daily_snapshot_prep.py --website-gates` to run the hard gate before or alongside the website test suite. Check: prep packet captures a `website_bundle_parity` result with status `ok` or `failed`.
5. Run targeted verification without regenerating snapshots. Check: relevant LOVS unit tests pass and the hard gate fails against the current contaminated local website branch for the expected reasons.

## Risks
- R1: The gate could reject valid historical source carry-forward objects. Mitigation: allow an explicit historical source allowlist and only apply the strict canonical requirement to latest snapshot references by default.
- R2: The gate could duplicate existing cross-surface byte parity code. Mitigation: reuse the existing mirror set idea and keep the new module focused on snapshot JSON plus canonical asset comparison.
- R3: Running the gate against the current dirty local website will fail. Mitigation: this is expected and useful; report it as local review branch blocked, not as a code failure.
- R4: Daily prep could become too expensive if it runs the full website suite. Mitigation: this change adds the hard Python gate and preserves the existing focused website suite; expanding the full suite can be a follow-up if runtime becomes acceptable.

## Skip Decision
Skip Phase 8 because this is an internal release gate, not a user-facing rollout. Use phases 1-7 because the change touches release workflow, cross-repo contracts, and daily automation.

## Glossary
| Term | Definition |
| --- | --- |
| Canonical LOVS root | The authoritative `projects/bdbv-2026-lovs` checkout whose generated output, manifest, and deliverables define the reviewable BDBV state. |
| Website review snapshot | The latest registered JSON under `apps/site/app/bdbv-2026/_data/snapshots/` in the website checkout. |
| Release bundle | The coupled set of LOVS output, snapshot contract, source manifest, workbook package, brief, visuals, and website snapshot expected to agree. |
| Asset parity | Byte identity between canonical LOVS deliverables and files served from `apps/site/public/bdbv-2026/`. |
| Source reference parity | The invariant that website snapshot source references resolve to canonical LOVS manifest sources, except explicitly allowed frozen historical carry-forward IDs. |
