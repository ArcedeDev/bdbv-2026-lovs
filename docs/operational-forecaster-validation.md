# Operational forecaster: method and validation

Companion to `lovs/forecast/opsforecast.py` and the operational calibration
ledger. Written before the first block resolved, so nothing here is chosen with
hindsight.

## Why an operational forecaster

The corridor model saturated. At the current burden its target-level union
hazard is 1.000000 for every eligible target, so it can no longer discriminate
between places. Corridor Block 5 pins that saturation as a falsification test.
This forecaster goes where discrimination still exists: the response system's
own indicators.

That choice is not only pragmatic. Everyone forecasts the virus. The response
system's degradation is slower-moving and therefore more forecastable, it is
actionable in a way transmission is not, and it decides whether anyone's case
numbers mean anything at all.

## Substrate

`data/operational-series-2026-09-01.json`: a frozen tidy extract of 101
INSP/INRB SitRep packets covering 2026-05-14 to 2026-08-28. Committed rather
than read live from a sibling checkout, so a forecast run reproduces from this
repository alone.

Coverage of the metrics this block uses:

| metric | observations | last value | observed range |
|---|---|---|---|
| `contact_followup_percent` | 81 | 84.4 | 21.3 – 87.3 |
| `hospital_isolation_total` | 80 | 896 | 258 – 896 |
| `new_confirmed_today` | 80 | 82 | 17 – 125 |
| `health_zones_touched` | 80 | 60 | 25 – 60 |
| `alerts_reported` | 79 | 2025 | 257 – 2371 |
| `lab_positivity_percent` | 66 | 14.4 | 10.6 – 54.4 |

## Method

Stationary block bootstrap over day-over-day changes. Five-day blocks, 20,000
paths, seeded per pin. Blocks rather than single days because these series are
autocorrelated: a contact follow-up rate does not jump independently each
morning, and resampling single days would understate the chance of a sustained
excursion. Differences are normalised per elapsed day so a three-day reporting
gap does not enter the bootstrap as one enormous single-day move.

## Validation: walk-forward backtest

At each origin, forecast 30 days ahead using only data available at that
origin, then score against what actually happened. Question shapes are the ones
the block actually pins.

| | value |
|---|---|
| forecasts scored | 450 |
| base rate of occurrence | 0.533 |
| forecaster Brier | 0.1996 |
| always-predict-base-rate Brier | 0.2489 |
| **skill vs base rate** | **+0.198** |

**Corrected 2026-09-01.** The table above truncates history on the DATA day. A
packet describing data day D is published on D+1, so that hands the backtest one
publication lag it would not have had — a real one-lag look-ahead, found by
`cadenceintegrity.py` in its own build and checked here. Re-run over the same
450 forecasts with publication-day truncation: **Brier 0.1870, skill +0.2486**.
The bias ran in the conservative direction; the original figure understated the
forecaster. Anchoring the bootstrap on a one-day-older level appears to be less
sensitive to the latest noisy observation. No pinned probability is affected —
the pins involve no backtest.

By question shape:

| shape | n | Brier |
|---|---|---|
| `ends_above` | 150 | 0.1739 |
| `ends_below` | 150 | 0.1884 |
| `sustained7_below` | 150 | 0.2365 |

End-of-window levels score better than sustained-excursion questions, so the
pin ladder leans on them.

### Reliability, and a real bias

| predicted | n | observed |
|---|---|---|
| 0.05 | 81 | **0.23** |
| 0.15 | 19 | **0.42** |
| 0.25 | 31 | 0.19 |
| 0.35 | 43 | 0.40 |
| 0.45 | 55 | 0.42 |
| 0.55 | 51 | 0.61 |
| 0.65 | 47 | 0.66 |
| 0.75 | 33 | 0.70 |
| 0.85 | 29 | 0.93 |
| 0.95 | 61 | 0.90 |

The middle of the range is well behaved. **The low end is not: things the
forecaster prices at 0.05 happen 23% of the time, and things priced at 0.15
happen 42% of the time.** Restricting to the 0.15–0.85 band gives skill +0.119
over 282 forecasts — smaller, but measured in the band the pins occupy.

Two consequences, both applied:

1. Pins are drawn from the band where skill is measured.
2. Two pins deliberately commit the model's own low numbers (`iso-below-850` at
   0.129, `cft-below-70` at 0.180) as **bias tests**. The alternative — quietly
   shrinking low forecasts upward — would hide the bias instead of measuring
   it, and would make the next resolution uninterpretable.

## What this method cannot support

The bootstrap encodes only how these series have moved. A structural break is
outside it: a funding cliff, a security incident closing a province, a change
in reporting definition. A pin whose resolution turns on such an event should
say so in its own rationale rather than lean on this number.

Two candidate metrics were considered and **rejected** for this block:

- **`confirmed_cfr_percent`.** The bootstrap returned 1.000 for "ever exceeds
  50 percent", purely by extrapolating upward drift in a ratio whose
  denominator is growing. The drift is real but decays; the bootstrap does not
  know that. CFR is better handled as a cross-province ascertainment instrument
  than as a bootstrapped level.
- **"Ever crosses X" on a noisy daily series.** Dominated by single-day noise
  rather than signal, which is why lab positivity returned 0.98 for "ever ≥ 20"
  and 0.78 for "ever ≤ 10" simultaneously. Sustained and end-of-window shapes
  filter that noise.

## How this block will be read

