# Reporting-cadence integrity: method and findings

Companion to `lovs/forecast/cadenceintegrity.py`. Every figure below is printed
by that module; none is typed in by hand. Reproduce the whole document with:

```
python3 -m lovs.forecast.cadenceintegrity --as-of 2026-09-01
```

The as-of date is a required argument. The module never reads the system clock,
which is what makes the backtest in the staleness section possible at all.

## Why monitor the feed

Every operational number in this repository is a reading taken off one stream:
the INSP/INRB SitRep packets. `opsforecast.py` models what those indicators say.
Nothing models whether the indicators are still being said.

That is not an abstract gap. In 2026 this programme lost 58 days to exactly it:
`data/calibration-resolution-evidence.json` stopped being written on 2026-06-20,
Blocks 3 and 4 sat unresolved past their dates, and the entire test suite stayed
green because no assertion covered the liveness of an input.
`tests/test_calibration_feed_liveness.py` now guards that one feed. This module
guards the substrate feed underneath it, and it does so with the same apparatus
the forecasters use, because the reliability of the reporting stream is itself a
measurable, distributed and forecastable quantity, and nobody was measuring it.

## Substrate

`data/operational-series-2026-09-01.json`: the same frozen tidy extract the
operational forecaster runs on. 101 rows drawn from 101 SitRep packets covering
2026-05-14 to 2026-08-28, carrying `sitrep`, `published_at`, `data_as_of`,
`ready_for_model_use` and 20 indicator fields.

The monitor reasons over 100 delivered packets, not 101 rows: SitRep 006 appears
twice in the extract, and counting it twice would inflate every coverage
denominator by a packet that was delivered once.

## The three states, and why the module refuses to guess

An indicator that is not in front of you is in exactly one of three situations,
and they demand opposite responses:

1. **The SitRep was not published.** No packet exists. Nothing is wrong with us.
2. **The SitRep was published without this indicator.** The packet exists and
   other fields are populated. A surveillance function stopped reporting. This is
   the silent loss, and it is the dangerous one, because the series simply ends
   and every downstream chart keeps rendering.
3. **Our extract failed to capture it.** The publication carried it, the tidy
   extract does not. Fix the pipeline. Reading this as (2) manufactures a
   surveillance failure that never happened.

A frozen extract cannot always separate (2) from (3) by inspection, so the module
reports evidence rather than picking. Two derived signals carry most of the load.

**Co-stopping.** When several indicators go dark on the same packet and stay
dark, one upstream schema change explains all of them and several independent
extraction failures do not line up that neatly.

**The derived-field check.** Three of the published fields are ratios that are
also reconstructible from other fields in the same packet:
`alert_investigation_rate_percent` from `alerts_investigated / alerts_reported`,
`lab_positivity_percent` from `samples_positive / samples_analyzed`, and
`confirmed_cfr_percent` from `confirmed_deaths_total / confirmed_total`. A ratio
absent from a packet that carries both its inputs was reconstructible at
extraction time, so its absence can only be ours.

**Result on this extract: zero.** No ratio is ever missing from a packet that
carried both its inputs. That is the strongest available evidence that none of
the indicator losses below is an artefact of our extraction, and it is a computed
result rather than an assurance. Where neither signal fires, the module returns
`UNRESOLVED from this extract` and names what would settle it.

## Two clocks, deliberately not merged

The **SitRep number** is the delivery ledger. It is sequential, so a hole in it
is a missing delivery. The **data day** is the content clock, and per the
extract's own reading note every time-since estimator is clamped to it, because a
publication gap otherwise reads as observed improvement.

The two disagree in both directions in this record. Two SitReps sometimes share
one data day (2026-05-19 carries 004 and 005; 2026-08-10 carries 087 and 088), so
the numbering overstates coverage. And one data day is uncovered while the
numbering runs unbroken (2026-08-08), so the numbering understates loss. A
monitor watching either clock alone misses one of those.

A third clock governs what may be read at all. `rows_as_of` truncates on
`published_at`, not on `data_as_of`, because a packet describing 22 August was
published on 23 August: a reading taken on the 22nd that includes it is reading a
document that did not yet exist. Truncating on the data day would import one
publication lag's worth of the future into every backtest, which is the same
defect shape as the corridor benchmark's backfilled response state.

## 1. Delivery integrity

SitReps 001 to 106: **100 delivered of 106 numbered, 6 absent.**

