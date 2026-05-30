# Public Adaptation Examples

These examples are safe templates for public-health analysts who want to adapt the public BDBV evidence package to their own aggregate review process.

The examples are grounded in the current public BDBV snapshot already published in this repository. They do not contain person-level data, private operational records, model parameters, probability intervals, source collection automation, or scoring machinery.

## Files

- `local_aggregate_input.example.json` shows the current public aggregate snapshot shape for headline counts and health-zone rows.
- `source_manifest_minimal.example.json` shows a public source-manifest subset with source clocks and source-use status.
- `public_calibration_commitments.example.csv` shows a public accountability row from the current calibration ledger.
- `summarize_public_package.py` prints a read-only summary of the public snapshot, source index, latency rows, blindspots, and calibration status.

## Use

1. Copy the example shape into your own private workspace.
2. Replace the current public BDBV values with aggregate values approved for your setting.
3. Keep private line lists, lab records, contact-tracing records, genomic sample IDs, and raw restricted source captures out of this public repository.
4. Use `PUBLIC_ADAPTATION_GUIDE.md` for the surrounding workflow.
5. Run `python3 examples/summarize_public_package.py` to inspect the public package locally without invoking private LOVS logic.

For private-data evaluation or implementation support, contact `frans@arcede.com`.