12 pins across 6 metrics spanning predicted 0.129 to 0.744, occupying six
deciles. For comparison, the corridor ledger's 19 resolved pins span 0.230 to
0.459 across 12 distinct events, which its own resolver reports as effectively
a single bin. This is the first block in the programme that can produce a
reliability curve rather than a single point.

## Blocks 8 and 9 (registered 2026-10-04): choosing the challengers

**The question.** Blocks 6 and 7 resolved with modest real skill: +0.144 against the pooled base rate, against a backtest skill of +0.249 that was never reproducible because its script was not committed. Their misses share one cause. The level bootstrap resamples day-over-day changes from the whole history, so it carries the spring's growth into plateaus (affected zones, lab volume) and lags steady growth (recovered). Blocks 8 and 9 test a fix rather than assume it: every question is priced by the incumbent and by one challenger, at the same threshold.

**The rule, fixed before the table below was read.**
1. The candidates were a pre-specified grid: the incumbent; a walk over the last 21, 28 or 42 days of changes; and recent levels over the last 14, 21, 28 or 42 days. It is `SELECTION_GRID` in `lovs/forecast/backtest.py`.
2. One window per class of series, never one per metric. Choosing each metric's best of seven on 9 to 25 origins would fit noise.
3. Cumulative series take a recent walk. Fluctuating series take recent levels, because a walk with drift is the wrong family for a series that rises and falls around a level.
4. The classes took the 28-day walk and the 21-day levels model.
   - These choices were made on an earlier, uncommitted data-day run over the first ten series.
   - No numeric selection criterion was fixed before that run. The windows were chosen by judgment, favouring mid-length windows, and they are not the grid's best on mean CRPS skill. In the committed runs below:
     - the 21-day walk edges the 28-day walk;
     - the 14-day levels window edges the 21-day one.
     - Both gaps are small next to origin-to-origin noise, and the threshold-Brier picture is the same for every levels window.
   - The three structural series added to Block 9 later (daily deaths, publication lag, Nord-Kivu isolation) did not influence the choice.
   - The full grid for both cuts follows the per-series table, so the choice can be checked.

**What the test is.**
- It is a walk-forward, retrospective test on the corrected extract `data/operational-series-2026-10-01.json`.
- At each origin, the history holds only data days whose SitRep had been published by then, so there is no data-day look-ahead. The 2026-09-01 erratum recorded that look-ahead in the earlier backtest.
- The values carry corrections made after first publication, so this is not a replay of the figures as they stood in real time.
- The horizon is 31 days, with 2,000 paths per forecast. Origins fall on every second publication day.
- Scores:
  - CRPS skill is 1 minus the challenger's mean CRPS over the incumbent's; positive means the challenger's whole distribution was better.
  - The threshold-Brier difference is challenger minus incumbent, over questions set at the 0.1/0.3/0.5/0.7/0.9 quantiles of a 50/50 mixture of the two methods' draws; negative means the challenger was better. It is the shape of the questions Blocks 8 and 9 pin.

Reproduce with `python3 -m lovs.forecast.backtest` (publication-day cut) or add `--data-day-cut`.

| Series | Block | Challenger | Origins | CRPS skill | Threshold-Brier difference | CRPS skill, data-day cut |
|---|---|---|---|---|---|---|
| confirmed_total | 8 | walk 28d | 25 | +0.338 | -0.2225 | +0.318 |
| confirmed_deaths_total | 8 | walk 28d | 18 | +0.199 | -0.0745 | +0.164 |
| cumulative_recovered | 8 | walk 28d | 18 | +0.514 | -0.4023 | +0.541 |
| health_zones_touched | 8 | walk 28d | 18 | +0.113 | -0.0616 | +0.109 |
| hospital_isolation_total | 9 | levels 21d | 18 | +0.019 | +0.0920 | -0.126 |
| contact_followup_percent | 9 | levels 21d | 23 | +0.215 | +0.0256 | +0.243 |
| lab_positivity_percent | 9 | levels 21d | 9 | +0.488 | -0.0763 | +0.516 |
| samples_analyzed | 9 | levels 21d | 9 | -0.296 | +0.0765 | -0.405 |
| alerts_reported | 9 | levels 21d | 18 | -0.879 | +0.1834 | -1.125 |
| new_confirmed_today | 9 | levels 21d | 18 | +0.406 | -0.0429 | +0.357 |
| new_confirmed_deaths_today | 9 | levels 21d | 16 | +0.089 | +0.0231 | +0.212 |
| publication_lag | 9 | levels 21d | 25 | +0.149 | +0.0037 | +0.144 |
| nordkivu_isolation | 9 | levels 21d | 16 | -0.908 | +0.2377 | -0.784 |

Full grid, by class. Mean skill is the mean of per-series values. "Improved" counts series with CRPS skill above zero, or a threshold-Brier difference below zero.

