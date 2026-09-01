# CFR ascertainment instrument: method and limits

Companion to `lovs/forecast/cfrascertainment.py`. Every figure in this document
is printed by `python3 -m lovs.forecast.cfrascertainment`; none was typed by
hand. Written while the question is open, so nothing here is chosen with
hindsight.

## Why read CFR as ascertainment

The operational forecaster rejected `confirmed_cfr_percent` as a bootstrap
target and said why: the bootstrap priced "ever exceeds 50 percent" at 1.000 by
extrapolating drift in a ratio whose denominator is growing, with no way to know
that the drift decays. That rejection was right and it left a real question
unasked.

Within one outbreak, one pathogen and one national response, the province split
puts Nord-Kivu 23.2 points of CFR above Ituri. Independently of anything in this
module, the LOVS zone-capacity diagnostic flags Nord-Kivu
`laboratory_confirmation` as severity=critical with gap=true and
gapBasis=no_evidenced_delivery. That flag is an input here, cited, not derived.

Those two facts have a joint explanation that is not biological. Where
confirmation is scarce, the cases that get confirmed are the ones whose illness
forces the system to look, and dying is the loudest way to force it. A province
that can only confirm its deaths reports a CFR approaching 100 percent whatever
its true CFR is. On that reading a high CFR does not mark a province where the
disease is worse. It marks a province whose mild and moderate cases are
invisible, and the CFR is a detector pointed at the surveillance system rather
than a measure pointed at the virus.

This document does not settle that reading. It sizes it, states what would
falsify it, and names the observation nobody has yet made.

## Substrate

`data/operational-series-2026-09-01.json`, the same frozen extract of 101
INSP/INRB SitRep packets the operational forecaster uses. 81 packets carry a
`province_split`; after de-duplicating one repeated data day (2026-08-10 appears
in two SitReps) that is **80 province data days, 2026-06-04 to 2026-08-28**.

The last data day is 2026-08-28, published 2026-08-29. A figure quoted against
the publication date will not match the figures below, and should not be made to.

Six provinces appear: Ituri, Nord-Kivu, Haut-Uele, Tshopo, Bas-Uele, Sud-Kivu.

### The split does not always agree with the national line

76 of 80 days reconcile with `confirmed_total` and `confirmed_deaths_total`
within a tolerance of 2. The distribution of the disagreement is published so
the tolerance is auditable rather than inherited:

| max abs. gap on the day | days |
|---|---|
| 0 | 74 |
| 1 | 2 |
| 67 | 1 |
| 156 | 1 |
| 328 | 1 |
| 396 | 1 |

The two one-unit days are reclassification noise. The four large ones are a
single contiguous event: **2026-08-06, 08-07, 08-09 and 08-10**, when the
province split stopped moving while the national line kept climbing, then caught
up. A tolerance of 2 admits the former and rejects the latter.

Those days are flagged and kept, not dropped. The module refuses to compute a
rate whose *endpoint* falls inside the stall, but allows a window that spans it
between two clean endpoints, because a difference of cumulative counts does not
care that the counter paused and caught up in between. That distinction refuses
8 of the 66 candidate interval windows, a cost the module prints rather than
absorbing into a shorter series.

## 1. Per-province CFR, with the denominator made explicit

Cumulative CFR at 2026-08-28, Wilson 95 percent intervals. National CFR that day
is 48.1 percent.

| province | confirmed | deaths | CFR | 95% CI | CI width | scoreable |
|---|---|---|---|---|---|---|
| Ituri | 4911 | 2212 | 45.0% | 43.7 - 46.4 | 2.8 pp | yes |
| Nord-Kivu | 800 | 546 | **68.2%** | 64.9 - 71.4 | 6.4 pp | yes |
| Haut-Uele | 209 | 93 | 44.5% | 37.9 - 51.3 | 13.4 pp | yes |
| Tshopo | 19 | 8 | 42.1% | 23.1 - 63.7 | 40.6 pp | **no** |
| Bas-Uele | 3 | 2 | 66.7% | 20.8 - 93.9 | 73.1 pp | **no** |
| Sud-Kivu | 3 | 1 | 33.3% | 6.1 - 79.2 | 73.1 pp | **no** |

The gate is stated in the terms of the question being asked, not as a
conventional minimum cell size: the gap under investigation is roughly twenty
points, so a province whose own CFR cannot be placed inside a band narrower than
thirty points cannot testify about it. Sud-Kivu's 33.3 percent is not a low CFR.
It is one death in three cases, and its interval covers almost the whole range.
Those three provinces are listed and marked, never ranked and never dropped, on
the principle that a hidden exclusion is worse than a visible one.

