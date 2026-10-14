"""Build the uploadable ebook with nothing but the standard library.

Why this exists: every route in `01-book/build.sh` needs pandoc installed, and the free fallback
is "push and let the GitHub Action do it" — which is fine for the archive and useless at 11 p.m.
when you want to open the file on your own phone before you promise it to a stranger. EPUB is a
zip with three XML files and some XHTML inside it, so the machine can produce it.

What this module does *not* do: write the book, design the cover, or produce the paperback PDF.
A print PDF needs a typesetting engine (xelatex/typst), and pretending otherwise would mean
shipping a PDF with no page geometry — so the PDF stays pandoc's job, documented in
`01-book/build.sh`, and KDP's own print-cost calculator stays the authority on the price.

Two properties are non-negotiable because the operator has to trust the artefact:

* **Deterministic.** The zip is written in a fixed order with a fixed internal timestamp, and the
  EPUB identifier is derived from the content rather than generated at random. So "rebuild it" is
  a diff, not a dice roll, and CI can prove the committed file matches the committed chapters.
* **Honest about what is missing.** No cover is embedded until the cover file exists; the real
  dimensions of that file are checked against KDP's requirement; a `TODO`, a placeholder image or
  a stale manuscript fails the build instead of shipping.
"""

from __future__ import annotations

import html
import io
import json
import re
import subprocess
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import kdp, site

CHAPTER_DIR = "01-book/chapters"
MANUSCRIPT = "01-book/manuscript.md"
META_JSON = "02-kdp/kdp-metadata.json"
COVER_JPG = "02-kdp/cover-2560x1600.jpg"
DEFAULT_OUT = "01-book/build"

KDP_COVER_W, KDP_COVER_H = 2560, 1600
WORDS_PER_READ_MINUTE = 250          # for the "is this too long to read in one sitting" line only

TODO_RE = re.compile(r"\b(TODO|FIXME|TBD|XXX)\b|\[[ ]*(placeholder|insert|fill in|add|write)\b", re.I)
IMAGE_RE = re.compile(r"^!\[", re.M)
VOID_RE = re.compile(r"<(hr|br|img|col|source|wbr)\b([^>]*?)(?<!/)>")


# --------------------------------------------------------------------- parts ----
def _xhtml(body_html: str) -> str:
    """The site renderer emits HTML5; an EPUB spine member must be well-formed XML."""
    return VOID_RE.sub(lambda m: f"<{m.group(1)}{m.group(2)}/>", body_html)


