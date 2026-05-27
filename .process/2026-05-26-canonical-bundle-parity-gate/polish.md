# Polish: 2026-05-26-canonical-bundle-parity-gate

## Reuse Scan

- Reused `lovs.cross_surface_parity.check_cross_surface_parity` for public asset hashing instead of creating a second asset comparator.
- Reused the website publisher's source-reference shape: `sourceId`, `primarySourceId`, `sourceIds`, and `metricSourceIds`.
- Reused existing release and daily prep wiring points instead of adding a new command runner.

## Interface Narrowing

- Public Python surface is one function: `check_website_bundle_parity(lovs_root, website_root, allow_historical_source_ids=None)`.
- The function returns plain JSON-serializable data so `daily_snapshot_prep.py` can store it directly in prep packets.
- No new dependency, CLI, process, service, or generated artifact was introduced.

## Comment Audit

- Kept one module docstring and short docstrings for the public function and source-id normalizer.
- Avoided explanatory comments where function names and return shapes are already explicit.
- No TODO markers, em dashes, or internal runtime details were added to the source files.

## Net LOC Delta

- Tracked orchestration files: `daily_snapshot_prep.py` +6 net lines, `release_snapshot.py` +1 net line.
- New local gate module: `lovs/website_bundle_parity.py`, 261 lines.
- New unit test: `tests/test_website_bundle_parity.py`, 127 lines.
- New process artifacts: validation, verification, review, polish, stress, and threat-model notes.

The positive LOC delta is justified because the module creates the missing hard gate as a reusable primitive and avoids spreading equivalent checks across release, prep, and website test code.
