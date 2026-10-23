# SPDX-License-Identifier: Apache-2.0
"""Lane-neutral consistency primitives for published official figures.

A lane (an outbreak, a disease programme) supplies observations: printed
figures keyed by edition, scope, field and section. This module checks the
arithmetic relations among them (a printed rate against its own counts, parts
against a total, a printed remainder against its minuend and subtrahend),
detects values carried forward from an earlier edition, and keeps an
append-only residue ledger of figures that no candidate explains.

Nothing here knows which lane it serves. Lane adapters live beside it.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Iterable, Mapping

OK = "ok"
MISMATCH = "mismatch"
UNDEFINED = "undefined"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class Observation:
    """One printed figure. The same quantity printed in two sections of one
    edition is two observations, never one overwritten value."""

    lane: str
    edition: int
    scope: str
    field: str
    section: str
    value: int | float | None
    printed: str | None = None
    decimals: int | None = None

    @property
    def key(self) -> tuple[str, int, str, str, str]:
        return (self.lane, self.edition, self.scope, self.field, self.section)


def round_half_up(value: float | Decimal, decimals: int) -> Decimal:
    quantum = Decimal(1).scaleb(-decimals)
    return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)


def rate_status(numerator: int | None, denominator: int | None, printed: float | None, decimals: int) -> str:
    """A printed percentage reproduces from its counts only at printed precision."""
    if numerator is None or denominator is None or printed is None:
        return INCOMPLETE
    if denominator == 0:
        return UNDEFINED
    recomputed = round_half_up(Decimal(numerator) * 100 / Decimal(denominator), decimals)
    return OK if recomputed == round_half_up(printed, decimals) else MISMATCH


def sum_status(parts: Iterable[int | None], total: int | None) -> str:
    values = list(parts)
    if total is None or not values or any(value is None for value in values):
        return INCOMPLETE
    return OK if sum(values) == total else MISMATCH  # type: ignore[arg-type]


def difference_status(minuend: int | None, subtrahend: int | None, remainder: int | None) -> str:
    if minuend is None or subtrahend is None or remainder is None:
        return INCOMPLETE
    return OK if minuend - subtrahend == remainder else MISMATCH


Figures = Mapping[str, Any]


def tuple_matches(target: Figures, predicted: Figures | None, *, rate_fields: Mapping[str, tuple[str, str, int]] = {}) -> tuple[bool, list[str]]:
    """Every printed element of ``target`` must be reproduced.

    Count and label fields need exact equality. A rate field named in
    ``rate_fields`` (field -> (numerator field, denominator field, decimals)) is
    reproduced when the predicted counts, rounded half-up at the printed
    precision, give the printed value. Returns (all matched, matched fields).
    """
    if not predicted:
        return False, []
    matched: list[str] = []
    for name, wanted in target.items():
        if wanted is None:
            continue
        if name in rate_fields:
            num, den, decimals = rate_fields[name]
            if rate_status(predicted.get(num), predicted.get(den), wanted, decimals) == OK:
                matched.append(name)
            continue
        if name in predicted and predicted[name] == wanted:
            matched.append(name)
    printed = [name for name, wanted in target.items() if wanted is not None]
    return bool(printed) and len(matched) == len(printed), matched


def carried_forward_lags(
    edition: int,
    target: Figures,
    history: Mapping[int, Figures],
    *,
    max_lag: int,
    rate_fields: Mapping[str, tuple[str, str, int]] = {},
) -> list[int]:
    """Lags k (1..max_lag) whose earlier figures reproduce every printed element."""
    return [
        lag
        for lag in range(1, max_lag + 1)
        if tuple_matches(target, history.get(edition - lag), rate_fields=rate_fields)[0]
    ]


@dataclass(frozen=True)
class Candidate:
    """A registered generator: a formula over recorded fields that may have
    produced a printed figure. ``hypotheses`` is its parameter-grid size, which
    the judge counts toward multiplicity."""

    candidate_id: str
    description: str
    generate: Callable[[int], Figures | None] = field(compare=False)
    hypotheses: int = 1


@dataclass(frozen=True)
class Finding:
    """One arithmetic relation evaluated in one edition.

    ``covered_by`` holds the reviewed conflict note that already discloses a
    mismatch; a mismatch with no note is unexplained residue."""

    lane: str
    edition: int
    scope: str
    relation: str
    section: str
    printed: Any
    expected: Any
    status: str
    covered_by: str | None = None

    @property
    def unexplained(self) -> bool:
        return self.status == MISMATCH and self.covered_by is None

    def residue(self, candidates_tried: list[str], note: str = "") -> "ResidueEntry":
        return ResidueEntry(
            lane=self.lane, edition=self.edition, scope=self.scope, field=self.relation,
            section=self.section, printed=self.printed, relation=f"expected {self.expected}",
            status=self.status, candidates_tried=candidates_tried, note=note,
        )


def number_tokens(value: int | float) -> set[str]:
    """Ways an official document or a reviewer note may print one number."""
    if isinstance(value, bool):
        return set()
    tokens: set[str] = set()
    if isinstance(value, float):
        # A rate is printed at one decimal or more: 95.0 may appear as "95,0".
        text = f"{value}"
        tokens |= {text, text.replace(".", ",")}
    if isinstance(value, int) or float(value).is_integer():
        n = int(value)
        grouped = f"{n:,}"
        tokens |= {str(n), grouped, grouped.replace(",", " "), grouped.replace(",", "\u00a0")}
    return tokens


# A number token stands alone: not inside a longer number ("1 234", "612"), an
# identifier ("SR153") or a date ("2026-10-16"). A lowercase letter may touch
# it, because extracted notes glue words to numbers ("and18.1%").
_TOKEN = r"(?<![A-Z\d_.,\-/])(?<!\d[ \u00a0]){}(?![\w\-/]|[.,]\d|[ \u00a0]\d{{3}}(?!\d))"


def find_note(notes: Iterable[str], *values: int | float | None) -> str | None:
    """The first note that prints every given value as a whole token."""
    wanted = [number_tokens(v) for v in values if v is not None]
    if not wanted:
        return None
    for note in notes:
        if all(any(re.search(_TOKEN.format(re.escape(t)), note) for t in tokens) for tokens in wanted):
            return note
    return None


@dataclass
class ResidueEntry:
    lane: str
    edition: int
    scope: str
    field: str
    section: str
    printed: Any
    relation: str
    status: str
    candidates_tried: list[str]
    note: str = ""

    def as_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def write_ledger(path: pathlib.Path, entries: Iterable[ResidueEntry]) -> int:
    """Write the ledger deterministically (sorted lines, atomic replace)."""
    lines = sorted({entry.as_json() for entry in entries})
    for line in lines:
        if "\u2014" in line:
            raise ValueError("residue ledger text must not contain an em dash")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    tmp.replace(path)
    return len(lines)
