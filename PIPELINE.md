<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Snapshot release pipeline

This repository publishes dated, immutable snapshots of the BDBV-2026 LOVS
analysis. This document is both the runbook for releasing a new snapshot when
new public data is reported, and the design contract the release tooling
follows.

## Principles (non-negotiable)

- **Snapshots are immutable.** A released snapshot (its data, its calibration
  points, and its resolution date) is never edited after commit. New
  information produces a NEW dated snapshot. This is the pre-commitment
  integrity the calibration scoring depends on.
- **Every figure traces to a dated, byte-archived source.** No hardcoded
  counts. Counts and the public-reporting timeline derive from the SHA-256
  archive in `data/bundibugyo-2026/manifest.json`.
- **Calibration points are pinned once and carried forward unchanged.** A data
  refresh must never re-derive or overwrite an already-pinned calibration
  point. See "Calibration and resolution" below.
- Stdlib-only Python; byte-deterministic HTML and SVG for a fixed snapshot;
  dates derived from the snapshot, never from the wall clock.

## The release command

```bash
python release_snapshot.py                             # default --check: regenerate + verify, no commit
python release_snapshot.py --as-of YYYY-MM-DD          # also assert the built snapshot date
python release_snapshot.py --with-website              # also dry-run the website sync (sibling repo)
python release_snapshot.py --commit                    # release after the review gate (type "release")
python release_snapshot.py --commit --yes              # non-interactive confirm (CI)
```

One command builds the snapshot from the manifest plus the active calibration
ledger, computes the LOVS modules, renders the brief, exports the public-health
dataset, and runs the full test suite. It then proves byte-determinism (every
generated artifact except the timestamped `brief.pdf` is identical on a second
run) and prints a review gate: snapshot date, reconciled counts, carried-forward
calibration points, and resolution date. Nothing is committed without explicit
`--commit` plus an operator confirmation. Source ingest (step 1 below) stays a
deliberate manual step; the website lives in a separate repo and is synced with
`--with-website`, then committed there.

## Runbook: releasing a snapshot

1. **Ingest new source(s).** Fetch each new public report, byte-archive it
   under `data/bundibugyo-2026/raw/<sha256>`, and add a manifest entry
   (`published_at`, `publisher`, `source_tier`, `url`, `content_hash`, and
   `normalized_content` figures). For restricted-bytes sources set
   `raw_bytes_relpath: null` and `raw_archive_status: private_restricted_bytes`
   and keep the hash for provenance.
2. **Run the release command** for the new as-of date. It builds the snapshot
   counts and the public-reporting timeline from the dated manifest sources,
   recomputes visibility, transmission, and corridor risk, carries forward the
   active calibration ledger unchanged,
   renders the brief with all dates derived from `as_of` and `resolves_at`,
   exports the public-health dataset, runs the tests, and proves byte-determinism
   before stopping at the review gate.
3. **Review.** Inspect the diff, the rendered brief, and the gates: tests,
   evidence-chain validation, and manifest integrity. Cross-check the headline
   numbers against the cited sources.
4. **Release.** Commit the NEW dated files (never edit a prior snapshot) and
   push; redeploy the website.

## (a) Cadence

Run when new data is reported (assumed roughly daily, but the pipeline assumes
no fixed schedule; it is invoked per as-of date). `--check` reports whether any
source dated after the last snapshot exists, so a release is only cut when there
is genuinely new information. There is no unattended auto-release: every pinned
snapshot passes a human review gate, because each release pins calibration data
for a real outbreak.

## (b) Adding a new data source

A new publisher or source type is additive:

1. Archive its bytes and add a manifest entry with a `source_tier` and
   `normalized_content` figures.
2. If it introduces a new tier, add that tier to the source-tier priority the
   reconciler uses (configuration, not code) so conflicts resolve predictably.
3. Re-run the release command; the snapshot picks it up automatically.

No code change is needed for a new instance of an existing tier (for example,
the next ECDC or WHO update): only a manifest entry.

