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

The reader is deliberately narrow. It reads what headless Chrome writes: a classic
cross-reference table, FlateDecode content streams, fonts with ToUnicode maps, and
spaces carried as ActualText spans. Anything else raises :class:`BriefPdfError`, so a
change in Chrome's output fails the gate instead of passing it.

``release_snapshot.py`` runs the gate through :func:`release_problems`. The evidence-server
cadence runs ``python3 -m lovs.brief_pdf --json <pdf>`` and parses its output, so that
command and its JSON fields are a contract. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import zlib
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


# ----- PDF objects -----

_WHITESPACE = b" \t\r\n\f\x00"
_DELIMITERS = b"()<>[]{}/%"


class Name(str):
    """A PDF name, without its leading slash."""


@dataclass(frozen=True)
class Ref:
    num: int
    gen: int


@dataclass(frozen=True)
class Operator:
    word: str


class _Lexer:
    """Tokenizes PDF object syntax. Content streams reuse it and receive operators."""

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def _skip(self) -> None:
        data = self.data
        while self.pos < len(data):
            byte = data[self.pos]
            if byte in _WHITESPACE:
                self.pos += 1
            elif byte == 0x25:  # % comment
                end = data.find(b"\n", self.pos)
                self.pos = len(data) if end == -1 else end + 1
            else:
                return

    def at_end(self) -> bool:
        self._skip()
        return self.pos >= len(self.data)

    def peek_keyword(self, word: bytes) -> bool:
        self._skip()
        end = self.pos + len(word)
        return self.data[self.pos:end] == word and (
            end >= len(self.data) or self.data[end] in _WHITESPACE + _DELIMITERS
        )

    def value(self):
        """Read one value. Integers followed by ``<gen> R`` become a :class:`Ref`."""
        self._skip()
        data = self.data
        if self.pos >= len(data):
            raise BriefPdfError("unexpected end of PDF data")
        byte = data[self.pos]
        if data.startswith(b"<<", self.pos):
            self.pos += 2
            result: dict[str, object] = {}
            while True:
                self._skip()
                if data.startswith(b">>", self.pos):
                    self.pos += 2
                    return result
                key = self.value()
                if not isinstance(key, Name):
                    raise BriefPdfError(f"dictionary key is not a name at byte {self.pos}")
                result[key] = self.value()
        if byte == 0x5B:  # [
            self.pos += 1
            items = []
            while True:
                self._skip()
                if self.pos < len(data) and data[self.pos] == 0x5D:  # ]
                    self.pos += 1
                    return items
                items.append(self.value())
        if byte == 0x28:  # (
            return self._literal_string()
        if byte == 0x3C:  # <
            end = data.find(b">", self.pos)
            if end == -1:
                raise BriefPdfError("unterminated hex string")
            digits = re.sub(rb"\s", b"", data[self.pos + 1:end])
            self.pos = end + 1
            if len(digits) % 2:
                digits += b"0"
            return bytes.fromhex(digits.decode("ascii"))
        if byte == 0x2F:  # /
            self.pos += 1
            start = self.pos
            while self.pos < len(data) and data[self.pos] not in _WHITESPACE + _DELIMITERS:
                self.pos += 1
            raw = data[start:self.pos]
            return Name(re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw).decode("latin-1"))
        if byte in b"{}":  # PostScript procedure braces (CMap files): no meaning for text
            self.pos += 1
            return Operator(chr(byte))
        start = self.pos
        while self.pos < len(data) and data[self.pos] not in _WHITESPACE + _DELIMITERS:
            self.pos += 1
        word = data[start:self.pos]
        if not word:
            raise BriefPdfError(f"unexpected byte {chr(byte)!r} at {start}")
        if re.fullmatch(rb"[+-]?\d+", word):
            number = int(word)
            mark = self.pos
            match = re.compile(rb"\s+(\d+)\s+R(?![^\s()<>\[\]{}/%])").match(data, self.pos)
            if match:
                self.pos = match.end()
                return Ref(number, int(match.group(1)))
            self.pos = mark
            return number
        if re.fullmatch(rb"[+-]?(\d+\.\d*|\.\d+|\d+\.)", word):
            return float(word)
        if word == b"true":
            return True
        if word == b"false":
            return False
        if word == b"null":
            return None
        return Operator(word.decode("latin-1"))

    def _literal_string(self) -> bytes:
        data = self.data
        self.pos += 1
        depth = 1
        out = bytearray()
        escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
        while self.pos < len(data):
            byte = data[self.pos]
            self.pos += 1
            if byte == 0x5C:  # backslash
                nxt = data[self.pos]
                self.pos += 1
                if nxt in escapes:
                    out.append(escapes[nxt])
                elif 0x30 <= nxt <= 0x37:
                    digits = bytes([nxt])
                    while len(digits) < 3 and 0x30 <= data[self.pos] <= 0x37:
                        digits += bytes([data[self.pos]])
                        self.pos += 1
                    out.append(int(digits, 8) & 0xFF)
                elif nxt == 0x0D:
                    if data[self.pos] == 0x0A:
                        self.pos += 1
                elif nxt != 0x0A:
                    out.append(nxt)
            elif byte == 0x28:
                depth += 1
                out.append(byte)
            elif byte == 0x29:
                depth -= 1
                if depth == 0:
                    return bytes(out)
                out.append(byte)
            else:
                out.append(byte)
        raise BriefPdfError("unterminated literal string")


