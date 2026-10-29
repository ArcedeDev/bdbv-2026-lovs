# Verification - prospective cross-border setup
## Test execution
Guarded command: python3 -m pytest -q tests/test_cross_border_validation.py tests/test_daily_snapshot_prep.py tests/test_distance_only_frontier_30d.py tests/test_tournament_binding.py tests/test_model_tournament.py tests/test_tournament_resolution.py
Final result: 115 passed in 3.05s.
The first run exposed incomplete test registry fixtures (missing cumulative_confirmed); corrected fixtures then exercised the intended provenance and data guards. No failing check was suppressed.
## Step mapping
- Step 1: validation cites country attribution, geometry, cadence and source contracts.
- Step 2: protocol.DRAFT.json plus cross_border_validation.py prepare both ranks on one complete reviewed country matrix; missing/future/stale/malformed inputs return blocked metadata.
- Step 3: daily_snapshot_prep packet and journal include readiness; integration test exercises the existing prep/release flow without changing ordering.
- Step 4: existing distance, tournament binding, scoring and resolution suites pass; draft status is generated with no real feature bundle or registered forecast.
- Step 5: tests cover malformed JSON/UTF-8, deep nesting, NaN/infinity/overflow, missing country/role, exact cutoff/window boundaries, stale source, partial file, denied read, changed hash, path escape, oversized input and concurrent identical readers.
## Integrity
origin/main independently read as 5a271ff1542ed70db1dbb06736afadf143dbf9e0, matching this branch base. Frozen DRC round, existing registry/schedule/control, international-event records and separate ground-truth sealed files are not modified. The draft protocol is a separate population and review state.
No deployment, actual activation or country-round registration is represented as complete.

Independent review found uncaught RegistrationError from shared registry validation; explicit handling and semantic-registry tests now pass. Gitleaks and focused AST checks passed; bounded-load evidence is retained in stress-evidence.json.

Adversarial FIFO and metadata-disclosure findings fixed and independently re-probed: nonblocking no-follow open plus regular-file fstat, bounded/redacted diagnostics and bounded-subprocess regression. Final security review has zero open findings; launch remains blocked.

Final adversarial findings fixed and independently re-probed: nonblocking regular-file-only input and bounded/redacted errors. Final security review: zero open findings.

Final focused AST check and Gitleaks8.30.1 passed after security repairs: seven files, about125682bytes, no leaks. Frozen data diff is empty; ground-truth repo is clean.
