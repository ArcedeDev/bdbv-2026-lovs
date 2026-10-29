# Grill - prospective cross-border validation

Pass count: 1

## Auto-Resolved

1. **Which decisions may this review draft authorize, and which activation, forecast creation, or publication steps require later approval?**
   Answer: Authorized scope is review draft, deterministic DRAFT preparation/readiness contract and private daily hook. No freeze, immutable forecast, activation or publication. Existing freeze separately requires founder approval and exact merged candidate receipt.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:15; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:21; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/schedule.json:12
   Confidence: 0.95

2. **Which existing cadence, protocol, and review standards are authoritative for this experiment?**
   Answer: Reuse recurring 30-day tournament lifecycle, current source-availability/build receipt contract, reviewed resolution and existing scorer. Existing DRC round and separate sealed November protocol remain unchanged.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:5; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:31; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:51; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:55
   Confidence: 0.95

3. **What corridor model version, parameters, and source snapshot must be frozen before observation begins?**
   Answer: No eligible cross-border corridor candidate version/parameters are established. Require separately versioned reviewed target-level adapter, exact parameters/code/input hashes and source cutoff; keep missing candidate null and readiness blocked.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:40; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:49; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:26
   Confidence: 0.95

4. **What forecast and source history must be inspected to exclude leaked, retrospective, or ineligible predictions?**
   Answer: Inspect exact candidate, model registry promotion/version, source publication/review clocks, input hashes, original public availability and full target matrix. Existing failed corridor rows and currently unfinished DRC round cannot be recycled as new cross-border predictions.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:26; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:31; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:15; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:39
   Confidence: 0.95

5. **How are destination risks or ranks made comparable when candidate models have different output scales?**
   Answer: Compare rank outputs with ROC AUC on identical evaluable countries; never invent a probability from distance/seats. Only separately reviewed and pre-cutoff fitted/versioned probability mapping permits Brier/calibration comparisons.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:15; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:830; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:849; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:862
   Confidence: 0.9

6. **What defines a positive event, a reviewed negative, an unresolved label, and an excluded observation?**
   Answer: Positive is first authority-publication of laboratory-confirmed case in target country in frozen window, attributed where confirmed, excluding planned evacuation/transit. NO needs reviewed target-specific full-window coverage; missing/conflicting evidence remains unscoreable. Already affected exclusion requires new reviewed universe policy.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:6; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:8; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:14; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:47; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:448
   Confidence: 0.9

7. **How are corrections, late reports, duplicate events, and disputed geographic attribution adjudicated?**
   Answer: Reuse append-only reports, dated retractions and attributions, authority cumulative count semantics and publication clocks. Later facts apply only on disclosure date. Disputed calls use separately named reviewer under predeclared rule; cross-border grace/ruling details await approval.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:10; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:11; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:13; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:15; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:17
   Confidence: 0.85

8. **What secondary calibration, ranking, coverage, and operational metrics are descriptive rather than decisive?**
   Answer: Existing evaluator reports scored count, positives, coverage, rank AUC, probability Brier/Brier skill and calibration only above frozen minimum. Treat secondary/descriptive metrics separately from still-unapproved primary effect criterion; commercial utility needs its own decision log.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:825; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:830; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:839; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:849; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:65
   Confidence: 0.95

9. **What behavior is expected for zero events, all-zero scores, missing destinations, and tied forecasts?**
   Answer: Zero-event/all-one outcomes yield null AUC with reason; ties remain tied and receive half credit. Missing countries must block full matrix, never be quietly dropped or assigned invented zero. Identical resolved subset used for all models; all-zero ranks are valid only when backed by reviewed measurements.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:17; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/distance_only_frontier_30d.py:23; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:26; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:801; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:862
   Confidence: 0.9

10. **How does this cross-border protocol coexist with the current DRC-only tournament without changing its eligibility rules?**
   Answer: Keep new DRAFT protocol and country preparation contract separate from existing live registry/schedule, original 103-zone round and sealed November test. A future country adapter needs explicit routing; current resolver rejects country targets.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_resolution.py:223; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:51; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:55
   Confidence: 0.95

