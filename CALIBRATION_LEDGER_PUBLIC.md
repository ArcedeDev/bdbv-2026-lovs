# Public Calibration Ledger

The public calibration ledger is an accountability artifact. It records pre-registered public questions, registration dates, horizons, resolution dates, public resolution policy, status, and commitment hashes for the 2026 BDBV calibration commitments.

## What The Ledger Supports

- MOH, CDC, WHO, Africa CDC, ECDC, INRB, and peer analysts can see what was registered before outcomes resolved.
- Public readers can inspect the resolution policy and later compare open commitments with resolved public evidence.
- Each row has a `commitment_hash` so the public row payload can be checked for stability across releases.

## Where Each Part Of The Record Lives

| File | What it holds |
|---|---|
| `data/public_calibration_ledger.csv` | One row per public commitment: the question, registration date, horizon, resolution date, source policy, public tier or threshold, status, resolved value, and commitment hash. |
| `data/calibration-ledger.json` | The corridor calibration blocks, with each point's pinned probability interval (`risk_adj_50`) and, once resolved, its outcome, outcome evidence, and resolution provenance. |
| `data/operational-calibration-ledger.json` | The operational blocks pinned on 2026-09-01, with each pin's generated probability, the generator that produced it, and the block's registration note. |
| `data/calibration-ledger.pinned-block-hashes.json` | Hashes of each pinned block, so a later change to a pinned block is detectable, plus a log of authorized outcome appends. |
| `calibration_resolver.py`, `lovs/forecast/opsresolver.py` | The resolvers that turn recorded outcomes into Brier scores. |
| Zenodo, concept DOI [10.5281/zenodo.21233091](https://doi.org/10.5281/zenodo.21233091) | The pre-registration of the 41-commitment block registered on 2026-07-05, including the tier-to-probability mapping (its Section 5.3) under which that block's tier-valued rows are scored. |

Two groups were pinned before they were public, and their rows in `data/public_calibration_commitments.json` say so in `first_published_at`: the 2026-06-04 corridor block, first published in this repository on 2026-06-12, and the 2026-09-01 blocks, first published on 2026-09-17. The earliest date anyone outside can verify a pin from a public record is its first-published date.

A resolved outcome changes only under a founder ruling, recorded as a dated amendment that states what changed and how the record moves. The evidence entry behind it is superseded rather than edited, the ledger point keeps its earlier outcome fields in `superseded_outcomes`, and the block hash is re-pinned. The one correction so far (2026-09-26) moved the two kisangani-cod pins of the 2026-06-04 block from YES to NO; the `resolution_note` of their rows in `data/public_calibration_commitments.json` also states the reading on which the YES could stand.

The `score_after_resolution` column in the CSV is not populated. Scores for the corridor and operational blocks are computed from their pinned probabilities and recorded outcomes by the resolvers above. The 2026-07-05 block's rows are tier-valued and are scored under the mapping fixed in its Zenodo pre-registration.

## What Is Not Redistributed

Restricted publisher bytes and private-data inputs are not redistributed. `data/bundibugyo-2026/manifest.json` keeps the URL, timestamp, and hash of each withheld source, so a claim that depends on one can still be checked against the publisher.

## Resolution

Open commitments should be resolved from public MOH, WHO, Africa CDC, CDC, ECDC, INRB, or other cited public authority reporting available by the row's `resolution_date`. Ambiguous or unavailable public evidence should remain open until a documented review is added.