| Cut | Class | Window | Mean CRPS skill | Series improved (CRPS) | Mean threshold-Brier difference | Series improved (Brier) |
|---|---|---|---|---|---|---|
| publication day | cumulative (4) | walk 21d | +0.299 | 4 of 4 | -0.1974 | 4 of 4 |
| publication day | cumulative (4) | walk 28d | +0.291 | 4 of 4 | -0.1902 | 4 of 4 |
| publication day | cumulative (4) | walk 42d | +0.187 | 4 of 4 | -0.1504 | 4 of 4 |
| publication day | fluctuating, first six | levels 14d | +0.030 | 4 of 6 | +0.0453 | 2 of 6 |
| publication day | fluctuating, first six | levels 21d | -0.008 | 4 of 6 | +0.0430 | 2 of 6 |
| publication day | fluctuating, first six | levels 28d | -0.066 | 3 of 6 | +0.0548 | 2 of 6 |
| publication day | fluctuating, first six | levels 42d | -0.210 | 3 of 6 | +0.0715 | 2 of 6 |
| publication day | fluctuating, all nine | levels 14d | -0.051 | 6 of 9 | +0.0592 | 2 of 9 |
| publication day | fluctuating, all nine | levels 21d | -0.080 | 6 of 9 | +0.0581 | 2 of 9 |
| publication day | fluctuating, all nine | levels 28d | -0.132 | 5 of 9 | +0.0720 | 2 of 9 |
| publication day | fluctuating, all nine | levels 42d | -0.263 | 4 of 9 | +0.0859 | 2 of 9 |
| data day | cumulative (4) | walk 21d | +0.291 | 3 of 4 | -0.1872 | 4 of 4 |
| data day | cumulative (4) | walk 28d | +0.283 | 4 of 4 | -0.1882 | 4 of 4 |
| data day | cumulative (4) | walk 42d | +0.182 | 4 of 4 | -0.1467 | 4 of 4 |
| data day | fluctuating, first six | levels 14d | -0.047 | 3 of 6 | +0.0560 | 2 of 6 |
| data day | fluctuating, first six | levels 21d | -0.090 | 3 of 6 | +0.0565 | 2 of 6 |
| data day | fluctuating, first six | levels 28d | -0.144 | 3 of 6 | +0.0651 | 2 of 6 |
| data day | fluctuating, first six | levels 42d | -0.282 | 3 of 6 | +0.0770 | 1 of 6 |
| data day | fluctuating, all nine | levels 14d | -0.078 | 5 of 9 | +0.0578 | 4 of 9 |
| data day | fluctuating, all nine | levels 21d | -0.107 | 5 of 9 | +0.0607 | 3 of 9 |
| data day | fluctuating, all nine | levels 28d | -0.149 | 5 of 9 | +0.0683 | 3 of 9 |
| data day | fluctuating, all nine | levels 42d | -0.260 | 5 of 9 | +0.0821 | 1 of 9 |

**Reading.**
- **Cumulative series.** The 28-day walk beat the incumbent on all four series, on both scores. That historical record supports running the test; it does not promise a prospective win.
- **Fluctuating series.** The levels model beat the incumbent on CRPS for six of the nine series but on the threshold Brier for only two (lab positivity, daily new cases). It prices any threshold outside its 21-day range at exactly 0 or 1, and those certainties are expensive when wrong.
  - The prior for Block 9, on its own primary endpoint, leans to the incumbent.
  - The test is open, which is why it is worth running.
- **Correlated origins.** Origins two days apart share most of their horizon. Neither the origin count nor the per-series score is an independent sample.
- **Correlated rungs.** Each block's threshold questions are rungs of one ladder on one realised value. Blocks 8 to 10 add 70 rows to the headline record (63 incumbent rungs and 7 Block 10 pins), against 25 for Blocks 6 and 7. A pooled count or a recalibration floor that counts rows treats correlated rungs as separate evidence; read the pooled record with that in mind.

**How Blocks 8 and 9 will be read.**
- **Primary endpoint:** the mean over metrics of each metric's mean threshold-Brier difference, challenger minus incumbent.
- **Decision rule, written into each block before any outcome:** adopt the challenger as the provisional forecaster of record for its class if that mean is below zero AND the challenger is better on at least 3 of 4 metrics (Block 8) or 6 of 9 (Block 9).
  - A difference of exactly zero is not a win.
  - With only k scoreable metrics, the requirement scales to ceil(3k/4) or ceil(6k/9). Below 3 (or 6) scoreable metrics the block is inconclusive and the incumbent stays.
  - A cumulative question that a SitRep published on or before the registration receipt date already decides is void for both methods (`lovs/forecast/pins_block8.questions_decided_before`).
  - The rule is evaluated by `lovs/forecast_scoring.paired_block_decision`, which is committed before any outcome.
  - Adoption is provisional: the displaced method keeps being priced beside it, and permanence needs a second prospective block that agrees.
  - Pins keep the role they were registered with, so challenger rows never enter the headline record (`lovs/forecast/record.py` `of_record`).
- **What is reported descriptively, with no significance claim:**
  - the exact sign-flip p-value over metrics (`lovs/forecast_scoring.sign_flip_p_value`), together with its floor: the smallest attainable two-sided value is 0.125 with four metrics and about 0.004 with nine. Without justified joint sign-exchangeability, which national series that move together do not provide, the number is descriptive, not calibrated significance evidence;
  - the PIT position of each outcome among a metric's thresholds;
  - the hit rate of pins priced at or below 0.20;
  - the pooled recalibration map applied to incumbent prices (never a published price).

## Blocks 8, 9 and 10: resolved on 2026-11-11

