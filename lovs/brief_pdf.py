# SPDX-License-Identifier: Apache-2.0
"""Read the brief PDF's page-1 date and headline, and check them against the snapshot.

``deliverables/brief.pdf`` is overwritten by every release, and nothing else proves
that the copy on disk is the brief of the snapshot beside it. This module reads the
PDF's own page 1 and returns what a reader sees there:

- the snapshot date in the subtitle ("... calibration points, as of 19 November 2026.")
  and in the at-a-glance paragraph ("Public counts as of 19 November 2026: ..."), and
- the headline figures of the at-a-glance paragraph: DRC confirmed cases, Uganda
  confirmed cases, the DRC + Uganda count and confirmed deaths.

The phrases mirror the page-1 template in ``make_brief.py`` (``render_html``). Each must
appear exactly once on page 1; a wording change there must change these patterns too,
and the test that reads the committed PDF fails until it does.

Page text comes from pypdf, pinned in ``requirements.txt``: the one dependency outside
the standard library, used only by this check. It is imported when a PDF is read, so
modules that import this one (``release_snapshot.py``, and through it the site sync)
still load without it. A missing pypdf, an encrypted PDF or anything pypdf cannot read
raises :class:`BriefPdfError`, so the gate fails instead of passing.

``release_snapshot.py`` runs the gate through :func:`release_problems`. The evidence-server
cadence runs ``python3 -m lovs.brief_pdf --json <pdf>`` and parses its output, so that
command and its JSON fields are a contract.
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_PDF = REPO_ROOT / "deliverables" / "brief.pdf"
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "live-bdbv-2026-output.json"
DEFAULT_CONTRACT = REPO_ROOT / "data" / "snapshot_contract.json"


class BriefPdfError(ValueError):
    """The PDF could not be read, or its page 1 does not carry the brief's headline."""


@dataclass(frozen=True)
class BriefHeadline:
    """The snapshot date and headline figures, as printed on page 1 or as expected."""

    as_of: str  # YYYY-MM-DD
    drc_confirmed: int
    uganda_confirmed: int
    total_confirmed: int
    confirmed_deaths: int


# Chrome writes every space as a glyph, so page 1 needs no space that pypdf infers from a
# gap between glyphs; at pypdf's default (200), 6.21.4 splits some numbers ("1019 3" for
# 10193 in the 27 October brief). Every brief in the current page-1 wording reads right
# from 400 up; 1000 keeps a margin.
_SPACE_WIDTH = 1000.0


def page_text(pdf: bytes, page_number: int = 1) -> str:
    """Return the text of one page (1-based), as pypdf extracts it."""
    try:
        import pypdf
    except ImportError as exc:
        raise BriefPdfError(
            "pypdf is not installed; run python3 -m pip install --require-hashes -r requirements.txt"
        ) from exc
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf), strict=True)
        if reader.is_encrypted:
            raise BriefPdfError("the PDF is encrypted")
        if not 1 <= page_number <= len(reader.pages):
            raise BriefPdfError(f"the PDF has {len(reader.pages)} page(s); page {page_number} does not exist")
        return reader.pages[page_number - 1].extract_text(space_width=_SPACE_WIDTH)
    except BriefPdfError:
        raise
    except Exception as exc:  # pypdf raises many types on a damaged file; the gate names them all
        raise BriefPdfError(f"the PDF cannot be read ({type(exc).__name__}: {exc})") from exc


# ----- The brief's headline -----

_DATE = r"(\d{1,2} [A-Z][a-z]+ \d{4})"
_COUNT = r"(\d{1,3}(?:,\d{3})+|\d+)"
_HYPHEN = r"[-‐‑] ?"
_PATTERNS = {
    "subtitle date": re.compile(rf"calibration points, as of {_DATE}\."),
    "at-a-glance date and DRC count": re.compile(
        rf"Public counts as of {_DATE}: {_COUNT} cumulative laboratory{_HYPHEN}confirmed cases in the DRC"
    ),
    "Uganda and DRC + Uganda counts": re.compile(
        rf"with {_COUNT} confirmed cases in Uganda, the DRC \+ Uganda count is {_COUNT} confirmed cases"
    ),
    "confirmed deaths": re.compile(rf", and {_COUNT} confirmed deaths in the DRC and Uganda"),
}


def _normalize(text: str) -> str:
    # NFKC spells out ligatures (pypdf returns "ﬁ" where Chrome drew one) and turns
    # non-breaking spaces into spaces; then every run of whitespace becomes one space.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _iso(long_date: str) -> str:
    try:
        return datetime.strptime(long_date, "%d %B %Y").date().isoformat()
    except ValueError as exc:
        raise BriefPdfError(f"page 1 prints an unreadable date {long_date!r}") from exc


def _int(value: str) -> int:
    return int(value.replace(",", ""))


