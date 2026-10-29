# Review - prospective cross-border setup

Reviewer: independent agent, given the spec, validation, glossary and staged diff, without the implementation conversation.

Round 1 verdict: blocked (0 Critical, 1 Important, 1 Minor).
Round 2 verdict: ready to proceed after the exception fix and regression verification.

## Spec restatement

Prepare a concrete review-only country-level validation protocol and two comparable rank baselines, then expose their blocked readiness through existing daily preparation. Baselines must share one complete reviewed country matrix, exclude pre-cutoff confirmed countries, bind source bytes and conservative clocks, and reject incomplete or corrupt inputs. No candidate promotion, registration, scoring, publication or activation is authorized. Real current inputs, a new eligible challenger, country adapters, independent outcome coverage and statistical review remain prerequisites.

## Findings

### Round 1 finding resolved in-session

- The registry domain exception at `lovs/cross_border_validation.py:195` originally escaped observational readiness and could abort ordinary prep. Fixed by explicitly catching `countries.RegistrationError`. `tests/test_cross_border_validation.py:234` adds semantically invalid registry coverage for uncited reports and cumulative-count mismatch, asserting blocked metadata and no preview. This reviewer independently executed that regression method successfully. The author reports the guarded six-file focused suite now passes 112 tests in 3.15 seconds.

### Accepted residual limit

- [Minor] Real baseline evidence remains unavailable in this review branch, as explicitly acknowledged at `docs/prospective-cross-border-validation.md:19` and `specs/cross-border-validation/protocol.DRAFT.json:43`. Fixture tests verify adapter behavior, not source truth or full global data adequacy. This is an accepted review-stage evidence limit and remains an activation blocker.

## Assessment

Correctness: matched targets, rank direction, exact ties, strict travel window and temporal report filtering match the proposal. The exception propagation defect is fixed and independently reprobed.

Security: bounded repository-contained byte reads, input hashes and protocol/preview content hashes bind the reviewed preview. Source claims and reviewer metadata remain human assertions, clearly disclosed.

Scope: the named synthesis object is implemented: draft protocol plus a small preparation adapter and existing cadence hook. The antithesis is materially respected: there is no eligible challenger or actual travel export, no reuse of the failed model, no new scoring and no activation path. Filling draft fields cannot set launch_ready true.

Edges and blast radius: tests cover numerous malformed data and path cases; the previously missed registry domain exception now returns invalid readiness without disrupting ordinary prep. Future country resolution/scoring adapters remain expressly absent. Negative coverage, independent freeze witness, pre-window conflicts, descriptive inference limits and six-round stopping are disclosed rather than silently implemented as approved policy.

## Verdict

Verdict: ready to proceed

No Critical or Important findings remain, and none are deferred. This verdict approves proceeding with the review-only setup, not activating a study or making a public scientific claim. The accepted evidence limit remains an activation blocker. This reviewer ran only small semantic-registry probes; the author supplied the guarded focused-suite result.
