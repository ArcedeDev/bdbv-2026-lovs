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
| How can a partner adapt the public package to aggregate local data? | `PUBLIC_ADAPTATION_GUIDE.md`, `examples/` |
| Which artifact hashes belong to the same release? | `data/release_manifest.json` |

## Integrity Boundary

The public interface is read-only. It does not mutate snapshots, source manifests, publication state, calibration ledgers, resolution outcomes, or precommitment target sets.

## Controlled Surfaces

The public interface does not publish source collection automation, mutable resolver tools, private-data adapters, probability intervals, model parameters, scoring implementation, or private calibration code. Those surfaces remain unpublished method assets and can be shared through partner-specific agreements when appropriate.
