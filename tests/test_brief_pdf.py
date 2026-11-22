# SPDX-License-Identifier: Apache-2.0
"""The brief PDF gate: the reader, the headline check, make_brief's required mode and
the release check's placement of the gate."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import stat
import sys
import tempfile
import textwrap
import unittest
import zlib
from dataclasses import asdict
from unittest import mock

import make_brief
import release_snapshot
from lovs import brief_pdf

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ----- A minimal PDF shaped like the one headless Chrome writes -----
# Type0 font with a ToUnicode map, one glyph per Tj with a Td advance, every space an
# ActualText span around a glyph that the map sends elsewhere (Chrome maps it to a line
# feed), FlateDecode streams and a classic cross-reference table.

_CMAP = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfchar
<0003> <000A>
endbfchar
1 beginbfrange
<0100> <01FF> <0000>
endbfrange
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""


def _glyphs(text: str) -> bytes:
    out = []
    for char in text:
        if char == " ":
            out.append(b"/Span<</ActualText ( )>> BDC\n<0003> Tj\nEMC\n5 0 Td\n")
        else:
            out.append(b"<%04X> Tj\n6 0 Td\n" % (0x100 + ord(char)))
    return b"".join(out)


def _content(lines) -> bytes:
    """Each line is a string, or a tuple of runs drawn as separate text objects on one line."""
    parts = []
    for index, line in enumerate(lines):
        runs = (line,) if isinstance(line, str) else line
        y = 800 - 14 * index
        for number, run in enumerate(runs):
            parts.append(
                b"q\n1 0 0 1 %d %d cm\nBT\n/F1 10 Tf\n1 0 0 -1 0 10 Tm\n" % (40 + 200 * number, y)
                + _glyphs(run)
                + b"ET\nQ\n"
            )
    return b"".join(parts)


def _stream(meta: bytes, data: bytes) -> bytes:
    packed = zlib.compress(data)
    return b"<< /Filter /FlateDecode /Length %d %s>>\nstream\n" % (len(packed), meta) + packed + b"\nendstream"


def build_pdf(pages, *, trailer_extra: bytes = b"", raw_content: bytes | None = None) -> bytes:
    """A PDF with one page per entry of ``pages`` (each a list of lines)."""
    page_count = len(pages)
    objects: dict[int, bytes] = {}
    font_num, cmap_num = 3, 4
    first_page = 5
    kids = b" ".join(b"%d 0 R" % (first_page + 2 * i) for i in range(page_count))
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, page_count)
    objects[font_num] = b"<< /Type /Font /Subtype /Type0 /BaseFont /AAAAAA+Test /Encoding /Identity-H /ToUnicode 4 0 R >>"
    objects[cmap_num] = _stream(b"", _CMAP)
    for i, lines in enumerate(pages):
        page_num, content_num = first_page + 2 * i, first_page + 2 * i + 1
        objects[page_num] = (
            b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >> "
            b"/MediaBox [0 0 595 842] /Contents %d 0 R >>" % content_num
        )
        content = raw_content if (raw_content is not None and i == 0) else _content(lines)
        objects[content_num] = _stream(b"", content)
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objects[num] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for num in range(1, size):
        out += b"%010d 00000 n \n" % offsets[num]
    out += b"trailer\n<< /Size %d /Root 1 0 R %s>>\nstartxref\n%d\n%%%%EOF\n" % (size, trailer_extra, xref)
    return bytes(out)


def brief_page_one(
    date: str = "19 November 2026",
    drc: str = "11723",
    uganda: str = "20",
    total: str = "11743",
    deaths: str = "5754",
    glance_date: str | None = None,
) -> list:
    """Page 1 in the brief's wording, wrapped the way Chrome wraps it."""
    return [
        "Bundibugyo virus, Democratic Republic of the Congo, 2026",
        "Latent Outbreak Visibility System (LOVS) applied to the 2026 BDBV outbreak. Ascertainment, detection depth, and",
        f"pre-committed methodology calibration points, as of {date}.",
        ("At a glance.", f" Public counts as of {glance_date or date}: "),
        f"{drc} cumulative laboratory-",
        f"confirmed cases in the DRC (INSP/INRB SitRep; with {uganda} confirmed cases in Uganda, the DRC + Uganda count is {total}",
        f"confirmed cases; earlier anchors are 10 confirmed as of 17 May per WHO PHEIC), and {deaths} confirmed deaths in the DRC and Uganda.",
    ]


