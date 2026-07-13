# Public Methodology

This repository publishes a dated public-source evidence snapshot for the 2026 Bundibugyo virus disease event in DRC and Uganda. It is designed to help MOH, INSP, INRB, CDC, WHO, Africa CDC, ECDC, and peer analysts inspect the public evidence trail without depending on unpublished implementation details.

The public methodology is deliberately useful but bounded. It exposes the reasoning discipline, artifact shapes, and review steps that make the public package auditable. It does not expose the private LOVS implementation, source collection automation, private-data adapters, quantitative model internals, mutable resolver tools, or private scoring code.

## Public-Source Scope

The public artifacts use only source-attributed public facts and public-source review metadata. Operational partners may hold line lists, laboratory timestamps, genomic data, contact-tracing records, field investigation notes, or non-public dashboards that are more complete than this package.

Public claims should remain traceable to:

- a source ID;
- publisher;
- source tier;
- publication date;
- retrieval date;
- source-use status;
- and, where available, a source data date.

## Snapshot Dating

`as_of` is the publication snapshot timestamp. `data_as_of` is the latest data date represented by the headline snapshot. Source rows may have earlier `published_at`, `retrieved_at`, `report_date`, or `publication_date` values because public outbreak reporting is asynchronous.

The method keeps these clocks separate:

- `data_as_of`: what date the source says the data represents.
- `published_at`: when the source made the report available.
- `retrieved_at`: when this repository captured or reviewed the source.

This prevents false disagreements when two sources are actually describing different data dates.

## Count Handling

The public snapshot preserves the headline reported-count range, primary source ID, and conflict-anchor source IDs for confirmed cases and confirmed deaths, which are the only cumulative metrics (see Cumulative Metrics And The Operational Axis below). It does not assert that every public source agrees. Source disagreement is documented in `data/public_source_conflicts.json`.

Counts are interpreted as public claims tied to sources, not as private surveillance records. When public sources disagree, this package preserves the disagreement instead of forcing a single blended value.

## Cumulative Metrics And The Operational Axis

Laboratory-confirmed cases and confirmed deaths are the only cumulative epidemiological metrics on the headline surface. The confirmed tier is the laboratory-anchored rung of the WHO suspected, probable, confirmed classification ladder, and a cumulative confirmed count behaves like a running incidence total that does not decrease as the event progresses.

The suspected counts INRB now publishes (cases under investigation and cases in isolation) are an operational caseload: a point-in-time count of who is currently in the response pipeline at the latest SitRep (116 under investigation plus 173 in isolation, 289 active, as of 2026-06-01). They live on a separate, clearly labeled operational axis (`operational_status` in `data/public_snapshot.json`), are national-only, are not cumulative, and are never added into the confirmed count.

This package deliberately does not reproduce a composite "total" that sums confirmed cases with the active suspected caseload. Confirmed is a cumulative incidence quantity and the active suspected caseload is a point prevalence; summing a running total with a current-state count mixes a stock with a flow, and it also conflates the diagnostic-certainty classification axis with the operational-status axis. The upstream cumulative-suspected series is additionally unreliable as a cumulative quantity because investigation re-bases it downward (the national cumulative-suspected figure fell from 1077 to 906 to 349 across consecutive reporting days), and the event has no published probable tier, so cumulative reduces to confirmed only under the standard WHO-AFRO convention.

The cumulative suspected tier (both suspected cases and suspected deaths) is paused and archived, not deleted: prior suspected figures and their source conflict trails are retained as dated provenance, and the tier can be reactivated in a future snapshot if a sound cumulative suspected or probable series becomes available upstream. The grounding references for this section are listed in `CITATIONS.md` under "Case classification and the cumulative-versus-operational distinction."

Counts are interpreted as public claims tied to sources, not as private surveillance records. When public sources disagree, this package preserves the disagreement instead of forcing a single blended value.

## Health-Zone Tables

`data/public_zone_counts_2026-05-29.csv` exposes source-attributed health-zone counts for public-health review. The table is a public evidence artifact, not a replacement for official health-zone reporting or case management.

Health-zone rows can lag national or country-scope headline totals. The method records the gap as attribution lag unless a later public source assigns the cases. It does not scale all zones upward to make a public map match a newer headline count.

## Public Method Cards

`METHOD_CARDS_PUBLIC.md` breaks the public method into reusable cards:

- source reconciliation;
- source clocks;
- health-zone attribution lag;
- blindspot register;
- calibration accountability;
- nowcast boundary.

These cards are the safest place to reuse the method in another public or partner-local aggregate workflow.

## Worked Snapshot Review

`WORKED_SNAPSHOT_REVIEW.md` applies the public method to the current real snapshot. It shows how to:

- identify the snapshot clock;
- read headline counts as public claims;
- compare health-zone attribution with headline totals;
- review source-clock coverage;
- interpret blindspots;
- inspect calibration-accountability status.

The same review can be run locally with:

```bash
python3 examples/review_public_methodology.py
```

## Calibration Accountability

The public calibration files expose pre-registered public questions, target roles, status summaries, resolution dates, public resolution policy, and commitment hashes. They do not publish private scoring implementation, target-generation logic, or quantitative internals.

The public rule is simple: keep rows open until citable public authority evidence supports resolution under `CALIBRATION_RESOLUTION_PUBLIC.md`.

## Blindspots And Latency

`data/public_blindspots.json` tracks evidence states that public sources cannot fully answer. `data/public_latency_observatory.csv` measures reporting latency only where source clocks allow it. Rows with missing source dates remain visible because missingness is part of the public evidence state.

Health-zone rows can lag national or country-scope headline totals. The method records the gap as attribution lag unless a later public source assigns the cases. It does not scale all zones upward to make a public map match a newer headline count.

## Public Method Cards

`METHOD_CARDS_PUBLIC.md` breaks the public method into reusable cards:

- source reconciliation;
- source clocks;
- health-zone attribution lag;
- blindspot register;
- calibration accountability;
- nowcast boundary.

These cards are the safest place to reuse the method in another public or partner-local aggregate workflow.

## Worked Snapshot Review

`WORKED_SNAPSHOT_REVIEW.md` applies the public method to the current real snapshot. It shows how to:

- identify the snapshot clock;
- read headline counts as public claims;
- compare health-zone attribution with headline totals;
- review source-clock coverage;
- interpret blindspots;
- inspect calibration-accountability status.

The same review can be run locally with:

```bash
python3 examples/review_public_methodology.py
```

## Calibration Accountability

The public calibration files expose pre-registered public questions, target roles, status summaries, resolution dates, public resolution policy, and commitment hashes. They do not publish private scoring implementation, target-generation logic, or quantitative internals.

The public rule is simple: keep rows open until citable public authority evidence supports resolution under `CALIBRATION_RESOLUTION_PUBLIC.md`.

## Blindspots And Latency

`data/public_blindspots.json` tracks evidence states that public sources cannot fully answer. `data/public_latency_observatory.csv` measures reporting latency only where source clocks allow it. Rows with missing source dates remain visible because missingness is part of the public evidence state.

## What Is Not In The Public Methodology

The public repo does not publish the LOVS implementation, calibration workbench, scoring infrastructure, source collection automation, private-data adaptation workflow, or quantitative model internals. Machine-readable public exports intentionally exclude private calibration blocks, private hypotheses, audit dependencies, under-ascertainment bands, and corridor probabilities.
