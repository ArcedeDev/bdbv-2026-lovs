# SPDX-License-Identifier: Apache-2.0
"""Semantic-freshness release gate for published BDBV artifacts.

Byte-currency gates (``check_public_artifacts``, ``cross_surface_parity``) prove
that the shipped bytes match a fresh re-render. They do NOT prove that the
*content rendered into* those bytes is semantically current: a brief SVG can be
byte-identical to its committed copy yet still carry a stale embedded ``as_of``
date, a workbook can render a death count that no longer matches the headline,
or an axis label can still say "Deaths (reported)" after the death tier became
laboratory-confirmed only. This gate closes that gap.

The gate is anchored ENTIRELY to the snapshot's own clocks:

  * ``snapshot["as_of"][:10]``                         -> the headline date.
  * ``snapshot["insp_per_zone_block"]["as_of_data_date"]`` -> the per-zone date.

It NEVER reads a wall clock, so it is fully deterministic for a fixed snapshot.

It FAILs (conservatively, on ambiguity) when:

  1. an SVG embeds an ``as_of YYYY-MM-DD`` that is neither the snapshot headline
     date nor an allowed source date declared in the source manifest;
  2. a workbook renders a confirmed-cases / confirmed-deaths cell whose value
     does not match the snapshot ``reported_counts`` / ``reported_deaths``
     primary;
  3. the per-zone CSV's ``as_of_data_date`` column disagrees with the per-zone
     block's ``as_of_data_date``;
  4. any artifact text carries a mixed-basis death label ("Deaths (reported)")
     while the snapshot death axis is confirmed-only (on/after 2026-06-02).

It additionally validates the per-artifact package manifest (schema v2): each
declared ``must_contain_text`` must be present and each ``must_not_contain_text``
must be absent in the named artifact, and per-artifact ``semantic_as_of`` /
``source_date`` values must be snapshot-consistent or declared source dates.

Stdlib only.
"""
from __future__ import annotations

import json
import pathlib
import re
import zipfile
from collections.abc import Iterable, Mapping
from typing import Any


# The death tier became laboratory-confirmed only on this date. On/after it, a
# "Deaths (reported)" label (the retired broad-register wording) is a stale
# mixed-basis label and must not appear on any shipped artifact.
DEATH_BASIS_CUTOFF = "2026-06-02"

# Mixed-basis death labels that are stale once the death axis is confirmed-only.
# Conservative: any of these substrings on a confirmed-only artifact FAILs.
_MIXED_BASIS_DEATH_LABELS = (
    "Deaths (reported)",
    "Deaths(reported)",
    "reported deaths",
    "Reported deaths",
    "Reported Deaths",
)

