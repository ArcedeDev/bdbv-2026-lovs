# Public Methodology

This repository publishes a dated public-source evidence snapshot for the 2026 Bundibugyo virus disease event in DRC and Uganda. It is designed to help MOH, CDC, WHO, Africa CDC, ECDC, INRB, and peer analysts inspect the public evidence trail without depending on unpublished implementation details.

## Public-Source Scope

The public artifacts use only source-attributed public facts and public-source review metadata. Operational partners may hold line lists, laboratory timestamps, genomic data, contact-tracing records, field investigation notes, or non-public dashboards that are more complete than this package.

## Snapshot Dating

`as_of` is the publication snapshot timestamp. `data_as_of` is the latest data date represented by the headline snapshot. Source rows may have earlier `published_at`, `retrieved_at`, `report_date`, or `publication_date` values because public outbreak reporting is asynchronous.

## Count Handling

The public snapshot preserves the headline reported-count range, primary source ID, and conflict-anchor source IDs for confirmed cases, suspected cases, and deaths. It does not assert that every public source agrees. Source disagreement is documented in `data/public_source_conflicts.json`.

## Health-Zone Tables

`data/public_zone_counts_2026-05-26.csv` exposes source-attributed health-zone counts for public-health review. The table is a public evidence artifact, not a replacement for official health-zone reporting or case management.

## What Is Not In The Public Methodology

The public repo does not publish the LOVS implementation, calibration workbench, scoring infrastructure, source collection automation, private-data adaptation workflow, or quantitative model internals. Machine-readable public exports intentionally exclude calibration blocks, hypotheses, audit dependencies, under-ascertainment bands, and corridor probabilities.
