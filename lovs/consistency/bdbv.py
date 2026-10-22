# SPDX-License-Identifier: Apache-2.0
"""BDBV lane adapter for the consistency core.

Turns reviewed SitRep promotions (Tableau 3 alert funnel, laboratory and
PoE/PoC blocks) and the text layer of each pinned INSP PDF (key messages and
surveillance prose) into figures the core and judge can test.
"""
from __future__ import annotations

import itertools
import pathlib
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping

from lovs.consistency.core import (
    MISMATCH,
    Candidate,
    Figures,
    Finding,
    difference_status,
    find_note,
    rate_status,
    round_half_up,
    sum_status,
)

LANE = "bdbv"
# Provinces reporting a Tableau 3 row in SR153; C2 and C4 range over them
# (JUDGE_V1.md candidate family).
PROVINCES = ("Haut-Uele", "Bas-Uele", "Ituri", "Nord-Kivu", "Sud-Ubangi", "Tshopo")
SEEN_EDITIONS = frozenset({151, 152, 153})
MAX_LAG = 3

PRESENT = "present"
ABSENT = "absent"
UNPARSEABLE = "unparseable"


def normalise_text(text: str) -> str:
    """Fold layout noise: NBSP and thin spaces, curly quotes, runs of space."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", text)


def parse_int(token: str) -> int:
    return int(re.sub(r"\s", "", token))


def parse_pct(token: str) -> tuple[float, int]:
    token = token.strip().replace(",", ".")
    decimals = len(token.split(".")[1]) if "." in token else 0
    return float(token), decimals


NUM = r"(\d{1,3}(?: \d{3})*|\d+)"
PCT = r"(\d{1,3}(?:[.,]\d+)?)"

_KM_COUNTS = (
    re.compile(NUM + r" cas suspects sur " + NUM + r" \(" + PCT + r" ?%\) ont été investigu"),
    re.compile(NUM + r" ?/ ?" + NUM + r" suspects ont été investigués[^()]{0,60}\(" + PCT + r" ?%\)"),
)
_KM_REMAINDER = re.compile(NUM + r" cas suspects restent non investigués en ([A-Z][A-Za-zÀ-ÿ'\-]+)")
_KM_PCT_NEAR = re.compile(PCT + r" ?%")
_SUIVI = re.compile(r"suivi|contacts?\b", re.IGNORECASE)


@dataclass
class KeyMessage:
    status: str
    figures: dict[str, Any] = field(default_factory=dict)
    decimals: int | None = None
    evidence: str = ""


def key_message_region(text: str) -> str | None:
    marker = re.search(r"MESSAGES CL[EÉ]S", text)
    return text[marker.end():] if marker else None


def extract_key_message(text: str) -> KeyMessage:
    """Investigation figures printed in MESSAGES CLES.

    ABSENT when the edition has no key-message block or the block never
    mentions investigation; PRESENT with the parsed tuple; UNPARSEABLE when the
    block mentions investigated suspects but no figure can be read (a reworded
    statement must fail loudly, never pass as clean).
    """
    region = key_message_region(normalise_text(text))
    if region is None:
        return KeyMessage(ABSENT, evidence="no MESSAGES CLES block")
    mentions = [m.start() for m in re.finditer(r"investigu", region, re.IGNORECASE)]
    mentions = [i for i in mentions if re.search(r"suspect", region[max(0, i - 160): i + 60], re.IGNORECASE)]
    if not mentions:
        return KeyMessage(ABSENT, evidence="no suspect investigation statement")
    for pattern in _KM_COUNTS:
        match = pattern.search(region)
        if match:
            pct, decimals = parse_pct(match.group(3))
            figures: dict[str, Any] = {
                "inv": parse_int(match.group(1)),
                "val": parse_int(match.group(2)),
                "pct": pct,
            }
            remainder = _KM_REMAINDER.search(region)
            if remainder:
                figures["not_inv"] = parse_int(remainder.group(1))
                figures["province"] = canonical_province(remainder.group(2))
            return KeyMessage(PRESENT, figures, decimals, match.group(0))
    for index in mentions:
        window_start = max(0, index - 90)
        window = region[window_start: index + 40]
        best: tuple[int, re.Match[str]] | None = None
        for match in _KM_PCT_NEAR.finditer(window):
            between = window[min(match.end(), index - window_start): max(match.start(), index - window_start)]
            if _SUIVI.search(between):
                continue
            distance = abs(match.start() - (index - window_start))
            if best is None or distance < best[0]:
                best = (distance, match)
        if best:
            pct, decimals = parse_pct(best[1].group(1))
            return KeyMessage(PRESENT, {"pct": pct}, decimals, window.strip())
    # SR89 prints the rate without its percent sign ("99,9 des cas suspects").
    bare = re.search(r"(\d{1,3}[.,]\d+) des (?:cas )?suspects ont été investigu", region)
    if bare:
        pct, decimals = parse_pct(bare.group(1))
        return KeyMessage(PRESENT, {"pct": pct}, decimals, bare.group(0))
    return KeyMessage(UNPARSEABLE, evidence=region[max(0, mentions[0] - 90): mentions[0] + 40].strip())


_BODY_PATTERNS = (
    re.compile(r"Sur ces " + NUM + r" cas suspects, " + NUM + r" \(" + PCT + r" ?%\) ont été investigu"),
    re.compile(NUM + r" alertes ont été validées comme cas suspects,? (?:et )?" + NUM + r" \(" + PCT + r" ?%\) (?:ont été investigu|ont fait l'objet d'une investigation)"),
)
_BODY_ALL = (
    re.compile(r"[Ll]es " + NUM + r" (?:cas )?suspects (?:validés )?ont tous été investigués"),
    re.compile(r"(?:dont|tous les) " + NUM + r" (?:\(\d+[.,]\d ?%\) )?(?:alertes )?validées comme cas suspects,? tous ont été investigu"),
)
_BODY_REMAINDER = re.compile(NUM + r" cas suspects n'ont (?:\d+ )?pas été investigués en ([A-Z][A-Za-zÀ-ÿ'\-]+)")


def extract_body(text: str) -> dict[str, Any] | None:
    """Investigation counts in the surveillance prose, before the key messages."""
    normal = normalise_text(text)
    marker = re.search(r"MESSAGES CL[EÉ]S", normal)
    body = normal[: marker.start()] if marker else normal
    for pattern in _BODY_PATTERNS:
        match = pattern.search(body)
        if match:
            figures: dict[str, Any] = {"val": parse_int(match.group(1)), "inv": parse_int(match.group(2))}
            figures["not_inv"] = figures["val"] - figures["inv"]
            remainder = _BODY_REMAINDER.search(body)
            if remainder:
                figures["not_inv"] = parse_int(remainder.group(1))
                figures["province"] = canonical_province(remainder.group(2))
            return figures
    for pattern in _BODY_ALL:
        match = pattern.search(body)
        if match:
            count = parse_int(match.group(1))
            return {"val": count, "inv": count, "not_inv": 0}
    return None


def canonical_province(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    folded = folded.replace(" ", "-")
    for province in PROVINCES + ("Sud-Kivu", "Nord-Ubangi"):
        if folded.lower() == province.lower():
            return province
    return folded


# ---------------------------------------------------------------------------
# Edition context from reviewed promotions


@dataclass
class Edition:
    number: int
    total: dict[str, int | None]
    provinces: dict[str, dict[str, int | None]]
    lab: dict[str, int | None]
    poe: dict[str, Any]
    alerts: dict[str, int | None]
    preserved_conflicts: list[str]


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def edition_from_promotion(payload: Mapping[str, Any]) -> Edition | None:
    figures = payload.get("figures") or {}
    tables = figures.get("operational_tables") or {}
    total = tables.get("alerts_total") or {}
    if _int(total.get("validated_as_suspect")) is None:
        return None
    provinces: dict[str, dict[str, int | None]] = {}
    for row in tables.get("alerts_by_province") or []:
        if not isinstance(row, dict) or not isinstance(row.get("province"), str):
            continue
        living, deaths = _int(row.get("validated_living")), _int(row.get("validated_deaths"))
        provinces[canonical_province(row["province"])] = {
            "val": None if living is None and deaths is None else (living or 0) + (deaths or 0),
            "val_living": living,
            "inv": _int(row.get("suspects_investigated")),
            "transferred": _int(row.get("suspects_transferred")),
        }
    lab = tables.get("lab_indicators_24h") or {}
    return Edition(
        number=int(payload["sitrep_number"]),
        total={
            "val": _int(total.get("validated_as_suspect")),
            "inv": _int(total.get("suspects_investigated")),
            "transferred": _int(total.get("suspects_transferred")),
        },
        provinces=provinces,
        lab={
            "tested": _int(lab.get("samples_analyzed")) if isinstance(lab, dict) else None,
            "positive": _int(lab.get("samples_positive")) if isinstance(lab, dict) else None,
        },
        poe=tables.get("poe_poc_national") or {},
        alerts={
            "received": _int(total.get("alerts_reported")),
            "verified": _int(total.get("alerts_verified_24h")),
        },
        preserved_conflicts=[c for c in figures.get("preserved_conflicts") or [] if isinstance(c, str)],
    )


def _funnel(val: int | None, inv: int | None, *, provinces: Mapping[str, Mapping[str, int | None]] | None = None) -> Figures | None:
    if val is None and inv is None:
        return None
    out: dict[str, Any] = {"val": val, "inv": inv}
    if val is not None and inv is not None:
        out["not_inv"] = val - inv
    if provinces:
        short = [
            name for name, row in provinces.items()
            if row.get("val") is not None and row.get("inv") is not None and row["val"] > row["inv"]  # type: ignore[operator]
        ]
        if len(short) == 1:
            out["province"] = short[0]
    return out


def national_figures(edition: Edition) -> Figures | None:
    """Tableau 3 national row as a key-message tuple: counts, their remainder,
    and the one province short of full investigation, if exactly one is."""
    return _funnel(edition.total["val"], edition.total["inv"], provinces=edition.provinces)


def build_candidates(
    editions: Mapping[int, Edition],
    key_messages: Mapping[int, Figures],
    bodies: Mapping[int, Figures],
    *,
    judge_version: int = 1,
) -> list[Candidate]:
    """The registered family: C1 to C12 under judge v1 (m_eff 88), plus the
    composite C13 under judge v2 (m_eff 89)."""

    def table(n: int) -> Figures | None:
        e = editions.get(n)
        return national_figures(e) if e else None

    candidates: list[Candidate] = [
        Candidate("C1", "same-edition Tableau 3 national total", table),
    ]
    for province in PROVINCES:
        def one(n: int, p: str = province) -> Figures | None:
            e = editions.get(n)
            row = e.provinces.get(p) if e else None
            if not row:
                return None
            out = _funnel(row["val"], row["inv"])
            if out is not None:
                out["province"] = p
            return out
        candidates.append(Candidate(f"C2:{province}", f"same-edition Tableau 3 row {province}", one))

    def verified_over_received(n: int) -> Figures | None:
        e = editions.get(n)
        return _funnel(e.alerts["received"], e.alerts["verified"]) if e else None

    def validated_over_verified(n: int) -> Figures | None:
        e = editions.get(n)
        return _funnel(e.alerts["verified"], e.total["val"]) if e else None

    candidates += [
        Candidate("C3:verified/received", "alerts verified over alerts received", verified_over_received),
        Candidate("C3:validated/verified", "suspects validated over alerts verified", validated_over_verified),
    ]
    for size in range(1, len(PROVINCES) + 1):
        for subset in itertools.combinations(PROVINCES, size):
            def subset_sum(n: int, s: tuple[str, ...] = subset) -> Figures | None:
                e = editions.get(n)
                if not e or any(p not in e.provinces for p in s):
                    return None
                vals = [e.provinces[p]["val"] for p in s]
                invs = [e.provinces[p]["inv"] for p in s]
                if any(v is None for v in vals + invs):
                    return None
                rows = {p: e.provinces[p] for p in s}
                return _funnel(sum(vals), sum(invs), provinces=rows)  # type: ignore[arg-type]
            candidates.append(Candidate("C4:" + "+".join(subset), "province subset sum", subset_sum))

    def cumulative(n: int) -> Figures | None:
        rows = [editions[k] for k in sorted(editions) if k <= n]
        if not rows or n not in editions:
            return None
        return _funnel(sum(r.total["val"] or 0 for r in rows), sum(r.total["inv"] or 0 for r in rows))

    def live_only(n: int) -> Figures | None:
        e = editions.get(n)
        if not e:
            return None
        living = [row["val_living"] for row in e.provinces.values()]
        if not living or any(v is None for v in living):
            return None
        return {"val": sum(living)}  # type: ignore[arg-type]

    candidates += [
        Candidate("C5", "cumulative Tableau 3 totals to this edition", cumulative),
        Candidate("C6", "validated living suspects only", live_only),
    ]
    for lag in range(1, MAX_LAG + 1):
        candidates.append(Candidate(f"C7:lag{lag}", f"Tableau 3 of edition E-{lag}", lambda n, k=lag: table(n - k)))
    for lag in range(1, MAX_LAG + 1):
        candidates.append(Candidate(f"C8:lag{lag}", f"surveillance prose of edition E-{lag}", lambda n, k=lag: bodies.get(n - k)))
    for lag in range(1, MAX_LAG + 1):
        candidates.append(Candidate(f"C9:lag{lag}", f"key message of edition E-{lag}", lambda n, k=lag: key_messages.get(n - k)))

    def two_day_sum(n: int) -> Figures | None:
        a, b = editions.get(n), editions.get(n - 1)
        if not a or not b:
            return None
        return _funnel((a.total["val"] or 0) + (b.total["val"] or 0), (a.total["inv"] or 0) + (b.total["inv"] or 0))

    def two_day_mean(n: int) -> Figures | None:
        a, b = editions.get(n), editions.get(n - 1)
        if not a or not b or None in (a.total["val"], b.total["val"], a.total["inv"], b.total["inv"]):
            return None
        half = lambda x, y: int(round_half_up(Decimal(x + y) / 2, 0))  # noqa: E731
        return _funnel(half(a.total["val"], b.total["val"]), half(a.total["inv"], b.total["inv"]))

    def lab_as_investigated(n: int) -> Figures | None:
        e = editions.get(n)
        return {"inv": e.lab["tested"]} if e and e.lab["tested"] is not None else None

    def lab_as_validated(n: int) -> Figures | None:
        e = editions.get(n)
        return {"val": e.lab["tested"]} if e and e.lab["tested"] is not None else None

    def poe(n: int) -> Figures | None:
        e = editions.get(n)
        val = _int(e.poe.get("validated_live_suspects")) if e else None
        return {"val": val} if val is not None else None

    candidates += [
        Candidate("C10:sum", "Tableau 3 of E plus E-1 (cut-off straddle)", two_day_sum),
        Candidate("C10:mean", "mean of Tableau 3 of E and E-1", two_day_mean),
        Candidate("C11:tested-as-investigated", "samples tested printed as investigated", lab_as_investigated),
        Candidate("C11:tested-as-validated", "samples tested printed as validated", lab_as_validated),
        Candidate("C12", "PoE/PoC validated live suspects", poe),
    ]
    if judge_version >= 2:
        def composite(n: int) -> Figures | None:
            counts = table(n)
            if counts is None:
                return None
            out = {key: counts[key] for key in ("val", "inv") if key in counts}
            carried = key_messages.get(n - 1) or {}
            out.update({key: carried[key] for key in ("not_inv", "province") if key in carried})
            return out
        candidates.append(Candidate(
            "C13", "counts from Tableau 3 of E, remainder sentence carried from key message of E-1", composite,
        ))
    return candidates


def field_groups(target: Mapping[str, Any]) -> list[list[str]]:
    """Judge v2 bar rule 1: dependent fields share a group; the printed
    remainder is its own group only when it is not val - inv."""
    counts = ["inv", "val", "pct"]
    inv, val, rem = target.get("inv"), target.get("val"), target.get("not_inv")
    if None not in (inv, val, rem) and val - inv == rem:
        return [counts + ["not_inv"], ["province"]]
    return [counts, ["not_inv"], ["province"]]


def rate_fields_for(decimals: int | None) -> dict[str, tuple[str, str, int]]:
    return {"pct": ("inv", "val", decimals if decimals is not None else 1)}


def load_editions(promotions: Iterable[Mapping[str, Any]]) -> dict[int, Edition]:
    out: dict[int, Edition] = {}
    for payload in promotions:
        edition = edition_from_promotion(payload)
        if edition is not None:
            out[edition.number] = edition
    return out


def edition_number_from_path(path: pathlib.Path) -> int | None:
    match = re.search(r"(?:MVE|MVEBDB)_0*(\d+)", path.name)
    return int(match.group(1)) if match else None


# Edition-by-edition table checks. Rates are printed to one decimal in
# Tableau 3; the national row of the older schema (SR1 to SR83) is based on
# alerts reported, the newer one on validated suspects (investigation_rate_basis).
RATE_DECIMALS = 1


def _rate_basis(row: Mapping[str, Any]) -> tuple[str, str] | None:
    if _int(row.get("validated_as_suspect")) is not None:
        return "suspects_investigated", "validated_as_suspect"
    if _int(row.get("alerts_investigated")) is not None:
        return "alerts_investigated", "alerts_reported"
    return None


def table_findings(payload: Mapping[str, Any]) -> list[Finding]:
    """Every arithmetic relation the reviewed promotion lets us test."""
    figures = payload.get("figures") or {}
    tables = figures.get("operational_tables") or {}
    total = tables.get("alerts_total")
    if not isinstance(total, dict):
        return []
    edition = int(payload["sitrep_number"])
    notes = [line for line in figures.get("preserved_conflicts") or [] if isinstance(line, str)]
    notes += [total[key] for key in ("scope_note", "data_gap_note") if isinstance(total.get(key), str)]
    rows = [r for r in tables.get("alerts_by_province") or [] if isinstance(r, dict) and isinstance(r.get("province"), str)]
    out: list[Finding] = []

    def add(scope: str, relation: str, printed: Any, expected: Any, status: str) -> None:
        # A note discloses a mismatch only when it prints both sides of it.
        covered = find_note(notes, printed, expected) if status == MISMATCH and expected is not None else None
        out.append(Finding(LANE, edition, scope, relation, "Tableau 3", printed, expected, status, covered))

    def rate(scope: str, row: Mapping[str, Any], relation: str, printed_key: str, num_key: str, den_key: str) -> None:
        printed = row.get(printed_key)
        if isinstance(printed, bool) or not isinstance(printed, (int, float)):
            return
        num, den = _int(row.get(num_key)), _int(row.get(den_key))
        status = rate_status(num, den, printed, RATE_DECIMALS)
        expected = None if status in ("incomplete", "undefined") else float(round_half_up(Decimal(num) * 100 / Decimal(den), RATE_DECIMALS))  # type: ignore[arg-type,operator]
        add(scope, relation, printed, expected, status)

    basis = _rate_basis(total)
    if basis:
        rate("national", total, f"investigation_rate_pct = {basis[0]} / {basis[1]}", "investigation_rate_pct", *basis)
    rate("national", total, "verification_rate_pct = alerts_verified_24h / alerts_reported", "verification_rate_pct", "alerts_verified_24h", "alerts_reported")
    rate("national", total, "transfer_rate_pct_printed = suspects_transferred / suspects_investigated", "transfer_rate_pct_printed", "suspects_transferred", "suspects_investigated")
    rate("national", total, "validated_rate_pct_printed = validated_as_suspect / alerts_verified_24h", "validated_rate_pct_printed", "validated_as_suspect", "alerts_verified_24h")
    for row in rows:
        if _int(row.get("alerts_investigated")) is not None:
            rate(canonical_province(row["province"]), row, "investigation_rate_pct = alerts_investigated / alerts_reported", "investigation_rate_pct", "alerts_investigated", "alerts_reported")

    val, inv = _int(total.get("validated_as_suspect")), _int(total.get("suspects_investigated"))
    remainder = _int(total.get("suspects_not_investigated"))
    if remainder is not None:
        status = difference_status(val, inv, remainder)
        add("national", "suspects_not_investigated = validated_as_suspect - suspects_investigated", remainder,
            None if val is None or inv is None else val - inv, status)
    if _int(total.get("suspected_cases_day")) is not None and _int(total.get("alerts_validated_living")) is not None:
        living, deaths, day = _int(total.get("alerts_validated_living")), _int(total.get("alerts_validated_deaths")), _int(total.get("suspected_cases_day"))
        status = sum_status([living, deaths], day)
        add("national", "suspected_cases_day = alerts_validated_living + alerts_validated_deaths", day,
            None if living is None or deaths is None else living + deaths, status)

    if rows:
        def national_sum(relation: str, national: int | None, parts: list[int | None]) -> None:
            if national is None:
                return
            status = sum_status(parts, national)
            expected = None if any(p is None for p in parts) else sum(parts)  # type: ignore[arg-type]
            add("national", relation, national, expected, status)

        for key in ("alerts_reported", "alerts_investigated", "suspects_investigated", "suspects_transferred",
                    "alerts_validated_living", "alerts_validated_deaths"):
            national_sum(f"{key} = sum of province rows", _int(total.get(key)), [_int(r.get(key)) for r in rows])
        if val is not None:
            parts = [
                None if _int(r.get("validated_living")) is None and _int(r.get("validated_deaths")) is None
                else (_int(r.get("validated_living")) or 0) + (_int(r.get("validated_deaths")) or 0)
                for r in rows
            ]
            national_sum("validated_as_suspect = sum of province validated_living + validated_deaths", val, parts)
    return out


def carried_forward_findings(editions: Mapping[int, Figures], *, section: str, scope: str = "national") -> list[Finding]:
    """A printed tuple identical to the previous edition's while the source
    table moved on is a carry-forward candidate; reported, never auto-resolved."""
    out = []
    for number in sorted(editions):
        current, previous = editions[number], editions.get(number - 1)
        if not current or not previous:
            continue
        if len(current) > 1 and current == previous:
            out.append(Finding(LANE, number, scope, f"{section} identical to edition {number - 1}", section,
                               dict(current), None, MISMATCH, None))
    return out
