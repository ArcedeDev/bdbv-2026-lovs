#!/usr/bin/env python3
"""Regenerate the BDBV 2026 surveillance brief and its visuals from frozen inputs.

Inputs:
 - ``data/live-bdbv-2026-output.json``: Stage Two pipeline output (visibility,
   transmission, corridors, Mode B hypothesis IDs).
 - ``lovs/`` package: methodology modules. Mode A v1 and v2 are re-computed
   live for verifiable reproducibility (deterministic, seed 20140323).

Outputs:
 - ``brief/brief.html``: single-page WHO-readable brief (A4, print-clean).
 - ``brief/visuals/visibility_gap.svg``: under-ascertainment posterior bar.
 - ``brief/visuals/detection_depth.svg``: generations-before-detection.
 - ``brief/visuals/corridor_risk.svg``: top corridor watch list.
 - ``brief/visuals/pre_registration_timeline.svg``: declaration to resolution.
 - ``deliverables/brief.pdf``: print rendering if headless Chrome is available.

The brief and the visuals are byte-deterministic across runs from the same
inputs. No clock-derived state, no network, no randomness in the SVG path.
"""
from __future__ import annotations

import html
import json
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any


REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DATA_DIR = REPO_ROOT / "data"
BRIEF_DIR = REPO_ROOT / "brief"
VISUALS_DIR = BRIEF_DIR / "visuals"
DELIVERABLES_DIR = REPO_ROOT / "deliverables"

PIPELINE_OUTPUT_PATH = DATA_DIR / "live-bdbv-2026-output.json"
MANIFEST_PATH = DATA_DIR / "bundibugyo-2026" / "manifest.json"
WA_SUBSTRATE_PATH = DATA_DIR / "west-africa-prefecture-weekly.json"
COVARIATES_WA_PATH = DATA_DIR / "covariates-wa-2014.json"
COVARIATES_WA_V3_PATH = DATA_DIR / "covariates-wa-2014-v3.json"

REPO_URL = "https://github.com/ArcedeDev/bdbv-2026-lovs"

# Color palette: print-friendly, accessible.
COLOR_PRIMARY = "#1a3552"     # dark navy
COLOR_ACCENT = "#c66020"      # warm orange (accent / 96% probability)
COLOR_INK = "#1a1a1a"          # near-black text
COLOR_GRAY = "#7d7d7d"         # axis / secondary text
COLOR_LIGHT = "#e5e5e5"        # rule / subtle border
COLOR_RISK = "#4a7da3"         # mid-blue (risk bars)
COLOR_SHADE = "#9bbcd4"        # lighter blue (interval shading)
COLOR_WHITE = "#ffffff"


# ----- Data loaders -----