EXPECTED = brief_pdf.BriefHeadline("2026-11-19", 11723, 20, 11743, 5754)


def _snapshot(as_of: str = "2026-11-19T23:59:59Z", total: int = 11743, deaths: int = 5754) -> dict:
    return {
        "as_of": as_of,
        "reported_counts": {"confirmed": {"primary": total}},
        "reported_deaths": {"confirmed": {"primary": deaths}},
    }


def _contract(as_of: str = "2026-11-19", drc: int = 11723) -> dict:
    return {"as_of": as_of, "confirmed_case_partition": {"drc_confirmed_total": drc}}


class ReaderTest(unittest.TestCase):
    def test_spaces_come_from_actual_text_and_lines_break_on_baseline(self) -> None:
        text = brief_pdf.page_text(build_pdf([["Hello world", ("Split", " run on one line")]]))
        self.assertEqual(text, "Hello world\nSplit run on one line")

    def test_reads_the_requested_page(self) -> None:
        pdf = build_pdf([["first page"], ["second page"]])
        self.assertEqual(brief_pdf.page_text(pdf, 2), "second page")
        with self.assertRaisesRegex(brief_pdf.BriefPdfError, "page 3 does not exist"):
            brief_pdf.page_text(pdf, 3)

    def test_refuses_what_it_cannot_read(self) -> None:
        pdf = build_pdf([brief_page_one()])
        cases = {
            "not a PDF": (b"plain text", "not a PDF"),
            "truncated": (pdf[: len(pdf) // 2], "startxref"),
            "encrypted": (build_pdf([["x"]], trailer_extra=b"/Encrypt 9 0 R "), "encrypted"),
            "cross-reference stream": (pdf.replace(b"xref\n0 ", b"XREF\n0 "), "cross-reference"),
            "glyph without a ToUnicode entry": (build_pdf([["x"]], raw_content=b"BT /F1 10 Tf <0500> Tj ET"), "ToUnicode"),
            "corrupt stream": (pdf.replace(b"stream\nx\x9c", b"stream\nXX", 1), "corrupt FlateDecode"),
            "string cut after an escape": (build_pdf([["x"]], raw_content=b"BT /F1 10 Tf (abc\\"), "malformed"),
            "bad hex string": (build_pdf([["x"]], raw_content=b"BT /F1 10 Tf <0G> Tj ET"), "malformed"),
            "deep nesting": (build_pdf([["x"]], raw_content=b"[" * 5000), "malformed"),
        }
        for label, (data, message) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(brief_pdf.BriefPdfError, message):
                brief_pdf.page_text(data)

    def test_restoring_the_graphics_state_restores_the_font(self) -> None:
        # The font set inside q ... Q is gone after Q, so this Tj has no font.
        pdf = build_pdf([["x"]], raw_content=b"q BT /F1 10 Tf ET Q BT <0141> Tj ET")
        with self.assertRaisesRegex(brief_pdf.BriefPdfError, "before any font was set"):
            brief_pdf.page_text(pdf)


class HeadlineTest(unittest.TestCase):
    def test_reads_date_and_headline(self) -> None:
        self.assertEqual(brief_pdf.read_headline(build_pdf([brief_page_one()])), EXPECTED)

    def test_reads_thousands_separators(self) -> None:
        pdf = build_pdf([brief_page_one(drc="11,723", total="11,743", deaths="5,754")])
        self.assertEqual(brief_pdf.read_headline(pdf), EXPECTED)

    def test_refuses_separators_out_of_groups_of_three(self) -> None:
        with self.assertRaisesRegex(brief_pdf.BriefPdfError, "DRC count phrase 0 times"):
            brief_pdf.read_headline(build_pdf([brief_page_one(drc="1,1,7,2,3")]))

    def test_refuses_a_phrase_that_is_missing_or_repeated(self) -> None:
        missing = brief_page_one()
        missing[2] = "pre-committed methodology calibration points."
        with self.assertRaisesRegex(brief_pdf.BriefPdfError, "subtitle date phrase 0 times"):
            brief_pdf.read_headline(build_pdf([missing]))
        repeated = brief_page_one() + ["pre-committed methodology calibration points, as of 19 November 2026."]
        with self.assertRaisesRegex(brief_pdf.BriefPdfError, "subtitle date phrase 2 times"):
            brief_pdf.read_headline(build_pdf([repeated]))

    def test_refuses_two_different_dates_on_page_one(self) -> None:
        pdf = build_pdf([brief_page_one(glance_date="18 November 2026")])
        with self.assertRaisesRegex(brief_pdf.BriefPdfError, "two snapshot dates"):
            brief_pdf.read_headline(pdf)

    def test_reads_only_page_one(self) -> None:
        pdf = build_pdf([["Bundibugyo virus"], brief_page_one()])
        with self.assertRaisesRegex(brief_pdf.BriefPdfError, "0 times"):
            brief_pdf.read_headline(pdf)


class GateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.pdf = self.dir / "brief.pdf"
        self.snapshot = self.dir / "live.json"
        self.contract = self.dir / "contract.json"
        self.snapshot.write_text(json.dumps(_snapshot()), encoding="utf-8")
        self.contract.write_text(json.dumps(_contract()), encoding="utf-8")

    def problems(self) -> list[str]:
        return brief_pdf.release_problems(self.pdf, self.snapshot, self.contract)[0]

    def test_happy_path_passes(self) -> None:
        self.pdf.write_bytes(build_pdf([brief_page_one()]))
        self.assertEqual(self.problems(), [])
        self.assertEqual(brief_pdf.release_problems(self.pdf, self.snapshot, self.contract)[1], EXPECTED)

    def test_stale_date_fails(self) -> None:
        self.pdf.write_bytes(build_pdf([brief_page_one(date="18 November 2026")]))
        self.assertEqual(self.problems(), ["snapshot date: page 1 prints 2026-11-18, the snapshot has 2026-11-19"])

    def test_mismatched_headline_fails(self) -> None:
        self.pdf.write_bytes(build_pdf([brief_page_one(deaths="5753")]))
        self.assertEqual(self.problems(), ["confirmed deaths: page 1 prints 5753, the snapshot has 5754"])
        self.pdf.write_bytes(build_pdf([brief_page_one(drc="11722", uganda="21")]))
        self.assertEqual(
            self.problems(),
            [
                "DRC confirmed cases: page 1 prints 11722, the snapshot has 11723",
                "Uganda confirmed cases: page 1 prints 21, the snapshot has 20",
            ],
        )

    def test_missing_or_unreadable_pdf_fails(self) -> None:
        self.assertIn("brief.pdf is missing", self.problems()[0])
        self.pdf.write_bytes(b"%PDF-1.4 truncated")
        self.assertIn("brief.pdf cannot be read", self.problems()[0])

    def test_snapshot_and_contract_of_different_days_fail(self) -> None:
        self.pdf.write_bytes(build_pdf([brief_page_one()]))
        self.contract.write_text(json.dumps(_contract(as_of="2026-11-18")), encoding="utf-8")
        self.assertIn("cannot read the snapshot's headline", self.problems()[0])

    def test_snapshot_figures_must_be_whole_numbers(self) -> None:
        self.pdf.write_bytes(build_pdf([brief_page_one()]))
        for bad in (True, 11743.9, "11743", None):
            with self.subTest(bad=bad):
                self.snapshot.write_text(json.dumps(_snapshot(total=bad)), encoding="utf-8")
                self.assertIn("cannot read the snapshot's headline", self.problems()[0])
        self.snapshot.write_text(json.dumps(_snapshot(total=11743.0)), encoding="utf-8")
        self.assertEqual(self.problems(), [])

    def test_cli_check_and_json(self) -> None:
        self.pdf.write_bytes(build_pdf([brief_page_one()]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(brief_pdf.main(["--json", str(self.pdf)]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"ok": True, **asdict(EXPECTED)})
        self.pdf.write_bytes(b"not a pdf")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(brief_pdf.main(["--json", str(self.pdf)]), 1)
        self.assertFalse(json.loads(out.getvalue())["ok"])

    def test_committed_brief_pdf_is_the_committed_snapshots_brief(self) -> None:
        # The repo invariant CI holds: a data change that is not re-rendered fails here.
        problems, expected = brief_pdf.release_problems()
        self.assertEqual(problems, [])
        snapshot = json.loads((REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text(encoding="utf-8"))
        self.assertEqual(expected.as_of, snapshot["as_of"][:10])


def _fake_chrome(directory: pathlib.Path, *, status: int, body: bytes | None) -> str:
    """An executable that behaves like Chrome's --print-to-pdf: writes ``body``, exits ``status``."""
    script = directory / "fake-chrome"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""
            import sys
            target = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--print-to-pdf=")][0]
            body = {body!r}
            if body is not None:
                open(target, "wb").write(body)
            sys.exit({status})
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


class MakeBriefPdfTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.html = self.dir / "brief.html"
        self.html.write_text("<p>brief</p>", encoding="utf-8")
        self.pdf = self.dir / "out" / "brief.pdf"
        self.pdf.parent.mkdir()
        self.pdf.write_bytes(b"stale PDF from an earlier run")

    def write(self, *, require: bool, chrome: str | None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()

        def lookup() -> str | None:
            # The earlier PDF must already be gone when Chrome is looked for.
            self.assertFalse(self.pdf.exists())
            return chrome

        with mock.patch.object(make_brief, "find_chrome", lookup), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = make_brief.write_pdf(self.html, self.pdf, "2026-11-19T23:59:59Z", require=require)
        return status, out.getvalue(), err.getvalue()

    def test_missing_renderer_fails_in_required_mode(self) -> None:
        status, _, err = self.write(require=True, chrome=None)
        self.assertEqual(status, 1)
        self.assertIn("[FAIL]", err)
        self.assertIn("no headless Chrome found", err)
        self.assertFalse(self.pdf.exists())

    def test_missing_renderer_is_best_effort_in_an_ad_hoc_run(self) -> None:
        status, out, err = self.write(require=False, chrome=None)
        self.assertEqual(status, 0)
        self.assertIn("best-effort ad hoc run", out)
        self.assertIn("removed so a stale PDF cannot ship", out)
        self.assertIn(f"git checkout -- {self.pdf}", out)
        self.assertEqual(err, "")
        self.assertFalse(self.pdf.exists())

    def test_failed_render_fails_in_required_mode_and_leaves_no_partial_file(self) -> None:
        chrome = _fake_chrome(self.dir, status=3, body=b"%PDF-1.4 partial")
        status, _, err = self.write(require=True, chrome=chrome)
        self.assertEqual(status, 1)
        self.assertIn("Chrome exited with status 3", err)
        self.assertFalse(self.pdf.exists())

    def test_empty_render_fails_in_required_mode(self) -> None:
        status, _, err = self.write(require=True, chrome=_fake_chrome(self.dir, status=0, body=None))
        self.assertEqual(status, 1)
        self.assertIn("wrote no PDF", err)

    def test_successful_render_writes_a_date_stamped_pdf(self) -> None:
        chrome = _fake_chrome(self.dir, status=0, body=b"%PDF-1.4\n/CreationDate (D:20200101000000)\n")
        status, out, _ = self.write(require=True, chrome=chrome)
        self.assertEqual(status, 0)
        self.assertIn("wrote", out)
        self.assertIn(b"(D:20261119120000)", self.pdf.read_bytes())

    def test_flag_is_on_the_command_line(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
            make_brief.main(["--help"])
        self.assertEqual(stop.exception.code, 0)
        self.assertIn("--require-pdf", out.getvalue())


class ReleaseCheckPlacementTest(unittest.TestCase):
    def test_release_render_requires_the_pdf(self) -> None:
        stages = dict(release_snapshot.PIPELINE_STAGES)
        self.assertEqual(stages["render brief"][1:], ["make_brief.py", "--require-pdf"])

    def run_check(self, problems: list[str]) -> tuple[int, mock.Mock, str]:
        err = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(release_snapshot, "free_space_preflight", return_value=(True, "space ok")))
            stack.enter_context(mock.patch.object(release_snapshot, "run_pipeline", return_value=True))
            stack.enter_context(
                mock.patch.object(brief_pdf, "release_problems", return_value=(problems, None if problems else EXPECTED))
            )
            run_tests = stack.enter_context(mock.patch.object(release_snapshot, "run_tests", return_value=False))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(err))
            status = release_snapshot.main(["--check"])
        return status, run_tests, err.getvalue()

    def test_a_stale_pdf_stops_the_check_before_the_test_suite(self) -> None:
        status, run_tests, err = self.run_check(["snapshot date: page 1 prints 2026-11-18, the snapshot has 2026-11-19"])
        self.assertEqual(status, 1)
        run_tests.assert_not_called()
        self.assertIn("[FAIL] brief PDF gate", err)
        self.assertIn("page 1 prints 2026-11-18", err)

    def test_a_current_pdf_lets_the_check_reach_the_test_suite(self) -> None:
        _, run_tests, err = self.run_check([])
        run_tests.assert_called_once()
        self.assertNotIn("brief PDF gate", err)


if __name__ == "__main__":
    unittest.main()
