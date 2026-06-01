# Glossary, schema evolution change

## Glossary

Load-bearing vocabulary for this change-id. Every entry cites the file:line where the term is operationally defined.

| Term | Definition | Citation |
|---|---|---|
| LOCF | Last-observation-carried-forward. A field whose upstream source has evolved its schema, or for which no fresh publication is yet available, holds its prior value into the new snapshot with explicit `carried_forward_from` and `carried_forward_reason` provenance, rather than being dropped or silently re-emitted as fresh. | `lovs/lovs_reconciler.py:84-95` |
| ReconciledCount | Frozen dataclass holding a per-metric reconciliation: minimum, maximum, primary value, primary source id, list of conflicting source ids, optional LOCF provenance pair. | `lovs/lovs_reconciler.py:62-95` |
| source_schema_evolved | New `CARRIED_FORWARD_REASONS` enum value. The upstream source has changed which fields it surfaces between cycles; the prior value remains the most recent comparable measure for the dropped or refined field. | `lovs/lovs_reconciler.py:56` (post-migration) |
| awaiting_next_publication | New `CARRIED_FORWARD_REASONS` enum value. No fresh upstream publication exists for the current snapshot cycle; the prior cycle's values carry forward unchanged with this provenance flag. | `lovs/lovs_reconciler.py:56` (post-migration) |
| deaths_confirmed | Headline metric: cumulative deaths among laboratory-confirmed cases. Sourced from INRB SitRep `cumul deces parmi confirmes` (DRC) plus ECDC Uganda confirmed deaths (back-compat). | `lovs/lovs_reconciler.py:reported_deaths` dict key (post-migration) |
| deaths_suspected | Headline metric: cumulative deaths among suspected (clinically-classifiable, not yet lab-confirmed) cases. Sourced from INRB SitRep `cumul deces suspects` when present; otherwise carried forward with `source_schema_evolved`. | `lovs/lovs_reconciler.py:reported_deaths` dict key (post-migration) |
| suspected_active | Headline metric: stock of suspected cases currently under clinical investigation or isolation. Sourced from INRB SitRep `#016` (cas suspects en cours d'investigation + cas suspects en isolement); preceded by no equivalent metric. | `lovs/lovs_reconciler.py:reported_counts` dict key (post-migration) |
| suspected_cumulative | Headline metric: cumulative count of all cases ever classified as suspected since the outbreak start (349 per SitRep `#015`, post-INRB-revision; the prior pre-revision count of 1077 from the May 28 INRB build was superseded by SitRep `#014` with the footnote "revised downward after investigation"). Successor to the legacy `suspected` key. | `lovs/lovs_reconciler.py:reported_counts` dict key (post-migration) |
| cumul_cas_confirmes | INRB SitRep dashboard tile: cumulative confirmed cases since outbreak start. SitRep `#015` value 263; SitRep `#016` value 282 (with footnote: *donnees en cours d'harmonisation). | `data/bundibugyo-2026/raw/<hash>.json` ingestion target (new) |
| cas_confirmes_actifs | INRB SitRep `#016` dashboard tile: active confirmed cases = cumul - deaths_confirmed - gueris (282 - 42 - 2 = 238). Derived metric, recorded for cross-check; not directly a primary reconciliation input. | `data/bundibugyo-2026/raw/<hash>.json` ingestion target (new) |
| harmonization-in-progress flag | Asterisk footnote on the SitRep `#016` cumul_cas_confirmes=282 tile indicating ongoing data reconciliation. Preserved in `ReconciledCount.carried_forward_reason` metadata via a sibling note field rather than altering the primary value. | `lovs/lovs_reconciler.py` ReconciledCount (post-migration; see plan §Approach step 2) |