**How they were resolved.**
- All 133 pins resolve from `data/operational-series-2026-11-07.json`, read with `lovs/forecast/opsresolver.py --as-of 2026-11-11`. The window's last data day is SitRep 171 (data 2026-11-01). No pin is stale, pending or unscoreable.
- Before any outcome was appended, the extract was checked value by value against the reviewed promotions in `data/sitrep_promotions/` for every data day from 2026-10-01 to 2026-11-01: 384 values over 12 fields, the Nord-Kivu isolation input and the publication dates. There is no difference.
- No question is void. The registration receipt (2026-10-04T11:55:39Z) is inside its deadline, and `questions_decided_before` finds no cumulative question already settled at that date.
- The blocks resolved ten days after their resolution date because no resolution step was scheduled for them. Outcomes are read by data day, so the delay changes nothing in them.
- Each pin carries the four appended fields (`outcome`, `resolved_as_of`, `outcome_series`, `resolution_provenance`). No pin, probability, threshold or block status changed.

**Block 8, cumulative series: the challenger is adopted provisionally.** Mean threshold-Brier difference, challenger minus incumbent, by metric:

| Metric | Difference |
|---|---|
| confirmed cases | -0.1140 |
| confirmed deaths | -0.0490 |
| health zones touched | -0.0982 |
| recovered | +0.1325 |

The mean is -0.0322 and the 28-day drift challenger is better on 3 of 4 metrics, which meets the registered requirement of 3. Under the rule written into the block before any outcome, it becomes the provisional forecaster of record for cumulative series in the next block. The incumbent keeps being priced beside it, and permanence needs a second prospective block that agrees. Over the 19 questions (9 resolved YES), mean Brier is 0.160 for the incumbent and 0.119 for the challenger.

The deaths result rests on a revision the source made and then undid inside the window. SitRep 169 (data 2026-10-30) harmonised Ituri's deaths retrospectively and stopped printing the deaths not yet allocated to a zone, which, with the day's 32 new deaths, raised cumulative confirmed deaths from 5,026 to 5,372. SitReps 170 and 171 returned to the earlier series (5,103, then 5,126). The registered rule reads the last data day on or before 2026-11-01, so SitRep 171 decides. Had the SitRep 169 series held, the rungs at 5,150 and 5,250 would have resolved NO, the challenger would have been better on 2 of 4 metrics (mean +0.0055), and the incumbent would have stayed.

**Block 9, fluctuating series: the incumbent stays.**

| Metric | Difference |
|---|---|
| alerts reported | +0.0522 |
| contact follow-up | -0.0475 |
| isolation, national | +0.2442 |
| lab positivity | -0.1078 |
| new confirmed deaths | +0.0285 |
| new confirmed cases | -0.0779 |
| isolation, Nord-Kivu | +0.4855 |
| publication lag | -0.0832 |
| samples analysed | +0.0115 |

The mean is +0.0561 and the 21-day levels challenger is better on 4 of 9, short of the required 6, so the incumbent stays. Over the 44 questions (20 resolved YES), mean Brier is 0.135 for the incumbent and 0.194 for the challenger. This is the direction the backtest's prior pointed. The challenger's two largest losses are on the isolation series, where the realised value fell above its 21-day range (961 nationally against 759 to 938, and 541 in Nord-Kivu against 316 to 403) and it had priced the rungs above that range at exactly 1, the failure the backtest named. The Nord-Kivu rung at 540 resolves NO on a value of 541, one above it; the value is the same in the SitRep 171 provincial table, its care movement table and its care note, and the national census reconciles to 961.

**Block 10, low counts and the feed.** The lowest daily count of new confirmed cases in the window was 40 (SitRep 156, data 2026-10-17), so the rungs at 0, 15 and 30 resolve NO and those at 45 and 60 resolve YES. The feed delivered 27 data days from 2026-10-06 to 2026-11-01 with no gap longer than one day, so both feed questions resolve YES. Over its 7 pins (4 YES), mean Brier is 0.118. The registered prediction was not falsified: the lowest count, 40, stayed above 27. The five rung prices sum to 2.675 and 2 rungs resolved YES, fewer than the prices imply, in the direction the hypothesis stated; one realised value is a single observation of it.

**Reported descriptively, with no significance claim.**
- Exact sign-flip p-value over metrics: 0.625 for Block 8 (smallest attainable 0.125) and 0.477 for Block 9 (smallest attainable 0.0039). National series move together, so the value is descriptive, not calibrated evidence.
- Pins priced at or below 0.20: Block 8, 5 incumbent pins with 1 YES and 6 challenger pins with 1 YES; Block 9, 7 incumbent pins and 9 challenger pins, none YES.
- Brier against the pooled base rate (0.463, over the 95 resolved operational pins of record): 0.249 for Block 8 and 0.248 for Block 9, beside 0.160 and 0.135 for the incumbent.
- The pooled recalibration map fitted on the 108 rows of record in this repository resolved before these blocks (cross-validated Brier 0.243 against 0.273 raw) would have made the incumbent's prices worse here: 0.186 against 0.160 for Block 8, and 0.157 against 0.135 for Block 9. It is never a published price.
- PIT position of each realised value among a metric's thresholds, as the interval its ladder allows:

