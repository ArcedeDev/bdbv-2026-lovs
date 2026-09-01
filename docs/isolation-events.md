# Isolation capacity and events: what the record holds, and what it stopped holding

Companion to `lovs/forecast/isolationevents.py`. Every figure below is printed by
`python3 -m lovs.forecast.isolationevents`; none is typed by hand. Re-run it and
the numbers regenerate, or the run fails.

As-of 2026-09-01. The last isolation data day is 2026-08-28, so every reading is
four days stale at the as-of date and the module reports that gap on every
forecast row rather than letting a 30-step path be read as reaching 30 days past
the as-of.

## 1. The finding that came first: which metrics actually exist

The LOVS evidence store defines `isolation_escapes`, `isolation_admissions` and
`isolation_deaths`. The task was to check whether they are in the SitRep packet
substrate and to say plainly if they are not.

They are. All three were published, for months. That is not the interesting part.

`audit_isolation_metrics` scans 101 INSP/INRB packets, matching every key the
SitRep schema has used for each family — the spelling drifted repeatedly
(`escaped` → `escaped_confirmed` → `escaped_suspect_or_confirmed_24h`), and a
census that matched one spelling would report a rename as a discontinuation.

| metric | source | data days | first | last | stale at as-of |
|---|---|---:|---|---|---:|
| `isolation_occupancy` | frozen extract | 90/101 | 2026-06-04 | 2026-08-28 | **4d** |
| `isolation_escapes` | packet substrate | 73/101 | 2026-05-20 | 2026-08-22 | **10d** |
| `isolation_admissions` | packet substrate | 68/101 | 2026-05-20 | 2026-08-02 | **30d** |
| `isolation_prior_day_census` | packet substrate | 68/101 | 2026-05-20 | 2026-08-02 | **30d** |
| `isolation_deaths` | packet substrate | 48/101 | 2026-05-20 | 2026-07-12 | **51d** |
| `isolation_non_cases` | packet substrate | 41/101 | 2026-05-20 | 2026-07-12 | **51d** |
| `isolation_exits` | packet substrate | 39/101 | 2026-06-01 | 2026-07-12 | **51d** |
| `isolation_bed_capacity` | packet substrate | 1/101 | 2026-06-11 | 2026-06-11 | **82d** |

Read the staleness column downward. The INSP ward-flow table — patients at bed
J−1, admissions, deaths, non-cases, escapes, total exits, end-of-day census — is
being dismantled column by column while the end-of-day census keeps running.
Deaths, non-cases and exits stopped on 2026-07-12. Admissions stopped on
2026-08-02. Escapes held out until 2026-08-22. Occupancy is current.

**The response is still publishing the stock and has stopped publishing the flow.**

That is worse than an absence, because an absence is visible. A stock without a
flow looks complete. It is what lets a reader say "occupancy rose 40 today"
without being able to ask whether that was forty more admissions or forty fewer
discharges — clinically and operationally opposite events, one a surge and one a
blockage.

Two consequences for this repository, both structural rather than fixable here:

1. **None of these three metrics is in `data/operational-series-2026-09-01.json`.**
   The frozen extract carries `hospital_isolation_total` and
   `isolation_by_province` and nothing else from the flow table. The forecast
   layer therefore cannot use escapes, admissions or isolation deaths without an
   extract change, which is outside this module's scope. The module reads them
   from the read-only packet checkout when it is present and degrades to
   extract-only figures when it is not.
2. **`isolation_escapes` is a per-period count, and the substrate agrees** — it is
   published as a 24-hour figure per province. Nothing here treats it as a
   running total.

### One trap worth naming

`patients_at_bed_j_minus_1` appears in 68 packets and reads, in English, like a
bed count. It is not. It is the previous day's census — patients in bed at J−1.
Counting it as capacity would manufacture exactly the denominator section 5
refuses to invent, and would do so with a plausible-looking series behind it.
`_packet_capacity_keys` deliberately looks only in the reviewed per-province
detail, and `test_prior_day_census_is_never_counted_as_bed_capacity` fails if
that separation is ever collapsed.

## 2. Occupancy trajectory

