# Glossary: early-lock outcome-append

## Glossary

| Term | Definition |
|---|---|
| Calibration ledger (`data/calibration-ledger.json`) | The immutable pre-commitment record of corridor-spillover predictions. Each block holds a pin date, a resolution date, and a list of points; each point holds a source HZ, target zone, horizon (30d), risk-adjusted 50% interval `[low, high]`, and a deterministic hypothesis_id. Schema v1. |
| Block | A set of points all pinned at one moment and all resolving at one moment. Block-1 was pinned 2026-05-20 (resolves 2026-06-19) with 4 points; Block-2 was pinned 2026-05-21 (resolves 2026-06-20) with 8 points. Blocks may be added but never edited. |
| Point | One corridor prediction (e.g. `bunia -> kampala-uga`, horizon 30d, `risk_adj_50: [0.229, 0.523]`). Identified by `hypothesis_id`. The append in this change adds outcome fields to two existing points; no point is added, removed, or otherwise edited. |
| Resolver (`calibration_resolver.py`) | The read-only tool that reads the ledger plus a provenanced evidence feed and computes per-point resolution status + Brier score. It never writes the ledger; `_atomic_write_text` refuses by path identity at `calibration_resolver.py:60-63`. |
| Early-lock | When a calibration point resolves YES before its block's `resolves_at`, because a NEW lab-confirmed case appeared in the target zone within the `[pinned_at, resolves_at]` window. Monotonic-safe: once a point goes to `resolved_yes`, it cannot flip back to pending. |
| Outcome-append | The founder-gated operation that records a resolved point's outcome into the immutable ledger. Append-only: adds new fields to an existing point, never mutates existing ones. This change is the first execution of this operation; the doctrine has authorized it from initial commit via `_meta.doctrine[3]`. |
| In-window confirmation | A NEW lab-confirmed case (not pre-window background) reported in the target zone on a date strictly within `[pinned_at, resolves_at]`. The 2026-05-15-16 Kampala imports do NOT count: they predate Block-1's `pinned_at` of 2026-05-20. The 2026-05-23 Kampala 3-new is in-window for both blocks. |
| Monotonic-guard test | The new test (`tests/test_ledger_outcome_monotonic.py`) that compares the working ledger to the merge-base ledger and fails closed on any field-level mutation of an existing outcome. It allows additions; it forbids edits and deletions of outcome data. |
| Resolution evidence feed (`data/calibration-resolution-evidence.json`) | The provenanced per-target-zone feed the resolver reads. For Uganda targets the source authorities are Uganda MoH and Africa CDC; for DRC targets the source is the promoted zone-attributed DRC MoH counts. The append in this change cites this feed's `uga-moh-2026-05-23-kampala-three-new` entry. |
| `outcome` field (newly introduced on ledger points) | Integer, 1 = resolved_yes, 0 = resolved_no. Absent when the point is still pending. This change adds the field with value 1 to exactly the two early-locked Kampala-target points. |
| `resolved_as_of` field (newly introduced) | String date (ISO YYYY-MM-DD), the in-window confirmation date that triggered the resolution. For both appended points: "2026-05-23". |
| `outcome_evidence` field (newly introduced) | Object mirroring the structure already used by the resolver's report `evidence` block: `source_id`, `source_url`, `classification`, `confirmed_in_window`, `first_in_window_confirmation_date`. Direct copy from the resolver's `proposed_ledger_outcomes` patch. |
| `resolution_provenance` field (newly introduced) | String, a brief note tying the append back to the resolver report that proposed it. Value for both points: "calibration_resolver.py report 2026-05-26; early-lock monotonic-safe". |

## What the change is NOT

- Not a doctrine extension. The fourth `_meta.doctrine` line authored at initial ledger commit already says "Resolve by appending. On a resolution date, score the due points against public reports and append the outcome; the original pinned range is never altered." The append in this change is the first execution of that rule.
- Not a new tool. The append is a hand-edit through `Edit`, gated by the founder review (you typed "Do 1"). A CLI tool can be built later if appends become repetitive; today's two-row scope does not warrant it.
- Not a schema bump. Schema stays at v1. The new fields are additive on existing v1 documents; readers that ignore unknown fields keep working unchanged.
- Not a public surface change. The website snapshot, the brief, and the dataset are unaffected; the ledger is not in `WEBSITE_ASSETS` or in the cross-surface parity mirror set.
- Not a block-level Brier score. Block-level scoring waits for ALL points in a block to terminate (early-locked or resolves_at-passed). The 10 still-pending points are unaffected by this change.
