"""Tests for the ebook builder.

The fixture tree is three short chapters I control; the last class runs the builder over *this
repository*. What these tests are really defending is the gap between "an EPUB was produced" and
"a file a stranger can pay for": the container spec, the anchors the e-reader's table of contents
follows, the cover whose pixels have to match its name, and the manuscript aggregate that the
paperback path consumes. Every one of those can be quietly wrong in a way only the reader sees.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wealforge import book  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p

CH0 = """# The Night Shift Reset

## Copyright

Copyright © 2026 J. Arek Thorne. All rights reserved.

## How to use this book

Read chapter one first.
"""

CH1 = """# 1. The Clock You Can't See

Body text about the clock, with a [source](https://example.org/study) and a rule.

---

## The arithmetic

| number | meaning |
|---|---|
| 3-2-1-0 | the countdown |
"""

CH2 = """# 2. The Descent

A table:

| time | action |
|---|---|
| 17:40 | light |

```
FRIDGE CARD
day 1: bed 08:30
```

## Caffeine

Stop at 02:00.
"""

STYLE = "h1 { font-weight: bold; }\n"


def fake_jpeg(w: int, h: int) -> bytes:
    """Smallest thing a JPEG dimension reader must accept: SOI + SOF0 + EOI."""
    return (b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", h, w) + b"\x00" * 9 + b"\xff\xd9")


def fake_png(w: int, h: int) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR" +
            struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="wf-book-"))
        for rel, text in (("01-book/chapters/00-front-matter.md", CH0),
                          ("01-book/chapters/01-the-clock.md", CH1),
                          ("01-book/chapters/02-descent.md", CH2),
                          ("01-book/style.css", STYLE),
                          ("config/site.json", json.dumps({"site_name": "Test", "base_url": ""}))):
            _write(self.root, rel, text)
        self.out = self.root / "01-book" / "build"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def build(self, check=False, write_manuscript=True):
        return book.build_book(self.root, out_dir=self.out, check=check,
                               write_manuscript=write_manuscript)

    def parts(self, res=None):
        res = res or self.build()
        with zipfile.ZipFile(res.epub) as z:
            return {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}

    # ---- the container
    def test_the_mimetype_is_the_first_entry_and_stored_uncompressed(self):
        res = self.build()
        with zipfile.ZipFile(res.epub) as z:
            names = z.namelist()
            self.assertEqual(names[0], "mimetype")
            self.assertEqual(z.read("mimetype").decode(), "application/epub+zip")
            self.assertEqual(z.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(names[1], "META-INF/container.xml")

    def test_every_xml_part_parses_and_the_opf_declares_the_modified_stamp(self):
        parts = self.parts()
        for name, text in parts.items():
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                ET.fromstring(text)                     # Kindle refuses a file that doesn't parse
        opf = parts["OEBPS/content.opf"]
        m = re.search(r'<meta property="dcterms:modified">([^<]+)<', opf)
        self.assertIsNotNone(m, "EPUB3 requires dcterms:modified")
        self.assertRegex(m.group(1), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertNotIn("TODO", m.group(1))

    def test_the_nav_is_a_real_table_of_contents_and_every_anchor_exists(self):
        parts = self.parts()
        nav = parts["OEBPS/nav.xhtml"]
        self.assertIn('epub:type="toc"', nav)
        linked = 0
        for chapter in ("00-front-matter.xhtml", "01-the-clock.xhtml", "02-descent.xhtml"):
            body = parts[f"OEBPS/{chapter}"]
            ids = set(re.findall(r'id="([^"]+)"', body))
            for target in re.findall(rf'href="{chapter}#([^"]+)"', nav):
                self.assertIn(target, ids, f"{chapter}#{target} is in the TOC but not on the page")
                linked += 1
        self.assertGreaterEqual(linked, 3, "a TOC with no sub-entries is not a nav")
        self.assertIn("The arithmetic", nav)
        self.assertIn("Caffeine", nav)
        ncx = parts["OEBPS/toc.ncx"]
        self.assertEqual(len(re.findall(r"<navPoint ", ncx)),
                         len(re.findall(r"<navPoint ", ncx.replace("np", "np"))))
        self.assertIn('name="dtb:uid"', ncx)
        for n, order in enumerate(re.findall(r'playOrder="(\d+)"', ncx), 1):
            self.assertEqual(int(order), n, "playOrder must be sequential or the reader skips entries")

    def test_tables_survive_and_the_ascii_card_stays_monospaced(self):
        parts = self.parts()
        ch = parts["OEBPS/02-descent.xhtml"]
        self.assertIn("<table><thead>", ch)
        self.assertIn("<pre><code>FRIDGE CARD", ch)
        self.assertIn("<hr/>", parts["OEBPS/01-the-clock.xhtml"])   # self-closed for XML validity
        self.assertNotIn("<hr>", ch)

    def test_rebuilding_the_same_chapters_produces_the_same_bytes(self):
        first = self.build().epub.read_bytes()
        second = self.build().epub.read_bytes()
        self.assertEqual(first, second)
        with zipfile.ZipFile(self.build().epub) as z:
            for info in z.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0),
                                 f"{info.filename} carries a build timestamp, so a rebuild diffs")

    def test_the_identifier_is_content_derived_stable_and_a_valid_uuid(self):
        def ident(res):
            opf = self.parts(res)["OEBPS/content.opf"]
            return re.search(r"<dc:identifier[^>]*>urn:uuid:([^<]+)<", opf).group(1)
        a = self.build()
        self.assertEqual(ident(a), ident(self.build()))
        UUID(ident(a))                                    # raises if the shape is invented
        (self.root / "01-book" / "chapters" / "03-new.md").write_text("# 3. New\n\nText here now.\n",
                                                                      encoding="utf-8")
        self.assertNotEqual(ident(a), ident(self.build()), "the id should follow the content")

    # ---- honesty about what is missing
    def test_an_unfilled_placeholder_fails_the_build(self):
        (self.root / "01-book" / "chapters" / "01-the-clock.md").write_text(
            "# 1. The Clock\n\nTODO(human) verify the half-life figure.\n", encoding="utf-8")
        res = self.build()
        self.assertFalse(res.ok)
        self.assertTrue(any("placeholder" in e for e in res.errors), res.errors)

    def test_an_image_reference_fails_rather_than_shipping_a_site_placeholder(self):
        # The site renders a missing photo as a labelled slot. In a paid ebook that slot is a
        # customer complaint, so the build refuses instead.
        (self.root / "01-book" / "chapters" / "04-photo.md").write_text(
            "# 4. Photos\n\n![My room](images/room.jpg)\n", encoding="utf-8")
        res = self.build()
        self.assertFalse(res.ok)
        self.assertTrue(any("image" in e.lower() for e in res.errors), res.errors)

    def test_a_chapter_without_a_heading_fails(self):
        (self.root / "01-book" / "chapters" / "05-lost.md").write_text("Just a paragraph.\n",
                                                                      encoding="utf-8")
        res = self.build()
        self.assertFalse(res.ok)
        self.assertTrue(any("no `# ` heading" in e for e in res.errors), res.errors)

    def test_a_thin_book_warns_instead_of_pretending(self):
        for f in sorted((self.root / "01-book" / "chapters").glob("*.md"))[1:]:
            f.unlink()
        res = self.build()
        self.assertTrue(any("lead magnet" in w for w in res.warnings), res.warnings)

    def test_an_external_link_in_a_chapter_is_reported_not_silently_dropped(self):
        res = self.build()
        self.assertTrue(any("external link" in w for w in res.warnings), res.warnings)

    def test_no_errors_when_there_is_no_cover_because_kdp_wants_it_separately(self):
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        self.assertTrue(any("cover" in w for w in res.warnings), res.warnings)
        self.assertNotIn("OEBPS/cover.xhtml", self.parts(res))

    def test_a_cover_is_embedded_only_if_its_pixels_match_its_name(self):
        d = self.root / "02-kdp"
        d.mkdir(parents=True, exist_ok=True)
        (d / "cover-2560x1600.jpg").write_bytes(fake_jpeg(1000, 800))
        res = self.build()
        self.assertFalse(res.ok)
        self.assertTrue(any("1000×800" in e for e in res.errors), res.errors)
        (d / "cover-2560x1600.jpg").write_bytes(fake_jpeg(2560, 1600))
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        parts = self.parts(res)
        self.assertIn("OEBPS/cover.xhtml", parts)
        self.assertIn('properties="cover-image"', parts["OEBPS/content.opf"])

    def test_a_file_that_is_not_really_a_jpeg_is_an_error(self):
        d = self.root / "02-kdp"
        d.mkdir(parents=True, exist_ok=True)
        (d / "cover-2560x1600.jpg").write_text("printscreen pasted and renamed", encoding="utf-8")
        res = self.build()
        self.assertFalse(res.ok)
        self.assertTrue(any("not a readable JPEG" in e for e in res.errors), res.errors)

    # ---- the manuscript aggregate the pandoc path and the Action consume
    def test_the_manuscript_is_regenerated_and_staleness_fails_a_check(self):
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        ms = (self.root / "01-book" / "manuscript.md").read_text()
        self.assertTrue(ms.startswith("% The Night Shift Reset\n% J. Arek Thorne\n% epub\n"))
        self.assertIn("\\newpage", ms)
        self.assertIn("Stop at 02:00", ms)
        self.assertTrue(self.build(check=True).ok)
        (self.root / "01-book" / "manuscript.md").write_text("% stale\n", encoding="utf-8")
        res = self.build(check=True)
        self.assertFalse(res.ok)
        self.assertTrue(any("stale" in e for e in res.errors), res.errors)

    def test_check_mode_writes_nothing(self):
        before = sorted(p.name for p in self.out.glob("*")) if self.out.exists() else []
        self.build(check=True)
        self.assertEqual(before, sorted(p.name for p in self.out.glob("*")) if self.out.exists() else [])

    # ---- metadata
    def test_the_listing_file_is_the_source_for_the_epub_metadata(self):
        (self.root / "02-kdp").mkdir(parents=True, exist_ok=True)
        _write(self.root, "02-kdp/kdp-metadata.json", json.dumps({
            "title": "Better Title", "author": "Someone Else", "language": "en-GB",
            "rights": "Copyright © 2031 Someone Else. All rights reserved.",
            "description_html": "<p>Long enough description for the test fixture to pass."
                                " And more words so the length rule is satisfied here.</p>",
            "keywords": ["a b", "c d", "e f", "g h", "i j", "k l", "m n"],
            "categories": ["x", "y"], "modified": "2026-03-04"}, ensure_ascii=False))
        parts = self.parts()
        opf = parts["OEBPS/content.opf"]
        self.assertIn("<dc:title>Better Title</dc:title>", opf)
        self.assertIn("<dc:creator id=\"creator\">Someone Else</dc:creator>", opf)
        self.assertIn("<dc:language>en-GB</dc:language>", opf)
        self.assertIn(">2026-03-04T00:00:00Z<", opf)
        self.assertIn('xml:lang="en-GB"', parts["OEBPS/nav.xhtml"])
        self.assertIn("Long enough description", opf)
        self.assertIn("<dc:description>", opf)

    def test_manifest_ids_are_legal_xml_ids_not_paths(self):
        # `id="OEBPS/01-x"` and a leading digit are both invalid xsd:IDs. Kindle lets it slide; every
        # validator, and KDP's own previewer on a bad day, does not.
        opf = self.parts()["OEBPS/content.opf"]
        ids = re.findall(r'<item id="([^"]+)"', opf) + re.findall(r'<itemref idref="([^"]+)"', opf)
        self.assertGreater(len(ids), 5)
        for i in ids:
            self.assertRegex(i, r"^[A-Za-z_][A-Za-z0-9._-]*$", f"{i} is not a valid ID")
        manifest = set(re.findall(r'<item id="([^"]+)"', opf))
        spine = re.findall(r'<itemref idref="([^"]+)"', opf)
        self.assertTrue(set(spine) <= manifest,
                        "every spine reference must resolve to a manifest item")
        self.assertEqual(len(spine), 3, "nav, ncx and style belong in the manifest but not the spine")
        self.assertEqual(manifest - set(spine), {"nav", "ncx", "style"})

    def test_a_rights_line_that_ignores_the_front_matter_year_is_reported(self):
        (self.root / "02-kdp").mkdir(parents=True, exist_ok=True)
        _write(self.root, "02-kdp/kdp-metadata.json", json.dumps(
            {"rights": "Copyright © 1999 Someone. All rights reserved."}))
        res = self.build()
        self.assertTrue(any("out of date" in w for w in res.warnings), res.warnings)

    def test_a_broken_metadata_file_warns_and_builds_from_defaults(self):
        (self.root / "02-kdp").mkdir(parents=True, exist_ok=True)
        (self.root / "02-kdp" / "kdp-metadata.json").write_text("{not json", encoding="utf-8")
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        self.assertTrue(any("not valid JSON" in w for w in res.warnings), res.warnings)

    def test_a_missing_chapter_folder_is_an_error_not_a_crash(self):
        shutil.rmtree(self.root / "01-book" / "chapters")
        res = self.build()
        self.assertFalse(res.ok)
        self.assertIn("no chapters", res.errors[0])

    # ---- small readers that must not be fooled
    def test_dimension_readers(self):
        p = self.root / "x.png"
        p.write_bytes(fake_png(1200, 630))
        self.assertEqual(book.image_size(p), (1200, 630))
        j = self.root / "y.jpg"
        j.write_bytes(fake_jpeg(2560, 1600))
        self.assertEqual(book.image_size(j), (2560, 1600))
        txt = self.root / "z.jpg"
        txt.write_text("nope", encoding="utf-8")
        self.assertIsNone(book.image_size(txt))

    def test_the_stamp_normalises_and_refuses_garbage(self):
        self.assertEqual(book._stamp("2026-03-04"), "2026-03-04T00:00:00Z")
        self.assertEqual(book._stamp("2026-03-04T10:00:00+02:00"), "2026-03-04T08:00:00Z")
        self.assertIsNone(book._stamp("whenever"))
        self.assertIsNone(book._stamp(""))

    def test_the_report_quotes_the_size_that_the_delivery_fee_is_charged_on(self):
        res = self.build()
        md = book.report_markdown(res)
        self.assertIn("**3 chapter(s)**", md)
        self.assertIn("print pages", md)
        self.assertIn("Delivery fee at this size", md)
        self.assertIn("deliberately does not fake a PDF", md)

    def test_the_report_lists_each_chapter_once(self):
        md = book.report_markdown(self.build())
        for want in ("The Clock You Can't See", "The Descent"):
            self.assertIn(want, md)
        self.assertEqual(md.count("| chapter | words |"), 1)


class RepoBook(unittest.TestCase):
    """The promise, applied to the repo: the chapters in git build a clean, uploadable file."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wf-book-repo-"))
        cls.res = book.build_book(REPO, out_dir=cls.tmp, check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_real_manuscript_builds_with_no_errors(self):
        self.assertTrue(self.res.ok, "\n".join(self.res.errors))

    def test_the_committed_manuscript_matches_the_chapters(self):
        f = self.res.fields
        want = book.assemble_manuscript(REPO, f["title"], f["author"])
        self.assertEqual((REPO / book.MANUSCRIPT).read_text(encoding="utf-8"), want,
                         "01-book/manuscript.md is stale — the paperback path and the GitHub "
                         "Action both read that file, so run `./wf book` and commit it")

    def test_every_chapter_in_the_repo_has_a_heading_and_no_placeholders(self):
        titles = []
        for f in book.chapter_files(REPO):
            text = f.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# "), f"{f} must open with an h1")
            titles.append(text.split("\n", 1)[0][2:].strip())
            self.assertIsNone(book.TODO_RE.search(text), f"{f} still has a placeholder")
        self.assertEqual(len(set(titles)), len(titles), "two chapters share a title")

    def test_the_listing_metadata_is_valid_and_matches_the_sheet_it_came_from(self):
        meta = json.loads((REPO / book.META_JSON).read_text(encoding="utf-8"))
        from wealforge import validate
        v = validate.validate_kdp(meta)
        self.assertTrue(v.ok, v.render())
        self.assertEqual([i for i in v.warnings], [], v.render())
        sheet = (REPO / "02-kdp" / "02-KDP-BACKEND-UPLOAD.md").read_text(encoding="utf-8")
        self.assertIn(meta["description_html"], sheet)
        for kw in meta["keywords"]:
            self.assertIn(kw, sheet)
        self.assertEqual(meta["title"], "The Night Shift Reset")
        self.assertEqual(len(meta["keywords"]), 7)
        self.assertEqual(len(meta["categories"]), 2)

    def test_the_declared_language_matches_the_copy_it_declares(self):
        """The site's schema and the EPUB's dc:language both claim a variety; the prose has to use it.

        A `language` key is cheap to write and easy to leave stale, and a wrong one is not a typo —
        it tells a screen reader and a search engine to expect a text they will not find.
        """
        declared = json.loads((REPO / "config" / "site.json").read_text(encoding="utf-8"))["language"]
        meta = json.loads((REPO / book.META_JSON).read_text(encoding="utf-8"))["language"]
        self.assertEqual(declared.replace("-", "").lower()[:2], "en")
        self.assertEqual(meta.lower(), declared.lower(),
                         "the site and the book must not be written in different varieties of English")
        us_only = re.compile(r"\b(apnea|anemia|fiber|color|behavior|neighbor|center|liter|"
                             r"diarrhea|defense|license|program|trash|apartment|faucet|sidewalk|"
                             r"emergency room|organiz[a-z]*|realiz[a-z]*|analyz[a-z]*)\b", re.I)
        uk_only = re.compile(r"\b(colour|behaviour|neighbour|centre|litre|apnoea|organis[a-z]*|"
                             r"realis[a-z]*|programme|licence|kerb|whilst|amongst)\b", re.I)
        # A declared variety is a promise about the *other* one's words: en-GB text must not contain
        # "apnea", en-US text must not contain "apnoea". (`licence`/`license` is the trap here — the
        # noun differs, the verb is "license" in both, so only the noun spelling is listed.)
        want, reject = (("gb", us_only), ("us", uk_only))[0 if declared.lower().endswith("gb") else 1]
        corpus = list((REPO / "01-book" / "chapters").glob("*.md")) + \
                 list((REPO / "04-seo" / "articles").glob("*.md")) + \
                 list((REPO / "04-seo" / "pages").glob("*.md")) + \
                 list((REPO / "05-marketing").glob("*.md"))
        offenders = []
        for f in corpus:
            prose = re.sub(r"```.*?```", "", f.read_text(encoding="utf-8"), flags=re.S)
            for m in reject.finditer(prose):
                offenders.append(f"{f.relative_to(REPO)}: {m.group(0)!r}")
        self.assertEqual(offenders, [],
                         f"declared en-{want} but the copy contains the other variety: {offenders[:6]}")

    def test_the_number_the_sales_page_quotes_is_the_number_the_builder_computes(self):
        """The one length claim a buyer can verify before paying, checked against the build.

        A page that says "~11,600 words" while the manuscript is 11,262 is not a rounding difference;
        it is a promise made by one file and kept by another, and the paperback price is derived from
        the same figure.
        """
        page = (REPO / "04-seo" / "pages" / "03-book.md").read_text(encoding="utf-8")
        m = re.search(r"([\d,]{4,7}) words, which prints to about (\d+) pages", page)
        self.assertIsNotNone(m, "the sales page must state the word count and the print page count")
        words, pages = int(m.group(1).replace(",", "")), int(m.group(2))
        self.assertEqual(words, self.res.words,
                         f"the page promises {words:,} words and the book is {self.res.words:,} — "
                         f"edit 04-seo/pages/03-book.md to say {self.res.words:,}")
        self.assertEqual(pages, self.res.pages,
                         f"the page says {pages} print pages; `wf price`/`wf book` compute "
                         f"{self.res.pages} from the current manuscript")

    def test_the_build_is_reproducible_in_this_repo(self):
        a = book.build_book(REPO, out_dir=self.tmp / "a", write_manuscript=False)
        b = book.build_book(REPO, out_dir=self.tmp / "b", write_manuscript=False)
        self.assertTrue(a.ok and b.ok)
        self.assertEqual((a.epub).read_bytes(), (b.epub).read_bytes(),
                         "the ebook must rebuild byte-for-byte, or CI cannot diff it")


if __name__ == "__main__":
    unittest.main()