80 observations, 2026-06-04 (258) to 2026-08-28 (896). 896 is the series maximum,
so occupancy stands at its all-time high.

| | value |
|---|---|
| day-over-day moves | 51 up / 24 down / 4 flat |
| **monotone non-decreasing** | **no** |
| mean change | +5.78 / day |
| median change | +10.00 / day |
| OLS slope | +6.08 / day |
| largest single rise | +115 on 2026-08-13 |
| largest single fall | −146 on 2026-08-11 |
| occupancy doubling time | 47.3 days |

**The Block 6 rationale for `iso-above-1000` states that occupancy "has risen
monotonically to 896". It has not.** Thirty percent of day-over-day moves are
falls, the largest a −146 on 2026-08-11. The pinned
probability is unaffected — the bootstrap resamples the actual differences and
never consulted the prose — but the rationale as written would mislead a reader
scoring the block, and section 3 explains why it is wrong in a way that matters.

The median daily change (+10.0) is nearly double the mean (+5.78) because the
falls are fewer but much larger than the rises. That is the signature of a series
whose downward moves are not the same kind of event as its upward ones.

The doubling time is a doubling time **for beds occupied**. It must never be read
as an incidence doubling time. Occupancy is a stock governed by admissions minus
discharges; a lengthening average stay doubles it with no change in transmission
whatsoever.

## 3. Most of the volatility is reporting, not patients

The per-province table is a coverage-varying sum: provinces drop out between
cycles and return. Comparing only the provinces that reported on **both** days of
each pair is arithmetic, not modelling — a province that vanished from the table
did not discharge its patients.

| | raw | constant panel |
|---|---:|---:|
| day-over-day sd | 62.7 | **35.7** |
| day-over-day mean | +8.7 | **+8.5** |

Over 73 comparable pairs across 74 panel days, 28 days changed province
membership. **67.5% of the day-over-day variance disappears when the panel is
held constant, while the drift survives intact.** The trend is real; most of the
noise around it is provinces appearing and disappearing.

The worst single artifact is 2026-08-12: the raw province sum moves **+312**
while the constant-panel move is **+63**, because Haut-Uele, Nord-Kivu, Sud-Kivu
and Tshopo all re-entered the table that day after being absent the day before.
Twenty-eight of the 73 pairs carry a membership change like it, in both
directions.

This is what makes the non-monotonicity in section 2 more than a wording
correction. Those falls are substantially not discharges. And the Block 6
bootstrap resamples them as if they were: a five-day block that happens to
contain 2026-08-11 carries a 146-patient collapse into every path that draws it.
The pins are not re-priced here — they are reproduced exactly in section 7 — but
any interval built on this series is wider than the underlying clinical process
warrants, and the direction of that bias is knowable.

Fixing it needs a change to the extract, not to the forecaster: a national series
reconstructed on a constant province panel, or a coverage flag per row.

## 4. Isolation pressure by province

Occupancy on 2026-08-28 against each province's active confirmed burden, with a
14-day trailing OLS slope. Active confirmed is confirmed minus confirmed deaths.

| province | occupancy | active confirmed | occ / active | slope /day | slope per 100 active | window pts |
|---|---:|---:|---:|---:|---:|---:|
| **Haut-Uele** | 70 | 116 | 0.60 | **+1.79** | **+1.546** | 11 |
| **Nord-Kivu** | 236 | 254 | 0.93 | **+3.67** | **+1.445** | 15 |
| Ituri | 552 | 2699 | 0.20 | +0.18 | +0.007 | 15 |
| _Sud-Kivu_ | 26 | 2 | 13.00 | +1.50 | +75.181 | 11 |
| _Tshopo_ | 11 | 11 | 1.00 | +0.41 | +3.730 | 12 |
| _Bas-Uele_ | 1 | 1 | 1.00 | −0.11 | −10.829 | 7 |

Italicised provinces sit below the 30-active-case floor and are **excluded from
the ranking**, not ranked. At 2 active confirmed cases Sud-Kivu's +75.2 per 100
measures its denominator's roundoff and nothing else. Reporting them without the
flag would put Sud-Kivu at the top of a pressure table on the strength of three
cases.

