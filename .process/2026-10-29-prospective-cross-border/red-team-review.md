# Adversarial review - prospective cross-border validation

Verdict: ready to proceed with the review-only setup

Open severity counts: 0 Critical, 0 Important, 0 Minor.

Initial review found 1 Important and 1 Minor issue; both are resolved and independently reprobed.

## Scope

Reviewed the staged adapter, draft protocol, tests, guide, plan and threat model. This is a local read-only review/preparation system. Source truth, complete global coverage, licensing and independent witnesses remain hard-blocked manual reviews; they are not implemented authorization checks. No network, promotion, activation or heavy suite was run.

## Resolved findings

- Resolved [Important]: Special files originally bypassed the bounded-read availability guarantee. `lovs/cross_border_validation.py:43-45` opens any confined path using blocking file I/O before hash or size validation. An in-repository FIFO is accepted by `_path`; opening it waits indefinitely without a writer. With the supplied valid fixture, changing one receipt to a newly created FIFO made `prep_status` hang until a 2-second subprocess timeout killed the child. The daily hook calls this synchronously at `daily_snapshot_prep.py:1080`, so a selected bundle can prevent the existing prep packet from completing. This requires a special file already in the local checkout, not merely hostile JSON text, and does not provide activation. The author replaced blocking open with O_RDONLY|O_NONBLOCK|O_NOFOLLOW and fstat regular-file validation before bounded reads, and added a bounded FIFO regression. An independent subprocess probe now rejects the same receipt in approximately 0.05 seconds with invalid_review_inputs, no preview and launch_ready false.
- Resolved [Minor]: Readiness originally serialized raw exception messages. A missing input produces a blocker containing the absolute root and input path. Registry validation errors also echo attacker-controlled report IDs. This exposes local filesystem metadata in persisted packets and carries untrusted text into later summaries. The packets are private and no secret file contents were observed, so severity is Minor. The author now emits filename-free OSError details and generic registry/distance diagnostics with a length bound. Independent probes confirm that missing-file blockers omit the absolute root and semantically invalid registry blockers omit a private report-ID marker.

## Attack coverage

- Input fuzzing: replaced each of five payload roles with null, true, false, zero, empty list, empty object and a string: 35 shape probes returned invalid readiness without uncaught exceptions. The previously fixed RegistrationError issue was not repeated as a new finding. A symlink-loop path resolved on the actual Python 3.14 runtime and fails at open as an OSError; no untested claim of a RuntimeError escape is made.
- Authorization bypass: approval fields and protocol metadata cannot change the literal false launch/registration flags; no executable launch path exists. Reduced/made-up country universes and fabricated source metadata remain source-review assertions, not a bypass of an implemented authority check.
- Prompt injection: reviewer/source/report strings have no eval, shell, network or model execution path in the adapter. The original error text reflection is resolved above; no direct code execution was found.
- Supply-chain trust: only existing local modules are imported; no new dependency or remote executable is introduced. Hashes bind parsed bytes but cannot establish truthful data or real independent review, as the guide explicitly states.
- Error leakage: originally confirmed absolute filesystem paths in missing-file blockers; independently confirmed their removal after the fix. No credential values or private file contents were retrieved.
- Scope: protocol describes a review-only baseline setup, and the daily hook only emits observational metadata. No change to scoring or active study state was found. Launch must remain blocked after these review findings are repaired.

## Final assessment

No Critical, Important or Minor findings remain open. The author reports the six affected suites passed 115 tests in 3.05 seconds; this reviewer independently ran only the small FIFO and error-disclosure probes. Verdict is ready to proceed with the draft review branch. It does not authorize study activation, source promotion or predictive-value claims. The hard manual launch gates remain in place.