def _plain(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _jpeg_size(path: Path) -> tuple[int, int] | None:
    """(w, h) from the SOF marker, or None if it is not a readable JPEG.

    stdlib only, and deliberately strict: a cover whose filename promises 2560×1600 and whose
    pixels say otherwise is exactly the mistake that costs a rejection at upload.
    """
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return (w, h)
        if marker in (0xDA, 0xD9):        # start of scan / end of image: no size to find
            return None
        i += 2 + seglen
    return None


def _png_size(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        return None
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def image_size(path: Path) -> tuple[int, int] | None:
    if path.suffix.lower() in (".jpg", ".jpeg"):
        return _jpeg_size(path)
    if path.suffix.lower() == ".png":
        return _png_size(path)
    return None


def _stamp(value: str) -> str | None:
    """Normalise an ISO date or datetime into the W3C form EPUB wants, or None."""
    if not value:
        return None
    v = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(v, fmt)
            if fmt.endswith("%z"):
                dt = dt.astimezone(timezone.utc)
            else:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    try:                                        # git's strict ISO form, with a colon in the offset
        dt = datetime.fromisoformat(v).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def git_modified(root: Path, subpath: str) -> str | None:
    """The date of the newest commit touching `subpath`, in UTC.

    Used for `dcterms:modified` — a field that asks *when was this file last changed*, which git
    genuinely knows. Publication dates are a different question and are left out entirely, because
    no one has published anything yet and a back-dated `dc:date` is a small fraud with a timestamp.
    """
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", subpath],
                             cwd=str(root), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return _stamp(out.stdout.strip())


# --------------------------------------------------------------------- model ----
@dataclass
class Chapter:
    src: Path
    name: str                       # "01-the-clock"
    title: str
    file_name: str                  # "OEBPS/ch01-the-clock.xhtml"
    words: int = 0
    xhtml: str = ""
    toc: list[tuple[int, str, str]] = field(default_factory=list)   # (level, id, label)


@dataclass
class BookResult:
    chapters: list[Chapter] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    size_kb: float = 0.0
    epub: Path | None = None

    @property
    def words(self) -> int:
        return sum(c.words for c in self.chapters)

    @property
    def pages(self) -> int:
        return kdp.estimated_pages(self.words)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {"chapters": [{"name": c.name, "title": c.title, "words": c.words} for c in self.chapters],
                "words": self.words, "print_pages_estimate": self.pages, "errors": self.errors,
                "warnings": self.warnings, "size_kb": self.size_kb,
                "fields": {k: v for k, v in self.fields.items() if not isinstance(v, list)},
                "epub": str(self.epub) if self.epub else None}


# ----------------------------------------------------------------- metadata ----
DEFAULT_FIELDS = {
    "title": "The Night Shift Reset",
    "subtitle": "The 28-day system for eating, caffeine and light timing",
    "author": "J. Arek Thorne",
    "language": "en-GB",
    "publisher": "Independently published",
    "rights": "Copyright © 2026 J. Arek Thorne. All rights reserved.",
}


def read_fields(root: Path) -> tuple[dict[str, Any], list[str]]:
    """Listing fields, with the front matter as the fallback.

    `02-kdp/kdp-metadata.json` is the file the operator pastes into the KDP backend, so it is also
    the file the EPUB's own metadata is built from — one source of truth. If it is missing, the book
    still builds from the defaults below, with a warning saying so rather than an error, because the
    ebook and the listing do not have to be written in the same hour.
    """
    fields = dict(DEFAULT_FIELDS)
    warns: list[str] = []
    f = root / META_JSON
    if not f.exists():
        warns.append(f"{META_JSON} not found — the EPUB was titled from the front matter defaults; "
                     "write that file once and both the listing and the book read from it")
        return fields, warns
    try:
        loaded = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        warns.append(f"{META_JSON} is not valid JSON ({e}) — the EPUB was built from defaults instead")
        return fields, warns
    if not isinstance(loaded, dict):
        warns.append(f"{META_JSON} must be a JSON object — built from defaults")
        return fields, warns
    for k, v in loaded.items():
        if isinstance(v, str) and v.strip():
            fields[k] = v.strip()
        elif isinstance(v, list):
            fields[k] = [str(x).strip() for x in v]
    # The copyright line is in the book, so it gets to overrule the default if they disagree.
    front = root / CHAPTER_DIR / "00-front-matter.md"
    if front.exists():
        m = re.search(r"Copyright\s*©?\s*(\d{4})\s+([^.]+)\.", front.read_text(encoding="utf-8"))
        if m:
            year, holder = m.group(1), m.group(2).strip()
            stated = str(fields.get("rights", ""))
            if stated and year not in stated:
                warns.append(f"rights line ({stated!r}) does not mention the year in the front matter "
                             f"(© {year}) — one of the two is out of date")
            if not loaded.get("rights"):
                fields["rights"] = f"Copyright © {year} {holder}. All rights reserved."
    return fields, warns


# -------------------------------------------------------------------- build ----
def chapter_files(root: Path) -> list[Path]:
    d = root / CHAPTER_DIR
    return sorted([p for p in d.glob("*.md") if p.is_file()], key=lambda p: p.name) if d.exists() else []


def assemble_manuscript(root: Path, title: str, author: str) -> str:
    """The single-file source `build.sh` and the GitHub Action both consume.

    The pandoc metadata block stays, because `build.sh`'s pandoc invocations read it and the
    paperback PDF path depends on this exact shape.
    """
    parts = [f"% {title}", f"% {author}", "% epub", ""]
    for f in chapter_files(root):
        parts.append(f.read_text(encoding="utf-8").rstrip("\n"))
        parts.append("\n\\newpage\n")
    return "\n".join(parts) + "\n"


def _opf(fields: dict[str, Any], chapters: list[Chapter], stamp: str, lang: str,
         cover: str | None, id_urn: str) -> str:
    m = [
        f'    <dc:identifier id="BookId">{html.escape(id_urn)}</dc:identifier>',
        f"    <dc:title>{html.escape(fields['title'])}</dc:title>",
        f'    <dc:creator id="creator">{html.escape(fields["author"])}</dc:creator>',
        f"    <dc:language>{html.escape(lang)}</dc:language>",
        f"    <dc:publisher>{html.escape(fields.get('publisher', ''))}</dc:publisher>",
    ]
    # Either key, deliberately: KDP's own field is "description", the JSON slot a model tends to
    # return is "description_html", and a metadata block that silently loses the blurb is the kind
    # of bug only the store's preview shows you.
    blurb = fields.get("description") or fields.get("description_html")
    if blurb:
        m.append(f"    <dc:description>{html.escape(_plain(blurb))}</dc:description>")
    if fields.get("rights"):
        m.append(f"    <dc:rights>{html.escape(fields['rights'])}</dc:rights>")
    m.append(f'    <meta property="dcterms:modified">{stamp}</meta>')
    items = ['    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
             '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
             '    <item id="style" href="style.css" media-type="text/css"/>']
    if cover:
        items.append(f'    <item id="cover-image" href="{cover}" '
                     f'media-type="image/jpeg" properties="cover-image"/>')
        items.append('    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
    for c in chapters:
        items.append(f'    <item id="ch-{c.name}" href="{c.file_name.split("/")[-1]}" '
                     f'media-type="application/xhtml+xml"/>')
    refs = ([f'    <itemref idref="cover"/>'] if cover else []) + \
           [f'    <itemref idref="ch-{c.name}"/>' for c in chapters]
    return "\n".join([
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookId" '
        f'xml:lang="{html.escape(lang)}">',
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
        *m,
        '  </metadata>',
        "  <manifest>", *items, "  </manifest>",
        '  <spine toc="ncx">', *refs, "  </spine>",
        '  <guide>',
        f'    <reference type="text" title="{html.escape(chapters[0].title)}" '
        f'href="{chapters[0].file_name.split("/")[-1]}"/>',
        "  </guide>",
        "</package>", ""])


def _ncx(fields: dict[str, Any], chapters: list[Chapter], id_urn: str) -> str:
    nav = ['      <navMap>']
    order = 1
    for c in chapters:
        nav.append(f'        <navPoint id="np{order}" playOrder="{order}">'
                   f'<navLabel><text>{html.escape(c.title)}</text></navLabel>'
                   f'<content src="{c.file_name.split("/")[-1]}"/></navPoint>')
        order += 1
        for level, anchor, label in c.toc:
            if level != 2:
                continue
            nav.append(f'        <navPoint id="np{order}" playOrder="{order}">'
                       f'<navLabel><text>{html.escape(label)}</text></navLabel>'
                       f'<content src="{c.file_name.split("/")[-1]}#{anchor}"/></navPoint>')
            order += 1
    nav.append("      </navMap>")
    return "\n".join([
        '<?xml version="1.0" encoding="utf-8"?>',
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
        "  <head>",
        f'    <meta name="dtb:uid" content="{html.escape(id_urn)}"/>',
        '    <meta name="dtb:depth" content="2"/>',
        '    <meta name="dtb:totalPageCount" content="0"/>',
        '    <meta name="dtb:maxPageNumber" content="0"/>',
        "  </head>",
        '  <docTitle><text>' + html.escape(fields["title"]) + "</text></docTitle>",
        *nav,
        "</ncx>", ""])


def _nav(fields: dict[str, Any], chapters: list[Chapter], lang: str) -> str:
    li = []
    for c in chapters:
        kids = [f'<li><a href="{c.file_name.split("/")[-1]}#{a}">{html.escape(l)}</a></li>'
                for level, a, l in c.toc if level == 2]
        inner = (f'<a href="{c.file_name.split("/")[-1]}">{html.escape(c.title)}</a>'
                 + (f"<ol>{''.join(kids)}</ol>" if kids else ""))
        li.append(f"<li>{inner}</li>")
    return _doc(lang, "Contents", '<nav epub:type="toc" id="toc"><h1>Contents</h1>'
                                    f'<ol>{"".join(li)}</ol></nav>', extra_ns=" epub")


def _chapter_doc(c: Chapter, fields: dict[str, Any], lang: str) -> str:
    body = f'<section epub:type="chapter" id="ch-{c.name}">{c.xhtml}</section>' 
    return _doc(lang, _plain(c.title), body, extra_ns=" epub")


def _doc(lang: str, title: str, body: str, extra_ns: str = " epub") -> str:
    # `xmlns:epub` on every part, not only where `epub:type` appears today: a part that gains a
    # `properties="cover-image"` or an `epub:type` later should not fail Kindle's parser in CI, and
    # an unbound prefix is precisely the error a hand-written EPUB makes.
    ns = ' xmlns:epub="http://www.idpf.org/2007/ops"' if "epub" in (extra_ns or "epub") else ""
    return "\n".join([
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!DOCTYPE html>",
        f'<html xmlns="http://www.w3.org/1999/xhtml"{ns} xml:lang="{html.escape(lang)}" '
        f'lang="{html.escape(lang)}">',
        f"<head><title>{html.escape(title)}</title><meta charset=\"utf-8\"/>"
        f'<link rel="stylesheet'
        f'" type="text/css" href="style.css"/></head>',
        f"<body>{body}</body>",
        "</html>", ""])


def build_book(root: str | Path = ".", out_dir: str | Path | None = None,
               check: bool = False, write_manuscript: bool = True) -> BookResult:
    """Render `01-book/chapters/*.md` into an uploadable EPUB. Never touches the network."""
    root = Path(root)
    out = Path(out_dir) if out_dir else root / DEFAULT_OUT
    res = BookResult()
    files = chapter_files(root)
    if not files:
        res.errors.append(f"no chapters found in {CHAPTER_DIR}/ — there is nothing to build")
        return res

    fields, warns = read_fields(root)
    res.warnings.extend(warns)
    res.fields = fields
    lang = fields.get("language") or "en-GB"

    # ---- render each chapter, collecting the faults a stranger would notice
    for n, src in enumerate(files):
        text = src.read_text(encoding="utf-8")
        body = text
        m = re.match(r"^#\s+(.+)$", body.strip(), re.M)
        title = ""
        first = body.strip().split("\n", 1)[0]
        if first.startswith("# "):
            title = first[2:].strip()
        elif m:
            title = m.group(1).strip()
        if not title:
            res.errors.append(f"{src}: no `# ` heading — every chapter needs one or the e-reader "
                              "has nothing to put in its table of contents")
            title = src.stem
        if TODO_RE.search(body):
            res.errors.append(f"{src}: unfilled placeholder in a file that will be sold "
                              f"({TODO_RE.search(body).group(0)!r})")
        if IMAGE_RE.search(body):
            res.errors.append(f"{src}: references an image (`![…]'). The site renders those as "
                              "labelled placeholders; a paid ebook must not ship with one — supply "
                              "the file or cut the line")
        links = site.LinkSink()
        inner, heads = site.render_body(body, f"{src.stem}/", links, (), anchor_mode="heading")
        if links.external:
            res.warnings.append(f"{src}: {len(links.external)} external link(s) in the ebook — fine, "
                                "but a reader on e-ink cannot click through to a source, so keep the "
                                "citation text next to it")
        c = Chapter(src=src, name=src.stem, title=title, words=site.count_words(body),
                    file_name=f"OEBPS/{src.stem}.xhtml", xhtml=_xhtml(inner))
        c.toc = [(int(m.group(1)), m.group(2), _plain(m.group(3)))
                 for m in re.finditer(r'<h([12]) id="([^"]+)">(.*?)</h\1>', inner, re.S)]
        if not c.toc:
            res.errors.append(f"{src}: rendered to nothing useful — the file produced no heading")
        res.chapters.append(c)

    chapters = res.chapters
    if len({c.name for c in chapters}) != len(chapters):
        res.errors.append("two chapter files share a basename — the EPUB writes one file per stem")

    # ---- manuscript (the aggregate the Action and build.sh consume)
    manuscript = assemble_manuscript(root, fields["title"], fields["author"])
    mf = root / MANUSCRIPT
    if check:
        if not mf.exists():
            res.errors.append(f"{MANUSCRIPT} does not exist — run `./wf book`")
        elif mf.read_text(encoding="utf-8") != manuscript:
            res.errors.append(f"{MANUSCRIPT} is stale — it no longer matches {CHAPTER_DIR}/, so "
                              "whatever was uploaded last was not this book. Run `./wf book`")
    elif write_manuscript:
        mf.write_text(manuscript, encoding="utf-8")
        res.written.append(mf.relative_to(root))

    # ---- cover, only if it exists, and only checked against the truth of its pixels
    cover = None
    cf = root / COVER_JPG
    if cf.exists():
        size = image_size(cf)
        if size is None:
            res.errors.append(f"{COVER_JPG} is not a readable JPEG — KDP will reject it, and so does "
                              "this build")
        elif size != (KDP_COVER_W, KDP_COVER_H):
            res.errors.append(f"{COVER_JPG} measures {size[0]}×{size[1]}, not "
                              f"{KDP_COVER_W}×{KDP_COVER_H}. Re-export at the size in the brief, or "
                              "rename the file to what it actually is — the name is a promise")
        else:
            cover = cf.name
    else:
        res.warnings.append(f"no cover at {COVER_JPG} — the ebook builds without one, which is "
                            "correct: KDP asks for the ebook cover as a separate upload, so the "
                            "EPUB does not need it embedded")

    # ---- stability: the identifier is a hash of the content, and the timestamp is a git fact
    stamp = _stamp(str(fields.get("modified") or "")) or git_modified(root, "01-book")
    if not stamp:
        stamp = _stamp(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        res.warnings.append("no `modified` in " + META_JSON + " and no git history to read it from, "
                            "so dcterms:modified used today's date — set the field in the metadata "
                            "file if you want this build reproducible")
    # A name-based UUID, not a random one: valid per RFC 4122, identical on every rebuild, and it
    # says what it is — "the book with this content". KDP assigns its own ASIN at upload, and we
    # never invent an ISBN to look more established than we are.
    digest = "".join(c.xhtml for c in chapters) + manuscript
    id_urn = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'wealforge:ebook:' + digest)}"

    parts: dict[str, str] = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '  <rootfiles>\n'
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
            "  </rootfiles>\n</container>\n"),
        "OEBPS/content.opf": _opf(fields, chapters, stamp, lang, cover, id_urn),
        "OEBPS/nav.xhtml": _nav(fields, chapters, lang),
        "OEBPS/toc.ncx": _ncx(fields, chapters, id_urn),
        "OEBPS/style.css": (root / "01-book" / "style.css").read_text(encoding="utf-8"),
    }
    if cover:
        parts["OEBPS/cover.xhtml"] = _doc(lang, "Cover",
                                         '<section epub:type="cover" id="cover">'
                                         '<img alt="Cover" src="cover-2560x1600.jpg"/></section>')
    for c in chapters:
        parts[c.file_name] = _chapter_doc(c, fields, lang)

    # ---- the checks that make this worth running before a deploy
    try:
        import xml.etree.ElementTree as ET
        for name in [k for k in parts if k.endswith((".xhtml", ".opf", ".ncx", ".xml"))]:
            try:
                ET.fromstring(parts[name])
            except ET.ParseError as e:
                res.errors.append(f"{name}: not well-formed XML ({e}) — Kindle will refuse the file")
    except Exception as e:                                       # pragma: no cover
        res.errors.append(f"XML validation could not run: {e}")

    for c in chapters:
        present = set(re.findall(r'id="([^"]+)"', c.xhtml))
        for level, anchor, label in c.toc:
            if anchor not in present:
                res.errors.append(f"{c.src}: the table of contents points at #{anchor}, which the "
                                  "page does not contain")
        for frag in re.findall(r'<a href="[^"]*#([^"]+)"', c.xhtml):
            if frag not in present:
                res.errors.append(f"{c.src}: in-page link to #{frag} has no target on the page")
    words = sum(c.words for c in chapters)
    if words < 1200:
        res.warnings.append(f"{words} words total — that is a lead magnet, not a book. Priced as a "
                            "book it will be refunded; `wf price --words` shows what it supports")
    for c in chapters:
        if c.words < 150:
            res.warnings.append(f"{c.src}: {c.words} words — a chapter this thin reads as a mistake "
                                "in a paid file (a worksheet chapter is the legitimate exception)")

    payload: list[tuple[str, bytes]] = [(k, v.encode("utf-8")) for k, v in sorted(parts.items())
                                        if k != "mimetype"]
    if cover:
        payload.append((f"OEBPS/{cover}", (root / COVER_JPG).read_bytes()))
    data = _pack(payload)
    # Same bytes in --check as on disk, because the number that matters — the one the 70% band's
    # delivery fee is charged against — is the size of the *file*, and an uncompressed estimate
    # would be a different book's figure.
    res.size_kb = round(len(data) / 1024, 1)
    epub_path = Path(out) / "night-shift-reset.epub"
    res.epub = epub_path
    if check:
        return res
    out.mkdir(parents=True, exist_ok=True)
    epub_path.write_bytes(data)
    res.written.append(epub_path.relative_to(root) if epub_path.is_relative_to(root) else epub_path)
    return res