| absent | between | calendar gap | verdict |
|---|---|---|---|
| 003 | 002 (2026-05-17) and 004 (2026-05-19) | 2d | not published |
| 029 | 028 (2026-06-11) and 030 (2026-06-13) | 2d | not published |
| 043 | 042 (2026-06-25) and 044 (2026-06-27) | 2d | not published |
| 045 | 044 (2026-06-27) and 046 (2026-06-29) | 2d | not published |
| 075, 076 | 074 (2026-07-27) and 077 (2026-07-30) | 3d | not published |

Every hole in the numbering is matched by exactly the right number of missing
data days, which is what earns the `not published` verdict rather than the
alternative, `number skipped, content intact`. Nothing in this record is a
numbering quirk: six absent numbers, six lost days of surveillance.

**Data days covered: 98 of the 107-day span** (2026-05-14 to 2026-08-28).
Uncovered: 2026-05-15, 05-16, 05-18, 06-12, 06-26, 06-28, 07-28, 07-29, 08-08.

Of those nine, three are uncovered while the numbering is intact: **2026-05-15,
2026-05-16 and 2026-08-08**. The first two are the outbreak's ramp-up, when the
feed was not yet daily (001 covers 05-14, 002 covers 05-17, and no number is
missing between them), so they are cadence, not loss. **2026-08-08 is the one
that matters**: the numbering runs unbroken across it and a day of surveillance
is gone anyway. A delivery monitor built on SitRep numbers alone would report a
clean feed for that week.

No packet was delivered empty. 18 were delivered carrying a single indicator,
all of them before the packet schema widened on 2026-06-04.

Extract anomalies: one duplicate row (006), no unparseable SitRep ids, no rows
flagged not ready for model use, no packet published before its own data day.

## 2. Publication lag

`published_at` minus `data_as_of`, over 100 delivered packets.

| | value |
|---|---|
| n | 100 |
| min | 0 |
| p50 | 1 |
| p90 | 2 |
| max | 4 |
| mean | 1.330 |

Histogram: 0d x1, 1d x70, 2d x26, 3d x1, 4d x2.

**The lag is drifting, and this is the one finding here that is an early warning
rather than a post-mortem.** Mean lag over the first fifty packets is 1.120 days;
over the second fifty it is 1.540. The difference of **+0.420 days** has a
two-sided permutation p of **0.0005** over 20,000 relabellings.

Drift is tested by permutation rather than by a regression slope on purpose. Lag
here is a small integer bounded below by zero and taking four distinct values; a
least-squares slope over that reports a direction with a standard error assuming
a continuum that does not exist. Shuffling the observed lags between the halves
assumes nothing and answers the question actually asked: could a split this
uneven come from a feed whose lag behaviour never changed? At p = 0.0005, no.

A feed under strain slows before it stops. Half a day is small. It is also the
only degradation signal in this document that is visible while the feed is still
delivering everything.

## 3. Indicator completeness

Coverage is measured **from each indicator's first appearance**, not from the
first packet. Most of these fields did not exist until the schema widened on
2026-06-04; charging them for packets that predate them would report twenty
surveillance gaps and bury the real ones underneath.

Trend is Fisher's exact test on the two halves of that window, two-sided (an
indicator that suddenly appears in every packet is as much a change in the feed
as one that vanishes), Holm-corrected across the 20 indicators tested at
alpha = 0.05. Exact rather than sampled, so the answer is a property of the table
and not of a seed.

