# Weekly forecast loop: method and registration

Companion to `lovs/forecast/weekly/` and the public store `data/weekly-forecasts/`.
Written before the first weekly registration, so nothing here is chosen with
hindsight. The monthly operational calibration blocks continue unchanged: the weekly
loop never writes the operational ledger, the public calibration commitments, the
public register or the refresh pipeline, and its rows never reach `record.py` or
`recalibration.py` (tests/test_weekly_isolation.py checks this).

## What is registered each week

Every Wednesday R (UTC) one build registers forecasts for four weeks ahead. Week k
covers data days R+7(k-1)+1 to R+7k, so the horizons are 7, 14, 21 and 28 days. The
build reads the LOVS repository and the evidence packets at fetched commits only,
never a working tree, and must finish before 20:00Z. A week is registered only if its
branch `bdbv-weekly-<R>` reaches the public repository by R 23:59:59Z; a branch
created later voids the week, which is then reported and never scored.

Public files, under `data/weekly-forecasts/<R>/`:

| File | Contents |
|---|---|
| `manifest.json` | spec version, the full spec digest and the core digest, cutoff, fetched commits, generator commit, input hashes, active and skipped series, file hashes |
| `pins.csv` | threshold pins with one price per method |
| `commitment.json` | sha256 and size of the private `forecasts.json`, with its counts |
| `vintage-delta.jsonl` | the numeric vintage rows added since the previous week |
| `zones-delta.jsonl` | affected-zone records added since the previous week |
| `province-covariates-delta.jsonl` | province table rows added since the previous week |

The quantile forecasts stay private until the founder decides otherwise; the
commitment hash fixes them at registration. From the public archive and the private
file, `register.regenerate` rebuilds the week byte for byte; the build fails if it
cannot.

## Series

| Id | Series | Target | Pin step |
|---|---|---|---|
| N1 | new confirmed cases | weekly flow | 10 |
| N2 | new confirmed deaths | weekly flow | 5 |
| N3 | new recoveries | weekly flow | 5 |
| N4 | affected health zones | level at week end | 1 |
| N5 | contact follow-up percent | mean of reported days | 1 |
| N6 | alerts reported | mean of reported days | 100 |
| N7 | publication lag in days | lag of the end Wednesday, capped at 14, scored settled only; censored if its promotion is not on main at settle | 1 |
| P1 | new confirmed cases, Ituri | weekly flow | 5 |
| P2 | new confirmed deaths, Ituri | weekly flow | 5 |
| P3 | new confirmed cases, Nord-Kivu | weekly flow | 5 |
| P4 | new confirmed deaths, Nord-Kivu | weekly flow | 5 |
| P5 | new confirmed cases, Haut-Uele | weekly flow | 5 |
| P6 | new confirmed deaths, Haut-Uele | weekly flow | 5 |
| P7 | new confirmed cases, Tshopo | weekly flow | 1 |
| P8 | new confirmed deaths, Tshopo | weekly flow | 1 |

A flow is the cumulative value on the week's last Wednesday minus the cumulative value
on the Wednesday before it, with no fallback to a nearby day. In the public files
`target_start` is that start boundary Wednesday and `target_end` the end Wednesday; a mean
covers the seven data days after `target_start`. A mean needs at least 5
of the 7 days reported. A province series is active only if the province appeared in
every SitRep of the trailing 28 days and had at least 20 new confirmed cases in that
window. A series with fewer than 30 observations, or whose last observation is more
than 3 days before the cutoff, is skipped for the week with its reason code.

## Vintage

History to SitRep 177 comes from `data/operational-series-2026-11-07.json`, checked by
its sha256. Later days come from reviewed SitRep promotions in this repository. At
every build the promotions are cross-checked against the extract and against the
evidence packets on every field both carry; a single mismatch stops the build. Only
data published before R and on or before the cutoff is used. Basis flags (a tracked
province missing from the alerts table, a withheld field, a withheld laboratory
denominator) are stored as codes on each row.

## Methods

Method ids are frozen with the prefix `wk1.`.

| Class | A (record) | B (challenger) |
|---|---|---|
| flows and N4 | `drift_bootstrap_28d` | `level_bootstrap` |
| N5 to N7 | `level_bootstrap` | `recent_levels_21d` |

Every class also has `persistence_8w`, `climatology_asof` and `ens_pool2`, an equal
linear pool of A and B. The bootstraps resample 5-day blocks of daily steps over
20,000 paths. Flows never go negative: each negative daily step and the step before it
are dropped, then each step is max(0, step), and persistence and climatology draws of a
flow are floored at 0. Percent and lag series are clamped to
their bounds; N4 never falls below its last level. Seeds come from
`pins_block13.derived_seed` with a counter, unique against every seed in the
operational ledger at the fetched commit and every earlier weekly seed.