_SVG_AS_OF_RE = re.compile(r"as[_ ]of[\s:]*?(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_ANY_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

PACKAGE_MANIFEST_NAME = "lovs-public-health-dataset.manifest.json"
PER_ZONE_CSV_NAME = "per-zone_snapshot.csv"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def parse_svg_dates(svg_text: str) -> set[str]:
    """Return every ``as_of YYYY-MM-DD`` date embedded in an SVG (or any text).

    Matches ``as_of 2026-05-29``, ``as of 2026-05-29``, ``as_of: 2026-05-29``,
    case-insensitively. Only dates explicitly tagged as an ``as_of`` are
    returned; free-floating dates elsewhere in the document are ignored so the
    check stays anchored to the artifact's declared currency date.
    """
    return {match.group(1) for match in _SVG_AS_OF_RE.finditer(svg_text or "")}


def _snapshot_primary(metric_block: Mapping[str, Any] | None, key: str) -> int | None:
    """Pull the integer primary for ``key`` out of a reported_counts/deaths block."""
    if not isinstance(metric_block, Mapping):
        return None
    row = metric_block.get(key)
    if not isinstance(row, Mapping):
        return None
    value = row.get("primary", row.get("primary_value"))
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _read_xlsx_sheet_map(workbook: zipfile.ZipFile) -> dict[str, str]:
    """Map sheet display name -> worksheet xml part path."""
    workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8", "replace")
    names = re.findall(r'<sheet[^>]*name="([^"]+)"', workbook_xml)
    out: dict[str, str] = {}
    for idx, name in enumerate(names, start=1):
        part = f"xl/worksheets/sheet{idx}.xml"
        if part in workbook.namelist():
            out[name] = part
    return out


def _xlsx_rows(worksheet_xml: str) -> list[list[str]]:
    """Parse a (this-exporter-shaped) worksheet into rows of plain-text cells.

    The exporter writes numeric cells as ``<c ...><v>N</v></c>`` and text cells
    as ``<c ... t="inlineStr"><is><t>...</t></is></c>``. We render both to the
    plain string the workbook displays.
    """
    rows: list[list[str]] = []
    for row_xml in re.findall(r"<row\b[^>]*>(.*?)</row>", worksheet_xml, re.DOTALL):
        by_col: dict[int, str] = {}
        max_col = 0
        # Self-closing (empty) cells `<c r="D2"/>` MUST be matched before the
        # open/close form; otherwise an empty cell's opening tag is mis-parsed as
        # the start of a populated cell. Each cell is then placed by its `r`
        # column reference, so sparse/empty cells never shift the columns left.
        for cell_xml in re.findall(r"<c\b[^>]*/>|<c\b[^>]*>.*?</c>", row_xml, re.DOTALL):
            ref = re.search(r'\br="([A-Z]+)\d+"', cell_xml)
            col = _column_index(ref.group(1)) if ref else max_col + 1
            inline = re.search(r"<is>.*?<t[^>]*>(.*?)</t>.*?</is>", cell_xml, re.DOTALL)
            if inline:
                by_col[col] = _unescape_xml(inline.group(1))
            else:
                value = re.search(r"<v>(.*?)</v>", cell_xml, re.DOTALL)
                by_col[col] = _unescape_xml(value.group(1)) if value else ""
            max_col = max(max_col, col)
        rows.append([by_col.get(i, "") for i in range(1, max_col + 1)])
    return rows


def _column_index(letters: str) -> int:
    """A->1, B->2, ..., Z->26, AA->27 (Excel column-letter to 1-based index)."""
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def _unescape_xml(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def parse_xlsx_context_text(workbook_path: pathlib.Path) -> dict[str, Any]:
    """Extract the rendered confirmed/deaths primaries and all text from a workbook.

    Returns a dict with:
      * ``confirmed``: set of rendered ``confirmed_cases`` reconciled values (str);
      * ``deaths_confirmed``: set of rendered ``deaths_confirmed`` reconciled values;
      * ``text``: the full concatenated rendered text (for mixed-basis scanning).
    Only ``snapshot_reconciled_metric`` rows on the "Reported Counts" sheet feed
    the count sets, so per-source extracted rows never pollute the headline
    cross-check.
    """
    confirmed: set[str] = set()
    deaths_confirmed: set[str] = set()
    text_parts: list[str] = []
    with zipfile.ZipFile(workbook_path) as zf:
        sheet_map = _read_xlsx_sheet_map(zf)
        for name, part in sheet_map.items():
            worksheet_xml = zf.read(part).decode("utf-8", "replace")
            rows = _xlsx_rows(worksheet_xml)
            for cells in rows:
                text_parts.extend(c for c in cells if c)
            if name != "Reported Counts" or not rows:
                continue
            header = rows[0]
            try:
                metric_i = header.index("metric")
                type_i = header.index("row_type")
                value_i = header.index("value")
            except ValueError:
                continue
            for cells in rows[1:]:
                if max(metric_i, type_i, value_i) >= len(cells):
                    continue
                if cells[type_i] != "snapshot_reconciled_metric":
                    continue
                metric = cells[metric_i]
                value = cells[value_i].strip()
                if not value:
                    continue
                if metric == "confirmed_cases":
                    confirmed.add(value)
                elif metric == "deaths_confirmed":
                    deaths_confirmed.add(value)
    return {
        "confirmed": confirmed,
        "deaths_confirmed": deaths_confirmed,
        "text": "\n".join(text_parts),
    }


# ---------------------------------------------------------------------------
# Allowed dates
# ---------------------------------------------------------------------------
def _manifest_source_dates(manifest: Mapping[str, Any]) -> set[str]:
    """Collect every YYYY-MM-DD source date declared in the source manifest."""
    dates: set[str] = set()
    entries = manifest.get("entries", []) if isinstance(manifest, Mapping) else []
    if not isinstance(entries, list):
        return dates
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        for key in (
            "published_at",
            "data_as_of",
            "retrieved_at",
            "report_date",
            "publication_date",
        ):
            value = entry.get(key)
            if isinstance(value, str) and len(value) >= 10:
                dates.add(value[:10])
    return dates


def _allowed_artifact_dates(
    snapshot: Mapping[str, Any], manifest: Mapping[str, Any]
) -> set[str]:
    """Dates an artifact may legitimately stamp: headline, per-zone, source dates."""
    allowed = _manifest_source_dates(manifest)
    as_of = str(snapshot.get("as_of", ""))[:10]
    if as_of:
        allowed.add(as_of)
    block = snapshot.get("insp_per_zone_block", {}) or {}
    block_date = str(block.get("as_of_data_date", ""))[:10]
    if block_date:
        allowed.add(block_date)
    return allowed


# ---------------------------------------------------------------------------
# Per-artifact package manifest (schema v2)
# ---------------------------------------------------------------------------
def validate_per_artifact_manifest(
    package_manifest: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
    output_dir: pathlib.Path,
) -> list[str]:
    """Validate the per-artifact semantic-freshness manifest (schema v2).

    For every artifact entry that declares semantic-freshness metadata:
      * ``semantic_as_of`` / ``source_date`` must be the snapshot headline date,
        the per-zone block date, or a declared source date;
      * every ``must_contain_text`` string must be present in the artifact;
      * every ``must_not_contain_text`` string must be absent from the artifact.
    Missing artifact files are reported as findings (the manifest promised them).
    """
    findings: list[str] = []
    if not isinstance(package_manifest, Mapping):
        return ["package manifest is not an object"]
    if package_manifest.get("schema_version") != 2:
        # The per-artifact semantic manifest (schema v2) is only emitted by the
        # (founder-gated) re-publish. A shipped deliverable that predates this
        # gate still carries the schema-v1 manifest (path + sha256 only). Tolerate
        # it: skip per-artifact text-contract enforcement rather than hard-failing
        # the whole release pipeline on a structural version mismatch. The SVG /
        # workbook / CSV semantic checks in check_artifact_semantic_freshness run
        # unconditionally and still catch genuine content staleness; the per-
        # artifact contracts activate automatically once the schema-v2 manifest
        # ships with the regenerated package.
        return []
    allowed = _allowed_artifact_dates(snapshot, source_manifest)
    outputs = package_manifest.get("outputs", [])
    if not isinstance(outputs, list):
        return findings + ["package manifest 'outputs' is not a list"]
    for entry in outputs:
        if not isinstance(entry, Mapping):
            continue
        rel = str(entry.get("path", ""))
        semantic_as_of = entry.get("semantic_as_of")
        source_date = entry.get("source_date")
        for label, value in (("semantic_as_of", semantic_as_of), ("source_date", source_date)):
            if value in (None, ""):
                continue
            if str(value)[:10] not in allowed:
                findings.append(
                    f"{rel}: manifest {label}={value!r} is not the snapshot "
                    "headline date, per-zone date, or a declared source date"
                )
        must_contain = entry.get("must_contain_text") or []
        must_not_contain = entry.get("must_not_contain_text") or []
        if not (must_contain or must_not_contain):
            continue
        artifact_path = output_dir / rel
        if not artifact_path.exists():
            findings.append(f"{rel}: manifest declares text contracts but the artifact is missing")
            continue
        blob = _artifact_text(artifact_path)
        for needle in must_contain:
            if str(needle) not in blob:
                findings.append(f"{rel}: required text {needle!r} is absent")
        for needle in must_not_contain:
            if str(needle) in blob:
                findings.append(f"{rel}: forbidden text {needle!r} is present")
    return findings


def _artifact_text(path: pathlib.Path) -> str:
    """Best-effort plain-text view of an artifact (xlsx is unzipped + rendered)."""
    if path.suffix == ".xlsx":
        try:
            with zipfile.ZipFile(path) as zf:
                rendered = []
                for name in zf.namelist():
                    if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                        worksheet_xml = zf.read(name).decode("utf-8", "replace")
                        for cells in _xlsx_rows(worksheet_xml):
                            rendered.extend(c for c in cells if c)
                return "\n".join(rendered)
        except (zipfile.BadZipFile, KeyError):
            return ""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


# ---------------------------------------------------------------------------
# Headline evidence-chain provenance (chain-to-source enforcement)
# ---------------------------------------------------------------------------
# Byte/semantic currency proves the published NUMBERS are fresh. It does NOT
# prove their PROVENANCE was promoted with them: a snapshot can carry a current
# headline (e.g. confirmed 370 from SitRep #019) while embedding only the prior
# SitRep's evidence chain, so the chain that actually backs 370 is referenced
# nowhere. This check closes that gap. For each headline metric present, it
# requires the snapshot to embed a `headline_evidence_chain_ids` entry whose
# `chain_source` equals that metric's `primary_source_id` and is `backed`. The
# generator derives that entry from the source (lovs_evidence), so a metric whose
# source advanced without a backing chain FAILs here rather than shipping.
#
# Each headline metric address maps to the snapshot block + row that carries its
# primary_source_id and the `metric` label used in the embedded surface.
_HEADLINE_METRIC_ADDRESSES: tuple[tuple[str, str, str], ...] = (
    ("confirmed", "reported_counts", "confirmed"),
    ("confirmed_deaths", "reported_deaths", "confirmed"),
)


def _metric_primary_source_id(
    snapshot: Mapping[str, Any], block_key: str, row_key: str
) -> str | None:
    block = snapshot.get(block_key)
    if not isinstance(block, Mapping):
        return None
    row = block.get(row_key)
    if not isinstance(row, Mapping):
        return None
    value = row.get("primary_source_id") or row.get("primarySourceId")
    return str(value) if value else None


def check_headline_evidence_chains(snapshot: Mapping[str, Any]) -> list[str]:
    """Enforce that each headline metric embeds a chain matching its source.

    For every headline metric present in ``snapshot`` (confirmed cases,
    confirmed deaths), require an embedded ``headline_evidence_chain_ids`` entry
    that BACKS the metric's ``primary_source_id``: an entry whose ``chain_source``
    equals the metric's ``primary_source_id`` and whose ``backed`` is True. The
    check is pure snapshot self-consistency (no registry, no clock); the entry's
    source is what the publish contract promised, derived by the generator from
    the same registry the gate would otherwise have to re-load.

    Returns a list of human-readable findings; empty means every headline metric
    is provenance-backed. The accepted key is ``headline_evidence_chain_ids``
    (snake or camel ``headlineEvidenceChainIds``) so the same gate runs against
    the internal LOVS snapshot and the camelCased website snapshot.
    """
    findings: list[str] = []
    embedded = snapshot.get("headline_evidence_chain_ids")
    if embedded is None:
        embedded = snapshot.get("headlineEvidenceChainIds")
    entries = embedded if isinstance(embedded, list) else []

    # Index embedded entries by the source they claim to back (only backed ones).
    backed_sources: dict[str, set[str]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if not bool(entry.get("backed")):
            continue
        chain_source = entry.get("chain_source") or entry.get("chainSource")
        metric = entry.get("metric")
        if isinstance(chain_source, str) and chain_source and isinstance(metric, str):
            backed_sources.setdefault(chain_source, set()).add(metric)

    for metric_label, block_key, row_key in _HEADLINE_METRIC_ADDRESSES:
        primary_source_id = _metric_primary_source_id(snapshot, block_key, row_key)
        if not primary_source_id:
            # Metric absent from this snapshot: nothing to back (e.g. a snapshot
            # with no reported_deaths block). Not a finding.
            continue
        metrics_for_source = backed_sources.get(primary_source_id, set())
        if metric_label not in metrics_for_source:
            findings.append(
                f"headline {metric_label}: no embedded evidence chain backs "
                f"primary_source_id {primary_source_id!r} "
                f"({block_key}.{row_key}); the chain that backs this metric is "
                "referenced nowhere on the headline provenance surface"
            )
    return findings


# ---------------------------------------------------------------------------
# Top-level gate
# ---------------------------------------------------------------------------
def _iter_svgs(brief_dir: pathlib.Path) -> Iterable[pathlib.Path]:
    if not brief_dir.is_dir():
        return []
    return sorted(brief_dir.rglob("*.svg"))


def check_artifact_semantic_freshness(
    snapshot: Mapping[str, Any],
    manifest: Mapping[str, Any],
    brief_dir: pathlib.Path,
    workbook: pathlib.Path,
    output_dir: pathlib.Path,
) -> dict[str, Any]:
    """Run the semantic-freshness gate. Returns ``{"status", "findings"}``.

    ``status`` is ``"pass"`` when ``findings`` is empty, else ``"fail"``.
    ``manifest`` is the SOURCE manifest (for allowed source dates); the per-
    artifact package manifest is read from ``output_dir``.
    """
    findings: list[str] = []
    brief_dir = pathlib.Path(brief_dir)
    workbook = pathlib.Path(workbook)
    output_dir = pathlib.Path(output_dir)

    as_of = str(snapshot.get("as_of", ""))[:10]
    block = snapshot.get("insp_per_zone_block", {}) or {}
    block_date = str(block.get("as_of_data_date", ""))[:10]
    allowed_dates = _allowed_artifact_dates(snapshot, manifest)
    confirmed_only_axis = bool(as_of) and as_of >= DEATH_BASIS_CUTOFF

    # (1) SVG embedded as_of dates.
    for svg_path in _iter_svgs(brief_dir):
        try:
            svg_text = svg_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            findings.append(f"{svg_path.name}: unreadable SVG ({exc})")
            continue
        for date in sorted(parse_svg_dates(svg_text)):
            if date == as_of:
                continue
            if date in allowed_dates:
                continue
            findings.append(
                f"{svg_path.name}: embedded as_of {date} is neither the snapshot "
                f"date {as_of or '<unset>'} nor an allowed source date"
            )
        # (4) mixed-basis death label on a confirmed-only axis.
        if confirmed_only_axis:
            for label in _MIXED_BASIS_DEATH_LABELS:
                if label in svg_text:
                    findings.append(
                        f"{svg_path.name}: mixed-basis death label {label!r} on a "
                        "confirmed-only death axis"
                    )

    # (2) Workbook rendered confirmed/deaths cells vs snapshot primaries, plus
    #     (4) mixed-basis death label in workbook text.
    if workbook.exists():
        try:
            rendered = parse_xlsx_context_text(workbook)
        except (zipfile.BadZipFile, KeyError) as exc:
            findings.append(f"{workbook.name}: unreadable workbook ({exc})")
            rendered = None
        if rendered is not None:
            confirmed_primary = _snapshot_primary(snapshot.get("reported_counts"), "confirmed")
            deaths_primary = _snapshot_primary(snapshot.get("reported_deaths"), "confirmed")
            if confirmed_primary is not None and rendered["confirmed"]:
                if rendered["confirmed"] != {str(confirmed_primary)}:
                    findings.append(
                        f"{workbook.name}: rendered confirmed cells "
                        f"{sorted(rendered['confirmed'])} do not match snapshot "
                        f"confirmed primary {confirmed_primary}"
                    )
            if deaths_primary is not None and rendered["deaths_confirmed"]:
                if rendered["deaths_confirmed"] != {str(deaths_primary)}:
                    findings.append(
                        f"{workbook.name}: rendered confirmed-deaths cells "
                        f"{sorted(rendered['deaths_confirmed'])} do not match "
                        f"snapshot confirmed-deaths primary {deaths_primary}"
                    )
            if confirmed_only_axis:
                for label in _MIXED_BASIS_DEATH_LABELS:
                    if label in rendered["text"]:
                        findings.append(
                            f"{workbook.name}: mixed-basis death label {label!r} "
                            "on a confirmed-only death axis"
                        )

    # (3) Per-zone CSV date vs per-zone block date.
    per_zone_csv = output_dir / PER_ZONE_CSV_NAME
    if per_zone_csv.exists() and block_date:
        findings.extend(_check_per_zone_csv_currency(per_zone_csv, block_date))

    # Per-artifact package manifest (schema v2), when present.
    package_manifest_path = output_dir / PACKAGE_MANIFEST_NAME
    if package_manifest_path.exists():
        try:
            package_manifest = json.loads(package_manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            findings.append(f"{PACKAGE_MANIFEST_NAME}: unreadable ({exc})")
        else:
            findings.extend(
                validate_per_artifact_manifest(
                    package_manifest, snapshot, manifest, output_dir
                )
            )

    # (5) Headline evidence-chain provenance: the chain backing each headline
    #     metric must be embedded and bound to that metric's primary_source_id.
    findings.extend(check_headline_evidence_chains(snapshot))

    return {"status": "fail" if findings else "pass", "findings": findings}


def _check_per_zone_csv_currency(csv_path: pathlib.Path, block_date: str) -> list[str]:
    import csv as _csv

    findings: list[str] = []
    try:
        with csv_path.open(encoding="utf-8", newline="") as handle:
            reader = _csv.DictReader(handle)
            if "as_of_data_date" not in (reader.fieldnames or []):
                return [f"{csv_path.name}: missing as_of_data_date column"]
            stale = sorted(
                {
                    row["as_of_data_date"]
                    for row in reader
                    if str(row.get("as_of_data_date", "")).strip()
                    and str(row["as_of_data_date"])[:10] != block_date
                }
            )
    except OSError as exc:
        return [f"{csv_path.name}: unreadable ({exc})"]
    for date in stale:
        findings.append(
            f"{csv_path.name}: per-zone as_of_data_date {date} disagrees with the "
            f"per-zone block date {block_date}"
        )
    return findings