class _Document:
    """Objects of a PDF with a classic cross-reference table (what Chrome writes)."""

    def __init__(self, data: bytes) -> None:
        if not data.startswith(b"%PDF-"):
            raise BriefPdfError("not a PDF (no %PDF- header)")
        self.data = data
        self.offsets: dict[int, int] = {}
        self.trailer: dict[str, object] = {}
        self._cache: dict[int, object] = {}
        self._read_xref()
        if "Encrypt" in self.trailer:
            raise BriefPdfError("encrypted PDF")

    def _read_xref(self) -> None:
        data = self.data
        marker = data.rfind(b"startxref")
        if marker == -1:
            raise BriefPdfError("no startxref")
        match = re.match(rb"startxref\s+(\d+)", data[marker:])
        if not match:
            raise BriefPdfError("unreadable startxref")
        pos = int(match.group(1))
        if not data.startswith(b"xref", pos):
            raise BriefPdfError("no classic cross-reference table (a cross-reference stream is not supported)")
        pos += 4
        while True:
            header = re.compile(rb"\s*(\d+)\s+(\d+)\s*\r?\n").match(data, pos)
            if not header:
                break
            first, count = int(header.group(1)), int(header.group(2))
            pos = header.end()
            for index in range(count):
                entry = re.compile(rb"\s*(\d{10})\s(\d{5})\s([nf])").match(data, pos)
                if not entry:
                    raise BriefPdfError(f"unreadable cross-reference entry for object {first + index}")
                pos = entry.end()
                if entry.group(3) == b"n":
                    self.offsets[first + index] = int(entry.group(1))
        lexer = _Lexer(data, pos)
        if not lexer.peek_keyword(b"trailer"):
            raise BriefPdfError("no trailer after the cross-reference table")
        lexer.pos = data.index(b"trailer", lexer.pos) + len(b"trailer")
        trailer = lexer.value()
        if not isinstance(trailer, dict):
            raise BriefPdfError("trailer is not a dictionary")
        if "Prev" in trailer:
            raise BriefPdfError("incrementally updated PDF (more than one cross-reference table)")
        self.trailer = trailer

    def get(self, num: int):
        if num in self._cache:
            return self._cache[num]
        if num not in self.offsets:
            raise BriefPdfError(f"object {num} is not in the cross-reference table")
        lexer = _Lexer(self.data, self.offsets[num])
        header = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj\b").match(self.data, lexer.pos)
        if not header or int(header.group(1)) != num:
            raise BriefPdfError(f"object {num} is not at its cross-reference offset")
        lexer.pos = header.end()
        value = lexer.value()
        if lexer.peek_keyword(b"stream"):
            if not isinstance(value, dict):
                raise BriefPdfError(f"object {num} has a stream without a dictionary")
            start = self.data.index(b"stream", lexer.pos) + len(b"stream")
            if self.data.startswith(b"\r\n", start):
                start += 2
            elif self.data.startswith(b"\n", start):
                start += 1
            length = self.resolve(value.get("Length"))
            if not isinstance(length, int):
                raise BriefPdfError(f"object {num} has no stream length")
            value = _Stream(value, self.data[start:start + length])
        self._cache[num] = value
        return value

    def resolve(self, value):
        seen = 0
        while isinstance(value, Ref):
            value = self.get(value.num)
            seen += 1
            if seen > 32:
                raise BriefPdfError("reference loop")
        return value

    def pages(self) -> list[dict]:
        root = self.resolve(self.trailer.get("Root"))
        if not isinstance(root, dict):
            raise BriefPdfError("no document catalog")
        out: list[dict] = []

        def walk(node, depth: int) -> None:
            node = self.resolve(node)
            if not isinstance(node, dict) or depth > 32:
                raise BriefPdfError("malformed page tree")
            if node.get("Type") == "Pages":
                for kid in node.get("Kids") or []:
                    walk(kid, depth + 1)
            else:
                out.append(node)

        walk(root.get("Pages"), 0)
        return out


