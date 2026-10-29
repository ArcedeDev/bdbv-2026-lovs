# Stress report

## Edge Cases
- unicode: zero-width-joined country codes reject in test_malformed_missing_unicode_and_numeric_overflow_inputs_are_explicit.
- boundary: exact UTC cutoff, zero-seat coverage, 28-day travel period and size thresholds are covered in the named provenance/window/zero tests.
- Empty/null/missing/duplicate role, malformed JSON/UTF-8 and deep nesting: covered by tests/test_cross_border_validation.py.
- Boundary cases: zero/NaN/infinity/overflow, exact cutoff/travel periods and stale source: covered by the same suite.
- Large: >4 MiB and >1024 source points reject explicitly; 193-country load probe recorded in stress-evidence.json.
- Concurrent: 16 readers on 4 workers return identical previews and no changed files.
- Semantically invalid country registry: uncited report and cumulative mismatch return invalid_review_inputs after the independent-review fix.

## Failure Injection
- Each of five local input boundaries: partial JSON and missing file fail explicitly; file permission denial also returns blocked metadata.
- Wrong hash, future review/publication, wrong units, missing coverage and ambiguous date bounds never produce a partial preview.
- Network 500/503/auth/timeout injection skipped: no network API or remote client exists in this adapter. It consumes already reviewed local aggregates. Licensing/access remains a launch prerequisite.

## SLI
Target: one draft preview below 1 second at 193 countries/1024 source points. Three samples per level: max 0.004387s at 32; 0.009387s at 128; 0.033858s at 512; 0.065594s at 1024. At 1025 points the admitted-size SLI is violated and all three requests reject in <=0.000951s before the distance loop. Files >4 MiB also reject before unbounded reading. This is a tested size-boundary failure shape, not an unmeasured production p99 or latency saturation claim. All outputs remain launch_ready false.

Guarded affected suites: 115 passed in 3.05s. Probe uses generated test fixtures, not actual forecasts.

Special-file boundary: FIFO input rejects inside a 2-second bounded subprocess, independently re-probed at about0.05s. Path/report-ID error disclosure is also regression-tested.

- Unicode boundary: zero-width-joined country code is rejected; malformed UTF8 and deep nesting are covered.
- FIFO boundary: bounded subprocess rejects special file; independent probe completed in about0.05s.