That leaves three provinces that can speak. Two of them, Ituri at 45.0 and
Haut-Uele at 44.5, are indistinguishable. **Nord-Kivu is the single outlier**,
which is what makes the comparison worth running at all.

## 2. Is the gap widening, holding, or converging?

Two different answers, and the difference between them is the finding.

### Cumulative basis: converging

| | |
|---|---|
| days compared | 80 |
| first (2026-06-04) | +44.4 pp |
| last (2026-08-28) | +23.2 pp |
| observed range | +20.3 to +53.5 pp |
| trend, full window | -6.73 pp / 30d |
| trend, recent 30 obs | -2.85 pp / 30d |
| endpoint to endpoint | -7.49 pp / 30d |
| **verdict** | **converging** |

A direction is declared only when all three trend measures agree in sign and the
full-window trend clears a 2 pp / 30d hold band. Here they agree. No p-value is
offered and none should be: a cumulative CFR is a running ratio and a rolling
interval gap reuses most of its data between consecutive points, so both series
are autocorrelated by construction and any nominal standard error would be a
confident-looking lie.

But a closing gap is not the same event as the outlier coming down. Decomposing
the 21.2 point change:

| | 2026-06-04 | 2026-08-28 | contribution to the gap |
|---|---|---|---|
| Nord-Kivu | 60.0% | 68.2% | **+8.2 pp** (widening) |
| Ituri | 15.6% | 45.0% | **-29.5 pp** (closing) |

**The gap is closing because Ituri's CFR nearly tripled, not because Nord-Kivu's
fell. Nord-Kivu's rose.** Most of Ituri's rise is not a change in the disease; it
is right censoring resolving. Cumulative CFR in a growing epidemic is biased
downward because recently confirmed cases have not yet had time to die, and that
bias shrinks as a cohort ages. Two provinces at different epidemic ages differ in
cumulative CFR for reasons that have nothing to do with either ascertainment or
severity.

### Interval basis: not resolved by this window

Deaths added over cases added within a 14-observation window. This does not
remove the censoring bias, but it stops each cohort's whole history from
dominating the comparison.

Interval CFR at 2026-08-28 (window 2026-08-14 to 2026-08-28):

| province | new confirmed | new deaths | interval CFR |
|---|---|---|---|
| Ituri | +806 | +421 | 52.2% |
| Nord-Kivu | +212 | +135 | 63.7% |
| Haut-Uele | +74 | +31 | 41.9% |
| Sud-Kivu | +0 | +0 | no rate: a reclassification, not a rate |

| | |
|---|---|
| days compared | 58 |
| first (2026-06-19) | +25.6 pp |
| last (2026-08-28) | +11.4 pp |
| observed range | +5.5 to +31.8 pp |
| trend, full window | +1.48 pp / 30d |
| trend, recent 30 obs | -1.02 pp / 30d |
| endpoint to endpoint | -6.05 pp / 30d |
| **verdict** | **holding: the trend measures do not agree in sign** |

### The finding

The headline 23.2 point gap is roughly half epidemic-age artifact. On the
maturation-free measure it is **11.4 points, 49 percent of the headline number**,
and on that measure eighty days of data do not establish that it is going
anywhere. Anyone reading the cumulative series alone would report that Nord-Kivu
is converging on the national picture. It is not. Its own CFR rose eight points
over the window; the appearance of convergence belongs to Ituri.

## 3. Implied unconfirmed: a conditional, never a count

The estimator asks one question: if a province's true CFR were the reference
band, how many cases would its observed deaths imply? The arithmetic is one
division and its entire content is the assumption, so the assumption travels
with every number.

The reference band is derived from the reference province's own estimates, not
picked. At 2026-08-28 the estimators available for Ituri are its cumulative CFR
(45.0 percent, biased low by censoring) and its interval CFR at 7, 14 and 28
observations (the 21-observation window is refused because its start endpoint
falls inside the reporting stall). Their spread is the band, so the output range
inherits the estimator disagreement instead of hiding it behind one choice.

**Reference band: 45.0 - 52.2 percent, from Ituri at 2026-08-28.**

> IF Nord-Kivu's true CFR were 45.0 - 52.2 percent, the band Ituri itself
> occupies, THEN its 546 confirmed deaths to 2026-08-28 would imply
> **1045 - 1212 cases**, against 800 confirmed.

| | |
|---|---|
| implied cases | 1045 - 1212 |
| observed confirmed | 800 |
| **implied unconfirmed** | **245 - 412** |
| implied confirmed share | 66.0% - 76.6% |

The reference is a required argument with no default. A default would let the
assumption be inherited by a caller who never considered it, and the assumption
is the whole result.

Five things this number is not:

1. **It is not an observation.** No unconfirmed case is counted here and none may
   be reported as counted. It is what a stated assumption implies.