| indicator | n | cov | since first | h1 | h2 | p | trend | status | last |
|---|---|---|---|---|---|---|---|---|---|
| confirmed_total | 100 | 1.00 | 1.00 | 1.00 | 1.00 | 1.0000 | stable | live | 2026-08-28 |
| confirmed_deaths_total | 81 | 0.81 | 1.00 | 1.00 | 1.00 | 1.0000 | stable | live | 2026-08-28 |
| confirmed_cfr_percent | 81 | 0.81 | 1.00 | 1.00 | 1.00 | 1.0000 | stable | live | 2026-08-28 |
| new_confirmed_today | 81 | 0.81 | 1.00 | 1.00 | 1.00 | 1.0000 | stable | live | 2026-08-28 |
| new_confirmed_deaths_today | 71 | 0.71 | 0.88 | 0.88 | 0.88 | 1.0000 | stable | live | 2026-08-28 |
| **new_suspects_today** | 53 | 0.53 | 0.65 | 0.88 | 0.44 | 0.0000 | **degrading** | **lost** | **2026-08-05** |
| health_zones_touched | 81 | 0.81 | 1.00 | 1.00 | 1.00 | 1.0000 | stable | live | 2026-08-28 |
| provinces_touched | 81 | 0.81 | 1.00 | 1.00 | 1.00 | 1.0000 | stable | live | 2026-08-28 |
| hospital_isolation_total | 81 | 0.81 | 1.00 | 1.00 | 1.00 | 1.0000 | stable | live | 2026-08-28 |
| cumulative_recovered | 81 | 0.81 | 1.00 | 1.00 | 1.00 | 1.0000 | stable | live | 2026-08-28 |
| **contact_followup_percent** | 82 | 0.82 | 0.86 | 0.72 | 1.00 | 0.0000 | **improving** | live | 2026-08-28 |
| samples_analyzed | 66 | 0.66 | 0.82 | 0.97 | 0.68 | 0.0007 | **degrading** | live | 2026-08-28 |
| samples_positive | 66 | 0.66 | 0.82 | 0.97 | 0.68 | 0.0007 | **degrading** | live | 2026-08-28 |
| lab_positivity_percent | 66 | 0.66 | 0.82 | 0.97 | 0.68 | 0.0007 | **degrading** | live | 2026-08-28 |
| lab_pending | 8 | 0.08 | 0.10 | 0.20 | 0.00 | 0.0053 | stable | **lost** | 2026-06-14 |
| alerts_reported | 79 | 0.79 | 0.98 | 1.00 | 0.95 | 0.4938 | stable | live | 2026-08-28 |
| **alerts_investigated** | 58 | 0.58 | 0.72 | 1.00 | 0.44 | 0.0000 | **degrading** | **lost** | **2026-08-05** |
| **alert_investigation_rate_percent** | 58 | 0.58 | 0.72 | 1.00 | 0.44 | 0.0000 | **degrading** | **lost** | **2026-08-05** |
| isolation_by_province | 77 | 0.77 | 0.95 | 1.00 | 0.90 | 0.1158 | stable | live | 2026-08-28 |
| province_split | 81 | 0.81 | 1.00 | 1.00 | 1.00 | 1.0000 | stable | live | 2026-08-28 |

The module rounds p to four places. The four rows printed as `0.0000` are, exactly:
`alerts_investigated` and `alert_investigation_rate_percent` at 3.049e-09,
`new_suspects_today` at 4.974e-05, `contact_followup_percent` at 4.031e-05.

Trend and status are separate axes and are meant to be read together. Trend says
what happened across the window; status says whether the indicator is arriving
now. The lab triad is `degrading` and `live` at once, which is exactly right and
is discussed below.

### The named case: `alert_investigation_rate_percent`. Confirmed.

The suspicion the monitor was asked to check holds, and the data says more than
the suspicion did.

`alert_investigation_rate_percent` last appears on data day **2026-08-05**
(SitRep 083) and has not appeared in the 23 packets since. Before that it had a
perfect record: **zero internal dark packets** across the 81 packets from its
first appearance on 2026-06-04. It did not decay. It stopped.

It did not stop alone. **`alerts_investigated` and `new_suspects_today` stopped
on the same packet**, and none of the three has returned. That is the co-stopping
signal: one schema change on SitRep 084 explains all three, and three independent
extraction failures landing on the same packet does not.

The attribution is firmer still for the ratio itself. `alerts_reported` is
**still live** through 2026-08-28 at 0.98 coverage, so alerts are still being
counted. What stopped is the *numerator*: how many of them were investigated.
Since `alerts_investigated` is absent from every later packet, the ratio cannot
be reconstructed from any of them, which rules out our extract and puts the loss
upstream. The module states this as `UPSTREAM: the inputs alerts_investigated
stopped arriving too`.

The operational reading is worse than a missing chart. Alert volume is still
published; the fraction of alerts anyone followed up is not. The one number that
distinguished "we are seeing a lot of alerts because there is a lot happening"
from "we are seeing a lot of alerts and investigating fewer of them" went dark on
2026-08-05, and nothing said so.

### Also degrading

**The lab triad** (`samples_analyzed`, `samples_positive`,
`lab_positivity_percent`, all p = 0.0007) fell from 0.97 coverage in the first
half of their window to 0.68 in the second. The cause is a single **11-packet
blackout from 2026-08-12 to 2026-08-22**. Run the module at `--as-of 2026-08-22`
and it reports the triad's previous worst internal dark run as **2 packets**,
which is the bar the blackout was breaking at the time; at `--as-of 2026-09-01`
that figure reads 11, because the blackout has closed and is now itself the worst
on record. They resumed on 2026-08-23 and are `live` at the as-of date.
This is the module's `INTERRUPTED` shape rather than its `LOST` shape, and the
difference matters: an indicator that has been this quiet before and come back is
not yet a loss. See the backtest below for what the monitor would have said while
it was dark.