| Block | Metric | Incumbent | Challenger |
|---|---|---|---|
| 8 | confirmed cases | 0.69 to 0.79 | 0.39 to 0.66 |
| 8 | confirmed deaths | 0.47 to 0.63 | 0.48 to 0.82 |
| 8 | health zones touched | 0.02 to 0.10 | 0.69 to 0.98 |
| 8 | recovered | 0.66 to 0.95 | 0.00 to 0.07 |
| 9 | alerts reported | 0.31 to 0.46 | 0.72 to 0.91 |
| 9 | contact follow-up | 0.49 to 0.64 | 0.55 to 0.91 |
| 9 | isolation, national | 0.44 to 0.80 | 1.00 to 1.00 |
| 9 | lab positivity | 0.21 to 0.35 | 0.00 to 0.31 |
| 9 | new confirmed deaths | 0.23 to 0.46 | 0.00 to 0.27 |
| 9 | new confirmed cases | 0.38 to 0.45 | 0.28 to 0.50 |
| 9 | isolation, Nord-Kivu | 0.81 to 1.00 | 1.00 to 1.00 |
| 9 | publication lag | 0.49 to 0.70 | 0.23 to 0.86 |
| 9 | samples analysed | 0.37 to 0.46 | 0.73 to 0.96 |

- Each metric's threshold questions are rungs of one ladder on one realised value. With these blocks the operational record of record holds 95 resolved pins (mean Brier 0.159 against 0.249 for the pooled base rate), and most of the new ones are correlated rungs; read the per-block figures beside the pooled one.

## Block 13 (registered 2026-11-12): the second test of the drift method

**Why this block.** Block 8's rule adopted the 28-day drift method as the provisional forecaster of record for cumulative series in the next block, and asked for a second prospective block that agrees before the adoption becomes permanent. Block 13 is that block. Block 9's incumbent stays the forecaster of record for fluctuating series, and Block 10's hypothesis was not falsified; neither registered rule asks for a repeat, so neither is carried.

**What is fixed by Block 8.** The four cumulative series (confirmed cases, confirmed deaths, recovered, affected health zones), their question wording and steps, the thresholds at the 0.1/0.3/0.5/0.7/0.9 quantiles of a 50/50 mixture of the two methods' end values, 20,000 paths per method with block length 5, the 31-day horizon from the substrate's last data day, and both methods. Only the roles are exchanged, as the rule requires: the drift method is the forecaster of record and the level bootstrap is priced beside it. `lovs/forecast/pins_block13.py` reuses Block 8's definitions from `lovs/forecast/pins_block8.py` and draws its own seeds, refusing any seed an earlier pin records.

**Substrate.** `data/operational-series-2026-11-07.json`, sha256 `b9a4be20c2d15df673cdacc5f0c214a8b4ea2d52a25e231d60462b89a8e66748`, through SitRep 177 (data 2026-11-07, published 2026-11-09). Every row of Block 8's substrate is unchanged in it, and its four cumulative series for SitReps 141 to 177 were checked against the reviewed promotions before generation, with no difference. SitRep 172 (data 2026-11-02) is absent from both. SitRep 178 (data 2026-11-08) was published on 2026-11-10, before the pin date, but is not in the extract. Its values (10,990 confirmed, 5,354 deaths, 2,951 recovered, 67 zones) lie below every threshold, so it decides no question.

**Window.** It opens 2026-11-13, the UTC day after the registration deadline (2026-11-12T23:59:59Z). End-of-window questions read the last SitRep data day on or before 2026-12-08.

**Registered prices** (probability that the value is at or below the threshold):

| Question | Drift method (record) | Level bootstrap (comparison) |
|---|---|---|
| confirmed cases at or below 12,650 | 0.0000 | 0.2197 |
| confirmed cases at or below 12,950 | 0.0115 | 0.5927 |
| confirmed cases at or below 13,050 | 0.3398 | 0.7188 |
| confirmed cases at or below 13,100 | 0.6651 | 0.7729 |
| confirmed cases at or below 13,200 | 0.9798 | 0.8567 |
| confirmed deaths at or below 6,125 | 0.1085 | 0.0713 |
| confirmed deaths at or below 6,325 | 0.1890 | 0.3830 |
| confirmed deaths at or below 6,425 | 0.4626 | 0.6154 |
| confirmed deaths at or below 6,475 | 0.6955 | 0.7121 |
| confirmed deaths at or below 6,725 | 0.8558 | 0.9536 |
| recovered at or below 3,450 | 0.0002 | 0.1530 |
| recovered at or below 3,550 | 0.3314 | 0.5483 |
| recovered at or below 3,600 | 0.7443 | 0.7681 |
| recovered at or below 3,650 | 0.9506 | 0.9214 |
| affected health zones at or below 69 | 0.4481 | 0.0158 |
| affected health zones at or below 70 | 0.7440 | 0.0500 |
| affected health zones at or below 71 | 0.9245 | 0.1147 |
| affected health zones at or below 74 | 1.0000 | 0.4386 |
| affected health zones at or below 79 | 1.0000 | 0.8358 |

At registration the series stood at 10,920 confirmed, 5,323 deaths, 2,940 recovered and 67 zones. As in Block 8, the methods part most on affected health zones: the drift method reads the recent plateau (65 to 67 zones from 2026-10-11 to 2026-11-07) and the level bootstrap extrapolates the spring's growth.

**A disclosed feature of the deaths series.** SitRep 169 (data 2026-10-30) harmonised Ituri's deaths retrospectively and raised cumulative confirmed deaths from 5,026 to 5,372; SitRep 170 returned to the earlier series (5,103). Both changes fall inside the drift method's 28-day window, and both methods read the series as published. Nothing is cleaned, because a cleaning rule chosen now would be authored with the outcome of Block 8 in view.

