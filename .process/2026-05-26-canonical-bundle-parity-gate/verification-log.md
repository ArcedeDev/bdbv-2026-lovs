# Verification: 2026-05-26-canonical-bundle-parity-gate

## Commands

1. `python3 -m py_compile lovs/website_bundle_parity.py release_snapshot.py daily_snapshot_prep.py`
   - Result: passed.

2. `python3 -m unittest tests.test_website_bundle_parity tests.test_release_gates`
   - Result: passed, 5 tests.

3. `python3 -m unittest tests.test_website_bundle_parity tests.test_release_gates tests.test_publication_clock_contract`
   - Result: passed, 7 tests.

4. Local hard-gate probe against `/Users/frans/Documents/Arcede/projects/website/arcede-site/apps/site`
   - Result: expected failed local review state.
   - Latest registered website snapshot: `2026-05-26`.
   - Checked: 3 count metrics, 195 source refs, 8 asset pairs.
   - Findings: stale analytic clock, two non-canonical source ids, analysis audit drift, and six public-asset byte drifts.

## Outcome

The code path is verified without regenerating snapshots, changing counts, pushing, publishing, or promoting any preview.
