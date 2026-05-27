# Threat Model: 2026-05-26-canonical-bundle-parity-gate

## Trust Boundaries

- Canonical LOVS checkout to website checkout: a separate repo can be dirty, stale, or generated from the wrong LOVS root.
- Source manifest to website source refs: website JSON may carry source ids that do not resolve in canonical LOVS.
- Release operator to public promotion: a human may mistake a local review branch for a clean release candidate.

## Assets

- Canonical headline counts and primary source IDs.
- Website latest registered snapshot JSON.
- Public-health workbook, schema, package manifest, brief PDF, and SVG visuals.
- Source provenance and date semantics.

## Entry Points

- `release_snapshot.py --with-website` invokes the release-side parity check at `release_snapshot.py:784` through `release_snapshot.py:794`.
- `daily_snapshot_prep.py --website-gates` invokes bundle parity before website tests at `daily_snapshot_prep.py:303` through `daily_snapshot_prep.py:342`.
- Direct local callers can invoke `lovs.website_bundle_parity.check_website_bundle_parity` at `lovs/website_bundle_parity.py:131` through `lovs/website_bundle_parity.py:141`.

## Abuse Paths

- [Important] A stale LOVS checkout generates a website snapshot with correct-looking internal refs but wrong canonical counts.
- [Important] A context source is copied into website JSON before it exists in the canonical manifest.
- [Important] Public assets are copied from a different bundle than the snapshot JSON.
- [Minor] A missing website checkout could be misread as clean if skipped output is ignored.

## Mitigations

- Count and primary-source mismatches are compared field-by-field at `lovs/website_bundle_parity.py:108` through `lovs/website_bundle_parity.py:128`.
- Source ids are canonicalized and checked against the LOVS manifest at `lovs/website_bundle_parity.py:26` through `lovs/website_bundle_parity.py:28` and `lovs/website_bundle_parity.py:227` through `lovs/website_bundle_parity.py:238`.
- Asset drift reuses byte parity at `lovs/website_bundle_parity.py:245` through `lovs/website_bundle_parity.py:250`.
- Release failures are surfaced as hard failures at `release_snapshot.py:668` through `release_snapshot.py:674`.
- Daily prep packet captures the structured parity result at `daily_snapshot_prep.py:339` through `daily_snapshot_prep.py:342`.

## Residual Risk

- Skipped website parity remains non-blocking when the website checkout is absent, matching current local/CI behavior. Operators should treat skipped parity as not reviewed, not clean.