Among provinces where the denominator carries meaning, **the pressure is outside
the epicentre**. Ituri holds 552 of the 896 patients but its ward is flat
(+0.18/day, +0.007 per 100 active) on a caseload of 2,699. Nord-Kivu is filling at
+1.445 beds per 100 active cases per day against Ituri's +0.007, and Haut-Uele
faster still at +1.546. The national rise is
being driven by the secondary provinces while the epicentre's ward has stopped
growing.

Two limits, both stated rather than corrected:

- **The denominator is an over-estimate.** Recoveries are published nationally
  only, so they cannot be subtracted per province. Every ratio here is therefore a
  *lower* bound on pressure. Applying the national recovery rate province-wise
  would assume exactly the uniformity the ranking exists to detect.
- **The numerator is not confirmed cases.** Isolation holds confirmed and
  suspected patients together; the packets show the ward running roughly half
  suspects. A ratio above 1.0 does not mean a province hospitalised more people
  than it has cases — it means the ward is suspect-dominated. The ratio has no
  ceiling at 1.0 and is not a containment share.

## 5. Headroom: not computable, and this is the reason

Headroom is occupancy against beds. Occupancy is published every cycle. **Beds
are published once**: `beds_available` appears in exactly 1 of 101 packets, on
2026-06-11.

`headroom()` therefore returns a refusal, not a number, and carries no float
field at all — `test_headroom_exposes_no_numeric_percentage` fails if one is ever
added. A headroom percentage computed against an assumed capacity would be quoted
as an operational ceiling. It would be the single most dangerous figure this
repository could emit, because unlike a forecast it would not look like an
estimate.

What would have to be published to compute it:

- per-province beds established in dedicated isolation and treatment units,
  republished each cycle rather than once;
- the confirmed/suspect split of those beds, since the two cannot share a ward;
- beds **staffed**, as distinct from beds installed — an unstaffed bed is not
  headroom;
- the same series for the transit and triage points that feed the units.

The closest available reading is occupancy per active confirmed case in section
4, which is a burden share and not a capacity share.

## 6. Turnover: an inference, measured and rejected

The identity is exact: occupancy(t) − occupancy(t−1) = admissions − exits. Net
ward flow is therefore as observed as the occupancy series it comes from, which
section 3 shows is not perfectly observed either. Over 74 consecutive-day pairs
it runs **+3.8/day with a standard deviation of 44.1**. Non-consecutive days are
dropped rather than spread across the gap: the identity survives a gap but the
daily terms do not line up, and normalising per elapsed day would invent
within-gap admissions.

The tempting next step is to add back the exits the extract can see — new
recoveries and new confirmed deaths — and read off admissions. That inference
returns a mean of 53.0 admissions/day over 74 days.

**It is wrong, and it is wrong by a measurable amount.** Scored against the 50
days where the packets still carry an observed admissions figure:

| | value |
|---|---|
| days compared | 50 |
| median share of observed admissions recovered | **44%** |
| correlation with observed | **r = 0.30** |
| mean absolute error | **56 patients/day** |

It recovers under half of true admissions and barely tracks their day-to-day
movement. Three causes, all structural:

1. Suspect admissions never enter `new_confirmed_today`, and the ward is roughly
   half suspects.
2. The non-case and escape exit routes are absent from the extract entirely, so
   the exit side of the identity is systematically short.
3. Community deaths inflate `confirmed_deaths_total` without any corresponding
   ward exit, biasing the other way and blurring what is left.

So this module reports the inference as **REJECTED**, not as a caveated estimate.
The pass thresholds (r ≥ 0.8 and a median ratio in 0.8–1.25) are stated in the
code ahead of the comparison rather than chosen after seeing the answer, and
`test_the_inference_validates_when_it_actually_tracks_observations` feeds the
same function a synthetic case where the inference is good and asserts it passes
— so the rejection comes from the data and not from a hard-coded verdict.

The honest summary: **ward turnover cannot be recovered from the frozen extract.
It could be recovered from the SitReps up to 2026-08-02, and after that date it
cannot be recovered from the public record at all.**