**`lab_pending`** was populated 8 times between 2026-06-05 and 2026-06-14 and
never again, 71 packets ago. Its status is `lost`. Its *trend* is reported as
`stable` because Holm's correction across 20 indicators sets the bar at
0.05/13 = 0.0038 by the time it is reached and its raw p is 0.0053. That is the
correction working, not failing: eight observations in a ten-day window is not a
trend, and `lost` is the right verdict for it. The module reaches that verdict
through the dark-run rule, which does not depend on the significance test at all.

Its attribution is `UNRESOLVED from this extract`. Nothing co-stopped with it and
it is not reconstructible, so the extract cannot say whether INSP stopped
publishing pending-sample counts or our extractor stopped picking them up.
Settling it requires re-reading the source packets. The module says so rather
than picking the more interesting answer.

### Improving

**`contact_followup_percent`** is the one indicator moving the right way: 0.72
coverage in the first half of its window, **1.00** in the second, p = 0.0000. It
appeared once on 2026-05-21, went dark for 13 packets, and has been present in
every packet since 2026-06-04. Contact-tracing reporting became reliable and
stayed reliable. A one-sided degradation test would have called this stable and
missed it.

`alerts_reported` (p = 0.4938) and `isolation_by_province` (p = 0.1158) both have
mild coverage dips that do not clear the bar, and are correctly left alone.

## 4. Staleness clock

Severity is graded against what this feed has already done, not against an
authored threshold. GREEN is a silence inside the p90 of silences the feed
routinely produces; AMBER is longer than routine but precedented; RED is longer
than the feed has ever been quiet, which is the first moment at which "it is
probably fine" stops resting on anything. "Alert after 7 days" would have been an
opinion. This is a measurement.

**As of 2026-09-01:** last data day 2026-08-28 (4 days ago), last publication
2026-08-29 (3 days ago). Feed reference: p90 gap 1 day, worst gap 3 days. Four
days is **1.33x the worst silence on record**, so the feed reads **RED**. The
extract was frozen on 2026-09-01, so this is expected, and the monitor reports it
anyway: from inside the extract, four days of silence and a failed capture look
identical, and the module is not entitled to assume which.

The reading separates alarms an indicator owns from alarms it inherits. When the
whole extract is four days old, every field in it is four days stale; reporting
twenty alarms for one stale extract would bury the ones that matter.

**Indicator-specific alarms (4). Refreshing the feed will not clear these:**

| indicator | last | days | behind the feed | multiple of its own worst gap |
|---|---|---|---|---|
| lab_pending | 2026-06-14 | 79 | 75 | 39.5x (own worst 2d) |
| alerts_investigated | 2026-08-05 | 27 | 23 | 9.0x (own worst 3d) |
| alert_investigation_rate_percent | 2026-08-05 | 27 | 23 | 9.0x (own worst 3d) |
| new_suspects_today | 2026-08-05 | 27 | 23 | 4.5x (own worst 6d) |

The remaining 16 indicators are 0 days behind the feed: as fresh as the extract
permits.

### Worked backtest: what the monitor would have said on 2026-08-22

Because the as-of date is a parameter, the monitor can be run at any past date
against the view that was publishable then:

```
python3 -m lovs.forecast.cadenceintegrity --as-of 2026-08-22
```

On 2026-08-22, mid-blackout, the feed itself read AMBER (2 days since the last
data day, worst on record 3) and **seven** indicators carried their own alarms,
including the lab triad at **11 days, 3.67x their own worst gap of 3 days** (9
packets dark, against a previous worst of 2). The
monitor would have flagged the lab blackout while it was happening, from a
severity bar it derived from the lab series itself. On 2026-09-01, after the
triad resumed, the same run correctly drops them back to feed-owned.

That is the whole argument for taking the date as a parameter. A monitor that
read `date.today()` could not have been shown to fire, and a liveness monitor
that has never been shown to fire is a comment.

## 5. Continuity forecast

**Question.** Will the next 30 days sustain the recent delivery cadence?

**Method.** Stationary block bootstrap over the inter-arrival gaps between data
days in a 60-day reference window: 56 gaps, 20,000 paths, 5-day blocks, seed
20260901. Blocks rather than single gaps because reporting stress is
autocorrelated. A week in which the response is overwhelmed produces several slow
days in a row, and resampling gaps independently would price a sustained slowdown
as the product of independent unlikely days.

`opsforecast`'s bootstrap is not reused directly. It walks a level forward from
first differences; an inter-arrival gap is the quantity itself rather than a
difference of one, and cumulating gaps into a fixed horizon is a different walk.
The resampling scheme, block length and seeding discipline are the same.