**How Block 13 will be read.**
- **Primary endpoint:** as in Block 8, the mean over metrics of each metric's mean threshold-Brier difference, with the sign Block 8 used: drift method minus level bootstrap.
- **Decision rule:** the drift method's adoption becomes permanent for cumulative series if the endpoint is below zero and the drift method is better on at least 3 of 4 metrics, the rule Block 8 met. Otherwise the level bootstrap returns as the forecaster of record for cumulative series in the next block, with the drift method priced beside it. Block 8's rule said permanence needs a block that agrees; this block also states, before any outcome, what follows when it does not.
  - A difference of exactly zero is not a win; with k scoreable metrics the requirement is ceil(3k/4), and below 3 scoreable metrics the block is inconclusive and the level bootstrap returns.
  - It is evaluated by `lovs/forecast_scoring.paired_block_decision(need=3, of_metrics=4)` on `paired_metric_differences`, passed the level bootstrap's probability first and the drift method's second.
  - Read the function's output by argument order, not by this block's roles: `adopt_challenger_provisionally` means the drift method's adoption becomes permanent, and `keep_incumbent` or `inconclusive` means the level bootstrap returns.
- **Void rule:** a question that a SitRep data day published on or before the registration receipt date already decides is void for both methods (`lovs/forecast/pins_block8.questions_decided_before`).
- **Reported descriptively:** the sign-flip p-value with its floor (0.125 for four metrics), PIT positions, pins priced at or below 0.20, Brier against the pooled base rate, and the Block 8 and Block 13 differences side by side.
- Each metric's questions are rungs of one ladder on one realised value; the unit of inference is the metric.

## Blocks 14 and 15 (registered 2026-11-18): Block 9's comparison again, and Block 10's questions

**Why these blocks.** Block 9 was the most informative operational block: 63 of its 88 pins were priced between 0.1 and 0.9, and its outcomes split 20 YES to 24 NO. Its registered rule kept the incumbent and asks for no repeat; Block 13 therefore carried only the cumulative series. Block 14 repeats Block 9's comparison on a later window by the founder's choice, as a second prospective test, so each fluctuating metric gets a second realised value. Block 15 carries Block 10's seven questions forward. Both are generated by `lovs/forecast/pins_block14.py`.

**What is fixed by Blocks 9 and 10.** Block 14 keeps Block 9's methods and roles (the level bootstrap is the incumbent, the 21-day levels method the challenger), its question wording apart from the window date and the basis statements below, its steps, the thresholds at the 0.1/0.3/0.5/0.7/0.9 quantiles of a 50/50 mixture of the two methods' end values, 20,000 paths per method with block length 5, and the 31-day horizon from the substrate's last data day. Block 15 keeps Block 10's rungs (0, 15, 30, 45 and 60 new confirmed cases), the termination bootstrap, the gap bootstrap over the last 60 days of inter-arrival gaps, and both feed questions. Both blocks draw their own seeds and refuse any seed an earlier pin records.

**Substrate.** `data/operational-series-2026-11-15.json`, sha256 `e74fbc5231ee0e52b9410ec011c2d41614adc3832c27ae202b91bd9e5051fb53`, 179 rows through SitRep 185 (data 2026-11-15, published 2026-11-17). It was written by `tools/extract_operational_series.py` from the evidence store's SitRep packets: SitReps through 184 at lovs-evidence-mcp commit 7b370f8, and the SitRep 185 packet (sha256 `a434351341d5da4222babc2687eaadc14fc533efd1a6c965fe4be1a898cbd723`), read from the evidence store's SitRep 185 release work before it was committed there. Every SitRep 178 to 185 value used here was checked against the reviewed promotions, so no value rests on that packet alone. The same packets with `--through 2026-11-07` rebuild Block 13's substrate byte for byte, and every row of that substrate is unchanged here. SitRep 172 (data 2026-11-02) is absent from both and is not filled. Before generation, the SitRep 178 to 185 values were checked against the reviewed promotions in `data/sitrep_promotions/`: 120 values over 15 fields (every series used here, the Nord-Kivu census in its care and movement tables, the cumulative series and the publication dates), with no difference.

**SitReps after the cutoff.** No SitRep for a data day before the window decides any question here: end-of-window questions read the last in-window data day, and the low-count and feed questions read only data days from 2026-11-19. SitReps 186 to 188 (data 2026-11-16 to 2026-11-18) therefore decide nothing, whether or not they are published before the registration receipt. They were not read for this design.

