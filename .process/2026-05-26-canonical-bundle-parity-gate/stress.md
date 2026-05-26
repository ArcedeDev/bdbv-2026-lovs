# Stress: 2026-05-26-canonical-bundle-parity-gate

## Edge Cases

- Empty website snapshot directory: returns `skipped` with a reason instead of raising.
- Null or missing canonical live output: returns `skipped` with the missing path.
- Missing latest registered snapshot file: returns `failed` with the exact registered date and path.
- Unicode source labels: not parsed by this gate; only source ids and JSON keys are compared, so label encoding cannot alter parity.
- Boundary date semantics: route date may differ from analytic `asOf`; only `snapshot.date == registered latest` and `dateSemantics.analyticAsOf == live.as_of` are hard checks.
- Large current website snapshot: local probe checked 195 source refs and 8 asset pairs in under one second.
- Concurrent agent work: function is read-only and accepts explicit roots, so it does not mutate another agent's dirty website or LOVS worktree.

## Failure Injection

- Missing `data/live-bdbv-2026-output.json`: handled at `lovs/website_bundle_parity.py:163` through `lovs/website_bundle_parity.py:168`.
- Missing `data/bundibugyo-2026/manifest.json`: handled at `lovs/website_bundle_parity.py:169` through `lovs/website_bundle_parity.py:172`.
- Missing website snapshot directory: handled at `lovs/website_bundle_parity.py:173` through `lovs/website_bundle_parity.py:176`.
- Missing latest registered JSON: handled at `lovs/website_bundle_parity.py:185` through `lovs/website_bundle_parity.py:192`.
- Missing or drifted public assets: handled through `cross_surface_parity` at `lovs/website_bundle_parity.py:245` through `lovs/website_bundle_parity.py:257`.

## SLI

- SLI: local hard-gate run completes without writes and emits actionable field-level findings.
- Observed local run: `status=failed`, `counts=3`, `source_refs=195`, `asset_pairs=8`, `finding_count=27`.
- Stop condition: any non-empty `findings` list sets `status=failed`, which causes release and daily website gates to fail closed.

## Verification

- `python3 -m py_compile lovs/website_bundle_parity.py release_snapshot.py daily_snapshot_prep.py`: passed.
- `python3 -m unittest tests.test_website_bundle_parity tests.test_release_gates tests.test_publication_clock_contract`: passed, 7 tests.