def load_pipeline_output() -> dict[str, Any]:
    with open(PIPELINE_OUTPUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_manifest_content(source_id: str) -> dict[str, Any]:
    """Return normalized manifest content for a canonical source id."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for entry in manifest.get("entries", []):
        entry_id = str(entry.get("source_id", ""))
        canonical_id = entry_id.removesuffix("-live")
        if canonical_id == source_id:
            content = entry.get("normalized_content") or {}
            if isinstance(content, dict):
                return content
    return {}


def _count(reported_counts: dict[str, Any], category: str, field: str) -> int:
    """Required integer count from the pipeline's reported_counts; fails loudly.

    No silent fallback to a stale literal: a missing category, missing field, or
    non-integer value means the pipeline output is malformed, so the brief raises
    instead of rendering a guessed number. Mirrors sync_to_website._mf.
    """
    if category not in reported_counts:
        raise ValueError(f"reported_counts has no category '{category}'")
    block = reported_counts[category]
    if field not in block:
        raise ValueError(f"reported_counts['{category}'] lacks field '{field}'")
    value = block[field]
    if not isinstance(value, int):
        raise ValueError(
            f"reported_counts['{category}']['{field}'] is not an int: {value!r}"
        )
    return value


@dataclass(frozen=True)
class ModeAResult:
    label: str
    brier: float
    wis: float
    ece: float


def compute_mode_a() -> tuple[ModeAResult, ModeAResult, ModeAResult]:
    """Run Mode A v1, v2, v3 backtests live for verifiable reproducibility."""
    from lovs import lovs_validation
    v1 = lovs_validation.mode_a_backtest_wa_2014(WA_SUBSTRATE_PATH)
    v2 = lovs_validation.mode_a_backtest_wa_2014_t3(WA_SUBSTRATE_PATH, COVARIATES_WA_PATH)
    v3 = lovs_validation.mode_a_backtest_wa_2014_t3(WA_SUBSTRATE_PATH, COVARIATES_WA_V3_PATH)
    return (
        ModeAResult("v1 (no T3)", v1.next_zone_brier, v1.next_zone_wis, v1.expected_calibration_error),
        ModeAResult("v2 (country T3)", v2.next_zone_brier, v2.next_zone_wis, v2.expected_calibration_error),
        ModeAResult("v3 (per-prefecture T3)", v3.next_zone_brier, v3.next_zone_wis, v3.expected_calibration_error),
    )


@dataclass(frozen=True)
class RobustnessSummary:
    auc_model: float
    auc_distance: float
    auc_source_load: float
    bss: float
    bss_ci_lo: float
    bss_ci_hi: float


def compute_robustness() -> RobustnessSummary:
    """Sparse-window rolling-origin robustness (no-context) for the brief.

    Computes only the headline (sparse) window for speed; the full pre-registered
    grid, baselines, and per-window CIs are reproducible via robustness_backtest.py.
    """
    from lovs import lovs_validation as _v
    rep = _v.rolling_origin_robustness(
        WA_SUBSTRATE_PATH,
        (("no-context", None),),
        windows=(("sparse W3,5,7,9,11", (3, 5, 7, 9, 11)),),
    )
    c = rep.cells[0]
    return RobustnessSummary(
        auc_model=c.auc_model,
        auc_distance=c.auc_distance_only,
        auc_source_load=c.auc_source_load_only,
        bss=c.brier_skill_score,
        bss_ci_lo=c.brier_skill_score_ci[0],
        bss_ci_hi=c.brier_skill_score_ci[1],
    )


# ----- Formatting helpers -----


def _signed_delta(d: float) -> str:
    """Format a small float diff for tabular display.

    Rounds to four decimal places first, then adds 0.0 to coerce both
    IEEE-754 negative zero and tiny negative floats (e.g. -1e-17 from float
    subtraction noise) to positive zero. Prevents misleading "-0.0000"
    sign display when two metrics are byte-identical at the display
    precision.
    """
    rounded = round(d, 4) + 0.0
    return f"{rounded:+.4f}"


# ----- SVG helpers -----


def svg_header(width: int, height: int, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" '
        f'role="img" aria-labelledby="t">'
        f'<title id="t">{html.escape(title)}</title>'
    )


def svg_footer() -> str:
    return "</svg>"


def svg_text(x: float, y: float, text: str, *, size: int = 11, color: str = COLOR_INK,
             anchor: str = "start", weight: str = "normal") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" '
        f'font-family="Helvetica,Arial,sans-serif" '
        f'font-size="{size}" fill="{color}" font-weight="{weight}" '
        f'text-anchor="{anchor}">{html.escape(text)}</text>'
    )


def svg_rect(x: float, y: float, w: float, h: float, fill: str, *,
             stroke: str = "none", stroke_width: float = 0) -> str:
    sw = f' stroke="{stroke}" stroke-width="{stroke_width}"' if stroke != "none" else ""
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}"{sw}/>'


def svg_line(x1: float, y1: float, x2: float, y2: float,
             stroke: str = COLOR_GRAY, width: float = 1.0) -> str:
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{stroke}" stroke-width="{width}"/>'
    )


def svg_circle(cx: float, cy: float, r: float, fill: str, stroke: str = "none") -> str:
    sw = f' stroke="{stroke}" stroke-width="1.5"' if stroke != "none" else ""
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"{sw}/>'


# ----- Visual 1: visibility gap (under-ascertainment posterior) -----


def render_visibility_gap_svg(visibility: dict[str, Any]) -> str:
    width, height = 600, 100
    margin_left, margin_right = 40, 40
    bar_y = 48
    bar_h = 22
    track_width = width - margin_left - margin_right

    lo, hi = visibility["reporting_completeness_50"]
    # interval is in [0,1] (proportion of cases visible)
    lo_x = margin_left + lo * track_width
    hi_x = margin_left + hi * track_width

    parts = [svg_header(width, height, "Visibility gap: reporting completeness 50% interval")]
    parts.append(svg_text(margin_left, 18,
                          f"50% uncertainty interval: {lo*100:.1f}% to {hi*100:.1f}% of cases visible in the public picture.",
                          size=10, color=COLOR_INK))
    # track (0% to 100%)
    parts.append(svg_rect(margin_left, bar_y, track_width, bar_h, COLOR_LIGHT))
    # shaded interval
    parts.append(svg_rect(lo_x, bar_y, hi_x - lo_x, bar_h, COLOR_SHADE))
    # endpoints
    parts.append(svg_line(lo_x, bar_y - 4, lo_x, bar_y + bar_h + 4, COLOR_PRIMARY, 1.5))
    parts.append(svg_line(hi_x, bar_y - 4, hi_x, bar_y + bar_h + 4, COLOR_PRIMARY, 1.5))
    # numeric labels above interval
    parts.append(svg_text(lo_x, bar_y - 8, f"{lo*100:.0f}%", size=10, color=COLOR_PRIMARY,
                          anchor="middle", weight="bold"))
    parts.append(svg_text(hi_x, bar_y - 8, f"{hi*100:.0f}%", size=10, color=COLOR_PRIMARY,
                          anchor="middle", weight="bold"))
    # axis ticks
    for t in (0, 25, 50, 75, 100):
        tx = margin_left + (t / 100.0) * track_width
        parts.append(svg_line(tx, bar_y + bar_h, tx, bar_y + bar_h + 4, COLOR_GRAY, 1))
        parts.append(svg_text(tx, bar_y + bar_h + 16, f"{t}%", size=9, color=COLOR_GRAY,
                              anchor="middle"))
    parts.append(svg_footer())
    return "".join(parts)


# ----- Visual 2: detection depth (generations before detection) -----


def render_detection_depth_svg(transmission: dict[str, Any]) -> str:
    width, height = 600, 150
    margin_left, margin_right = 60, 40
    base_y = 118
    chart_width = width - margin_left - margin_right
    chart_height = 80

    gens = transmission["generations"]
    def prob_at_least(threshold: int) -> float:
        return sum(p for k, p in gens.items() if int(k) >= threshold)

    # categories: cumulative probabilities, consistent with the chart title.
    categories = [
        ("1+ generation", prob_at_least(1)),
        ("2+ generations", prob_at_least(2)),
        ("3+ generations", prob_at_least(3)),
    ]
    n = len(categories)
    bar_w = chart_width / (n * 2)
    gap = bar_w

    parts = [svg_header(width, height, "Detection generation depth posterior")]
    parts.append(svg_text(margin_left, 18,
                          "P(at least N transmission generations occurred before detection)",
                          size=10, color=COLOR_INK))

    # y-axis
    parts.append(svg_line(margin_left, base_y - chart_height, margin_left, base_y, COLOR_GRAY, 1))
    parts.append(svg_line(margin_left, base_y, margin_left + chart_width, base_y, COLOR_GRAY, 1))
    # y-axis ticks at 0, 25, 50, 75, 100
    for pct in (0, 25, 50, 75, 100):
        ty = base_y - (pct / 100.0) * chart_height
        parts.append(svg_line(margin_left - 4, ty, margin_left, ty, COLOR_GRAY, 1))
        parts.append(svg_text(margin_left - 8, ty + 3, f"{pct}%",
                              size=9, color=COLOR_GRAY, anchor="end"))

    # bars
    for i, (label, p) in enumerate(categories):
        bx = margin_left + gap / 2 + i * (bar_w + gap)
        bh = p * chart_height
        # highlight the 3+ generations bar
        bar_color = COLOR_ACCENT if i == 2 else COLOR_PRIMARY
        parts.append(svg_rect(bx, base_y - bh, bar_w, bh, bar_color))
        # value label
        parts.append(svg_text(bx + bar_w / 2, base_y - bh - 6,
                              f"{p*100:.1f}%",
                              size=11, color=bar_color, weight="bold", anchor="middle"))
        # category label
        parts.append(svg_text(bx + bar_w / 2, base_y + 18, label,
                              size=10, color=COLOR_INK, anchor="middle"))

    parts.append(svg_footer())
    return "".join(parts)


# ----- Visual 3: corridor watch list -----


def render_corridor_risk_svg(corridors: list[dict[str, Any]], top_n: int = 6) -> str:
    # Already sorted in the pipeline by adj_upper_50 descending.
    items = corridors[:top_n]
    width = 600
    row_h = 20
    header_h = 36
    footer_h = 18
    height = header_h + row_h * len(items) + footer_h

    margin_left = 150
    margin_right = 90
    chart_width = width - margin_left - margin_right

    # x-axis range chosen so the largest value sits at <=80% of chart_width,
    # leaving room for the numeric label to the right of the bar.
    max_p = max(c["risk_adj_upper_50"] for c in items)
    x_max = max(0.10, max_p / 0.80)

    parts = [svg_header(width, height, "Corridor watch list: visibility-adjusted 30-day next-zone risk")]
    parts.append(svg_text(20, 18,
                          f"Top {len(items)} inter-zone corridors by visibility-adjusted upper 50% risk, 30-day horizon",
                          size=10, color=COLOR_INK))

    # x-axis at top of bars
    axis_y = header_h - 2
    parts.append(svg_line(margin_left, axis_y, margin_left + chart_width, axis_y, COLOR_GRAY, 1))
    # x-axis ticks
    tick_count = 5
    for k in range(tick_count + 1):
        frac = k / tick_count
        tx = margin_left + frac * chart_width
        val = frac * x_max
        parts.append(svg_line(tx, axis_y - 3, tx, axis_y, COLOR_GRAY, 1))
        parts.append(svg_text(tx, axis_y - 5, f"{val*100:.0f}%", size=8, color=COLOR_GRAY,
                              anchor="middle"))

    # rows
    for i, c in enumerate(items):
        ry = header_h + i * row_h + row_h / 2
        label = f"{c['source']} → {c['target']}"
        parts.append(svg_text(margin_left - 8, ry + 3, label,
                              size=10, color=COLOR_INK, anchor="end"))
        lo = c["risk_adj_lower_50"]
        hi = c["risk_adj_upper_50"]
        lo_x = margin_left + (lo / x_max) * chart_width
        hi_x = margin_left + (hi / x_max) * chart_width
        # bar (interval)
        bar_h = row_h * 0.5
        parts.append(svg_rect(lo_x, ry - bar_h / 2, max(2.0, hi_x - lo_x), bar_h, COLOR_RISK))
        # range label
        parts.append(svg_text(hi_x + 6, ry + 3, f"[{lo*100:.1f}%, {hi*100:.1f}%]",
                              size=9, color=COLOR_GRAY))

    parts.append(svg_text(20, height - 4,
                          "Read as a watch list, not a ranking decisive enough to direct deployment.",
                          size=8, color=COLOR_GRAY))
    parts.append(svg_footer())
    return "".join(parts)


# ----- Visual 5: per-zone snapshot (Plan A 2026-05-28, spec section 5.1) -----


def render_per_zone_snapshot_svg(insp_per_zone_block: dict[str, Any] | None) -> str:
    """Render the INSP per-zone confirmed cases and confirmed deaths surface.

    Each row is one LOVS source zone. Bars show confirmed cases with a
    confirmed_deaths tick in a common scale. Sibling-HZ clusters (spec section
    6.9, e.g. karisimbi-cod + goma-cod) are labelled with a leader tag so
    readers see the agglomeration relationship. The cumulative surface carries
    laboratory-confirmed cases and confirmed deaths only.
    """
    if not insp_per_zone_block:
        return svg_header(600, 80, "INSP per-zone snapshot (no data this cycle)") + (
            svg_text(20, 40, "No INSP per-zone block in this snapshot; rendering skipped.",
                     size=10, color=COLOR_GRAY)
        ) + svg_footer()
    by_zone = insp_per_zone_block.get("by_lovs_zone", {}) or {}
    width = 720
    row_h = 22
    header_h = 50
    footer_h = 28
    height = header_h + row_h * len(by_zone) + footer_h
    margin_left = 150
    margin_right = 90
    chart_width = width - margin_left - margin_right
    # Use the max confirmed as the scale anchor; confirmed cases are the
    # cumulative metric carried on the per-zone surface.
    max_conf = max((int(z.get("confirmed", 0)) for z in by_zone.values()), default=1)
    x_max = max(10, max_conf)

    parts = [svg_header(
        width,
        height,
        "INSP per-zone snapshot, INRB-UMIE consortium release at as_of "
        f"{insp_per_zone_block.get('as_of_data_date', '')}",
    )]
    parts.append(svg_text(
        20, 18,
        "Confirmed cases (orange) and confirmed deaths (black tick) per LOVS "
        "source zone; sibling-HZ clusters tagged.",
        size=9, color=COLOR_INK,
    ))
    # Tick labels at top of the chart area.
    axis_y = header_h - 8
    parts.append(svg_line(margin_left, axis_y, margin_left + chart_width, axis_y, COLOR_GRAY, 1))
    tick_count = 5
    for k in range(tick_count + 1):
        frac = k / tick_count
        tx = margin_left + frac * chart_width
        parts.append(svg_line(tx, axis_y - 3, tx, axis_y, COLOR_GRAY, 1))
        parts.append(svg_text(
            tx, axis_y - 5, f"{int(frac * x_max)}",
            size=8, color=COLOR_GRAY, anchor="middle",
        ))

    for i, zone_id in enumerate(sorted(by_zone)):
        row = by_zone[zone_id]
        ry = header_h + i * row_h + row_h / 2
        sibling = row.get("sibling_hz_cluster") or ""
        label = zone_id + (f"  (cluster: {sibling})" if sibling else "")
        parts.append(svg_text(margin_left - 8, ry + 3, label,
                              size=10, color=COLOR_INK, anchor="end"))
        conf = int(row.get("confirmed", 0))
        cdth = int(row.get("confirmed_deaths", 0))
        bar_h = row_h * 0.5
        # confirmed bar
        conf_w = (conf / x_max) * chart_width
        parts.append(svg_rect(margin_left, ry - bar_h / 2, max(1.0, conf_w), bar_h, COLOR_ACCENT))
        # confirmed_deaths tick (vertical line at the deaths value)
        if cdth > 0:
            dx = margin_left + (cdth / x_max) * chart_width
            parts.append(svg_line(dx, ry - bar_h / 2 - 2, dx, ry + bar_h / 2 + 2,
                                  COLOR_INK, 2))
        # Row-end numeric summary
        parts.append(svg_text(
            margin_left + chart_width + 6, ry + 3,
            f"c={conf} d={cdth}",
            size=9, color=COLOR_GRAY,
        ))

    parts.append(svg_text(
        20, height - 8,
        "Sibling-HZ clusters: spec section 6.9 doctrine. Confirmed deaths trail "
        "by 1-3 weeks per INRB clinical review queue (spec section 2.3).",
        size=8, color=COLOR_GRAY,
    ))
    parts.append(svg_footer())
    return "".join(parts)


# ----- Visual 6: per-zone ascertainment bands (spec section 5.2) -----


def render_ascertainment_band_per_zone_svg(
    per_zone_bands: dict[str, Any] | None,
) -> str:
    """Render per-LOVS-zone PCR-modulated ascertainment band ranges."""
    if not per_zone_bands:
        return (
            svg_header(600, 80, "Per-zone ascertainment bands (no data this cycle)")
            + svg_text(20, 40, "No per_zone_under_ascertainment_bands in this snapshot; rendering skipped.",
                       size=10, color=COLOR_GRAY)
            + svg_footer()
        )
    by_zone = per_zone_bands.get("by_lovs_zone", {}) or {}
    species_lo = float((per_zone_bands.get("species_default_band") or {}).get("lo", 0.3))
    species_hi = float((per_zone_bands.get("species_default_band") or {}).get("hi", 0.9))
    width = 720
    row_h = 18
    header_h = 56
    footer_h = 30
    height = header_h + row_h * len(by_zone) + footer_h
    margin_left = 150
    margin_right = 60
    chart_width = width - margin_left - margin_right
    x_max = 1.0

    parts = [svg_header(
        width,
        height,
        f"Per-zone diagnostic-access gap (PCR testing capacity): shadow surface, feeds no published count",
    )]
    parts.append(svg_text(
        20, 18,
        f"Diagnostic-access signal (PCR testing capacity per LOVS zone), not a burden modulator; "
        f"species default band ({species_lo:.2f}-{species_hi:.2f}) dashed. Feeds no published count.",
        size=9, color=COLOR_INK,
    ))
    # Species default reference shading
    sp_lo_x = margin_left + species_lo * chart_width
    sp_hi_x = margin_left + species_hi * chart_width
    axis_y = header_h - 10
    parts.append(svg_rect(sp_lo_x, axis_y, sp_hi_x - sp_lo_x,
                          header_h + row_h * len(by_zone), COLOR_LIGHT))
    # Axis ticks 0.0, 0.25, 0.5, 0.75, 1.0
    for k in range(5):
        frac = k / 4.0
        tx = margin_left + frac * chart_width
        parts.append(svg_line(tx, axis_y - 3, tx, axis_y, COLOR_GRAY, 1))
        parts.append(svg_text(
            tx, axis_y - 5, f"{frac:.2f}",
            size=8, color=COLOR_GRAY, anchor="middle",
        ))

    for i, zone_id in enumerate(sorted(by_zone)):
        row = by_zone[zone_id]
        ry = header_h + i * row_h + row_h / 2
        sibling = row.get("sibling_hz_cluster") or ""
        label = zone_id + (f"  (cluster: {sibling})" if sibling else "")
        parts.append(svg_text(margin_left - 8, ry + 3, label,
                              size=9, color=COLOR_INK, anchor="end"))
        lo = row.get("lo")
        hi = row.get("hi")
        if lo is None or hi is None:
            # species default fallback shown as full-range dashed
            sp_w = (species_hi - species_lo) * chart_width
            parts.append(svg_rect(sp_lo_x, ry - row_h * 0.3, sp_w, row_h * 0.6,
                                  COLOR_LIGHT))
            parts.append(svg_text(
                margin_left + chart_width + 4, ry + 3,
                "(species default)",
                size=8, color=COLOR_GRAY,
            ))
        else:
            lo_x = margin_left + float(lo) * chart_width
            hi_x = margin_left + float(hi) * chart_width
            parts.append(svg_rect(lo_x, ry - row_h * 0.3, max(1.0, hi_x - lo_x),
                                  row_h * 0.6, COLOR_RISK))
            parts.append(svg_text(
                margin_left + chart_width + 4, ry + 3,
                f"[{float(lo):.2f}, {float(hi):.2f}]",
                size=8, color=COLOR_GRAY,
            ))

    parts.append(svg_text(
        20, height - 8,
        "Shadow surface, not the primary model input until Plan C parallel-scoring graduation.",
        size=8, color=COLOR_GRAY,
    ))
    parts.append(svg_footer())
    return "".join(parts)


# ----- Visual 4: pre-registration timeline -----


def render_timeline_svg(pipeline: dict[str, Any]) -> str:
    width, height = 600, 110
    margin_left, margin_right = 50, 50
    line_y = 56
    track_width = width - margin_left - margin_right
    cal_blocks = pipeline.get("calibration_blocks", [])
    pinned_dates = sorted(block["pinned_at"] for block in cal_blocks) or [pipeline["as_of"][:10]]
    resolution_dates = sorted(block["resolves_at"][:10] for block in cal_blocks) or [pipeline["resolves_at"][:10]]
    latest_pin = pinned_dates[-1]
    latest_resolution = resolution_dates[-1]
    latest_pin_date = _long_date(latest_pin)
    resolution_date = _long_date(latest_resolution)

    declaration_day = date.fromisoformat("2026-05-15")
    latest_pin_day = date.fromisoformat(latest_pin)
    resolution_day = date.fromisoformat(latest_resolution)
    span_days = max(1, (resolution_day - declaration_day).days)

    # Declaration to final active-block resolution.
    declaration_x = margin_left
    registration_x = margin_left + ((latest_pin_day - declaration_day).days / span_days) * track_width
    resolution_x = margin_left + track_width

    parts = [svg_header(width, height, "Pre-registration timeline")]
    parts.append(svg_text(margin_left, 18,
                          "Pre-registration window for active calibration blocks",
                          size=10, color=COLOR_INK))

    # main line
    parts.append(svg_line(margin_left, line_y, margin_left + track_width, line_y, COLOR_GRAY, 2))

    # week ticks
    for d in range(0, span_days + 1, 7):
        tx = margin_left + (d / span_days) * track_width
        parts.append(svg_line(tx, line_y - 4, tx, line_y + 4, COLOR_LIGHT, 1))

    # markers: declaration label above; registration label below; resolution above.
    markers = [
        (declaration_x, "15 May 2026", "declared", COLOR_PRIMARY, "above"),
        (registration_x, latest_pin_date, "latest pin", COLOR_ACCENT, "below"),
        (resolution_x, resolution_date, "resolution", COLOR_PRIMARY, "above"),
    ]
    for mx, date_label, event_label, mc, position in markers:
        parts.append(svg_circle(mx, line_y, 5.0, mc))
        if position == "above":
            parts.append(svg_text(mx, line_y - 14, date_label, size=9, color=mc,
                                  weight="bold", anchor="middle"))
            parts.append(svg_text(mx, line_y - 26, event_label, size=8, color=COLOR_INK,
                                  anchor="middle"))
        else:
            parts.append(svg_text(mx, line_y + 18, date_label, size=9, color=mc,
                                  weight="bold", anchor="middle"))
            parts.append(svg_text(mx, line_y + 30, event_label, size=8, color=COLOR_INK,
                                  anchor="middle"))
    parts.append(svg_text(margin_left, height - 6,
                          "Forecasts scored at resolution against public WHO, Africa CDC, DRC MoH, Uganda MoH announcements.",
                          size=8, color=COLOR_GRAY))
    parts.append(svg_footer())
    return "".join(parts)


# ----- HTML brief -----


_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _long_date(iso: str) -> str:
    """Format an ISO date string as e.g. '20 May 2026'.

    Input-derived only: it parses the snapshot's own ``as_of`` timestamp, never
    the wall clock, so the brief stays byte-deterministic for a fixed snapshot.
    """
    year, month, day = iso[:10].split("-")
    return f"{int(day)} {_MONTHS[int(month) - 1]} {int(year)}"


def _us_date(iso: str) -> str:
    """Format an ISO date string as e.g. 'May 20, 2026' (US long form).

    Same input-derived contract as ``_long_date``; used where the brief prose
    uses the US comma form rather than the day-first form.
    """
    year, month, day = iso[:10].split("-")
    return f"{_MONTHS[int(month) - 1]} {int(day)}, {int(year)}"


def _day_month(iso: str) -> str:
    """Format an ISO date string as e.g. '20 May' (day and month, no year).

    Same input-derived contract as ``_long_date``; used where the brief prose
    refers to the snapshot's own date in day-month form without the year.
    """
    year, month, day = iso[:10].split("-")
    return f"{int(day)} {_MONTHS[int(month) - 1]}"


def render_html(pipeline: dict[str, Any], mode_a_v1: ModeAResult, mode_a_v2: ModeAResult,
                mode_a_v3: ModeAResult, robustness: RobustnessSummary,
                svgs: dict[str, str]) -> str:
    visibility = pipeline["visibility"]
    transmission = pipeline["transmission"]
    corridors = pipeline["corridors"]
    mode_b = pipeline["mode_b_hypotheses"]
    resolves_at = pipeline["resolves_at"]
    cal_clock = pipeline.get("calibration_clock", {})
    cal_blocks = pipeline.get("calibration_blocks", [])
    snapshot_date = _long_date(pipeline["as_of"])  # e.g. "20 May 2026", from the snapshot
    resolution_date = _long_date(resolves_at)  # e.g. "19 June 2026", from the ledger
    calibration_pinned_date = _long_date(
        cal_clock.get("pinned_at") or mode_b[0].get("pinned_at", pipeline["as_of"])
    )
    calibration_horizon_days = int(
        cal_clock.get("horizon_days") or mode_b[0].get("horizon_days", 30)
    )
    calibration_remaining_days = cal_clock.get("remaining_days")
    calibration_elapsed_days = cal_clock.get("elapsed_days")
    calibration_clock_sentence = (
        f"As of this {snapshot_date} snapshot, "
        f"{calibration_elapsed_days} day{' has' if calibration_elapsed_days == 1 else 's have'} "
        f"elapsed since the pin and "
        f"{calibration_remaining_days} day{' remains' if calibration_remaining_days == 1 else 's remain'} "
        f"until resolution."
        if calibration_remaining_days is not None and calibration_elapsed_days is not None
        else "The original horizon is measured from the pin date, not from the page date."
    )
    carried_block_count = sum(1 for block in cal_blocks if block.get("status") == "carried_forward")
    pinned_block_count = sum(1 for block in cal_blocks if block.get("status") == "pinned_in_this_snapshot")
    calibration_block_sentence = (
        f"This {snapshot_date} snapshot carries forward {carried_block_count} earlier "
        f"calibration block{'s' if carried_block_count != 1 else ''} unchanged and pins "
        f"{pinned_block_count} new designed block{'s' if pinned_block_count != 1 else ''} before publication. "
        "The new block samples the then-current watchlist across relative high/mid/low bands, "
        "cross-border and in-country corridors, and likely positive / likely negative controls. "
        "Those probabilities remain the original pre-committed values; the current "
        "May 22 watchlist is regenerated separately from the zone-attributed source-load vector."
        if carried_block_count and pinned_block_count
        else (
            f"This {snapshot_date} snapshot carries forward the {calibration_pinned_date} "
            "calibration block unchanged; it does not pin a new calibration block. "
            "Future snapshots can append new blocks with their own pin date, horizon, "
            "and resolution timestamp without moving this block."
            if carried_block_count
            else "Calibration commitments are block-scoped: each block has its own pin date, horizon, and resolution timestamp."
        )
    )
    snapshot_date_us = _us_date(pipeline["as_of"])  # e.g. "May 20, 2026" (US long form)
    snapshot_date_dm = _day_month(pipeline["as_of"])  # e.g. "20 May" (day-month, no year)
    as_of_iso = pipeline["as_of"][:10]  # e.g. "2026-05-20"
    resolves_iso = resolves_at[:10]  # e.g. "2026-06-19"
    calibration_pinned_iso = (
        cal_clock.get("pinned_at") or mode_b[0].get("pinned_at", as_of_iso)
    )

    # Reporting completeness is a fraction in [0, 1]; clamp before display so a
    # downstream pipeline that emits malformed values does not produce
    # negative-percent or >100% claims in the brief text.
    raw_lo = visibility["reporting_completeness_50"][0]
    raw_hi = visibility["reporting_completeness_50"][1]
    completeness_lo = max(0.0, min(100.0, raw_lo * 100))
    completeness_hi = max(0.0, min(100.0, raw_hi * 100))
    gen3_p = max(
        0.0,
        min(
            100.0,
            sum(p for k, p in transmission["generations"].items() if int(k) >= 3) * 100,
        ),
    )
    # Display figures are derived from reported_counts so the brief body tracks
    # the data layer and cannot silently go stale on the next dated snapshot.
    # Every current-state figure (the confirmed-deaths band, the primary
    # confirmed count, its DRC/Kampala split, the point-in-time operational
    # caseload, and the snapshot-dated trajectory endpoint) is derived here via
    # _count or _maybe, which fail loudly rather than falling back to a stale
    # literal. The two earlier trajectory anchors
    # kept as literals in the prose below (10 confirmed on 17 May per WHO PHEIC,
    # 30 on 19 May per ECDC) are frozen historical facts bound to past dates, and
    # 30 is not carried in reported_counts at all; a future refresh extends that
    # trajectory by hand rather than re-deriving those past points.
    reported_counts = pipeline["reported_counts"]
    reported_deaths = pipeline.get("reported_deaths", {}) or {}
    confirmed_primary = _count(reported_counts, "confirmed", "primary")

    # Post 2026-06-01 schema split. Prefer the new split keys when present;
    # fall back to the legacy single-bucket key if the snapshot still uses it
    # (back-compat for any consumer reading older snapshot JSON).
    def _maybe(rcounts: dict, key: str, field: str) -> int | None:
        block = rcounts.get(key)
        if not isinstance(block, dict):
            return None
        v = block.get(field)
        return int(v) if isinstance(v, int) else None

    confirmed_active_primary = _maybe(reported_counts, "confirmed_active", "primary")
    recovered_primary = _maybe(reported_counts, "recovered", "primary")
    deaths_confirmed_primary = _maybe(reported_deaths, "confirmed", "primary")

    # Operational status axis (point-in-time, national-only). These are the
    # suspected cases currently in the response pipeline at the snapshot date:
    # under investigation plus in isolation. This is a point-prevalence caseload,
    # NOT a cumulative case count, and it is never added to the confirmed total.
    # The cumulative epidemiological surface carries only confirmed cases and
    # confirmed deaths.
    op_under_investigation = _maybe(
        reported_counts, "suspected_under_investigation", "primary"
    )
    op_in_isolation = _maybe(reported_counts, "suspected_in_isolation", "primary")
    op_active_total = _maybe(reported_counts, "suspected_active", "primary")

    # Provenance: which fields are carried forward (LOCF) at this cycle.
    def _carry(block_dict: dict, key: str) -> tuple[str, str] | None:
        block = block_dict.get(key)
        if not isinstance(block, dict):
            return None
        from_date = str(block.get("carried_forward_from") or "")
        reason = str(block.get("carried_forward_reason") or "")
        return (from_date, reason) if from_date and reason else None

    # LOCF provenance is tracked only for cumulative metrics (confirmed cases,
    # active confirmed, recovered, confirmed deaths). The operational caseload is
    # a point-in-time snapshot, not a carried-forward cumulative series, so it is
    # excluded from the carry-forward footnote.
    locf_fields: list[tuple[str, str, str]] = []
    for key in ("confirmed", "confirmed_active", "recovered"):
        c = _carry(reported_counts, key)
        if c:
            locf_fields.append((key, c[0], c[1]))
    for key in ("confirmed",):
        c = _carry(reported_deaths, key)
        if c:
            locf_fields.append((f"deaths_{key}", c[0], c[1]))

    confirmed_source_id = str(reported_counts["confirmed"].get("primary_source_id") or "")
    confirmed_source_content = load_manifest_content(confirmed_source_id)
    confirmed_uganda = int(
        confirmed_source_content.get("cases_confirmed_uganda")
        or confirmed_source_content.get("cases_confirmed_uganda_imported")
        or 0
    )
    confirmed_drc = int(
        confirmed_source_content.get("cases_confirmed_drc")
        or max(0, confirmed_primary - confirmed_uganda)
    )
    confirmed_source_label = (
        "CDC Current Situation"
        if confirmed_source_id.startswith("cdc-current-situation")
        else "WHO Director-General remarks"
    )
    uganda_update = ""
    new_uganda = confirmed_source_content.get("new_confirmed_cases_uganda")
    if isinstance(new_uganda, int) and new_uganda > 0:
        uganda_update = (
            f" CDC also reports {new_uganda} additional Uganda cases linked "
            "to previously announced cases."
        )
    zone_attributed_counts = pipeline.get("zone_attributed_counts", {})
    zone_attributed_confirmed = sum(
        int(row.get("confirmed") or 0)
        for row in zone_attributed_counts.values()
        if isinstance(row, dict)
    )
    unallocated_confirmed = confirmed_primary - zone_attributed_confirmed
    source_zone_count = len(zone_attributed_counts) or len(pipeline.get("affected_zones", []))
    zone_source_ids = {
        str(row.get("source_id") or "")
        for row in zone_attributed_counts.values()
        if isinstance(row, dict) and row.get("source_id")
    }
    if zone_source_ids and all(
        source_id.startswith("drc-moh-epidemie-dashboard") for source_id in zone_source_ids
    ):
        source_zone_label = "DRC MoH source zones"
        source_vector_sentence = (
            "the DRC MoH SitRep MVE N 007/MVB_17/2026 PDF cumulative Table IV "
            f"reports {zone_attributed_confirmed} confirmed DRC cases across "
            f"{source_zone_count} DRC MoH source zones as of 21 May."
        )
    elif zone_source_ids and all(
        source_id.startswith("inrb-umie") for source_id in zone_source_ids
    ):
        source_zone_label = "INSP per-zone source zones"
        source_vector_sentence = (
            "the INRB-UMIE/INSP per-health-zone series (build-2026-05-28-bb8b7d5) "
            f"attributes {zone_attributed_confirmed} confirmed DRC cases across "
            f"{source_zone_count} monitored health zones as of 26 May."
        )
    else:
        source_zone_label = "WHO AFRO source zones"
        source_vector_sentence = (
            "WHO AFRO SitRep-01 reports "
            f"{zone_attributed_confirmed} confirmed DRC cases across "
            f"{source_zone_count} WHO AFRO source zones as of 18 May."
        )
    corridor_count = len(corridors)
    corridor_upper_min = min(c["risk_adj_upper_50"] for c in corridors) * 100
    corridor_upper_max = max(c["risk_adj_upper_50"] for c in corridors) * 100
    corridor_lower_min = min(c["risk_adj_lower_50"] for c in corridors) * 100
    corridor_lower_max = max(c["risk_adj_lower_50"] for c in corridors) * 100
    # Band over ONLY the pre-committed (pinned) calibration corridors, used by the
    # Blindspots note. mode_b risk_adj_50 is [lower, upper]; this is a strict
    # subset of the corridors[] watchlist, so its band is narrower than the
    # all-corridors band above. Keeping it separate prevents the Blindspots bullet
    # from labelling the full-watchlist spread as "the calibration corridors".
    pinned_lower_min = min(h["risk_adj_50"][0] for h in mode_b) * 100
    pinned_lower_max = max(h["risk_adj_50"][0] for h in mode_b) * 100
    pinned_upper_min = min(h["risk_adj_50"][1] for h in mode_b) * 100
    pinned_upper_max = max(h["risk_adj_50"][1] for h in mode_b) * 100

    # Human-readable display names for the source and target geographies.
    # Used in the plain-language calibration-point statements.
    _zone_display = {
        "mongbwalu": "Mongbwalu Health Zone (Ituri Province, DRC)",
        "rwampara": "Rwampara Health Zone (Ituri Province, DRC)",
        "bunia": "Bunia Health Zone (Ituri Province, DRC)",
        "bundibugyo": "the Bundibugyo cluster (DRC)",
        "ituri": "Ituri Province (DRC)",
        "kasese-uga": "Kasese District (Uganda)",
        "kampala-uga": "Kampala (Uganda)",
        "bundibugyo-uga": "Bundibugyo District (Uganda)",
        "beni-cod": "Beni Health Zone (North Kivu Province, DRC)",
        "arua-uga": "Arua District (Uganda)",
        "nebbi-uga": "Nebbi District (Uganda)",
    }

    def _zone_name(zone_id: str) -> str:
        return _zone_display.get(zone_id, zone_id)

    # Plain-language calibration-point rows. Public exports avoid raw internal
    # hypothesis identifiers; those remain in the machine-readable ledger.
    mode_b_rows_html = []
    for i, h in enumerate(mode_b, start=1):
        corridor_label = h["corridor"]
        # corridor_label is "source -> target"; split for plain-English rendering.
        if " -> " in corridor_label:
            source_id, target_id = corridor_label.split(" -> ", 1)
        else:
            source_id, target_id = corridor_label, ""
        lo, hi = h["risk_adj_50"]
        point_pinned_date = _long_date(h.get("pinned_at", calibration_pinned_iso))
        point_resolution_date = _long_date(h.get("resolves_at", resolves_at))
        point_block = h.get("block_id", "").split(":")[-1] or "block"
        design_tags = [
            h.get("risk_tier", "").replace("_", " "),
            h.get("geography_class", "").replace("_", "-"),
            h.get("control_role", "").replace("_", " "),
        ]
        design_note = " / ".join(tag for tag in design_tags if tag)
        statement = (
            f"At least one new laboratory-confirmed BDBV case appears in "
            f"{_zone_name(target_id)} between {point_pinned_date} and {point_resolution_date}, "
            f"given continued reporting from {_zone_name(source_id)}. "
            f"Model's ascertainment-adjusted 50% uncertainty range: "
            f"<strong>[{lo*100:.1f}%, {hi*100:.1f}%]</strong>."
        )
        mode_b_rows_html.append(
            f'<tr><td class="hid">Calibration point {i}</td>'
            f'<td>{html.escape(point_block)}'
            f'{f"<br><small>{html.escape(design_note)}</small>" if design_note else ""}</td>'
            f'<td>{statement}</td></tr>'
        )

    # ---- new schema display variables (2026-06-01 split) ----------------
    # Active confirmed inline clause. When the source publishes both
    # cumulative and active confirmed (SitRep #016 onward), surface both so
    # the reader sees the headline number and the currently-open caseload.
    if confirmed_active_primary is not None:
        confirmed_active_clause = (
            f" (of which <strong>{confirmed_active_primary}</strong> are "
            f"currently active)"
        )
    else:
        confirmed_active_clause = ""

    # Operational caseload clause. The suspected cases currently in the response
    # pipeline are an operational, point-in-time caseload (under investigation
    # plus in isolation), national-only and reported as of the snapshot date.
    # They are NOT a cumulative case count and are never added to the confirmed
    # total, so they are surfaced as a clearly separated, explicitly point-in-time
    # sentence rather than alongside the cumulative headline. We report only
    # laboratory-confirmed cumulative cases and deliberately do not reproduce any
    # dashboard total that adds these operational counts to confirmed cases.
    if op_under_investigation is not None and op_in_isolation is not None:
        operational_caseload_sentence = (
            f" Separately, a point-in-time operational caseload at {snapshot_date} "
            f"(national, not cumulative, and not added to the confirmed count) "
            f"comprises <strong>{op_under_investigation} suspected cases under "
            f"investigation</strong> and <strong>{op_in_isolation} in isolation</strong>"
            + (
                f", <strong>{op_active_total} active suspected in all</strong>."
                if op_active_total is not None
                else "."
            )
        )
    elif op_active_total is not None:
        operational_caseload_sentence = (
            f" Separately, a point-in-time operational caseload at {snapshot_date} "
            f"(national, not cumulative, and not added to the confirmed count) "
            f"records <strong>{op_active_total} active suspected cases</strong> in "
            f"the response pipeline."
        )
    else:
        operational_caseload_sentence = ""

    # Deaths display. The cumulative surface carries laboratory-confirmed deaths
    # only; the suspected-deaths series is retired from the cumulative count.
    if deaths_confirmed_primary is not None:
        deaths_display = f"{deaths_confirmed_primary} confirmed deaths"
    else:
        deaths_display = (
            f"{_count(reported_deaths, 'confirmed', 'primary')} confirmed deaths"
        )

    # LOCF provenance footnote. When any cumulative headline field is carried
    # forward (for example after a cycle with no fresh upstream publication),
    # emit a single inline sentence explaining where the value came from and
    # why it is being held. Apple-tier visual restraint: same gray <p> style
    # as other caveats, no new section, no charts.
    def _readable_locf_reason(reason: str) -> str:
        if reason == "source_schema_evolved":
            return (
                "the upstream source has refined which fields it publishes "
                "and the prior value remains the most recent comparable "
                "measure"
            )
        if reason == "awaiting_next_publication":
            return (
                "the next scheduled upstream publication for this cycle "
                "has not yet been released"
            )
        return "the prior value is carried forward with explicit provenance"

    _locf_friendly_field = {
        "confirmed": "confirmed cases",
        "confirmed_active": "active confirmed cases",
        "recovered": "recovered (cured) count",
        "deaths_confirmed": "confirmed deaths",
    }
    if locf_fields:
        # Group by (from_date, reason) so we emit one sentence per
        # provenance class rather than one per field.
        grouped: dict[tuple[str, str], list[str]] = {}
        for field, from_date, reason in locf_fields:
            grouped.setdefault((from_date, reason), []).append(
                _locf_friendly_field.get(field, field)
            )
        locf_sentences = []
        for (from_date, reason), fields in grouped.items():
            field_phrase = (
                fields[0]
                if len(fields) == 1
                else (", ".join(fields[:-1]) + ", and " + fields[-1])
            )
            locf_sentences.append(
                f"The {field_phrase} {'figure is' if len(fields) == 1 else 'figures are'} "
                f"carried forward from the {_long_date(from_date)} snapshot because "
                f"{_readable_locf_reason(reason)}."
            )
        locf_footnote_html = (
            '<p style="font-size:8.5pt; color:'
            + COLOR_GRAY
            + '; margin-top:2pt;"><strong>Provenance:</strong> '
            + " ".join(html.escape(s) for s in locf_sentences)
            + "</p>"
        )
    else:
        locf_footnote_html = ""

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Bundibugyo virus, DRC and Uganda, 2026: surveillance methodology brief</title>
<meta name="author" content="Frans Moore">
<style>
@page {{ size: A4; margin: 12mm 12mm 10mm 12mm; }}
* {{ box-sizing: border-box; }}
html {{ background: #fff; }}
body {{
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
  color: {COLOR_INK};
  font-size: 9.5pt;
  line-height: 1.32;
  margin: 0;
  padding: 0;
  max-width: 186mm;
}}
header {{ border-bottom: 1.5px solid {COLOR_PRIMARY}; padding-bottom: 3pt; margin-bottom: 5pt; }}
h1 {{ font-size: 14.5pt; margin: 0 0 1pt 0; color: {COLOR_PRIMARY}; font-weight: 700; }}
h1 .sub {{ font-size: 10pt; font-weight: 500; color: {COLOR_INK}; display: block; margin-top: 1pt; }}
.meta {{ font-size: 8.5pt; color: {COLOR_GRAY}; margin-top: 2pt; line-height: 1.32; }}
.meta a {{ color: {COLOR_PRIMARY}; text-decoration: none; }}
.framing {{
  background: #f5f7fa;
  border-left: 2.5pt solid {COLOR_PRIMARY};
  padding: 4pt 7pt;
  margin: 4pt 0 5pt 0;
  font-size: 8.5pt;
  line-height: 1.30;
}}
.framing strong {{ color: {COLOR_PRIMARY}; }}
.lede {{
  font-size: 9.5pt;
  margin: 4pt 0;
  font-style: italic;
  color: {COLOR_INK};
}}
h2 {{
  font-size: 10pt;
  color: {COLOR_PRIMARY};
  margin: 6pt 0 2pt 0;
  font-weight: 700;
  border-bottom: 0.6pt solid {COLOR_LIGHT};
  padding-bottom: 1pt;
}}
h2 .ord {{
  color: {COLOR_ACCENT};
  font-weight: 700;
  margin-right: 4pt;
}}
.panel {{ margin-bottom: 4pt; }}
.panel p {{ margin: 1pt 0 2pt 0; }}
.visual {{ margin: 1pt 0; }}
.visual svg {{ display: block; max-width: 100%; height: auto; }}
.modea-tbl {{
  font-size: 8.5pt;
  border-collapse: collapse;
  width: 100%;
}}
.modea-tbl th, .modea-tbl td {{
  padding: 2pt 5pt;
  border-bottom: 0.5px solid {COLOR_LIGHT};
  text-align: left;
}}
.modea-tbl th {{ color: {COLOR_GRAY}; font-weight: 600; }}
.modea-tbl td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.hypotheses-tbl {{
  width: 100%;
  border-collapse: collapse;
  font-size: 8pt;
  margin: 2pt 0;
}}
.hypotheses-tbl th {{
  color: {COLOR_GRAY};
  font-weight: 600;
  text-align: left;
  padding: 2pt 4pt;
  border-bottom: 0.5px solid {COLOR_LIGHT};
}}
.hypotheses-tbl td {{
  padding: 2pt 4pt;
  border-bottom: 0.5px solid {COLOR_LIGHT};
  vertical-align: top;
}}
.hypotheses-tbl td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.hypotheses-tbl code {{ font-size: 7.5pt; color: {COLOR_PRIMARY}; }}
ul.compact {{ margin: 2pt 0; padding-left: 14pt; }}
ul.compact li {{ margin-bottom: 0pt; line-height: 1.32; }}
.limits {{ background: #fafafa; padding: 4pt 8pt; border-radius: 2pt; margin: 4pt 0 4pt 0; }}
.limits h2 {{ margin-top: 0; border-bottom: none; margin-bottom: 2pt; }}
footer {{
  margin-top: 4pt;
  border-top: 0.6pt solid {COLOR_LIGHT};
  padding-top: 3pt;
  font-size: 7.5pt;
  color: {COLOR_GRAY};
  line-height: 1.38;
}}
footer a {{ color: {COLOR_PRIMARY}; text-decoration: none; }}
.tag {{
  display: inline-block;
  background: {COLOR_PRIMARY};
  color: {COLOR_WHITE};
  font-size: 7.5pt;
  padding: 1pt 5pt;
  border-radius: 2pt;
  margin-right: 4pt;
  vertical-align: middle;
  font-weight: 600;
  letter-spacing: 0.4pt;
}}
.tag.accent {{ background: {COLOR_ACCENT}; }}
.col2 {{ display: flex; gap: 12pt; }}
.col2 > * {{ flex: 1; }}
.disclaimer {{
  background: #fff4e8;
  border: 1pt solid {COLOR_ACCENT};
  border-radius: 2pt;
  padding: 5pt 8pt;
  margin: 5pt 0;
  font-size: 8.5pt;
  line-height: 1.30;
}}
.disclaimer strong {{ color: {COLOR_ACCENT}; }}
.sovereignty {{
  background: #f3f7f3;
  border-left: 2.5pt solid #4a7da3;
  padding: 4pt 7pt;
  margin: 4pt 0 4pt 0;
  font-size: 8.5pt;
  line-height: 1.30;
}}
</style>
</head>
<body>
<header>
<h1>Bundibugyo virus, Democratic Republic of the Congo and Uganda, 2026
<span class="sub">Latent Outbreak Visibility System (LOVS) applied to the 2026 BDBV outbreak. Ascertainment, detection depth, and pre-committed methodology calibration points, as of {snapshot_date}.</span></h1>
<div class="meta">
Author: <a href="https://www.linkedin.com/in/frans-moore/">Frans Moore</a>, <a href="mailto:frans@arcede.com">frans@arcede.com</a>.
Source repository: <a href="{REPO_URL}">{REPO_URL}</a>.
Document is reproducible from frozen inputs via <code>python make_brief.py</code>.
</div>
</header>

<div class="framing" style="border-left-color: #c66020;">
<strong>Bottom line.</strong> This is a <strong>methodology contribution</strong> in support of the DRC Ministry of Public Health, Hygiene and Social Welfare, the Uganda Ministry of Health, the World Health Organization, and Africa CDC. <strong>It is not a forecast, a travel advisory, or a deployment recommendation.</strong> The {snapshot_date} snapshot indicates the public reporting picture captures only an estimated <strong>{completeness_lo:.0f} to {completeness_hi:.0f} percent of laboratory-confirmable cases</strong> and that detection occurred after multiple silent transmission generations, both intrinsic to early-stage filovirus surveillance. The corridor watchlist is a pre-committed calibration test of the method's uncertainty quality, not a ranked deployment list, and on historical data the method's corridor ranking does not beat a simple proximity or caseload baseline (see calibration section).
</div>

<div class="framing" style="background:#f1ece0; border-left-color: #6e685f;">
<strong>Why a methodology brief, today.</strong> As of {snapshot_date_us}, the most prominent public quantitative output for this outbreak is the <a href="https://www.imperial.ac.uk/mrc-global-infectious-disease-analysis/research-themes/preparedness-and-response-to-emerging-threats/report-ebola-update-20-05-2026/">joint WHO-Imperial College MRC GIDA report (20 May 2026 update)</a> estimating <strong>400-900 total cases in DRC</strong> (values over 1,000 not excluded), via population-movement extrapolation and deaths-back-projection through the case-fatality ratio. This brief treats that estimate as a dated academic reference for outbreak size, and reproduces its deaths-back-projection (Method 2, CFR 26/33/40, at Imperial's borrowed 14-day central) to within a few cases, while re-grounding our own current central doubling time to the roughly 7 days observed in this outbreak's confirmed-case series (a log-linear fit whose every window excludes 14 days). What this brief adds is the work the size estimate does not do: it is a convergence point that reconciles the scattered public sources into one provenance-tracked, source-conflict-aware view, and it publishes a reporting-completeness posterior, a pre-committed calibration set, and a cross-border corridor-risk view with date-stamped resolution. Within the archived source set for this snapshot, the other reviewed public outputs (WHO Disease Outbreak News 2026-DON602, the WHO AFRO Weekly Sitrep, Africa CDC PHECS declaration, and US CDC HAN 00530) do not include this combination. No comparable public output from the WHO Hub for Pandemic and Epidemic Intelligence in Berlin or the US CDC's Center for Forecasting and Outbreak Analytics was identified in this review as of the snapshot date. That gap is what this brief is built to fill. It complements the WHO-Imperial estimate; it does not replace it.
</div>

<div class="sovereignty">
<strong>Authorities and standing.</strong> The Democratic Republic of the Congo (DRC) Ministry of Public Health, Hygiene and Social Welfare officially declared this outbreak on 15 May 2026 and is the lead authority on the DRC response, with the National Institute of Biomedical Research (INRB) confirming Bundibugyo virus (BDBV) by polymerase chain reaction (PCR). The Uganda Ministry of Health (MoH) is the lead authority on the Uganda response and confirmed two imported cases in Kampala on 15-16 May 2026. The World Health Organization (WHO) Director-General determined the outbreak a Public Health Emergency of International Concern (PHEIC) on 16 May 2026; WHO published the public statement on 17 May 2026. The Africa Centres for Disease Control and Prevention (Africa CDC), acting on the recommendation of the Emergency Consultative Group, declared the outbreak a Public Health Emergency of Continental Security (PHECS) on 18 May 2026. This brief is a methodology contribution in support of those authorities. It is not a substitute for them and does not speak on behalf of any of them.
</div>

<div class="framing">
<strong>At a glance.</strong> Ebola disease caused by BDBV, a less common species of Ebola first identified in Uganda in 2007, is spreading in eastern DRC with cross-border and inter-province detection events. Public counts as of {snapshot_date}: <strong>{confirmed_primary} cumulative laboratory-confirmed cases</strong>{confirmed_active_clause} ({confirmed_source_label}: {confirmed_drc} confirmed in DRC plus {confirmed_uganda} confirmed cases in Uganda; earlier anchors are 10 confirmed as of 17 May per WHO PHEIC after Kinshasa deconfirmation, and 30 as of 19 May per ECDC), and <strong>{deaths_display}</strong>, including four healthcare worker deaths at Mongbwalu General Referral Hospital within a four-day span per WHO DON 602.{uganda_update} The cumulative figures in this brief are laboratory-confirmed cases and confirmed deaths only.{operational_caseload_sentence} This brief applies the <strong>Latent Outbreak Visibility System (LOVS)</strong>, a stdlib-only Python pipeline that quantifies (a) the ascertainment gap, (b) the detection-depth posterior, and (c) inter-zone corridor risk under explicit calibration. The method was calibrated against the 2014 West Africa Ebola epidemic (a Zaire-species outbreak); applying it to a Bundibugyo-species outbreak introduces a species-transfer uncertainty that is reported honestly below.{locf_footnote_html}
</div>

<div class="disclaimer">
<strong>What this brief is NOT.</strong> It is NOT a travel advisory. It is NOT a recommendation to restrict cross-border movement, close markets, or redirect commercial activity. It is NOT a deployment recommendation. The named corridors below are descriptive watch points for further investigation; they are NOT predictions of where the outbreak will spread. The corridor-risk numbers reflect a known model property (see methodology caveat) that overstates per-zone risk when confirmed cases are distributed across multiple health zones, as is the case here. Decisions on travel, trade, surveillance allocation, and response posture rest with the DRC Ministry of Public Health, the Uganda Ministry of Health, WHO, and Africa CDC. The pre-committed calibration points are not surveillance forecasts.
</div>

<div class="panel">
<h2><span class="ord">1.</span> Ascertainment gap is wide and quantifiable</h2>
<p>
Reporting completeness (the share of underlying cases reflected in public counts), 50% uncertainty range: <strong>[{completeness_lo:.1f}%, {completeness_hi:.1f}%]</strong>. This is consistent with what is typical of early-stage filovirus outbreaks under any surveillance system, where the gap between the visible surface and the underlying outbreak reflects the inherent reporting delay (Rosello 2015 eLife, the BDBV Isiro 2012 onset-to-notification distribution adopted as the species-matched default in the 23 May parameter audit, with Camacho 2015 PLOS Currents EBOV-Zaire retained as a faster-reporting sensitivity comparator), the historical pattern of late Bundibugyo detection (Wamala 2010 EID), and operational realities of Ituri-region surveillance: ongoing insecurity in eastern DRC (per the Armed Conflict Location and Event Data (ACLED) project), internally displaced populations, and malaria plus other febrile, gastrointestinal, arboviral, or influenza-like illnesses that can complicate early clinical triage. The ascertainment gap is a structural feature of early outbreak reporting, not a critique of the national response. This ascertainment gap is a detection measure (its denominator is true, laboratory-confirmable infections) and should not be conflated with the source-attribution lag in the corridor section below (a spatial-allocation measure whose denominator is the cases already detected): the first asks how much of the true outbreak has been detected at all, the second asks how many detected cases have been geolocated to a specific health zone rather than held in the national unallocated residual. They are complementary layers of completeness, not competing methods.
</p>
<div class="visual">{svgs['visibility_gap']}</div>
</div>

<div class="panel">
<h2><span class="ord">2.</span> Detection occurred after multiple silent transmission rounds</h2>
<p>
Posterior probability that at least three transmission generations (person-to-person rounds of spread) had already occurred before the outbreak became visible: <strong>{gen3_p:.1f}%</strong>. With {confirmed_primary} laboratory-confirmed cases, the branching-process inversion places near-unity mass on three or more silent generations preceding public detection under the current R and under-ascertainment priors. Wamala 2010 and MacNeil 2010 anchor the Bundibugyo-species incubation and interval context, but the numeric bins in this chart are driven by confirmed cases, the interim R prior, and the under-ascertainment prior. The R prior remains an explicitly labeled modeling assumption because a direct Bundibugyo-virus R0 estimate has not been located.
</p>
<div class="visual">{svgs['detection_depth']}</div>
</div>

<div class="panel">
<h2><span class="ord">3.</span> Corridor watch list (descriptive, not a ranking, not a forecast)</h2>
<p>
The current {corridor_count}-corridor watchlist spans {corridor_lower_min:.1f}-{corridor_lower_max:.1f}% lower bounds and {corridor_upper_min:.1f}-{corridor_upper_max:.1f}% upper bounds at a 30-day horizon. The current correction is source-attribution lag, not missing cases: it separates the {confirmed_primary} confirmed cases in the headline aggregate from the official per-health-zone source-load vector. Corridor risk now uses {zone_attributed_confirmed} confirmed cases that are officially zone-attributed across {source_zone_count} {source_zone_label}, rather than applying the headline aggregate to every source zone. The remaining {unallocated_confirmed} confirmed cases are unallocated headline context until an official zone table assigns them. Per-zone confirmed deaths trail case attribution by roughly 1-3 weeks while the INRB clinical review queue closes, so the per-zone confirmed-deaths figure is a lower bound and the unallocated residual an upper bound. The remaining clustering is still a limitation signal: no single corridor stands clearly above the others; on historical data the method does not out-rank a simple proximity or caseload baseline (see calibration section).
</p>
<p style="font-size: 8pt; color: {COLOR_GRAY}; margin-top: 3pt;">
<strong>Methodology caveat (load-bearing).</strong> The snapshot carries two different count concepts. The headline public count is {confirmed_primary} confirmed cases as of {snapshot_date} ({confirmed_source_label}: {confirmed_drc} DRC plus {confirmed_uganda} Uganda cases). The corridor source-load vector is spatially attributed: {source_vector_sentence} The corridor model uses that per-zone vector because it is the newest officially zone-attributed table available in the archive. It does not scale the vector up to the {confirmed_primary} country-scope headline aggregate without a source table showing where the additional {unallocated_confirmed} confirmed cases belong, so those cases remain unallocated headline context. Separately, the corridor model's gravity parameters (the population, road, healthcare-distance, and conflict exponents and the clamp) are transparent engineering heuristics, not fitted to a mobility dataset: Backer &amp; Wallinga 2016 is the West Africa 2014 validation substrate and supports the broad gravity-type model family, but it does not source-fit the current LOVS constants; the public audit trail records that limitation without exposing internal evidence-chain identifiers.
</p>
<div class="visual">{svgs['corridor_risk']}</div>
</div>

<div class="panel">
<h2><span class="ord">4.</span> Calibration block carried by the {snapshot_date} snapshot</h2>
<p>
{calibration_block_sentence} The named corridors below were pre-committed as calibration points for the underlying method. Calibration is narrower than the descriptive watchlist: a corridor only becomes a calibration point when it is pinned before outcome assessment with a fixed source, target, uncertainty range, horizon, and resolution timestamp. The remaining watchlist corridors stay visible as descriptive signals; they are not counted as extra independent calibration evidence unless a later pre-publication block explicitly pins them. Each pinned point is paired with the model's ascertainment-adjusted 50% uncertainty range for a binary outcome at a {calibration_horizon_days}-day horizon from its pin date. {calibration_clock_sentence} The clock equation is <code>remaining_days = date(resolves_at) - date(as_of)</code>. Outcomes will be scored against publicly available reports from the DRC Ministry of Public Health, the Uganda Ministry of Health, the WHO, and Africa CDC, in order to evaluate whether the model's uncertainty ranges contained the observed outcome and to update the historical calibration. <strong>These calibration points are NOT recommendations for the active public-health response.</strong>
</p>
<p style="font-size: 8.5pt; margin-top: 2pt;">
<strong>What each calibration point claims, in plain language:</strong>
</p>
<table class="hypotheses-tbl">
<thead><tr><th>Calibration point</th><th>Block / design role</th><th>Plain-language statement</th></tr></thead>
<tbody>
{''.join(mode_b_rows_html)}
</tbody>
</table>
<div class="visual">{svgs['pre_registration_timeline']}</div>
</div>

<div class="panel">
<h2>Historical calibration on the 2014 West Africa Ebola epidemic</h2>
<p>
The method has been tested retrospectively against the 2014 West Africa Ebola epidemic (Backer &amp; Wallinga 2016 substrate, 62 prefectures across 74 weeks), where the eventual outcomes are public knowledge. Three runs are reported: without local context, with country-level local context (population density, road density, healthcare access, conflict intensity), and with district-level local context. Adding local context at country level tightens uncertainty (interval score down by roughly 35%); it does NOT improve discrimination of individual corridors above chance. The 2014 substrate was a Zaire-species outbreak; the method's calibration on Zaire data transfers to a Bundibugyo-species outbreak with a species-transfer uncertainty that is not separately quantified here.
</p>
<table class="modea-tbl">
<thead><tr><th>Metric</th><th class="num">No local context</th><th class="num">Country-level</th><th class="num">District-level</th><th class="num">Change vs country</th></tr></thead>
<tbody>
<tr><td>Brier score (probability accuracy; lower is better)</td>
<td class="num">{mode_a_v1.brier:.4f}</td>
<td class="num">{mode_a_v2.brier:.4f}</td>
<td class="num">{mode_a_v3.brier:.4f}</td>
<td class="num">{_signed_delta(mode_a_v3.brier - mode_a_v2.brier)}</td></tr>
<tr><td>Interval score (Bracher 2021; uncertainty quality; lower is better)</td>
<td class="num">{mode_a_v1.wis:.4f}</td>
<td class="num">{mode_a_v2.wis:.4f}</td>
<td class="num">{mode_a_v3.wis:.4f}</td>
<td class="num">{_signed_delta(mode_a_v3.wis - mode_a_v2.wis)}</td></tr>
<tr><td>Calibration error (predicted vs observed; lower is better)</td>
<td class="num">{mode_a_v1.ece:.4f}</td>
<td class="num">{mode_a_v2.ece:.4f}</td>
<td class="num">{mode_a_v3.ece:.4f}</td>
<td class="num">{_signed_delta(mode_a_v3.ece - mode_a_v2.ece)}</td></tr>
</tbody>
</table>
<p style="margin-top: 4pt; font-size: 8.5pt; color: {COLOR_GRAY};">
<strong>Finding.</strong> Country-level local context tightens uncertainty by approximately 35% but does not lift discrimination above chance, and district-level granularity does not add further. Candidate next-levers, ranked by expected impact: mobility data (Wesolowski 2016 Journal of Infectious Diseases), time-varying context (real-time political-violence intensity from ACLED and real-time MoH reporting cadence), re-engineering how the four context variables combine, and a richer transmission model. The calibration-error increase from 0.039 to 0.050 between no-context and context runs is small and most likely an artifact of the small evaluation set (five as-of dates).
</p>
<p style="margin-top: 6pt; font-size: 8.5pt; color: {COLOR_GRAY};">
<strong>Robustness check (skill, discrimination, calibration).</strong> A rolling-origin backtest across a pre-registered grid of as-of weeks, reproducible via <code>robustness_backtest.py</code>, refines the table above. At the early checkpoints the method ranks which prefectures appear next with a ROC AUC of {robustness.auc_model:.2f}, above the 0.50 chance level, but a distance-only baseline ({robustness.auc_distance:.2f}) and a source-load-only baseline ({robustness.auc_source_load:.2f}) rank just as well: the gravity and covariate machinery add no ranking value, so the discrimination that exists is the spatial autocorrelation of the epidemic rather than the model. Against a base-rate (climatology) reference the Brier skill score is {robustness.bss:.2f} (95 percent interval {robustness.bss_ci_lo:.2f} to {robustness.bss_ci_hi:.2f}); across the full grid no configuration shows a skill interval above zero at any window, so the method has no positive calibration skill beyond predicting the base rate. Intervals are from a target-prefecture clustered bootstrap. These are 2014 West Africa (Zaire-species) results and are not a portable skill claim for a Bundibugyo-species outbreak.
</p>
</div>

<div class="panel">
<h2>If you have point-of-care data</h2>
<p>
This brief reports method estimates from publicly aggregated reporting only. If you are working directly in the affected zones, you almost certainly hold information that is privileged, time-sensitive, and not appropriate for a public repository. <strong>Please do not paste line-list rows, GPS-tagged case locations, sequencing reads, or any identifying detail into a public GitHub issue.</strong> You can reach me directly at <a href="mailto:frans@arcede.com">frans@arcede.com</a> if any of the following would help your work.
</p>
<ul class="compact">
<li><strong>Onset-date line-list extract (de-identified).</strong> Even a partial onset-date histogram for one health zone substantially narrows the latent-active-chains plausibility interval emitted by Module D.</li>
<li><strong>Zone-attributed case counts.</strong> The load-bearing simplification flagged in Panel 3 is that the full confirmed count is contributed to each named source zone. A mapping of <code>{{zone_id: count}}</code> for the affected districts is the largest single discrimination lever the method is missing.</li>
<li><strong>Validated zone GPS centroids.</strong> The published map ships verified centroids for zones currently in scope (<code>data/zones.json</code>). For zones we may have missed, a centroid plus a one-sentence rationale is enough to extend the corridor model.</li>
<li><strong>Mobility traces or transport-flow snapshots.</strong> Call-detail-record summaries or surveyed transport flows, even at admin-2 aggregation, are the documented next-lever for moving the method above chance discrimination (Wesolowski 2015 PNAS).</li>
<li><strong>Case-confirmation latency.</strong> Field-observed delays (sample collection to PCR result, in days) are a direct prior update for Module C, replacing the historical onset-to-notification prior (Rosello 2015 BDBV Isiro 2012) currently used as the default.</li>
</ul>
<p style="font-size: 8.5pt; color: {COLOR_GRAY};">
Contributions that land in the repository are cited and timestamped. If you prefer to remain unnamed, I can credit you by initials or pseudonym at the time of contribution.
</p>
</div>

<div class="panel">
<h2>Known blindspots and calibration design notes</h2>
<ul class="compact">
<li><strong>Mahagi as a source zone is still outside the model, while Arua/Nebbi are now represented as target-side calibration/watch corridors.</strong> Mahagi (DRC) and Goli (Uganda) form one of DRC's busiest land border crossings on the East African Northern Corridor, with 95,000+ refugees at the Rhino Camp settlement near Arua (UNHCR, late-2025). The 21 May 2026 revision added Arua District and Nebbi District to the candidate-target watch set, and the unpublished 21 May calibration block pins selected Arua/Nebbi target corridors before publication. The 22 May WHO DG and IHR sources were rechecked for this blindspot and still do not name Mahagi health zone as case-affected. Mahagi (DRC) is therefore still not modeled as a source zone; <code>snapshot_sensitivity.py</code> shows where the omitted Mahagi-to-Arua corridor would rank under an explicit equal-burden counterfactual.</li>
<li><strong>The active calibration corridors preserve their original pinned probability band ({pinned_lower_min:.1f} to {pinned_lower_max:.1f}% lower, {pinned_upper_min:.1f} to {pinned_upper_max:.1f}% upper).</strong> The May 20 and May 21 calibration blocks are not re-derived after the May 22 spatial correction; that is intentional. The current watchlist now uses zone-attributed source loads, while the calibration ledger remains an immutable record of what was pre-committed before the correction.</li>
<li><strong>The corridor mongbwalu-to-beni-cod may be confounded.</strong> Beni Health Zone sits in North Kivu Province, which the 18 May Africa CDC reporting identifies as already part of the outbreak footprint. A positive resolution on this corridor may simply reflect Beni being part of the active outbreak rather than source-zone-to-target-zone transmission.</li>
<li><strong>Conflict-state coverage is qualitative.</strong> The brief invokes CODECO and ADF activity in Ituri/North Kivu and 7.3 million IDPs in eastern DRC as descriptive context, but the per-zone conflict-intensity input is the 2014 West Africa ACLED snapshot used in the Mode A historical calibration. A 2026 ACLED snapshot for eastern DRC is a documented next-lever.</li>
</ul>
</div>

<div class="limits">
<h2>What this brief does NOT claim</h2>
<ul class="compact">
<li><strong>Not a forecast.</strong> The pre-committed calibration points exist to evaluate the method's uncertainty quality at resolution. They are not surveillance recommendations and not deployment recommendations.</li>
<li><strong>Not a critique of the national response.</strong> Ascertainment gaps and late detection of filoviruses are intrinsic to the pathogen and to the operational context (security, displacement, co-circulating pathogens), not to the speed of any specific national response. The DRC Ministry of Public Health declared on 15 May, the Uganda Ministry of Health confirmed imported cases on 15-16 May, INRB confirmed BDBV by PCR within days. This brief takes the national declarations as the authoritative timeline.</li>
<li><strong>Does not replace field epidemiology.</strong> Line-listing, contact tracing, genomic sequencing, and clinical reasoning are where outbreak control happens.</li>
<li><strong>Species-transfer uncertainty is not separately quantified.</strong> The historical calibration substrate is a Zaire-species outbreak; transfer to Bundibugyo carries unquantified uncertainty in the priors and the corridor model.</li>
<li><strong>Numbers are prior-dominated at this case count.</strong> The ascertainment and transmission-depth posteriors are heavily informed by the prior delay and transmission distributions, not by data alone. In particular, the branching-process reproduction prior is an interim modeling assumption: no Bundibugyo-virus-specific basic reproduction number has been estimated in the published literature (Van Kerkhove 2015), so the detection-depth result is prior-driven and species-transferred, not a data-driven BDBV estimate. Sensitivity analyses across alternative priors are a recommended next step.</li>
<li><strong>Open to independent replication.</strong> Code is Apache 2.0 licensed; original authored methodology, prose, schema, and derived artifacts are CC BY 4.0; third-party source material and extracted publisher-owned tables retain their original terms. The method is described in <code>CITATIONS.md</code>. Independent replication is welcomed.</li>
</ul>
</div>

<footer>
<strong>Citation.</strong> Moore F. <em>Bundibugyo virus, DRC and Uganda, 2026: surveillance methodology brief.</em> Released 2026-05. <a href="{REPO_URL}">{REPO_URL}</a>.
&nbsp;|&nbsp;<strong>Live data:</strong> WHO Disease Outbreak News item 2026-DON602, WHO African Region Weekly External Situation Report 01 (data as of 18 May 2026), Africa CDC PHECS declaration (18 May 2026), and aggregated public reporting through {snapshot_date}.
&nbsp;|&nbsp;<strong>Methodology citations:</strong> Wamala 2010 Emerging Infectious Diseases (EID), MacNeil 2010 EID, Albariño 2013 Virology, Faye 2015 Lancet ID, Backer &amp; Wallinga 2016 PLOS Computational Biology, Bracher 2021 PLOS Computational Biology (full list at <code>CITATIONS.md</code>).
&nbsp;|&nbsp;<strong>License.</strong> Code Apache 2.0; original authored methodology/prose/schema/derived artifacts CC BY 4.0; third-party source material keeps publisher terms; WHO content Creative Commons BY-NC-SA 3.0 IGO.
&nbsp;|&nbsp;<strong>Pre-commitment timestamp:</strong> verifiable via the git commit history of <code>data/calibration-ledger.json</code> in the source repository (first pinned on {calibration_pinned_iso}, before the {resolves_iso} resolution window).
&nbsp;|&nbsp;<strong>Resolution:</strong> {resolves_at}.
</footer>
</body>
</html>
"""
    return body


# ----- PDF + PNG conversion (best-effort) -----


def find_chrome() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        shutil.which("chromium"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
    ]
    for c in candidates:
        if c and pathlib.Path(c).exists():
            return c
    return None


def _pdf_date_stamp(as_of: str) -> str:
    """14-digit YYYYMMDDHHMMSS stamp for deterministic PDF dates, from the snapshot date."""
    return as_of[:10].replace("-", "") + "000000"


def _normalize_pdf_dates(pdf_path: pathlib.Path, stamp: str) -> None:
    """Make the PDF byte-deterministic by fixing Chrome's render timestamp.

    Chrome stamps /CreationDate and /ModDate with the wall-clock render time, the
    only non-deterministic bytes in this PDF (it emits no /ID array and no XMP
    date block). Replace the 14-digit datetime in both with a fixed stamp derived
    from the snapshot date. The replacement is length-preserving (14 digits for
    14 digits), so every xref byte offset stays valid.
    """
    import re

    data = pdf_path.read_bytes()
    data = re.sub(
        rb"(/(?:CreationDate|ModDate) ?\(D:)\d{14}",
        rb"\g<1>" + stamp.encode("ascii"),
        data,
    )
    pdf_path.write_bytes(data)


def try_pdf(html_path: pathlib.Path, pdf_path: pathlib.Path, date_stamp: str | None = None) -> bool:
    chrome = find_chrome()
    if not chrome:
        return False
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        f"--print-to-pdf={pdf_path}",
        "--print-to-pdf-no-header",
        "--no-pdf-header-footer",
        html_path.as_uri(),
    ]
    # Remove any prior artifact first so a failed or skipped render cannot
    # masquerade as success by leaving a stale PDF in place.
    try:
        pdf_path.unlink()
    except FileNotFoundError:
        pass
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    if result.returncode != 0:
        return False
    if not (pdf_path.exists() and pdf_path.stat().st_size > 0):
        return False
    if date_stamp:
        _normalize_pdf_dates(pdf_path, date_stamp)
    return True


