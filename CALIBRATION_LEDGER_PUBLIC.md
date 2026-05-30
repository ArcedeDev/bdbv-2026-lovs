# Public Calibration Ledger

The public calibration ledger is an accountability artifact. It records pre-registered public questions, registration dates, horizons, resolution dates, public resolution policy, status, and commitment hashes for selected 2026 BDBV corridor-watch commitments.

## What The Ledger Supports

- MOH, CDC, WHO, Africa CDC, ECDC, INRB, and peer analysts can see what was registered before outcomes resolved.
- Public readers can inspect the resolution policy and later compare open commitments with resolved public evidence.
- Each row has a `commitment_hash` so the public row payload can be checked for stability across releases.

## What The Ledger Does Not Publish

The ledger does not publish probability intervals, feature weights, prior or posterior parameters, calibration code, scoring implementation, source collection machinery, private-data adapters, or corridor-generation internals. Those remain unpublished method assets and can be shared through partner-specific agreements when useful.

## Resolution

Open commitments should be resolved from public MOH, WHO, Africa CDC, CDC, ECDC, INRB, or other cited public authority reporting available by the row's `resolution_date`. Ambiguous or unavailable public evidence should remain open until a documented review is added.