11. **Which existing collector, scheduler, forecast store, label pipeline, and evaluator should be reused?**
   Answer: Reuse daily_snapshot_prep private packet and journal/health cadence; pins_block11 registry loader/validation/effective_reports/qualifying_reports for international facts; shared hash/date/haversine primitives and model_tournament scorer/lifecycle. Country candidate/resolution adapters are future reviewed seams, no new scheduler/scorer.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/daily_snapshot_prep.py:204; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/daily_snapshot_prep.py:871; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/daily_snapshot_prep.py:1094; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/forecast/pins_block11.py:106; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/forecast/pins_block11.py:193; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/forecast/pins_block11.py:246; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:798
   Confidence: 0.9

12. **What integrity checks detect forecast mutation, mismatched windows, universe drift, and incomplete comparator coverage?**
   Answer: Reuse hash-bound complete matrix, source availability validation, exact merged candidate receipt and deterministic rebuild. Future country adapter must hash all additional inputs/implementation, verify universe equality and require every model-target row; immutable outcome binding includes forecast hash/window.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:26; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:31; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:404; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:435; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:455; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:15
   Confidence: 0.9

13. **Which operator receives actionable alerts through the existing cadence, and what response is required?**
   Answer: BDBV Snapshot Prep Manager is existing owner; include actionable blocked gates in same private packet/journal. Owner acquires/reviews inputs and submits concrete approval package; deployment operator handles later website rollout. Setup must not autonomously activate.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:78; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:95; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/daily_snapshot_prep.py:204
   Confidence: 0.9

14. **What rollback or suspension mechanism stops new experiment work while preserving all evidence and existing production behavior?**
   Answer: Draft hook can report suspended/disabled setup without altering existing production contract; preserve all input/review evidence. Later operational suspension uses existing explicit tournament control disablement and verified rollback, retaining immutable artifacts.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:52; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:78
   Confidence: 0.85

15. **What dependency failures must fail closed, and which recoverable failures permit a documented retry?**
   Answer: Missing/ineligible inputs, incomplete coverage, invalid clocks/hashes, mismatched identities and unapproved freeze fail closed for experiment readiness. Transient transport errors may be retried with recorded reason; never infer zero/freshness from failure or alter frozen bytes.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:45; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:46; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:98; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:31
   Confidence: 0.85

16. **How are obsolete corridor claims and historical backtests labeled or deprecated without erasing audit evidence?**
   Answer: Retain historical forecasts/results and explicit failed/ineligible label; separately version any replacement and require promotion review. Existing corridor and research mobility entries cannot be silently relabeled or erased.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:49; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:74; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:76
   Confidence: 0.95

## Assumptions

1. **What exact claim should a prospective corridor model validate, and what prior failed claim is explicitly excluded?**
   Answer: Prospective incremental country-first-confirmed detection rank skill against matched geography and travel-hub alternatives; probability calibration only for approved probabilities. This is a proposed new estimand, excludes retrospective corridor superiority and commercial utility claims.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:43; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:59
   Confidence: 0.7

2. **What geographic baseline is specified mathematically, including tie handling and unavailable inputs?**
   Answer: Proposed country geography rank is negative nearest source-frontier great-circle km at a reviewed country representative point; reuse haversine, round to metre, preserve exact ties. Representative point/frontier bundle and policy remain review inputs; missing point blocks complete output rather than dropping country.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/distance_only_frontier_30d.py:18; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/distance_only_frontier_30d.py:23; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/distance_only_frontier_30d.py:41
   Confidence: 0.7

3. **What travel-hub baseline is specified mathematically, and which verified travel data does it require?**
   Answer: Proposed travel rank sums international arriving scheduled seats from all origins into each destination over a fixed pre-cutoff 28-day interval, higher ranks higher; exact ties remain tied. Actual connectivity data are unacquired. Airport inventory or stale routes cannot substitute for seat connectivity.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:68; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:63
   Confidence: 0.65

4. **What minimum travel-data coverage, freshness, provenance, and licensing conditions make that baseline eligible?**
   Answer: Propose complete fixed-destination coverage, unambiguous reviewed zeros, exact period and source publication/review clocks before cutoff, raw bytes/hash and legally usable license. Numerical freshness bound and exact supplier/license remain review-required; unknown or missing destination values block eligibility.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:31; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:404; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:63
   Confidence: 0.65

5. **What happens if travel data remain unavailable at the scheduled freeze or deteriorate during follow-up?**
   Answer: No approved travel bundle means blocked freeze/readiness; do not substitute routes or invent zeros. Later source changes cannot mutate already frozen inputs; retain immutable evidence and report availability failures in normal prep.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:45; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:46; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:78
   Confidence: 0.75