2. **No incidence, growth rate or projection may be derived from it.** The
   instrument speaks to ascertainment only. A larger implied denominator is not
   evidence of faster transmission, and this repository has been wrong that way
   before.
3. **Deaths are treated as observed.** That assumes Nord-Kivu's death
   ascertainment is better than its case ascertainment, which is the same
   asymmetry under test. The instrument cannot verify it.
4. **Ascertainment is a candidate, not the residual.** Case mix, treatment
   access and time to care all move a CFR. Attributing the whole gap to
   ascertainment because it is the explanation being tested would be the error
   this document exists to avoid.
5. **Both sides of the ratio are censored.** Cumulative deaths lag cumulative
   cases, so the implied total understates what the already-confirmed cohort will
   eventually record. The range is conservative at both ends and is not a bound.

A reference CFR at or above the province's observed CFR implies no missing
confirmations at all. The module reports that as zero with the reason attached,
never as a negative shortfall.

## 4. What would tell the two readings apart

Both readings explain the current table. They separate on what they predict.

| | ascertainment reading | genuine severity reading |
|---|---|---|
| what Nord-Kivu confirms | a death-enriched subset; mild and moderate cases never confirmed | the same spectrum Ituri confirms |
| clinical load not confirmed | arrives anyway, and is isolated on suspicion | none; what arrives is confirmed |
| isolation census per recent confirmation | **above** Ituri's | comparable to Ituri's |
| after confirmation capacity is delivered | confirmed cases step up, deaths continue on trend, interval CFR falls toward the reference band | interval CFR holds; confirmed cases rise no faster than trend |

### The observable available today

Isolation census against confirmations of the preceding 14 observations. This is
a genuinely independent test: it counts beds, not deaths, so it cannot be a
restatement of the CFR arithmetic.

| province | in isolation | confirmed in window | isolated per recent confirmation |
|---|---|---|---|
| Ituri | 552 | 806 | 0.685 |
| Nord-Kivu | 236 | 212 | **1.113** |
| Haut-Uele | 70 | 74 | 0.946 |

Tshopo, Bas-Uele and Sud-Kivu are not scored: below 30 recent confirmations a
single case moves the ratio by more than three percent, the same order as the
effect being looked for.

**Nord-Kivu holds 1.63 times the isolation load per recent confirmation that
Ituri does.** That is the direction the ascertainment reading predicts, and it is
the direction the severity reading does not. It is evidence, and it is weak
evidence, for three reasons stated with it: isolation occupancy is a stock
governed by bed supply and length of stay, so a province with more beds isolates
more for reasons unrelated to ascertainment; the census counts suspects as well
as confirmed cases, which is exactly what makes it informative here and exactly
what makes it soft; and one day's ratio is not a series.

It points. It does not settle.

### The observation that would settle it

**Primary.** The interval CFR of the Nord-Kivu cohort confirmed *after* a dated,
evidenced delivery of laboratory confirmation capacity, read against Nord-Kivu's
own death series over the same window. The ascertainment reading predicts
confirmed cases step up while deaths continue on their existing trend, so the
interval CFR falls toward the 45.0 - 52.2 band. The severity reading predicts the
interval CFR holds near 63.7 percent. The `no_evidenced_delivery` flag is what
currently prevents this test from being run, which is the sharpest available
statement of what that flag costs.

**Cheaper and available without waiting.** The Nord-Kivu line list: the share of
confirmations taken at or after death, and the onset-to-confirmation
distribution, both against Ituri's. The ascertainment reading requires
Nord-Kivu's post-mortem confirmation share to be materially higher and its
onset-to-confirmation interval materially longer. If Nord-Kivu confirms its cases
as early as Ituri does and buries as few of them unconfirmed, the ascertainment
reading is dead and the gap is about the disease or about care, not about the
laboratory.

Neither field is in this extract. That absence is why this instrument sizes the
question instead of closing it.

## What this method cannot support

- Any statement of how many people are infected or have died.
- Any incidence, growth or projection claim, in any province, from any figure
  here.
- Any ranking of the three thin provinces. Tshopo, Bas-Uele and Sud-Kivu produce
  CFR numbers and the module refuses to score them.
- Any claim about a province on a day whose split does not reconcile.
- Any attribution of the full gap to ascertainment. The instrument shows the gap
  is consistent with a measurement artifact and shows how large that artifact
  would have to be. Consistency is not identification.

## Reproducing

```
python3 -m lovs.forecast.cfrascertainment
python3 -m pytest tests/test_cfr_ascertainment.py -q -p no:cacheprovider
```

Deterministic, stdlib only, no network, and no clock of its own: the as-of day is
passed in and defaults to the last data day present in the frozen extract.