@dataclass(frozen=True)
class _Stream:
    meta: dict
    raw: bytes

    def decoded(self) -> bytes:
        filters = self.meta.get("Filter")
        if filters is None:
            return self.raw
        if isinstance(filters, Name):
            filters = [filters]
        data = self.raw
        for name in filters:
            if name != "FlateDecode":
                raise BriefPdfError(f"unsupported stream filter /{name}")
            if self.meta.get("DecodeParms"):
                raise BriefPdfError("unsupported FlateDecode parameters")
            try:
                data = zlib.decompress(data)
            except zlib.error as exc:
                raise BriefPdfError(f"corrupt FlateDecode stream: {exc}") from exc
        return data


# ----- Fonts and text -----


def _utf16(units: bytes) -> str:
    return units.decode("utf-16-be", errors="replace")


def _read_cmap(data: bytes) -> dict[bytes, str]:
    """Parse a ToUnicode CMap's bfchar and bfrange sections."""
    mapping: dict[bytes, str] = {}
    lexer = _Lexer(data)
    while not lexer.at_end():
        token = lexer.value()
        if not isinstance(token, Operator):
            continue
        if token.word == "beginbfchar":
            while not lexer.peek_keyword(b"endbfchar"):
                src, dst = lexer.value(), lexer.value()
                if not isinstance(src, bytes) or not isinstance(dst, bytes):
                    raise BriefPdfError("malformed bfchar entry in a ToUnicode map")
                mapping[src] = _utf16(dst)
            lexer.value()
        elif token.word == "beginbfrange":
            while not lexer.peek_keyword(b"endbfrange"):
                low, high, dst = lexer.value(), lexer.value(), lexer.value()
                if not isinstance(low, bytes) or not isinstance(high, bytes) or len(low) != len(high):
                    raise BriefPdfError("malformed bfrange entry in a ToUnicode map")
                lo, hi = int.from_bytes(low, "big"), int.from_bytes(high, "big")
                if hi < lo or hi - lo > 0xFFFF:
                    raise BriefPdfError("malformed bfrange bounds in a ToUnicode map")
                for offset, code in enumerate(range(lo, hi + 1)):
                    key = code.to_bytes(len(low), "big")
                    if isinstance(dst, list):
                        if offset < len(dst) and isinstance(dst[offset], bytes):
                            mapping[key] = _utf16(dst[offset])
                    elif isinstance(dst, bytes) and dst:
                        last = int.from_bytes(dst[-2:], "big") + offset
                        mapping[key] = _utf16(dst[:-2] + (last & 0xFFFF).to_bytes(2, "big"))
                    else:
                        raise BriefPdfError("malformed bfrange destination in a ToUnicode map")
            lexer.value()
    return mapping


