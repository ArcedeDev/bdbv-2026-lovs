# Outbreak termination: method and limits

Companion to `lovs/forecast/termination.py`. Written against the frozen extract
`data/operational-series-2026-09-01.json`, as of 2026-09-01, before anything
here resolved.

Every number below is produced by the module. `tests/test_termination.py`
re-runs it and asserts that each generated figure appears in this file, so a
hand-typed number fails the suite rather than sitting here unchallenged.

Reproduce the whole document:

```
python3 -m lovs.forecast.termination --as-of 2026-09-01 --figures
```

## The question, and why it is usually ducked

WHO declares an Ebola outbreak over 42 days after the second negative test or
the safe burial of the last confirmed case. Forty-two days is two maximum
incubation periods. It is the question every principal actually asks, and almost
nobody will forecast it, because the resolution rule is a bright line and being
wrong against a bright line is embarrassing in a way a wrong nowcast is not.

The rule is live, not hypothetical. The Uganda arm of this same outbreak was
declared over on 2026-08-26. Whatever this forecaster says about the DRC arm
will be scored the same way.

## The countdown mechanic, and the trap inside it

The clock is a run of days with no confirmed case, so everything turns on what
counts as a zero-case day.

A SitRep that reports zero confirmed cases is a zero-case day. **A day with no
SitRep is not.** It is an unobserved day. Crediting it to a countdown invents
evidence of precisely the thing the countdown exists to establish. The failure
is not theoretical here: the reading note carried by the series extract itself
says that a reporting gap otherwise reads as observed improvement, which is a
lesson this programme learned on a live burden figure rather than in review.

The module therefore enforces two rules, and the tests enforce the module:

1. **Clamp to the last data day.** Elapsed countdown is measured to the last day
   the series carries data, never to the as-of date, never to a publication
   date, and never to a system clock. The as-of date is always a parameter.
2. **A clock starts only on a reported zero.** If the last observed day reported
   cases, no clock is running, however long the silence after it runs.

`test_a_trailing_reporting_gap_does_not_start_a_countdown` and
`test_packets_that_omit_the_indicator_are_not_zero_case_days` hold both rules
against the two shapes the trap takes in the wild: a missing packet, and a
published packet that omits the indicator.

## Where the countdown stands

| | |
|---|---|
| as-of date (a parameter, not a clock) | 2026-09-01 |
| last day with data | 2026-08-28 |
| unreported days after it | 4 |
| last day that reported confirmed cases | 2026-08-28, at 82 |
| observed zero-case days | 0 |
| 42-day clock | not running |

No countdown has begun. The last observed day is also the last day with cases,
so the four unreported days between the data and the as-of date are exactly the
days a naive implementation would have converted into four days of progress.
They are not credited.

## The forecast

Three nested events over each horizon, measured from the last data day
(2026-08-28), not from the as-of date, for the same reason everything else is:
the model may only be anchored to observed data.

- **(a) first zero-case day.** Any single day recording no confirmed case.
- **(b) clock standing.** A countdown running uninterrupted at the horizon, or
  already completed. Deliberately not read as "any zero day starts a clock",
  which would make (b) identical to (a) and therefore worthless. A single zero
  day mid-outbreak is noise.
- **(c) declared over.** A completed 42-day clock inside the horizon: the start
  day, the 42 days that elapse, and the declaration day itself all case-free.

(c) implies (b) implies (a) by construction, and
`test_nested_probabilities_are_internally_consistent` asserts it on every
horizon and both variants.

The zero boundary is a modelling choice, so both choices are reported on the
same seeded draws. `reflecting` is the plain difference bootstrap: a path that
touches zero bounces straight back, which makes sustained termination nearly
impossible by construction. `absorbing` freezes a path at its first zero, which
makes resurgence impossible by construction. Neither is true. This series
contains no zero days at all, so a persistence at zero cannot be estimated from
it, and inventing one would be authoring a number. The pair brackets the answer:
reflecting is a floor, absorbing a ceiling.

### 30 days, to 2026-09-27

| event | reflecting (floor) | absorbing (ceiling) |
|---|---|---|
| (a) first zero-case day | 0.1371 | 0.1371 |
| (b) clock standing | 0.0086 | 0.1371 |
| (c) declared over | 0.0000 | 0.0000 |

Both (c) figures are structural zeros, not forecasts. A declaration needs 43
consecutive case-free days and this horizon offers 30. No trajectory produces
one. Median daily cases at the horizon: 93.0.

### 60 days, to 2026-10-27

| event | reflecting (floor) | absorbing (ceiling) |
|---|---|---|
| (a) first zero-case day | 0.2695 | 0.2695 |
| (b) clock standing | 0.0095 | 0.2695 |
| (c) declared over | 0.0000 | 0.0671 |

Median daily cases at the horizon: 106.2.

### 90 days, to 2026-11-26

| event | reflecting (floor) | absorbing (ceiling) |
|---|---|---|
| (a) first zero-case day | 0.3404 | 0.3404 |
| (b) clock standing | 0.0080 | 0.3404 |
| (c) declared over | 0.0000 | 0.2337 |

Median daily cases at the horizon: 122.5.

### Reading the table

The reflecting column says termination is essentially off the table inside 90
days: a bootstrap with no absorbing state at zero cannot hold 43 consecutive
zeros, so its (c) is 0.0000 at every horizon. That is a property of the boundary,
not a finding, and it is why the ceiling is reported beside it.

