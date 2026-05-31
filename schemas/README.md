# Public Schemas

These JSON Schemas document the public, read-only artifact shapes used by the BDBV package. They are intentionally focused on public-source and aggregate fields. They do not define or expose private model internals, source collection automation, mutable resolver tools, private adapters, probability intervals, feature weights, or scoring code.

## Schema Map

| Schema | Applies to |
|---|---|
| `public_snapshot.schema.json` | `data/public_snapshot.json` |
| `public_source_manifest.schema.json` | `data/public_source_manifest.json`, `examples/source_manifest_minimal.example.json` |
| `public_calibration_status.schema.json` | `data/public_calibration_status.json` |
| `public_blindspots.schema.json` | `data/public_blindspots.json` |
| `public_nowcast_status.schema.json` | `data/public_nowcast_status.json` |
| `local_aggregate_input.schema.json` | `examples/local_aggregate_input.example.json` |

CSV artifacts are documented in `DATA_DICTIONARY.md`.

## Source ID Join Contract

The canonical source key is the bare `source_id`. `data/public_snapshot.json` (its `primary_source_id` and `conflicting_source_ids`) uses the bare form, while some rows in `data/public_source_manifest.json` and `data/public_source_index.csv` carry a `-live` retrieval-variant suffix. To join a snapshot reference to the manifest or index, match on the bare `source_id` (strip a trailing `-live`); stripping `-live` yields a unique key with no collisions.

## Intended Use

- Confirm that public files contain the expected top-level fields.
- Help partners map aggregate local data into the public package shapes.
- Keep source clocks and blindspots explicit.
- Support lightweight validation in partner repositories without depending on the private implementation.

The schemas are permissive where publisher-specific metadata may vary. A file can satisfy a schema and still require epidemiological review before use in a public-health decision.