def read_headline(pdf: bytes) -> BriefHeadline:
    """Return the date and headline figures page 1 of the brief prints."""
    text = _normalize(page_text(pdf, 1))
    found = {}
    for label, pattern in _PATTERNS.items():
        matches = pattern.findall(text)
        if len(matches) != 1:
            raise BriefPdfError(
                f"page 1 shows the {label} phrase {len(matches)} times; expected exactly once "
                "(has the page-1 wording in make_brief.py changed?)"
            )
        found[label] = matches[0]
    subtitle_date = _iso(found["subtitle date"])
    glance_date, drc = found["at-a-glance date and DRC count"]
    if _iso(glance_date) != subtitle_date:
        raise BriefPdfError(
            f"page 1 prints two snapshot dates: {subtitle_date} in the subtitle and "
            f"{_iso(glance_date)} in the at-a-glance paragraph"
        )
    uganda, total = found["Uganda and DRC + Uganda counts"]
    return BriefHeadline(
        as_of=subtitle_date,
        drc_confirmed=_int(drc),
        uganda_confirmed=_int(uganda),
        total_confirmed=_int(total),
        confirmed_deaths=_int(found["confirmed deaths"]),
    )


def _whole(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not value.is_integer()):
        raise BriefPdfError(f"the {field} is not a whole number: {value!r}")
    return int(value)


def _expected_headline(snapshot: dict, contract: dict) -> BriefHeadline:
    """The headline the brief must print for this snapshot.

    Date and counts come from the snapshot (``data/live-bdbv-2026-output.json``); the
    DRC national count comes from the snapshot contract's confirmed-case partition,
    the same denominator the brief and every public surface use.
    """
    try:
        total = _whole(snapshot["reported_counts"]["confirmed"]["primary"], "snapshot's confirmed count")
        deaths = _whole(snapshot["reported_deaths"]["confirmed"]["primary"], "snapshot's confirmed deaths")
        drc = _whole(contract["confirmed_case_partition"]["drc_confirmed_total"], "contract's DRC count")
        as_of = str(snapshot["as_of"])[:10]
    except BriefPdfError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise BriefPdfError(f"the snapshot or contract has no headline field: {exc}") from exc
    if str(contract.get("as_of", ""))[:10] != as_of:
        raise BriefPdfError(
            f"the snapshot contract is dated {str(contract.get('as_of'))[:10]}, the snapshot {as_of}"
        )
    return BriefHeadline(
        as_of=as_of,
        drc_confirmed=drc,
        uganda_confirmed=total - drc,
        total_confirmed=total,
        confirmed_deaths=deaths,
    )


def _headline_problems(found: BriefHeadline, expected: BriefHeadline) -> list[str]:
    """Each field where page 1 differs from the snapshot; empty means they agree."""
    labels = {
        "as_of": "snapshot date",
        "drc_confirmed": "DRC confirmed cases",
        "uganda_confirmed": "Uganda confirmed cases",
        "total_confirmed": "DRC + Uganda confirmed cases",
        "confirmed_deaths": "confirmed deaths",
    }
    return [
        f"{label}: page 1 prints {getattr(found, field)}, the snapshot has {getattr(expected, field)}"
        for field, label in labels.items()
        if getattr(found, field) != getattr(expected, field)
    ]


def _brief_pdf_problems(pdf_path: pathlib.Path, expected: BriefHeadline) -> list[str]:
    """Problems with the brief PDF at ``pdf_path`` for this snapshot; empty means current."""
    if not pdf_path.is_file():
        return [f"{pdf_path.name} is missing; the release render must write it (make_brief.py --require-pdf)"]
    try:
        found = read_headline(pdf_path.read_bytes())
    except BriefPdfError as exc:
        return [f"{pdf_path.name} cannot be read: {exc}"]
    return _headline_problems(found, expected)


def release_problems(
    pdf_path: pathlib.Path = DEFAULT_PDF,
    snapshot_path: pathlib.Path = DEFAULT_SNAPSHOT,
    contract_path: pathlib.Path = DEFAULT_CONTRACT,
) -> tuple[list[str], BriefHeadline | None]:
    """The brief PDF gate: ``(problems, expected)``; no problems means the PDF is this snapshot's brief."""
    try:
        expected = _expected_headline(
            json.loads(snapshot_path.read_text(encoding="utf-8")),
            json.loads(contract_path.read_text(encoding="utf-8")),
        )
    except (OSError, json.JSONDecodeError, BriefPdfError) as exc:
        return [f"cannot read the snapshot's headline: {exc}"], None
    return _brief_pdf_problems(pdf_path, expected), expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read the brief PDF's page-1 date and headline.")
    parser.add_argument("pdf", nargs="?", type=pathlib.Path, default=DEFAULT_PDF)
    parser.add_argument("--json", action="store_true", help="Print the reading as JSON (for the cadence).")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare with data/live-bdbv-2026-output.json and data/snapshot_contract.json; exit 1 on a difference.",
    )
    args = parser.parse_args(argv)
    if args.check:
        problems, expected = release_problems(args.pdf)
        for problem in problems:
            print(f"[FAIL] brief PDF gate: {problem}", file=sys.stderr)
        if not problems:
            print(f"brief PDF gate OK ({expected.as_of}, {expected.total_confirmed} confirmed, "
                  f"{expected.confirmed_deaths} confirmed deaths)")
        return 1 if problems else 0
    try:
        found = read_headline(args.pdf.read_bytes())
    except (OSError, BriefPdfError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"[FAIL] {args.pdf}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, **asdict(found)}))
    else:
        print(f"{args.pdf}: as of {found.as_of}; DRC {found.drc_confirmed}, Uganda {found.uganda_confirmed}, "
              f"DRC + Uganda {found.total_confirmed}, confirmed deaths {found.confirmed_deaths}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