6. **What fixed target universe includes cross-border destinations, and what evidence establishes its completeness?**
   Answer: Complete reviewed country universe is not supplied. Proposed separately versioned ISO country manifest excludes already authority-confirmed countries as of cutoff, treats evacuation-only countries as at risk, and proves membership/completeness against a named authoritative universe independent of observed itineraries. Current manifest remains null.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:23; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:36; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:45
   Confidence: 0.65

7. **How are geographic identifiers, border changes, aliases, hubs, and origin-destination pairs normalized?**
   Answer: Propose canonical country codes plus explicit aliases and historical boundary version; airports map to exactly one reviewed country; deduplicate source OD records by stable keys before seat totals. Concrete source taxonomy, territorial policy and boundary cases remain review inputs.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:25; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:39
   Confidence: 0.55

8. **What common forecast origin, horizon, cadence, outcome window, and reporting-delay cutoff apply to every comparator?**
   Answer: Reuse UTC 30-day cadence and inclusive horizon beginning next UTC calendar day after actual freeze; identical source cutoff/window across all models. Cross-border evidence-grace duration is not specified and must be reviewed before activation.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/schedule.json:4; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/schedule.json:10; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/schedule.json:20; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:16
   Confidence: 0.75

9. **What dependence structure links repeated windows, connected corridors, shared origins, and outbreak clusters?**
   Answer: Countries share outbreak sources and connectivity; adjacent windows can overlap epidemic paths and repeated destinations. Existing policy recognizes spatial correlation and aggregation by round; exact independent-round/cluster definition must be prespecified.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:20; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:863
   Confidence: 0.7

10. **How are multiplicity, subgroup exploration, tuning, and repeated interim looks constrained?**
   Answer: Existing no-metric-shopping policy freezes eligible metrics before outcomes. Proposed both-baseline primary family, declared analysis dates, tuning only on past data and exploratory subgroup labels; multiplicity correction/alpha budget still needs reviewed statistical specification.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:18; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:31
   Confidence: 0.6

11. **What draft artifacts and interfaces can be implemented now without producing immutable or public records?**
   Answer: Implement review-only protocol, machine DRAFT input/readiness contract and deterministic read-only baseline readiness adapter embedded in ordinary private prep/journal. Missing reviewed country/points/travel/candidate stay null with reasons. No immutable forecast, public projection, live registry mutation or network acquisition.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/daily_snapshot_prep.py:871; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/daily_snapshot_prep.py:1094; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:5
   Confidence: 0.7

12. **What explicit state machine separates draft, reviewed, eligible, frozen, running, completed, and invalidated states?**
   Answer: Proposed setup states draft_blocked and ready_for_review, with ready_to_freeze always false until separately reviewed activation. Future lifecycle reviewed/eligible/frozen/running/completed/invalidated must route existing tournament controls. Avoid pretending new DRAFT state is live registry status.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/model-registry.json:90; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:5; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:44
   Confidence: 0.65

13. **What schema records comparator identity, universe version, input lineage, eligibility gates, freeze evidence, and outcomes?**
   Answer: Propose versioned DRAFT schema: protocol id/status, UTC cutoff and matched window, country manifest, representative points, frontier, 28-day travel bundle, distinct candidate adapter identity/parameters, hash/clock/license/coverage lineage, gates and unresolved approvals. Freeze evidence/outcomes null until existing lifecycle supplies them.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:396; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:421; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:47
   Confidence: 0.65

14. **What idempotency and concurrency rules prevent duplicate freezes, forecasts, labels, or scheduled runs?**
   Answer: Draft preparation is deterministic from explicit cutoff and exact input bytes and uses existing per-date/slot private packet writer, no new scheduler or freeze call. Future freeze concurrency must inherit authoritative lifecycle; no new lock/idempotency service is justified by this draft.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:38; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/daily_snapshot_prep.py:871; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:24
   Confidence: 0.7

15. **What telemetry distinguishes missing inputs, ineligible comparators, delayed labels, invalid runs, and genuine model failure?**
   Answer: Propose stable readiness diagnostics per missing/invalid input, coverage/hash/clock failure, ineligible candidate, pending review and frozen-outcome failure. Report readiness separately from model performance; absent travel input must never appear as losing travel forecast.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:42; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/daily_prep_health.py:293; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/daily_prep_health.py:370
   Confidence: 0.7