def try_png(svg_path: pathlib.Path, png_path: pathlib.Path) -> bool:
    """Convert SVG to PNG. Try rsvg-convert, cairosvg, then headless Chrome."""
    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        try:
            subprocess.run([rsvg, "-o", str(png_path), str(svg_path)], check=True,
                           capture_output=True, timeout=20)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    try:
        import cairosvg  # type: ignore[import-untyped]
        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), output_width=1200)
        return True
    except Exception:
        pass
    # Final fallback: render SVG via headless Chrome.
    chrome = find_chrome()
    if chrome:
        try:
            # Read SVG to determine viewBox; default window 1200x600.
            svg_text = svg_path.read_text(encoding="utf-8")
            # Extract viewBox dimensions if present
            import re
            m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg_text)
            if m:
                vw, vh = int(m.group(1)), int(m.group(2))
                scale = 2  # 2x for crisp rendering
                window_w, window_h = vw * scale, vh * scale
            else:
                window_w, window_h = 1200, 600
            png_path.parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--hide-scrollbars",
                f"--screenshot={png_path}",
                f"--window-size={window_w},{window_h}",
                svg_path.as_uri(),
            ]
            subprocess.run(cmd, capture_output=True, timeout=30)
            return png_path.exists() and png_path.stat().st_size > 0
        except (subprocess.TimeoutExpired, OSError):
            return False
    return False


