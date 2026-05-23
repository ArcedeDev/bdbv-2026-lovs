# Licensing

This repository is dual-licensed, with explicit carve-outs for third-party
content I archived or referenced but did not author. The default rule is:
**code is Apache 2.0; original authored prose, methodology text, and generated
presentation artifacts are CC BY 4.0; third-party source material and extracted
third-party tables keep their own terms and are not relicensed here.**

Opening the code under Apache 2.0 (rather than a Creative Commons license,
which is not designed for software) carries an explicit patent grant for the
original code. The CC BY 4.0 grant applies only to original authored portions;
it does not override publisher terms attached to archived pages, source
reports, extracted tables, or publisher-owned excerpts.

## Code and configuration: Apache License 2.0

SPDX: `Apache-2.0`. Full text in [`LICENSE`](LICENSE). Attribution notices in
[`NOTICE`](NOTICE).

Covers:

- `lovs/` (the methodology package)
- `make_brief.py`, `refresh_pipeline.py`
- `tools/`
- `tests/` (excluding fixture content under `tests/data/`, which mirrors the
  third-party license of whatever it fixtures)

## Original prose, methodology, and deliverables: CC BY 4.0

SPDX: `CC-BY-4.0`. <https://creativecommons.org/licenses/by/4.0/>

Covers original authored portions that are not code:

- Documentation and audit prose: `README.md`, `CITATIONS.md`,
  `NUMBERS_AUDIT.md`
- Original pipeline outputs I generate: `data/live-bdbv-2026-output.json`,
  `data/zones.json`, `data/evidence-chains.json`, `data/calibration-ledger.json`,
  `data/covariates-*.json`, `data/west-africa-prefecture-weekly.json`, excluding
  any embedded third-party source material or third-party table extracts
- The archive index I maintain: `data/bundibugyo-2026/manifest.json` (schema,
  structure, SHA-256 hashes, and my own annotations only; verbatim third-party
  excerpts quoted inside it stay under the source's own license, see below)
- Original deliverable layout, charts, and prose in `deliverables/`, `figures/`,
  and `brief/`, excluding any third-party source content cited or embedded
  inside those artifacts

## Third-party content retains its own license

I either archived public raw bytes or recorded hash-only provenance for
restricted publisher material. None of the third-party material is mine to
relicense; each item keeps the license under which it was published. **The
authoritative per-item license, attribution, archive status, and SHA-256 hash
live in [`data/bundibugyo-2026/manifest.json`](data/bundibugyo-2026/manifest.json)
and [`CITATIONS.md`](CITATIONS.md).** The table below is a convenience summary,
not the source of record.

| Path | Source | License (per manifest) |
| --- | --- | --- |
| `data/bundibugyo-2026/raw/` (WHO DON 602, PHEIC statement, DG remarks, IHR temporary recommendations, WHO AFRO pages) | World Health Organization | CC BY-NC-SA 3.0 IGO |
| `data/bundibugyo-2026/raw/` (consensus article) | Wikipedia contributors | CC BY-SA 4.0 |
| `data/bundibugyo-2026/raw/` (HAN 00530) | US CDC | Public domain (US Government) |
| Africa CDC hash-only provenance | Africa CDC | publisher terms not confirmed; restricted until verified |
| `data/bundibugyo-2026/raw/` (ECDC) | European Centre for Disease Prevention and Control | CC BY 4.0 unless otherwise stated; see manifest.json |
| Imperial MRC GIDA report hash-only provenance | Imperial College MRC GIDA, 18 May 2026 | CC BY-NC-ND 4.0 (recorded conservatively; see manifest `license_note`) |
| extracted Imperial Table 3 PoE dataset | Imperial College MRC GIDA, 18 May 2026, Table 3 | not redistributed in this public repository; confirm permissions or source from WHO primary sitreps before reuse |
| `data/natural_earth_outlines.json` | Natural Earth | Public domain |

Public raw archives are stored under content-addressed (SHA-256) filenames, so
map them through `manifest.json` rather than by filename. Restricted publisher
bytes may be represented as hash-only provenance with
`raw_archive_status=private_restricted_bytes`; those bytes are not
redistributed in the public repository.

**Verbatim excerpts inside `manifest.json`.** Some `normalized_content` blocks
quote short verbatim source text (`narrative_excerpt`, `declaration_text`).
Those quoted strings remain under the source's own license per the entry's
`license` field (WHO ShareAlike, Imperial NoDerivatives, Wikipedia ShareAlike,
and so on), not this repo's CC BY 4.0. Extracted counts and dates are treated
as source-attributed factual values for audit and reproducibility, but this
repository does not grant commercial reuse or redistribution rights to
publisher-owned source material. Confirm permissions before redistributing the
Imperial-derived PoE table or any full raw publisher archive outside this
non-commercial methodology-audit context.

## SPDX headers for new files

New files added to this repository should carry an SPDX identifier so the split
stays machine-checkable:

- Code: `# SPDX-License-Identifier: Apache-2.0`
- Standalone docs/data authored here: `SPDX-License-Identifier: CC-BY-4.0`

## How to cite

Moore F. *Bundibugyo virus, DRC and Uganda, 2026: surveillance methodology
brief.* Released 2026-05. <https://github.com/ArcedeDev/bdbv-2026-lovs>

See `CITATIONS.md` for the underlying methodology references.