class _Font:
    def __init__(self, doc: _Document, font: dict) -> None:
        self.code_length = 2 if font.get("Subtype") == "Type0" else 1
        to_unicode = doc.resolve(font.get("ToUnicode"))
        self.mapping = _read_cmap(to_unicode.decoded()) if isinstance(to_unicode, _Stream) else {}
        self.unmapped = 0

    def decode(self, data: bytes) -> str:
        step = self.code_length
        chars = []
        for index in range(0, len(data) - step + 1, step):
            code = data[index:index + step]
            text = self.mapping.get(code)
            if text is None:
                self.unmapped += 1
                text = "�"
            chars.append(text)
        return "".join(chars)


def _resources(doc: _Document, page: dict) -> dict:
    node = page
    for _ in range(32):
        resources = doc.resolve(node.get("Resources"))
        if isinstance(resources, dict):
            return resources
        parent = doc.resolve(node.get("Parent"))
        if not isinstance(parent, dict):
            return {}
        node = parent
    raise BriefPdfError("page resources nest too deeply")


def _content(doc: _Document, page: dict) -> bytes:
    contents = doc.resolve(page.get("Contents"))
    streams = contents if isinstance(contents, list) else [contents]
    parts = []
    for stream in streams:
        stream = doc.resolve(stream)
        if not isinstance(stream, _Stream):
            raise BriefPdfError("page contents are not a stream")
        parts.append(stream.decoded())
    return b"\n".join(parts)


def _actual_text(properties) -> str | None:
    if isinstance(properties, dict) and isinstance(properties.get("ActualText"), bytes):
        raw = properties["ActualText"]
        if raw.startswith(b"\xfe\xff"):
            return _utf16(raw[2:])
        return raw.decode("latin-1")
    return None


_Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: _Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
# Glyphs whose baselines differ by more than this (in page units, 1/72 inch) are on
# different lines. The brief's smallest type is about 7 pt, so lines are 8 or more apart.
_LINE_TOLERANCE = 2.0


def _multiply(m: _Matrix, n: _Matrix) -> _Matrix:
    a, b, c, d, e, f = m
    p, q, r, s, t, u = n
    return (a * p + b * r, a * q + b * s, c * p + d * r, c * q + d * s, e * p + f * r + t, e * q + f * s + u)


def _numbers(operands: list, count: int) -> list[float] | None:
    tail = operands[-count:] if len(operands) >= count else []
    if len(tail) != count or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in tail):
        return None
    return [float(x) for x in tail]


def page_text(pdf: bytes, page_number: int = 1) -> str:
    """Return the text of one page (1-based), in drawing order.

    A line break is written where a glyph's baseline (its position on the page, from
    the graphics and text matrices) moves to a new line. ActualText spans, which
    Chrome writes around every space, replace the glyphs they enclose. Anything the
    reader cannot read raises ``BriefPdfError``.
    """
    try:
        return _page_text(pdf, page_number)
    except BriefPdfError:
        raise
    except (ValueError, IndexError, KeyError, TypeError, RecursionError) as exc:
        raise BriefPdfError(f"the PDF is malformed ({type(exc).__name__}: {exc})") from exc