# ----- Main -----


def main() -> int:
    print("Loading frozen pipeline output and computing Mode A v1/v2/v3...", flush=True)
    pipeline = load_pipeline_output()
    mode_a_v1, mode_a_v2, mode_a_v3 = compute_mode_a()
    print(f"  Mode A v1: Brier={mode_a_v1.brier:.4f}  WIS={mode_a_v1.wis:.4f}  ECE={mode_a_v1.ece:.4f}")
    print(f"  Mode A v2: Brier={mode_a_v2.brier:.4f}  WIS={mode_a_v2.wis:.4f}  ECE={mode_a_v2.ece:.4f}")
    print(f"  Mode A v3: Brier={mode_a_v3.brier:.4f}  WIS={mode_a_v3.wis:.4f}  ECE={mode_a_v3.ece:.4f}")
    robustness = compute_robustness()
    print(
        f"  Robustness: AUC={robustness.auc_model:.3f} "
        f"(dist={robustness.auc_distance:.3f}, load={robustness.auc_source_load:.3f})  "
        f"BSS={robustness.bss:.3f} [{robustness.bss_ci_lo:.3f}, {robustness.bss_ci_hi:.3f}]"
    )

    print("Generating SVG visuals...", flush=True)
    svgs = {
        "visibility_gap": render_visibility_gap_svg(pipeline["visibility"]),
        "detection_depth": render_detection_depth_svg(pipeline["transmission"]),
        "corridor_risk": render_corridor_risk_svg(pipeline["corridors"]),
        "pre_registration_timeline": render_timeline_svg(pipeline),
        "per_zone_snapshot": render_per_zone_snapshot_svg(
            pipeline.get("insp_per_zone_block")
        ),
        "ascertainment_band_per_zone": render_ascertainment_band_per_zone_svg(
            pipeline.get("per_zone_under_ascertainment_bands")
        ),
    }

    VISUALS_DIR.mkdir(parents=True, exist_ok=True)
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in svgs.items():
        svg_path = VISUALS_DIR / f"{name}.svg"
        svg_path.write_text(content, encoding="utf-8")
        print(f"  wrote {svg_path.relative_to(REPO_ROOT)}")

    print("Generating PNG conversions (best-effort)...", flush=True)
    png_ok = 0
    for name in svgs:
        png_path = VISUALS_DIR / f"{name}.png"
        if try_png(VISUALS_DIR / f"{name}.svg", png_path):
            png_ok += 1
            print(f"  wrote {png_path.relative_to(REPO_ROOT)}")
    if png_ok == 0:
        print("  (no PNG converter available; SVG only. Install cairosvg or rsvg-convert for PNG.)")

    print("Generating brief.html...", flush=True)
    html_doc = render_html(pipeline, mode_a_v1, mode_a_v2, mode_a_v3, robustness, svgs)
    html_path = BRIEF_DIR / "brief.html"
    html_path.write_text(html_doc, encoding="utf-8")
    print(f"  wrote {html_path.relative_to(REPO_ROOT)}")

    print("Generating brief.pdf (best-effort via headless Chrome)...", flush=True)
    pdf_path = DELIVERABLES_DIR / "brief.pdf"
    if try_pdf(html_path, pdf_path, date_stamp=_pdf_date_stamp(pipeline["as_of"])):
        print(f"  wrote {pdf_path.relative_to(REPO_ROOT)}")
    else:
        print("  (no headless Chrome available; open brief.html in a browser and print to PDF.)")

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