**Basis breaks, and how each question treats them.** Every question resolves on the value in this repository's reviewed record of the SitRep (`data/sitrep_promotions`), which the extract carries and the resolver reads. A value the review withholds as a source conflict does not count, and the question then reads the last edition in the window whose record carries one. The questions on lab positivity and Nord-Kivu isolation say so in their text, because a reader of the PDF could otherwise take a different number.
- *National hospital isolation is not carried.* The national care census (`hospital_isolation_total`) is withheld as a source conflict in every edition from SitRep 179 to 185, so its last observation is 2026-11-08. A question on it would resolve only if the census were printed again, and the resolver would otherwise score it unscoreable. No province replaces it: Nord-Kivu is already a metric, and the Ituri census is missing at SitReps 181, 183 and 184. The drop was decided on the withholding alone. It removes the series with the challenger's second-largest Block 9 loss (+0.2442), which the design session had seen, as the registration's designer-exposure statement discloses.
- *Samples analysed is not carried.* The national count of samples analysed (`samples_analyzed`) moves mostly with what it includes rather than with laboratory work. It adds follow-up re-samples at SitReps 179 to 182 (at least 137, then 62, 44 and 103), adds samples awaiting analysis at SitReps 183 and 184 (18 and 51, and at SitRep 184 Nord-Kivu's received count in place of its analysed count), and leaves out the 78 samples of Haut-Uele, Bas-Uele and Nord-Ubangi at SitRep 185. That swing, from about -78 to +137, is wider than the 125 separating its three central thresholds (450, 475 and 575), so its questions would resolve mostly on the count's composition. Lab positivity is computed on the same count, so carrying both would score one denominator twice. Its Block 9 difference was +0.0115, close to zero, so leaving it out favours neither method. An independent pre-registration review recommended the drop; the designer-exposure statement records what that review had read. Block 14 therefore has 7 metrics.
- *Nord-Kivu isolation resolves on the total census in the reviewed record.* The reviewed records show the printed Nord-Kivu census including patients cared for outside CTE/CT at SitReps 165 to 173 and 176. From SitRep 178 the page reports those patients separately: SitRep 179 calls its figure a CTE/CT census, SitReps 183 and 184 state that they are not included, and SitRep 185 includes them again (343 in CTE/CT and 171 outside, 514 in all). The derived series cannot keep one basis across SitRep 178, so the question reads the total census the reviewed record carries for Nord-Kivu and says so: when the table prints a total and a CTE/CT subtotal, the total counts, and a count printed separately is not added. At SitRep 185 that is 514, not the 343 in CTE/CT; the difference is wider than the whole ladder (490 to 670). At SitRep 176 the record carries 578 against 394 in CTE/CT, and at SitRep 182 it carries 520 where the same paragraph also prints 540. Both methods are priced on the series as the record carries it, breaks included.
- *Lab positivity resolves on the reviewed record's national rate.* National denominators are withheld at SitReps 176 to 178. From SitRep 179 the national sample count includes follow-up re-samples: at least 137, 62, 44 and 103 at SitReps 179 to 182, against 11 at SitRep 171 (SitReps 169 and 170 kept Nord-Kivu's re-samples out of the count). The printed positivity is therefore biased low against earlier editions, and from SitRep 183 the printed denominator can also include samples awaiting analysis or leave provinces out. No cleaned series is constructible from what is published, because the provincial re-sample counts are not printed in every edition. The positivity questions resolve on the published rate as the reviewed record carries it and name that basis in their text. The record and the cover can differ: at SitReps 176 to 178 the cover printed a national count and rate, and the review withheld both as source conflicts. A withheld value does not count, so the question then reads the last edition in the window whose record carries one.
- *Alerts.* The national total is the sum over the provinces that report that day. Haut-Uele is absent from the alert table at SitReps 181, 182 and 185, and the national totals leave it out; Nord-Ubangi is absent at SitRep 184. The question names the basis.
- *Publication lag counts to the first insp.cd post.* The lag runs from the data day to the UTC date the SitRep was first posted on insp.cd (`published_at` in the reviewed record), not to the publication date printed in the PDF. At SitReps 150 to 185 the two give different lags in 26 of 35 editions: by post, 1 day in 8, 2 days in 26 and 3 days in 1; by PDF date, 1 day in 34. Block 9's question did not say which date it meant; this one names it, and its scoring is unchanged.
- *Contact follow-up.* The national rate is over the five or six provinces that report contacts (SitReps 176 to 185), and the cohort changes between editions. The question is Block 9's, unchanged apart from its date; the pin rationale states the break.

**Block 15: why the low-count questions still mean something.** Block 10 was registered when the daily count of new confirmed cases had stayed between 40 and 99 for a month. In the month to 2026-11-15 it stayed between 40 and 97, so Block 10's rungs sit where they sat then, and the ladder asks Block 10's question again without change. Block 10 tested its hypothesis (the termination bootstrap overprices low days) once, and it was not falsified; this block is a second observation of it. The feed questions are Block 10's. As in Block 10, the 60-day reference window holds one two-day gap (here 2026-11-01 to 2026-11-03, where SitRep 172 is missing), so the gap bootstrap can draw a two-day silence and nothing longer.

**Window.** Both blocks open 2026-11-19, the UTC day after the registration deadline (2026-11-18T23:59:59Z), and read data days to 2026-12-16.

**Registered prices, Block 14** (probability that the value is at or below the threshold):