The honest span for "declared over within 90 days" is therefore 0.0000 to
0.2337, and the ceiling assumes the most termination-favourable behaviour
available: that the epidemic never resurges once it records a single zero day.

Under the reflecting boundary (b) does not increase with the horizon
(0.0086, 0.0095, 0.0080). That is correct rather than a bug. A clock standing at
day 30 can be broken by day 60, and (b) asks about the state at the horizon, not
about anything that ever happened before it.

## The calibration caveat, which travels with the numbers

**Most figures above are small, and small is the right answer at 82 confirmed
cases a day. But this bootstrap is measurably overconfident at exactly that end
of the range.** From the walk-forward backtest in
`operational-forecaster-validation.md`: 81 forecasts priced 0.05 occurred 23% of
the time, and 19 forecasts priced 0.15 occurred 42% of the time.

Every probability at or below 0.15 above should be read as a **floor on the true
probability, not an estimate of it.** That covers all three reflecting (b)
figures, the 30-day (a) figures, and the 60-day absorbing (c) figure.

Nothing has been adjusted to compensate. The module emits the raw fraction of
simulated paths and attaches the measured bias as a caveat on the number itself,
which is the same discipline the operational pin block applies to its low-band
bias tests. Quietly shrinking or inflating a number to match a known bias hides
the bias instead of measuring it, and makes the next resolution uninterpretable.
`test_the_caveat_does_not_alter_the_number` recounts the seeded draws by hand and
asserts the emitted figure is the raw count.

The band is derived from the backtest table rather than asserted: a bin counts as
materially miscalibrated when the event occurred at least twice as often as it
was priced, which puts the ceiling at 0.15.

## Decay phase: is the count even falling

> The sign of the trend flips with the window. The fastest declining window halves in 32.1 days, faster than the 50.6 days termination inside 365 days requires, but other windows over the same series are still growing, so that decline is not established as the phase.

Log-linear and linear fits on `new_confirmed_today`, by trailing window:

| window | linear slope, cases/day | log slope, per day | doubling | halving | days to <1 case/day | implied declaration |
|---|---|---|---|---|---|---|
| 14 days | -1.7385 | -0.02159 | | 32.1 d | 204.1 | 2027-05-03 |
| 28 days | -0.2676 | -0.00329 | | 210.5 d | 1338.5 | 2030-06-10 |
| 56 days | 0.5724 | 0.00952 | 72.8 d | | never | |
| full series | 0.6927 | 0.01273 | 54.5 d | | never | |

The last fortnight is falling fast. The last two months are still growing. Both
are fits to the same series, and the fortnight is 14 points of a noisy daily
count, so it is a candidate turn, not a decay phase. Reporting the fortnight
alone would be a two-point diff with extra steps.

### The arithmetic termination has to satisfy

To be declared over within 365 days of the last data day, the last confirmed
case has to fall on or before day 322 of that year: 365 days, minus the 42 the
clock takes, minus the declaration day itself.

Getting from 82 cases a day to below one case a day inside 322 days needs a
sustained log-linear decline of 0.01369 per day, which is a **halving time of
50.6 days, held for ten months.**

Against that requirement:

- The 14-day window's 32.1 day halving clears it. Held from the last data day,
  it reaches below one case a day in 204.1 days and supports a declaration
  around 2027-05-03.
- The 28-day window's 210.5 day halving does not, by a factor of four. Held, it
  supports a declaration around 2030-06-10.
- The 56-day and full-series windows are the wrong sign entirely. Held, they
  never terminate.

So the requirement is not extreme. A halving time of 50 days is an ordinary
outbreak-control trajectory, and the most recent fortnight is already faster than
it. What is missing is not speed, it is evidence that the speed is the phase
rather than the fortnight.

## The earliest date termination is even arithmetically possible

**2026-10-10**, which is 43 days after the last data day.

That is the floor, and it is not reachable in practice. It assumes the very next
day after the last data day, 2026-08-29, was the first case-free day, and that
every day since has been case-free too. The four unreported days at the end of
the series cannot rule that out, which is exactly why it is a floor and not a
forecast. It also assumes a clock-start lag of zero, whereas WHO counts from the
second negative test or the safe burial of the last case, both of which fall at
or after the day that case was last counted. Any real declaration lands later.

## What this cannot support

The bootstrap encodes only how this series has moved. A structural break sits
outside it in both directions: a vaccination campaign reaching saturation, a
security incident closing a province, a change in case definition, a laboratory
outage that suppresses confirmations without suppressing cases.

That last one deserves naming. This forecaster reads termination off
**confirmed** cases, so anything that stops confirmations produces a zero-case
day that is a surveillance artifact rather than an epidemiological event. A
countdown resting on such days would be false in exactly the way the reporting
gap rule is designed to catch, and the module flags unobserved days inside a
running clock for that reason. It cannot flag a day that reported a zero it
should not have.

Two further limits, both structural:

- **The clock start is proxied.** WHO counts from the second negative test or
  the safe burial of the last case; the module counts from the day after that
  case was last recorded, with the lag exposed as a parameter and defaulted to
  zero. Every date here is therefore an earliest-possible date, and every (c)
  probability an upper bound within its variant.
- **The bracket is wide on purpose.** Where the reflecting floor and the
  absorbing ceiling disagree by 0.2337, the disagreement is the honest answer.
  Narrowing it needs data this outbreak has not yet produced: zero-case days, and
  a record of what followed them.