16. **What credentials, permissions, data minimization, and provenance controls apply to travel and outbreak inputs?**
   Answer: Use public authority aggregated country evidence, aggregate seat connectivity and explicit license/provenance; no personal travel records needed. Credentials stay existing credential store and must not enter artifacts/logs. Supplier permissions and legal reuse are not yet verified, so remain gating inputs.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:7; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/international-events.json:19; /Volumes/Workspace/Projects/Arcede/AGENTS.md:33
   Confidence: 0.65

17. **What storage, runtime, API quota, and evaluation bounds support the full fixed universe at the existing cadence?**
   Answer: Draft hook should use local small reviewed aggregates, stdlib deterministic work and no API calls/new background workers. Exact runtime/quota bounds cannot be established before source bundle exists; preserve mac-storage wrapper for heavy verification. Full target coverage remains mandatory regardless of compute.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/tournament_candidates.py:38; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/daily_snapshot_prep.py:871; /Volumes/Workspace/Projects/Arcede/AGENTS.md:7
   Confidence: 0.65

18. **What review package, acceptance checks, unresolved gates, and named approvals must exist before any later activation?**
   Answer: Review package needs protocol, DRAFT schema/readiness output, plan/validation/glossary, reproducible tests and unchanged-live-artifact evidence. Later activation additionally requires complete reviewed country/point/travel bundles, eligible versioned candidate, primary effect/power/stopping/uncertainty decisions, independent public witness and exact founder-approved candidate merge. Current unresolved inputs prohibit activation.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:15; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/runbooks/bdbv-model-tournament.md:21; /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/data/model-tournament/schedule.json:12; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:59
   Confidence: 0.75

19. **What dependence-aware confidence intervals and paired comparison method are prespecified?**
   Answer: Existing scorer forbids iid intervals for a correlated single round. Proposed paired round-level effects and dependence-aware clustered uncertainty require reviewed cluster definition, resampling method and minimum independent rounds; not yet configured.
   Citations: /Volumes/Workspace/Projects/Arcede/projects/.worktrees/bdbv-transfer-publisher/lovs/model_tournament.py:863; /Volumes/Workspace/Projects/Arcede/projects/arcede-ground-truth/CORRIDOR-VALIDATION.md:64
   Confidence: 0.3

## Human Decisions

1. **Review before launch**
   Answer: [HUMAN] "Can you set up the prospective validation against geographic and travel-hub baselines, using the existing cadence and in line with our standards? I then want to review". Setup is authorized; launch and scientific-design choices remain blocked for review.

## Decisions pending review

- What stop date or futility rule prevents an indefinite experiment without outcome-driven optional stopping? Proposed disposition: Fixed experiment end date, minimum follow-up and futility rule remain human-required; proposal must disallow outcome-driven optional stopping until these are approved. This is not approved.
- Who independently witnesses the freeze, and what timestamped evidence proves it preceded all scored outcomes? Proposed disposition: Independent public freeze witness identity, service and receipt schema remain human-required. Local git timestamps and merged approval alone do not prove independent public availability before every scored outcome. Draft must leave witness null. This is not approved.
- What event count, effective sample size, power assumptions, and minimum follow-up make conclusions eligible? Proposed disposition: Power, minimum positives/negatives, effective independent rounds and follow-up are not established. AUC needs outcome class variation; calibration minimum 20 is an existing descriptive gate, not sufficient power for country superiority. Human statistical design required. This is not approved.
- Who reviews negative labels, using what evidence and delay allowance, independently of model scores? Proposed disposition: Existing core requires review after completed window, sources and frozen country/window coverage binding. Named independent adjudicator and cross-border evidence-grace allowance are unresolved human inputs; no default reviewed NO. This is not approved.
- What prespecified primary metric and minimum improvement determine success against both baselines? Proposed disposition: Proposed primary rank comparison is paired AUC advantage over BOTH geography and travel comparators on identical evaluable sets across repeated independent rounds. Meaningful effect threshold and decisive success rule are unresolved; leave null and prohibit superiority claims. This is not approved.

Consolidated review gates: independent public witness; independent outcome adjudicator/evidence grace; effect, power, uncertainty and stopping design. Data and candidate-model prerequisites remain blocked. No decision is inferred from timeout.
Prior grill-log: no match above 0.15 floor (156 scanned, best 0.0463).
Cost / latency: Sonnet unavailable, inherited model. One resolver pass. Initial source-relative citations were expanded against the actual source root and revalidated, preserving original confidences; final 16 auto, 19 assumptions, 5 escalations. No answer was promoted without its cited file.
