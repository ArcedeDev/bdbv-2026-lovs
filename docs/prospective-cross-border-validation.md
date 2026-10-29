# Prospective cross-border validation protocol

**Status: draft for Frans's review. No study has been activated or registered.**

The setup prepares a new country-level ranking comparison through the existing daily BDBV prep cadence. It preserves the DRC health-zone tournament and the sealed November operational tests. The failed corridor implementation remains ineligible. The [machine-readable draft](../specs/cross-border-validation/protocol.DRAFT.json) and [readiness command](../lovs/cross_border_validation.py) cannot create or activate a forecast.

## Question and target population

Does a separately reviewed, versioned challenger rank future first-country confirmations better than simple geography and travel-hub baselines on the same countries and windows?

Proposed universe: the [193 UN member states](https://research.un.org/en/unmembers/currentmembers), with a reviewed ISO3 list and one fixed capital reference point per state. Exclude DRC and countries with a public-authority laboratory confirmation before the source cutoff. Kenya, Uganda and Nigeria's already known first detections cannot become future successes. Territories and non-member observers are outside this version's universe; coverage beyond that population cannot be claimed.

The endpoint is the **first public-authority report of a laboratory-confirmed outbreak case in a previously unaffected country**. It measures detection, not place of infection, local acquisition or transmission during travel. Transit and planned medical evacuation do not create a country event. Secondary spread inside already affected countries requires a different registered question.

## Baselines and matched comparison

| Comparator | Frozen definition | Required input |
| --- | --- | --- |
| Geography only | Negative great-circle kilometres from each fixed capital reference point to the nearest reviewed DRC frontier zone. Higher is nearer. Reuse the existing 30-day frontier selector and metre rounding. | Complete country points, reviewed source-zone points and pre-cutoff tables; latest source table no more than 7 days old. |
| Travel-hub only | Total international scheduled arriving seats into each country, from all origins, in the 28 complete UTC days preceding cutoff. Higher is more connected. | Current licensed/reusable aggregate seat data with complete country coverage, explicit reviewed zeros, source clocks and hashes. |
| Challenger | Separately versioned country-level model with a reviewed complete target adapter and immutable code/parameters. | No eligible challenger is currently selected. The failed corridor model is not automatically promoted. |

The seat baseline measures scheduled air capacity, not passenger movements, direct connectivity from DRC or ground travel. Land mobility is omitted; that limitation remains visible. Capital distance is a deliberately simple country proxy, not a patient's itinerary or border-crossing distance. Both baselines must cover the entire reviewed input universe before any eligible-country preview is produced. One missing country blocks the preview, including missing data for a country later excluded as already affected.

[OurAirports](https://ourairports.com/help/data-dictionary.html) provides airport attributes, not arriving seats. [OpenFlights](https://openflights.org/data.php) warns its route updates ceased in June 2014. Neither is substituted for current scheduled-seat data. Hashes prove which bytes were reviewed; they do not prove source completeness, correct licensing or truthful metadata.

## Timing and existing cadence

- Existing twice-daily prep remains the scheduling mechanism. Every private prep packet gains `cross_border_validation`, and its journal summary records that state. This branch contains the hook; it becomes part of the existing scheduled prep only after reviewed merge/deployment. No extra automation is created.
- The experiment inherits the canonical **30-day UTC** horizon/cadence. Proposed pilot: **six non-overlapping rounds**, stopping after the sixth regardless of apparent success. This is a proposal for review, not a study start date.
- Source cutoffs use UTC midnight because existing country reports can have date-only publication clocks. Every used source must be available and reviewed by cutoff. A daily preview accepts no cutoff later than its UTC start-of-day boundary.
- An actual round starts on the next UTC day after an independently witnessed public freeze. If the approved candidate is delayed, rebuild from current reviewed inputs and obtain a new exact-candidate approval. Never reuse an elapsed preview window or backdate availability.
- Recheck country eligibility at actual freeze. A first detection becoming public after source cutoff but before the forecast window creates a pre-window conflict; preserve it as unscoreable with its reason, rather than repairing the universe after outcomes.
- Proposed evidence-review grace: **7 UTC days** after each window, to inspect publication clocks and primary sources. Grace is time to adjudicate, not permission to count an event first published outside its registered window.

## Outcomes and evaluation

Review outcomes independently of the forecaster. Record authority URL, evidence hash, actual first-publication time, data/confirmation date separately, review time and adjudicator. NO requires reviewed country-specific full-window coverage. Silence, incomplete coverage, conflicting clocks or missing evidence remain unscoreable; they never become zero outcomes.

The primary descriptive comparison is **paired per-round ROC AUC difference** against each baseline on the identical evaluable targets. Preserve ties. A round without both positive and negative outcomes has undefined AUC, with its denominator and reason reported. Publish all rounds and all exclusions, including failed, all-negative and low-coverage rounds. Report the share adjudicated against the full frozen target set and source delays alongside every result.

The proposed pilot reports paired estimates and data adequacy; it has **no automatic validated-superiority label or optional early-success stop**. Do not convert ranks to probabilities, compare their Brier scores, or pool country rows as independent replications. Before any confirmatory claim, separately freeze a meaningful effect, power analysis, dependence-aware uncertainty/multiplicity, required information and stopping rule. Those choices require independent methods review before launch; this draft has not supplied or passed that gate. Commercial benefit also requires a separate decision/action/cost log.

Later scoring should reuse the existing generic tournament scorer and evidence rules through a reviewed country adapter. The current DRC candidate builder and resolver are not country adapters. This draft deliberately supplies no registration, freeze, resolution or scoring command: the remaining adapter and approval work is an explicit activation gate.

## What Frans should review

| Decision | Draft proposal | Activation requirement |
| --- | --- | --- |
| Question and geography | Previously unaffected UN member countries; first reported confirmation, not local secondary transmission. | Approve this endpoint/population and point policy, or revise before registration. |
| Pilot and cadence | Six non-overlapping 30-day rounds; daily readiness monitoring; no early-success stop. | Independent methods review must approve the pilot/stopping scope. |
| Baselines | Capital-to-frontier distance and 28-day international arriving seats; no proxy substitution. | Approve definitions; technical owner must obtain and review complete input data/license. |
| Challenger | No candidate selected; old failed model stays ineligible. | A separately versioned model and country adapter require promotion review. |
| Witness and outcomes | Independent public availability witness; independent source adjudicator; proposed 7-day evidence grace. | Name owners/services and approve the exact receipt/coverage rules. |
| Strength of claim | Pilot estimates only; no calibration or general superiority claim. | Complete effect, power, dependence, multiplicity and confirmatory stopping design before a confirmatory study. |

Approving this document alone cannot activate a study: complete data, a reviewed candidate/country adapter and independently verified approvals/receipts are still required. The current adapter always returns `launch_ready: false`, even if someone fills the draft approval fields. A later reviewed activation change must verify those facts, not just flip a flag.

## Review commands and input contract

Read-only current readiness:

```bash
python3 -m lovs.cross_border_validation --as-of 2026-10-29
```

To inspect baseline preparation when reviewed aggregate inputs exist, add `--bundle` with a repository-relative feature-bundle path. This prints a review preview only. The feature bundle has schema `bdbv-cross-border-feature-bundle/v1`, an exact midnight `cutoff_utc`, and five unique `inputs` receipts:

| Role | JSON payload |
| --- | --- |
| `country_universe` | Complete unique ISO3 member-code list including COD; proposed count 193. |
| `country_points` | Exactly that full code set mapped to `[latitude, longitude]` capital reference points. |
| `source_frontier` | `points` by source-zone ID plus reviewed dated `observations` with cumulative `counts`, as the existing distance model expects. |
| `travel_hub` | Exact `window_start`/`window_end`, `unit: international_scheduled_arriving_seats`, `coverage: complete_country_universe`, complete `countries` counts and `reviewed_zero_countries`. |
| `international_registry` | Reviewed existing international-event registry shape, with report publication dates and corrections. |

Each receipt names `role`, repository-relative `path`, exact raw-byte `sha256`, `source_url`, `published_at`, `reviewed_at` and `reviewed_by`. Files are bounded to 4 MiB, the frontier to 1,024 source points and 400 dated tables. External paths, traversal, escaping symlinks and non-regular files are rejected; special files cannot block the synchronous prep read. The reader hashes and parses the same bytes. Reviewer assertions are inputs to source review, not independently verified authority facts. Missing current travel data is a real launch blocker, not an assumed access denial.

Owner: BDBV Snapshot Prep Manager for source/cadence readiness; model owner for challenger and country adapter; independent methods and source reviewers for design/adjudication; Frans for scope and release approval.
