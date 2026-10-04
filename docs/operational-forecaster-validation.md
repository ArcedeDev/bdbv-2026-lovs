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
