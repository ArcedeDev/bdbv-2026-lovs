# Read-Only Public Interface

This document defines the current public, read-only LOVS interface. It exposes stable files, not write tools. It is an artifact map so public-health partners and technical users can answer bounded questions without bypassing the immutable public record.

## Interface Map

| Question | Artifact |
|---|---|
| What is the current public snapshot? | `data/public_snapshot.json` |
| Which public sources support the snapshot? | `data/public_source_manifest.json`, `data/public_source_index.csv` |
| What counts did public sources report? | `data/public_reported_counts.csv` |
| What health-zone counts are available? | `data/public_zone_counts_2026-05-26.csv` |
| What public source conflicts are documented? | `data/public_source_conflicts.json` |
| What calibration commitments are open? | `data/public_calibration_ledger.csv` |
| What is the block-level calibration status? | `data/public_calibration_status.json` |
| What target set was precommitted? | `data/public_precommitment_targets.csv` |
| What evidence gaps or unscoreable states remain? | `data/public_blindspots.json` |
| What reporting latency can be measured from public source dates? | `data/public_latency_observatory.csv` |
| Is a standing scored nowcast issued in this snapshot? | `data/public_nowcast_status.json` |
| What public method cards can partners reuse? | `METHOD_CARDS_PUBLIC.md` |
| How does the method apply to the current real snapshot? | `WORKED_SNAPSHOT_REVIEW.md`, `examples/review_public_methodology.py` |
| How might MOH, CDC, WHO, INRB, or peer analysts use the public package? | `PUBLIC_HEALTH_USE_CASES.md` |
| How can a partner adapt the public package to aggregate local data? | `PUBLIC_ADAPTATION_GUIDE.md`, `examples/` |
| What machine-readable shapes should public JSON artifacts follow? | `schemas/` |
| How can a reader summarize the public package locally? | `examples/summarize_public_package.py` |
| How should open calibration rows be reviewed after resolution dates? | `CALIBRATION_RESOLUTION_PUBLIC.md` |
| How can I inspect and hash-verify the pre-registered calibration record? | `examples/show_calibration_record.py`, `data/public_calibration_ledger.csv` |
| What do the core public terms mean? | `GLOSSARY.md` |
| How can a partner review their own aggregate file? | `examples/review_local_aggregate.py`, `schemas/local_aggregate_input.schema.json` |
| How should this package be cited? | `CITATIONS.md`, `CITATION.cff` |
| Which artifact hashes belong to the same release? | `data/release_manifest.json` |

## Integrity Boundary

The public interface is read-only. It does not mutate snapshots, source manifests, publication state, calibration ledgers, resolution outcomes, or precommitment target sets.

## Controlled Surfaces

The public interface does not publish source collection automation, mutable resolver tools, private-data adapters, probability intervals, model parameters, scoring implementation, or private calibration code. Those surfaces remain unpublished method assets and can be shared through partner-specific agreements when appropriate.
