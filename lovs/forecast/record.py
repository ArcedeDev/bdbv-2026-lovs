"""The unified scored-forecast record: one table, every system.

WHY THIS EXISTS. Three forecasting systems now run in this programme and each
stores its scored outcomes in its own schema: the corridor ledger, the
operational ledger, and the IDB Track B record in a separate repository. Every
one of them can report its own Brier. None of them can be pooled, and pooling is
the precondition for everything worth doing next.

The reason is not tidiness. A calibration map -- the function from what a
forecaster SAYS to what actually HAPPENS -- can only be fitted on the pooled
corpus, because no single system will reach the sample size on its own for
months. The Track B scoring already showed the shape that map would correct:
resolution 0.173 against reliability 0.166, which is a forecaster whose ranking
carries real information and whose prices are wrong. Recalibration converts
almost all of a reliability loss straight into skill. It does not need a better
forecaster; it needs the one we have, repriced.

WHAT A ROW IS. One resolved forecast: the probability as registered, the binary
outcome, and the context needed to ask WHEN the forecaster is trustworthy rather
than only whether it is on average. The context columns are the point. A pooled
Brier is a number; a pooled Brier sliced by method, band, series length and
question shape is a model of your own reliability.

APPEND-ONLY, AND NEVER REWRITTEN. A row enters when its forecast resolves and is
never edited afterwards. The probability is the one that was registered before
the outcome was known; that is the entire value of the corpus and the one thing
that cannot be reconstructed later.

THIS REPOSITORY'S SOURCES BY DEFAULT. The IDB Track B record is private and is
pooled only when a caller names it, by the `track_b` argument or the
LOVS_IDB_TRACK_B_RECORD environment variable. It is never found by a path
relative to this checkout: a figure built from the default record must come out
the same wherever the repository is checked out, and for a public reader.

Stdlib only. Deterministic. No clock of its own.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CORRIDOR_LEDGER = REPO / "data" / "calibration-ledger.json"
OPERATIONAL_LEDGER = REPO / "data" / "operational-calibration-ledger.json"
RESEARCH_STORE = REPO / "data" / "research-store-resolved-2026-09-01.json"
# Opt-in path to the private IDB Track B record. Unset means it is not pooled.
TRACK_B_ENV = "LOVS_IDB_TRACK_B_RECORD"


@dataclass(frozen=True, slots=True)
class ScoredForecast:
    """One resolved forecast, in the schema every system maps onto."""

    system: str
    block_id: str
    forecast_id: str
    probability: float
    outcome: int
    registered_at: str
    resolves_at: str
    horizon_days: int | None
    method: str
    metric: str
    question_shape: str
    n_observations_at_pin: int | None
    bias_test: bool
    geography_class: str | None
    # Fixed at registration and never rewritten. A "challenger" pin prices a question
    # beside the forecaster of record so two methods can be compared; it is not the
    # programme's forecast and stays out of the headline record even after its method
    # is adopted for later blocks.
    role: str = "record"
    # The realised outcome this row resolves on. Rows that share it are not separate
    # evidence: threshold rungs on one series in one window all resolve on one value,
    # and every corridor into one target in one window resolves on one event. Empty
    # means the row is its own outcome.
    outcome_group: str = ""

    @property
    def brier(self) -> float:
        return (self.probability - self.outcome) ** 2

    @property
    def band(self) -> str:
        """Decile label. The unit a calibration map is read in."""
        return f"{int(min(self.probability, 0.999) * 10) / 10:.1f}"


def window_group(kind: str, subject: str, opens: str, closes: str) -> str:
    """The outcome a row resolves on: one subject read over one window, by day."""
    return f"{kind}:{subject}:{opens[:10]}..{closes[:10]}"


# The research store holds the first corridor calibration block (pinned 2026-05-20,
# resolving 2026-06-19) a second time, under its own ids and prices. Each hypothesis
# names its target in its own text, and each resolved the same way as the corridor
# point. They are the same four events and share that outcome.
RESEARCH_STORE_CORRIDOR_COPIES = {
    "hyp:epi:bdbv-uga-cod-2026:30d:3c170d7cf1": "kampala-uga",
    "hyp:epi:bdbv-uga-cod-2026:30d:66d19db8e4": "beni-cod",
    "hyp:epi:bdbv-uga-cod-2026:30d:6a969b845d": "bundibugyo-uga",
    "hyp:epi:bdbv-uga-cod-2026:30d:fbab207204": "kasese-uga",
}


def _corridor_rows(path: Path) -> list[ScoredForecast]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for block in doc.get("blocks", []):
        for point in block.get("points", []):
            if "outcome" not in point:
                continue
            lo, hi = point["risk_adj_50"]
            out.append(ScoredForecast(
                system="corridor", block_id=block["block_id"],
                forecast_id=point["hypothesis_id"],
                probability=(lo + hi) / 2.0, outcome=int(point["outcome"]),
                registered_at=block["pinned_at"], resolves_at=block["resolves_at"],
                horizon_days=point.get("horizon_days"),
                method="corridor_monte_carlo", metric="target_first_confirmation",
                question_shape="ever_in_window",
                n_observations_at_pin=None, bias_test=False,
                geography_class=point.get("geography_class"),
                outcome_group=window_group("corridor", point["target"],
                                           block["pinned_at"], block["resolves_at"]),
            ))
    return out


def _operational_rows(path: Path) -> list[ScoredForecast]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for block in doc.get("blocks", []):
        for pin in block.get("points", []):
            if "outcome" not in pin:
                continue
            out.append(ScoredForecast(
                system="operational", block_id=block["block_id"],
                forecast_id=pin["pin_id"],
                probability=float(pin["probability"]), outcome=int(pin["outcome"]),
                registered_at=block["pinned_at"], resolves_at=block["resolves_at"],
                horizon_days=pin.get("horizon_days"),
                method=pin.get("method", "level_bootstrap"),
                metric=pin["metric"], question_shape=pin.get("shape", "unknown"),
                n_observations_at_pin=pin.get("observations_at_pin"),
                bias_test=bool(pin.get("bias_test")), geography_class=None,
                role=pin.get("role", "record"),
                # By the metric the pin was registered against, so sub-blocks pinned
                # together on one series share it.
                outcome_group=window_group("operational", pin["metric"],
                                           block["pinned_at"], block["resolves_at"]),
            ))
    return out


# The roles registered as the programme's own forecast. Any other role, including
# one added later for a baseline or a candidate method, stays out until it is named here.
ROLES_OF_RECORD = ("record", "incumbent")


def of_record(rows: list[ScoredForecast]) -> list[ScoredForecast]:
    """The programme's own forecasts: rows whose role is in ROLES_OF_RECORD.

    The headline record and any recalibration fit read this, so a question priced by
    two methods counts once. Other rows stay in the record and in method slices.
    """
    return [row for row in rows if row.role in ROLES_OF_RECORD]


def _track_b_rows(path: Path) -> list[ScoredForecast]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = doc.get("entries", [])
    if not entries:
        return []
    # The record is append-only and each entry re-scores the whole set, so the
    # last entry is the current view. Earlier entries are history, not extra
    # observations, and pooling them would count one forecast many times.
    latest = entries[-1]
    return [
        ScoredForecast(
            system="idb_track_b", block_id=f"track-b:{latest['scored_at']}",
            forecast_id=row["hypothesis_id"],
            probability=float(row["confidence"]), outcome=int(row["outcome"]),
            registered_at="2026-05-19", resolves_at=latest["scored_at"],
            horizon_days=None, method=f"engine_{row.get('tier', 'unknown')}",
            metric=row.get("hypothesis_class", "unknown"),
            question_shape="engine_hypothesis", n_observations_at_pin=None,
            bias_test=False, geography_class=None, outcome_group=row["hypothesis_id"],
        )
        for row in latest.get("per_hypothesis", [])
    ]


def _research_store_group(row: dict) -> str:
    target = RESEARCH_STORE_CORRIDOR_COPIES.get(row["hypothesis_id"])
    if target is None:
        # The export names no shared quantity, so each hypothesis counts once.
        return row["hypothesis_id"]
    return window_group("corridor", target, str(row.get("created_at") or ""),
                        str(row.get("resolves_at") or ""))


def _research_store_rows(path: Path) -> list[ScoredForecast]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [
        ScoredForecast(
            system="research_store", block_id=row["scope_id"],
            forecast_id=row["hypothesis_id"],
            probability=float(row["probability"]), outcome=int(row["outcome"]),
            registered_at=str(row.get("created_at") or "")[:10],
            resolves_at=str(row.get("resolves_at") or "")[:10],
            horizon_days=None,
            method=f"engine_{row.get('resolution_type', 'unknown')}",
            metric=row.get("hypothesis_class") or "unknown",
            question_shape="engine_hypothesis",
            n_observations_at_pin=None, bias_test=False, geography_class=None,
            outcome_group=_research_store_group(row),
        )
        for row in doc.get("rows", [])
    ]


def build(corridor: Path | None = None, operational: Path | None = None,
          track_b: Path | None = None, research_store: Path | None = None) -> dict:
    """Pool every named system into one table.

    The private Track B record joins only when `track_b` or the
    LOVS_IDB_TRACK_B_RECORD environment variable names it. Reports which sources
    were used, so a thin corpus is never mistaken for a complete one.
    """
    if track_b is None:
        named = os.environ.get(TRACK_B_ENV, "").strip()
        track_b = Path(named).expanduser() if named else None
    sources = {
        "corridor": corridor or CORRIDOR_LEDGER,
        "operational": operational or OPERATIONAL_LEDGER,
        "idb_track_b": track_b,
        "research_store": research_store or RESEARCH_STORE,
    }
    # Order matters: the first source to claim a forecast_id wins. The research
    # store is the live authority and goes first, because the IDB Track B
    # snapshot was CAPTURED FROM it on 2026-05-19 and re-states the same
    # hypotheses under the same ids. Pooling both without dedup would
    # systematically double-count exactly the rows the two systems share, and
    # would do it silently -- the pooled n would simply look larger.
    candidates = (_research_store_rows(sources["research_store"])
                  + _corridor_rows(sources["corridor"])
                  + _operational_rows(sources["operational"])
                  + (_track_b_rows(track_b) if track_b is not None else []))
    rows: list[ScoredForecast] = []
    seen: set[str] = set()
    duplicates: list[dict] = []
    for row in candidates:
        if row.forecast_id in seen:
            duplicates.append({"forecast_id": row.forecast_id, "dropped_from": row.system})
            continue
        seen.add(row.forecast_id)
        rows.append(row)
    rows.sort(key=lambda r: (r.registered_at, r.forecast_id))
    seen = {name: path is not None and path.exists() for name, path in sources.items()}
    return {
        "_meta": {
            "schema_version": 1,
            "purpose": ("Pooled append-only record of every resolved forecast across the "
                        "programme's systems. The substrate for calibration fitting, which "
                        "no single system will have the sample size for on its own."),
            "sources_found": seen,
            "sources_missing": [k for k, v in seen.items() if not v],
            "track_b_note": ("The IDB Track B record is private and is pooled only when the "
                             f"track_b argument or {TRACK_B_ENV} names it. Otherwise it is "
                             "listed as missing and the record holds this repository's "
                             "sources only."),
            "row_count": len(rows),
            "systems": sorted({r.system for r in rows}),
            "duplicates_dropped": len(duplicates),
            "duplicate_detail": duplicates,
            "dedup_note": ("Deduplicated by forecast_id. The IDB Track B snapshot was captured "
                           "from the research store, so the two overlap by construction; the "
                           "research store wins as the live authority."),
        },
        "rows": [asdict(r) for r in rows],
    }


def summary(record: dict) -> dict:
    """Pooled counts and Brier, sliced the ways a calibration map is read."""
    every = [ScoredForecast(**r) for r in record["rows"]]
    rows = of_record(every)
    if not rows:
        return {"n": 0, "challenger_rows": len(every)}
    base = sum(r.outcome for r in rows) / len(rows)

    def slice_by(key, source=None):
        out: dict[str, dict] = {}
        for row in rows if source is None else source:
            bucket = out.setdefault(str(getattr(row, key)), {"n": 0, "brier": 0.0, "hits": 0})
            bucket["n"] += 1
            bucket["brier"] += row.brier
            bucket["hits"] += row.outcome
        for bucket in out.values():
            bucket["brier"] = round(bucket["brier"] / bucket["n"], 4)
            bucket["observed_rate"] = round(bucket["hits"] / bucket["n"], 4)
        return dict(sorted(out.items()))

    return {
        "n": len(rows),
        "base_rate": round(base, 4),
        "brier": round(sum(r.brier for r in rows) / len(rows), 4),
        "base_rate_brier": round(sum((base - r.outcome) ** 2 for r in rows) / len(rows), 4),
        "challenger_rows": len(every) - len(rows),
        "by_system": slice_by("system"),
        # Method slices include challenger rows: comparing methods is what they are for.
        "by_method": slice_by("method", every),
        "by_band": slice_by("band"),
    }