## 7. Forecasts, and the Block 6 consistency check

Stationary block bootstrap from `opsforecast.py`: 5-day blocks, 20,000 paths,
walked 30 days forward from the last data day (2026-08-28, 896), floor at zero.
Seeds 7100–7109, deliberately far from Block 6's 6001–6012 so a number from this
ladder can never be mistaken for a pinned one;
`test_ladder_seeds_cannot_collide_with_the_pinned_block` enforces it.

| threshold | ends at or above | reaches at any point |
|---:|---:|---:|
| 850 | 0.8707 | 0.9961 |
| 900 | 0.7978 | 0.9811 |
| 1000 | **0.5935** | 0.7567 |
| 1100 | 0.3586 | 0.4595 |
| 1200 | 0.1671 | 0.2167 |

Monte Carlo standard errors run 0.0004–0.0035, so the fourth decimal is not
meaningful and the third is marginal.

**Read the `ever_above` column as an upper bound, not as a forecast.** The
validation doc found that "ever crosses X" on a noisy daily series is dominated
by single-day noise; section 3 explains why that noise exists here in particular,
since a province leaving the table looks like a 100-bed excursion. The gap
between the two columns at 850 — 0.8707 ending above against 0.9961 touching it
at some point — is almost entirely reporting churn.

### Block 6 reproduces exactly

Both committed isolation pins are re-derived from the ledger's own recorded
shape, threshold, seed, block length and path count. Nothing is typed.

| pin | shape | threshold | seed | pinned | reproduced | delta |
|---|---|---:|---:|---:|---:|---:|
| `iso-below-850` | ends_below | 850 | 6001 | 0.1285 | **0.1285** | +0.0000 |
| `iso-above-1000` | ends_above | 1000 | 6009 | 0.5895 | **0.5895** | +0.0000 |

To three decimals: **0.129 and 0.590**, matching the block registration exactly.
No defect to report.

An exact match on the ledger's own seed proves the pipeline is intact and says
nothing about whether the number is a lucky draw, so each pin is also re-priced
from an independent seed. `iso-below-850` has no `ends_below` row in the ladder,
so it is matched against its complement rather than skipped:

| pin | pinned | independent re-price | comparison | residual | 2 × combined SE |
|---|---:|---:|---|---:|---:|
| `iso-below-850` | 0.1285 | 0.8707 | complement | +0.0008 | 0.0067 |
| `iso-above-1000` | 0.5895 | 0.5935 | same shape | +0.0040 | 0.0098 |

Both residuals sit inside two combined Monte Carlo standard errors. The pinned
probabilities are properties of the series, not of seed 6001.

## 8. What this module cannot support

- **Anything about capacity.** See section 5. There is no denominator.
- **Ward turnover after 2026-08-02.** See section 6. The flow columns stopped.
- **A structural break.** The bootstrap encodes only how occupancy has moved. A
  new treatment centre opening, a discharge-criteria change, or a security
  incident closing a ward are all outside it. So is the most likely near-term
  structural event here: the province panel stabilising, which would *reduce*
  measured volatility without any clinical change at all.
- **Reading occupancy as burden.** Half the ward is suspects. Occupancy is a
  measure of what the response is holding, not of how many people have BDBV.

## 9. What would change the answers

In descending order of what it would buy:

1. **Republish the ward-flow table.** Admissions, exits by route, and escapes,
   per province, per cycle. This restores turnover, which is the difference
   between a surge and a blockage, and it is the only item here that was
   previously published and simply stopped.
2. **Publish bed capacity as a series.** Established, staffed, and split
   confirmed/suspect. This is the only thing that makes headroom a number.
3. **Flag province coverage on every row of the extract.** Two-thirds of the
   day-over-day variance in the occupancy series is currently reporting churn
   that a single per-row coverage field would let every downstream consumer
   remove.
4. **Publish recoveries per province.** This turns the pressure denominator in
   section 4 from a lower bound into a measurement.

Items 1 and 2 are asks of the publisher. Items 3 and 4 are partly recoverable
from the packet substrate that already exists, as an extract change.