def _page_text(pdf: bytes, page_number: int) -> str:
    doc = _Document(pdf)
    pages = doc.pages()
    if not 1 <= page_number <= len(pages):
        raise BriefPdfError(f"the PDF has {len(pages)} page(s); page {page_number} does not exist")
    page = pages[page_number - 1]
    font_dict = doc.resolve(_resources(doc, page).get("Font")) or {}
    fonts: dict[str, _Font] = {}
    lexer = _Lexer(_content(doc, page))
    out: list[str] = []
    operands: list = []
    marked: list[str | None] = []  # one entry per open marked-content span
    font: _Font | None = None
    ctm: _Matrix = _IDENTITY
    saved: list[tuple[_Matrix, _Font | None]] = []  # q/Q: the matrix and the font
    line_matrix: _Matrix = _IDENTITY
    leading = 0.0
    last_y: float | None = None

    def show(data: bytes) -> None:
        nonlocal last_y
        if font is None:
            raise BriefPdfError("text shown before any font was set")
        y = _multiply(line_matrix, ctm)[5]
        if last_y is not None and abs(y - last_y) > _LINE_TOLERANCE:
            out.append("\n")
        last_y = y
        text = font.decode(data)
        if not any(span is not None for span in marked):
            out.append(text)

    def move(tx: float, ty: float) -> None:
        nonlocal line_matrix
        line_matrix = _multiply((1.0, 0.0, 0.0, 1.0, tx, ty), line_matrix)

    while not lexer.at_end():
        token = lexer.value()
        if not isinstance(token, Operator):
            operands.append(token)
            continue
        op = token.word
        if op == "q":
            saved.append((ctm, font))
        elif op == "Q":
            if not saved:
                raise BriefPdfError("unbalanced graphics state (Q without q)")
            ctm, font = saved.pop()
        elif op == "cm" and (values := _numbers(operands, 6)):
            ctm = _multiply(tuple(values), ctm)
        elif op == "BT":
            line_matrix = _IDENTITY
        elif op == "Tm" and (values := _numbers(operands, 6)):
            line_matrix = tuple(values)
        elif op in ("Td", "TD") and (values := _numbers(operands, 2)):
            move(*values)
            if op == "TD":
                leading = -values[1]
        elif op == "TL" and (values := _numbers(operands, 1)):
            leading = values[0]
        elif op == "T*":
            move(0.0, -leading)
        elif op == "Tf" and len(operands) >= 2 and isinstance(operands[-2], Name):
            name = operands[-2]
            if name not in fonts:
                font_obj = doc.resolve(font_dict.get(name)) if isinstance(font_dict, dict) else None
                if not isinstance(font_obj, dict):
                    raise BriefPdfError(f"font /{name} is not in the page resources")
                fonts[name] = _Font(doc, font_obj)
            font = fonts[name]
        elif op == "Tj" and operands and isinstance(operands[-1], bytes):
            show(operands[-1])
        elif op == "TJ" and operands and isinstance(operands[-1], list):
            for item in operands[-1]:
                if isinstance(item, bytes):
                    show(item)
        elif op in ("'", '"') and operands and isinstance(operands[-1], bytes):
            move(0.0, -leading)
            show(operands[-1])
        elif op == "BDC":
            text = _actual_text(doc.resolve(operands[-1]) if operands else None)
            marked.append(text)
            if text is not None:
                out.append(text)
        elif op == "BMC":
            marked.append(None)
        elif op == "EMC":
            if not marked:
                raise BriefPdfError("unbalanced marked content (EMC without BDC/BMC)")
            marked.pop()
        elif op == "BI":
            end = lexer.data.find(b"EI", lexer.pos)
            if end == -1:
                raise BriefPdfError("unterminated inline image")
            lexer.pos = end + 2
        operands = []
    unmapped = sum(f.unmapped for f in fonts.values())
    if unmapped:
        raise BriefPdfError(f"{unmapped} glyph(s) on page {page_number} have no ToUnicode mapping")
    return "".join(out)


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
    return re.sub(r"\s+", " ", text.replace(" ", " ").replace(" ", " ")).strip()


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
