# Public Calibration Resolution Protocol

This protocol explains how public calibration-ledger rows should be reviewed after their resolution dates. It is a public accountability process, not the private scoring implementation.

## Scope

The protocol applies to:

- `data/public_calibration_ledger.csv`
- `data/public_calibration_status.json`
- `data/public_precommitment_targets.csv`

It does not publish probability intervals, feature weights, model parameters, private source inputs, mutable resolver tools, or scoring code.

## Resolution Sources

Use public authority or reference sources that are citable and available to external reviewers, including:

- Democratic Republic of the Congo Ministry of Public Health.
- Uganda Ministry of Health.
- INRB, INSP, or official DRC public-health releases.
- WHO and WHO regional offices.
- Africa CDC.
- CDC.
- ECDC.
- Other cited public authority sources when the row's public question clearly permits them.

Do not resolve public rows from private line lists, unpublished dashboards, private lab records, direct messages, or restricted source bytes unless the same claim is also available in a citable public source.

## Resolution States

Each public ledger row should remain in one of these states:

| State | Meaning |
|---|---|
| `open` | Resolution date has not arrived, or public evidence has not yet been reviewed. |
| `resolved` | Public evidence supports a clear yes/no or numeric resolution. |
| `ambiguous` | Public evidence exists but does not cleanly answer the registered question. |
| `unscoreable` | Public evidence needed for the row is unavailable, contradictory beyond resolution, or outside the row's source policy. |
| `retired` | The row is no longer eligible for scoring because the public question or source policy is invalidated by later review. |

## Review Steps

1. Confirm the row's `resolution_date` has passed.
2. Read the `public_question`, `source_geography`, `target_geography`, `horizon_days`, and `resolution_source_policy`.
3. Search the permitted public sources for evidence available by or after the resolution date.
4. Record the source ID, publisher, publication date, retrieval date, URL, and a short evidence note.
5. Assign one resolution state.
6. If resolved, fill `resolved_value` with the public outcome value or label.
7. If ambiguous or unscoreable, leave `resolved_value` blank and explain the limitation in notes.
8. Recompute the row commitment hash only if the public row payload intentionally changes in a new release.

## Evidence Priority

Prefer sources in this order when multiple public sources disagree:

1. Official national authority reporting for the geography named in the row.
2. Official laboratory or public-health institute reporting for the geography named in the row.
3. WHO, Africa CDC, CDC, or ECDC public reporting that cites or summarizes official authority data.
4. Other public authority sources that clearly identify the geography, date, and case definition.

When sources disagree, preserve the disagreement rather than silently selecting the most convenient source. Use `data/public_source_conflicts.json` as the style pattern.

## Public Notes

Resolution notes should be short and auditable:

- Name the deciding public source.
- Name the relevant date.
- State whether the source directly answers the row or only partially answers it.
- Preserve material conflicts.
- Avoid private operational details.

## Non-Operational Notice

Calibration resolution is a methods-accountability activity. It is not a field response recommendation, deployment recommendation, travel advisory, clinical instruction, or official outbreak classification.
