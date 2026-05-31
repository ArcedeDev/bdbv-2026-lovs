# Public Method Cards

These cards describe the public part of the BDBV evidence methodology. They are meant to help public-health analysts reuse the reasoning discipline in this repository without needing private implementation details.

The cards expose doctrine, artifact shapes, and review steps. They do not expose model internals, source collection automation, private-data adapters, probability intervals, feature weights, thresholds, mutable scoring tools, or private calibration code.

## Card 1: Source Reconciliation

**Use when:** public sources report different counts for the same outbreak period.

**Public artifacts:** `data/public_snapshot.json`, `data/public_reported_counts.csv`, `data/public_source_manifest.json`, `data/public_source_index.csv`, `data/public_source_conflicts.json`.

**Method:**

1. Keep each numerical claim tied to its source ID, publisher, source tier, source date, publication date, retrieval date, and source-use status.
2. Compare counts by metric and date before selecting a headline value.
3. Preserve material disagreements in `data/public_source_conflicts.json`.
4. Treat source disagreement as an evidence state, not as a formatting problem.

**What this enables:** an analyst can explain why WHO, CDC, ECDC, MOH, INRB, INSP, or a dashboard may show different public counts on the same day.

**Risk level:** low. The method is a transparent public-review practice and does not reveal private scoring or automation.

## Card 2: Source Clocks

**Use when:** a source's data date, publication date, and retrieval date differ.

**Public artifacts:** `data/public_source_index.csv`, `data/public_latency_observatory.csv`, `DATA_DICTIONARY.md`.

**Method:**

1. Use `data_as_of` for the date represented by the reported data.
2. Use `published_at` for when the source made the report available.
3. Use `retrieved_at` for when the repository captured or reviewed the source.
4. Measure publication lag and total visibility lag only when the source exposes enough date information.
5. Keep rows with missing `data_as_of` visible instead of silently dropping them.

**What this enables:** an analyst can separate true epidemiological change from reporting latency.

**Risk level:** low. The method improves auditability and does not expose model logic.

## Card 3: Health-Zone Attribution Lag

**Use when:** headline national or country-scope totals are newer than the latest public health-zone table.

**Public artifacts:** `data/public_snapshot.json`, `data/public_zone_counts_2026-05-26.csv`, `data/public_blindspots.json`, `WORKED_SNAPSHOT_REVIEW.md`.

**Method:**

1. Read health-zone rows as source-attributed public records, not as a live official line list.
2. Sum the source-attributed zone rows.
3. Compare that sum with the headline public confirmed total.
4. If the headline is larger, record the difference as attribution lag unless a later public source assigns those cases.
5. Do not scale every zone upward to force agreement with the headline.

**What this enables:** an analyst can publish a map-ready public table while clearly stating that national totals may be timelier than zone attribution.

**Risk level:** low to medium. The judgment rule is valuable, but it does not disclose private model machinery.

## Card 4: Blindspot Register

**Use when:** public evidence cannot fully answer a public-health question.

**Public artifacts:** `data/public_blindspots.json`, `LIMITATIONS.md`, `PUBLIC_ADAPTATION_GUIDE.md`.

**Method:**

1. Name each evidence gap as a tracked blindspot.
2. Record the public effect of the gap.
3. Record the mitigation or review action.
4. Keep unresolved blindspots visible across releases.

**What this enables:** an analyst can avoid false precision while still giving partners a usable evidence package.

**Risk level:** low. This is public uncertainty accounting.

## Card 5: Calibration Accountability

**Use when:** public commitments should be visible before outcomes resolve.

**Public artifacts:** `data/public_calibration_ledger.csv`, `data/public_calibration_status.json`, `data/public_precommitment_targets.csv`, `CALIBRATION_RESOLUTION_PUBLIC.md`.

**Method:**

1. Register public questions before their resolution windows.
2. Publish public target roles and tier labels, not probabilities.
3. Keep rows open until public authority evidence supports resolution.
4. Resolve rows with citable public sources under the published resolution protocol.
5. Preserve commitment hashes so public row payloads can be checked across releases.

**What this enables:** an analyst can inspect whether commitments were made before outcomes were known.

**Risk level:** medium. The public structure is valuable and should be visible; the private scoring implementation, target-generation logic, and quantitative internals should remain private.

## Card 6: Nowcast Boundary

**Use when:** a public package needs to describe nowcast readiness without publishing model internals.

**Public artifacts:** `data/public_nowcast_status.json`, `READONLY_INTERFACE_PUBLIC.md`.

**Method:**

1. Publish whether a standing scored nowcast has been issued.
2. Publish candidate quantities and readiness inputs that are already public.
3. Exclude point estimates, predictive intervals, model parameters, calculation components, and private source inputs unless a future public release intentionally exposes a scored public commitment.

**What this enables:** partners can understand the public interface shape without mistaking this repository for an operational forecast.

**Risk level:** medium. Interface clarity is useful; quantitative internals should remain controlled.

## Practical Boundary

Public and encouraged:

- Source reconciliation rules.
- Source-clock handling.
- Attribution-lag treatment.
- Blindspot taxonomy.
- Calibration accountability shape.
- Public resolution protocol.
- Aggregate schemas and read-only examples.

Controlled and not published:

- Source collection automation.
- Private-data adapters.
- Mutable resolver tools.
- Probability intervals, weights, thresholds, and model parameters.
- Private calibration workbench inputs.
- Target-generation and scoring implementation.
- Line lists, lab records, contact chains, genomic sample IDs, and private dashboards.