**Sustaining is two conditions, both benchmarked on the most recent 30 days**,
which is what "the recent cadence" means: at least as many arrivals, and no
silence longer than the worst one in that window. Both benchmarks are measured,
not chosen. The reference window the bootstrap draws from is deliberately wider
than the benchmark window so that it contains slow stretches the recent window
does not, otherwise the question would be scored against its own answer. A
trailing silence still open when the horizon ends counts only at its observed
length, because that is all anyone would have seen.

**Benchmark from the last 30 days: 29 arrivals, worst silence 2 days.**

| | probability |
|---|---|
| **P(sustains both)** | **0.4874** |
| P(no silence longer than 2d) | 0.5713 |
| P(at least 29 arrivals) | 0.4874 |

Simulated arrivals: mean 28.36, p05 26, p50 28, p95 30. Simulated worst silence:
p50 2d, p95 3d.

The two components are not independent here, and the arithmetic shows it: 29
arrivals in 30 days leaves at most one day of slack, so any path that clears the
volume bar cannot contain a 3-day silence. `P(both)` equals `P(volume)` exactly
for that reason, not by coincidence.

**The headline number is a coin flip, and that is informative rather than
disappointing.** The last 30 days were the feed's near-best month: 29 arrivals in
30 days with no gap over 2. Asking a stationary bootstrap whether the next month
will match a near-best month is close to asking whether a median draw exceeds a
high draw. The honest reading is not "the feed is at risk", it is **the current
cadence is at the top of this feed's range and there is no headroom left in it**.
Any degradation shows up immediately.

### Limits, and they are structural rather than decorative

**A gap bootstrap can never draw a gap longer than the longest gap it resamples.**
The worst silence this method can produce is 3 days, the worst in the reference
window, which is also the worst in the entire record. It prices variation inside
a regime that never broke. It cannot price the regime breaking. That is not a
hypothetical failure mode for this programme: an input feed stopping unannounced
is the exact failure that cost it 58 days, and the number above would have been
high right up to the morning it happened.

**The record cannot bound that tail.** The extract spans 3 non-overlapping 30-day
windows and contains **0** windows with a silence longer than anything else in
the record. The rule of three puts the 95% upper bound on the probability of such
a stall in a 30-day window at **1.0**. That is not a formatting error. Three
windows and zero events bounds nothing at all, and reporting 0.4874 without it
would be the more misleading number.

**One empirical check does exist, and it disagrees mildly.** 2 of the 3
non-overlapping 30-day windows in the record contained a silence longer than the
2-day benchmark, against a bootstrap estimate of 1 - 0.5713 = 0.4287. Three
windows is not a rate and the disagreement is well inside what three draws can
produce, but the direction is worth recording: the bootstrap is, if anything,
optimistic about silence.

**The benchmark can go degenerate.** When the worst silence in the recent window
equals the worst in the reference window, `P(no longer silence)` is 1.0 by
construction, because the bootstrap cannot exceed its own maximum. This happened
in the 2026-08-22 backtest, where the recent window contained the 3-day gap of
2026-07-28/29 and the silence component read exactly 1.0. Read that component as
uninformative rather than as reassurance whenever
`recent_worst_silence_days` equals `bootstrap_max_possible_silence_days`; the
module prints both so the check takes one glance.

## What this module cannot support

- **Attribution where the extract is silent.** Where nothing co-stopped and
  nothing is reconstructible, the module says `UNRESOLVED` and names the fix
  (re-read the source packets). `lab_pending` is the live instance. Treating an
  unresolved absence as a surveillance loss is the mirror of the error this
  module exists to prevent.
- **Anything upstream of the extract.** If the extraction pipeline stops running
  entirely, this module reports a stale feed and cannot tell you whether the
  outbreak response stopped publishing or we stopped reading. That distinction
  needs a liveness assertion on the extractor, not on the extract.
- **A forecast across a structural break.** See the limits above.
- **A judgement about the outbreak.** Nothing here is an epidemiological finding.
  A degraded reporting stream is a statement about surveillance, not about
  transmission. Reading the loss of `alert_investigation_rate_percent` as
  improved alert handling would be exactly the analysis-clock error the extract's
  own reading note warns about.

## Reading order for an operator

1. Section 3 first. Any RED that is **indicator-owned** will not clear itself,
   whoever refreshes the feed.
2. Section 1 next, for whether a delivery is missing or only a number.
3. Section 2's drift, which is the only signal that moves before delivery does.
4. Section 4 last, and never without its upper bound.
