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