| Question | Level bootstrap (incumbent) | 21-day levels (challenger) |
|---|---|---|
| contact follow-up rate at or below 70 percent | 0.1653 | 0.0478 |
| contact follow-up rate at or below 81 percent | 0.3726 | 0.2398 |
| contact follow-up rate at or below 84 percent | 0.4482 | 0.6233 |
| contact follow-up rate at or below 86 percent | 0.5020 | 0.9083 |
| contact follow-up rate at or below 96 percent | 0.8041 | 1.0000 |
| lab positivity at or below 6 percent | 0.1922 | 0.0000 |
| lab positivity at or below 12 percent | 0.3604 | 0.2750 |
| lab positivity at or below 14 percent | 0.4190 | 0.6653 |
| lab positivity at or below 18 percent | 0.5311 | 0.8898 |
| lab positivity at or below 30 percent | 0.7993 | 1.0000 |
| alerts (national total of reporting provinces) at or below 1,800 | 0.1601 | 0.0000 |
| alerts (national total of reporting provinces) at or below 2,800 | 0.3795 | 0.2372 |
| alerts (national total of reporting provinces) at or below 3,700 | 0.6251 | 0.3810 |
| alerts (national total of reporting provinces) at or below 4,100 | 0.7262 | 0.6682 |
| alerts (national total of reporting provinces) at or below 4,700 | 0.8450 | 0.9538 |
| new confirmed cases at or below 25 | 0.2198 | 0.0000 |
| new confirmed cases at or below 55 | 0.4627 | 0.1893 |
| new confirmed cases at or below 65 | 0.5384 | 0.5676 |
| new confirmed cases at or below 75 | 0.6121 | 0.7137 |
| new confirmed cases at or below 105 | 0.7945 | 1.0000 |
| new confirmed deaths at or below 10 | 0.2112 | 0.0000 |
| new confirmed deaths at or below 25 | 0.4480 | 0.1494 |
| new confirmed deaths at or below 35 | 0.6008 | 0.6729 |
| new confirmed deaths at or below 40 | 0.6704 | 0.8109 |
| new confirmed deaths at or below 50 | 0.7816 | 1.0000 |
| publication lag (to first insp.cd post) at or below 1 day | 0.3626 | 0.2782 |
| publication lag (to first insp.cd post) at or below 2 days | 0.5703 | 1.0000 |
| publication lag (to first insp.cd post) at or below 4 days | 0.8471 | 1.0000 |
| Nord-Kivu isolation census (total) at or below 490 | 0.1484 | 0.0461 |
| Nord-Kivu isolation census (total) at or below 520 | 0.2317 | 0.3291 |
| Nord-Kivu isolation census (total) at or below 550 | 0.3409 | 0.6645 |
| Nord-Kivu isolation census (total) at or below 600 | 0.5458 | 0.9028 |
| Nord-Kivu isolation census (total) at or below 670 | 0.8030 | 1.0000 |

No question was dropped as degenerate. Publication lag has three questions rather than five, because its mixture quantiles snap to the same whole days (0.1 and 0.3 to 1 day, 0.5 and 0.7 to 2 days). 50 of the 66 pins are priced between 0.1 and 0.9. The challenger prices 11 of its 33 pins at exactly 0 or 1, the feature that cost it most in Block 9. At registration the series stood at 85.1 percent contact follow-up, 12.9 percent positivity on 435 samples, 2,755 alerts, 56 new confirmed cases, 25 new confirmed deaths, a publication lag of 2 days and 514 patients in the Nord-Kivu census.

**Registered prices, Block 15:**

| Question | Price (record) |
|---|---|
| Does any SitRep data day from 2026-11-19 to 2026-12-16 report zero new confirmed cases? | 0.3987 |
| Does any SitRep data day from 2026-11-19 to 2026-12-16 report 15 or fewer new confirmed cases? | 0.5474 |
| Does any SitRep data day from 2026-11-19 to 2026-12-16 report 30 or fewer new confirmed cases? | 0.7063 |
| Does any SitRep data day from 2026-11-19 to 2026-12-16 report 45 or fewer new confirmed cases? | 0.8407 |
| Does any SitRep data day from 2026-11-19 to 2026-12-16 report 60 or fewer new confirmed cases? | 0.9388 |
| From 2026-11-19 to 2026-12-16, does every pair of consecutive SitRep data days sit at most one day apart? | 0.6042 |
| Do at least 27 SitRep data days fall from 2026-11-19 to 2026-12-16? | 0.9156 |

**How Block 14 will be read.**
- **Primary endpoint:** as in Block 9, the mean over metrics of each metric's mean threshold-Brier difference, challenger minus incumbent.
- **Decision rule:** Block 9's rule, applied unchanged. Adopt the challenger as the provisional forecaster of record for fluctuating series in the next block if the endpoint is below zero and the challenger is better on at least ceil(6k/9) of the k scoreable metrics; otherwise keep the incumbent.
  - *Scaling.* Block 9 registered 9 metrics and required 6. Block 14 registers 7, so with all 7 scoreable the requirement is ceil(6 x 7 / 9) = 5 of 7, by Block 9's own scaling; with 6 scoreable it is 4. With fewer than 6 scoreable metrics the block is inconclusive and the incumbent stays. A difference of exactly zero is not a win.
  - It is evaluated by `lovs/forecast_scoring.paired_block_decision(need=6, of_metrics=9)` on `paired_metric_differences`, passed the incumbent's probability first and the challenger's second. The function's `of_metrics=9` is Block 9's registered count, which sets the scaling; the 7 differences passed are Block 14's metrics.
  - Block 9's registered text asks for no repeat after the incumbent stays, and says nothing else that applies to one. Adoption stays provisional, the displaced method keeps being priced beside it, and permanence needs a further prospective block that agrees. Block 9 kept the incumbent, so a challenger win here would leave the two blocks split.
- **Void rule:** no question here can be decided before its window, because each reads the last in-window data day of a fluctuating series (`questions_decided_before` finds none). A recorded registration receipt later than the deadline marks the blocks void.
- **Reported descriptively:** the sign-flip p-value with its floor (0.0156 for seven metrics), PIT positions, pins priced at or below 0.20, Brier against the pooled base rate, and the Block 9 and Block 14 differences side by side.

**How Block 15 will be read.** As Block 10 was: the prediction is falsified if the lowest new-confirmed count on any data day from 2026-11-19 to 2026-12-16 is at or below the bootstrap's median lowest day (10.67); the five rungs are one realised value, and the two feed questions one realised series of arrival days. The gap bootstrap cannot price the feed stopping.

- Each metric's questions are rungs of one ladder on one realised value; the unit of inference is the metric.