Each forecast carries 23 quantile levels (0.01, 0.025, 0.05 to 0.95 by 0.05, 0.975,
0.99), monotone and inside the series' support, and the sha256 of its first 100
trajectories.

Two digests fix the rules. The core digest (`spec.core_sha256()`) covers everything a
week is judged on: the series set, steps, horizons and target calendar, the six methods
and their parameters, the ladder set, `ens_pool2`'s members and combiner, statuses,
settle and anomaly rules, the scorer, the truth reader, the archive allowlist, the
privacy gate's closed vocabulary, and the source code that prices, resolves, scores and
gates a week: the sha256 of each module in `spec.CODE_MODULES`, the import closure of the
weekly core modules and the build driver `lovs/forecast/weekly/__main__.py` (20 files under
`lovs/forecast/weekly/`, `lovs/forecast/` and `lovs/forecast_scoring.py`). The full spec digest (`spec.spec_sha256()`) is the
core plus the candidate registry (`spec.CANDIDATES`, empty at the first registration).
Adding a candidate changes only the full digest: a candidate is priced beside the frozen
set, never joins the ladder set or the pool, and never changes what the v2
pre-registration is judged on. A change to the core is a founder-approved revision with a
new spec version, and it ends v2 at that week. A change to any file in `spec.CODE_MODULES`,
a bug fix included, is a change to the core.

## Pins

Pins are thresholds at the 0.1, 0.3, 0.5, 0.7 and 0.9 quantiles of the mixture of A, B,
persistence and climatology, snapped to the series' step and merged; a threshold that
every method prices at 0, or every method at 1, is dropped. Each pin asks whether the
realised value is at or below its threshold, and carries a price from each of its
class's five methods.

## Truth and resolution

A value counts when it is first published on the main branch, and again when settled,
14 days after the week ends (23:59:59Z). Arrival on main is read from the repository's
activity record, each push naming the tip before and after it, with the pull request
merge time as the fallback; commit dates are never used. A boundary day counts only
once both its promotion and its evidence packet have arrived.

N7 is censored in real time, so it is scored settled only. The lag runs from the end
Wednesday's data day to the UTC date its SitRep was first posted on insp.cd, read from
the promotion's own `published_at` (a time without a zone is taken as written), capped at
14. It never depends on when the promotion reached main. When main holds no promotion for
the end Wednesday at settle, 14 days after the week ends, the cell is `censored` with the
anomaly code `lag_censored`: outside the primary endpoint, counted in the data-usefulness
report and written in the results file, never scored as a lag of 14.

Pin statuses: `resolved_yes`, `resolved_no`, `pending`, `pending_boundary`,
`unscoreable_span`, `void_decided_before`, `held_basis_break`, `censored`. A boundary day
still missing 28 days after the week ends makes the target unscoreable. Anomaly codes:
`cumulative_fall`, `withheld_field`, `settle_revision`, `lag_censored`. Each later build writes
`data/weekly-forecasts/results/<R>.csv` with Brier and log scores for every method;
quantile scores (weighted interval score, coverage, randomised PIT) stay private with
the forecasts.

## Privacy

Every public file and every results row passes a closed-vocabulary gate before it is
written: codes, ids, hashes, numbers and ISO dates of data, publication, registration
and build days only. Province and zone names are allowed only under province or zone
keys. No free text, SitRep text or packet text is published. Gate findings name the
path, never the value.

## Reproducing a week

    python3 -m lovs.forecast.weekly build --clock <R>T12:30:00Z \
        --lovs-repo <clone> --lovs-commit <sha> --mcp-repo <clone> --mcp-commit <sha> \
        --fetched fetched.json --arrivals arrivals.json --store <dir>/data/weekly-forecasts \
        --private-root <dir>/weeks --report-out <dir>/reports --generator-commit <sha>

The commits are those in the week's `manifest.json`. Pins and the commitment hash
reproduce exactly from the same inputs and generator commit.

What an outsider can rerun from the public repository: every week's resolution and
scores, from the promotions on main and the arrival times each manifest caches under
`arrivals` (the commit and its arrival on main, for every promotion version the build
used that no earlier manifest holds); and, once the forecasts are released, the
commitment hash and the regeneration of each week from its archives. The choice of
cutoff and the packet cross-check read the evidence packets, which are private, so
they cannot be rerun from the public repository; the manifest records the cutoff and
the sha256 of every packet used.

## Publication

No scheduled job publishes anything. Each week's branch is built and committed
locally; it is pushed only after the founder gives a go for that week and its pin
count.