def _pack(payload: list[tuple[str, bytes]]) -> bytes:
    """The zip, in memory, byte-for-byte reproducible.

    `mimetype` first and stored uncompressed — required by the container spec, and the one detail
    that separates a real EPUB from a pile of files in a zip. Fixed internal timestamps and a sorted
    entry list are what let CI diff the build instead of trusting it.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0)),
                   "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in payload:
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
    return buf.getvalue()


# -------------------------------------------------------------------- report ----
def report_markdown(res: BookResult, out_dir: str = DEFAULT_OUT) -> str:
    L = ["# Ebook build report", ""]
    L += [f"**{len(res.chapters)} chapter(s)** · {res.words:,} words · ~{res.pages} print pages · "
          f"{res.size_kb} KB file · {len(res.errors)} error(s), {len(res.warnings)} warning(s)", ""]
    L += ["| chapter | words | est. read | sections |", "|---|---|---|---|"]
    for c in res.chapters:
        mins = max(1, round(c.words / WORDS_PER_READ_MINUTE))
        L.append(f"| {c.title} | {c.words:,} | {mins} min | {len(c.toc) - 1} |")
    f = res.fields
    L += ["", f"**Listing fields** — title: {f.get('title')!r} · author: {f.get('author')!r} · "
              f"language: {f.get('language')!r} · modified: {f.get('modified', 'from git')}", ""]
    if res.size_kb:
        fee = kdp.delivery_fee_kb(res.size_kb)
        L.append(f"Delivery fee at this size: ${fee:.2f} (70%-band books are charged per MB, which is "
                 f"why a text-only file is the cheap one). Confirm the band in the app at upload.")
    if res.errors:
        L += ["", "## Errors", ""] + [f"- {e}" for e in res.errors]
    if res.warnings:
        L += ["", "## Warnings", ""] + [f"- {w}" for w in res.warnings]
    L += ["", f"_Rebuild with `./wf book`. The file under `{out_dir}/` is generated — edit the "
              f"markdown in `{CHAPTER_DIR}/` instead. Paperback PDF still needs `01-book/build.sh` "
              f"(pandoc + a LaTeX engine); this module deliberately does not fake a PDF._"]
    return "\n".join(L) + "\n"


def epub_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return z.namelist()
