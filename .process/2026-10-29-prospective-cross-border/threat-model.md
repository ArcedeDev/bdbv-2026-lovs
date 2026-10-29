# Threat model
## Trust Boundaries
Local reviewed protocol/files enter the private cadence at lovs/cross_border_validation.py:176 and daily_snapshot_prep.py:1080. Declared data/reviewer metadata enters preparation at lovs/cross_border_validation.py:87. Draft readiness is separate from authorization at :179.
## Assets
Frozen forecasts, correct target attribution, private files, daily prep availability and later predictive-value claims.
## Entry Points
CLI --as-of/--bundle at lovs/cross_border_validation.py:217, prep_status at :176, prepare_baselines at :87, confined local paths at :37. No HTTP, network client, shell, activation, scoring or persistence command is added.
## Abuse Paths
Abuse-case tests: tests/test_cross_border_validation.py:73, tests/test_cross_border_validation.py:149, tests/test_cross_border_validation.py:257. The named negative tests below fail if their mitigation is removed.
- High: forge approval fields or revive the failed model as an active study.
- High: drop missing countries or invent traffic zeros to improve a comparison.
- High: import future country knowledge or treat transit/evacuation as confirmation.
- High: escape input paths to unrelated private files.
- High: block the synchronous cadence with an in-repository FIFO/special file.
- Medium: malformed, oversized, deeply nested or invalid-registry input aborts prep.
- Medium: reflect private paths/report IDs into persisted readiness metadata.
- Medium: hash/parse mutation or concurrent readers mix byte versions.
## Mitigations
- Approval bypass: protocol and adapter checks at lovs/cross_border_validation.py:71; literal false authorization at :179 and no executable activation path. Test no_missing_or_filled_review_fields_can_activate_this_adapter.
- Mitigation: Missingness/substitution: complete universe, point/seat tables, units, reviewed zeros and shared targets in prepare_baselines at :87. Missing-country and zero-review tests reject before any preview.
- Mitigation: Timing/attribution: strict midnight cutoff, conservative clocks, pre-cutoff filtering and existing country validator at :87. Future-clock/source-observation and transit/evacuation tests preserve the endpoint.
- Mitigation: Path escape: resolved root guard at :37; traversal/symlink test rejects.
- Mitigation: FIFO/special file: nonblocking no-follow open, fstat regular-file check and bounded read at :45. A two-second bounded-subprocess test rejects FIFO immediately. Adversary independently re-probed at about0.05seconds.
- Mitigation: Availability: 4MiB reads at :45, source-point/table limits and strict types at :87; typed failure containment in prep_status at :176 explicitly catches shared RegistrationError. Malformed/overflow/semantic-registry tests and bounded-load probe pass.
- Mitigation: Error disclosure: prep_status at :176 uses filename-free OSError details, generic registry errors, safe distance diagnostics and a240-character bound. Tests assert no absolute root or private report ID. Independently re-probed.
- Mitigation: Byte identity/race: same raw bytes hashed/parsed in prepare_baselines at :87 and immutable preview hashes; sixteen concurrent readers produce identical output/no writes.
- Mitigation: Source truth, rights, full coverage and independent public priority remain unresolved launch gates. Hashes are not oracles. No accepted bypass or founder approval is invented; this local adapter is not a sandbox against a repository owner.
## Static and Secret Checks
Focused AST/read-only API check found no eval/exec/dynamic import/shell/network/write/delete path in the adapter. Semgrep/Bandit are unavailable, so this is a narrow static check, not a full ruleset claim. Gitleaks8.30.1 scanned the seven changed files (~123KB), exit0/no leaks; final post-repair scan also passed (about125.68KB), with zero open adversarial findings.
## Adversarial Review
Fresh-context reviewer ran35 JSON-shape probes and direct FIFO/disclosure attacks. Round1: one Important FIFO hang and one Minor disclosure. Both fixed and independently verified. Final red-team-review.md has zero open findings and is ready for the review-only setup. Launch remains blocked.