## (c) Calibration and resolution

- **Pinning.** Calibration points live in `data/calibration-ledger.json`, each
  with: id, corridor, model range, pin date, resolution date, and status. The
  current set (four corridors, pinned 2026-05-20, resolving 2026-06-19) is the
  first ledger block.
- **Carry-forward (critical).** Daily data refreshes DO NOT re-derive
  calibration points. The release command reads the active (unresolved) ledger
  points and carries them into the new snapshot unchanged. Re-deriving them from
  freshly ranked corridors would break the pre-commitment contract.
- **New points.** New calibration points are pinned only at explicit pin moments
  (for example, a new resolution window), appended to the ledger with their own
  resolution date. Existing entries are never edited. Planned: after the
  2026-06-19 resolution, pin a new block adding the `arua-uga` and `nebbi-uga`
  corridors.
- **Resolution.** On a resolution date, score the points due against public
  reports, append the outcome to the ledger, and update the historical
  calibration. Scoring never edits the original pinned range.

## Landmine status: RESOLVED (stage 1 landed)

`refresh_pipeline.py` previously re-derived the four calibration points from the
top corridors on every run and hardcoded `resolves_at`, so re-running it for a
new date would overwrite the 2026-05-20 pinned points and break pre-commitment.
Stage 1 closed this: the pinned points now live in `data/calibration-ledger.json`,
and `carry_forward_calibration()` reads the active block and emits the points and
`resolves_at` verbatim. The calibration set no longer depends on the live corridor
ranking, so a data refresh is safe to re-run. `tests/test_calibration_ledger.py`
locks the contract (a perturbed snapshot re-ranks corridors but cannot move the
pins).

One thing the ledger does NOT freeze: a snapshot's non-calibration figures
(visibility, transmission, corridors) are still derived from snapshot content via
a content-seeded Monte Carlo. Revising an already-released snapshot's inputs would
change those figures, which is what the immutability principle above forbids:
revise as a NEW dated snapshot, never in place.

## Build stages

1. **Calibration ledger.** DONE. The four 2026-05-20 points live in
   `data/calibration-ledger.json`; `carry_forward_calibration()` reads and carries
   them forward instead of re-deriving them. Landmine removed; faithfulness proven
   (the 20 May calibration block and resolution date are byte-identical to the
   pre-refactor output). Locked by `tests/test_calibration_ledger.py`.
2. **De-hardcode dates.** DONE. Every displayed date in `refresh_pipeline.py`
   and `make_brief.py` is derived from `as_of` and `resolves_at`.
3. **Manifest-driven counts.** DONE. `build_snapshot()` pulls every reported
   count value from `data/bundibugyo-2026/manifest.json` by source id; only the
   reconciliation policy (which dated source bounds each metric) stays in code,
   and a missing source or field fails loudly. Faithfulness proven (20 May
   output byte-identical). The public-reporting timeline rendering moves to the
   dated-source model in stage 5, alongside the website sync.
4. **Orchestrator + `--check`.** DONE. `release_snapshot.py` runs the full
   pipeline, runs the tests, proves byte-determinism, and gates a commit behind
   a review of snapshot date, reconciled counts, calibration points, and
   resolution date.
5. **Webpage sync.** DONE. `sync_to_website.py` `build_timeline` derives every
   timeline point from the dated manifest by canonical source id (the systemic
   fix that keeps the 19 May ECDC point manifest-driven); only the per-date
   source-and-field selection stays in code. Faithfulness proven: the live
   20 May website snapshot is byte-identical, so no website change ships.

## Invariants checklist (per release)

- [ ] A new dated snapshot file is added; no prior snapshot is edited.
- [ ] Every count traces to a dated manifest source.
- [ ] Active calibration points are carried forward unchanged.
- [ ] Tests pass; evidence chains validate; manifest integrity is 1:1.
- [ ] The brief is regenerated with dates derived from `as_of`.
