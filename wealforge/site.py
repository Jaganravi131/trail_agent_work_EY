"""Static-site builder for the SEO mini-site (markdown in, deployable folder out).

    ./wf site                      # build docs/ from 04-seo/ + 03-tool/
    ./wf site --check              # the same build, but 404s and gaps fail the command
    ./wf site --out _preview       # somewhere else, for looking at it

Why this exists: the articles, the about page and the editorial policy are the asset, but the
thing an operator can actually publish is a folder of HTML. Hand-converting seven pages is where
links rot, disclosures go missing and the deploy stalls. And because the output is generated,
`--check` can be a *gate* on the site graph: every internal link resolves, no page is orphaned,
affiliate links are disclosed, nothing uses an absolute self-link (which breaks on a GitHub
Pages subpath), and the deployable folder matches its sources byte for byte.

Stdlib only, like the rest of the package, and deliberately **deterministic**: no clock, no
sort-by-anything-random, same inputs → same bytes. That is what lets CI fail on a stale `docs/`
folder instead of trusting a human to rebuild it.
"""

from __future__ import annotations

import html
import unicodedata
import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import gate as gate_mod

# The site's sources: prose in, plus the tool which is already HTML.
SOURCE_DIRS = ("04-seo/articles", "04-seo/pages")
TOOL_DIR = "03-tool"
DEFAULT_OUT = "docs"

REQUIRED_KEYS = ("title", "slug", "description")
AFFILIATE_DEFAULT = ("amzn.to", "amazon.com", "amazon.co.uk", "awin1.com", "impact.com")

#: Strings that must never reach a published page. `TODO(human)` in a draft is a feature; in
#: `docs/` it is a 404-shaped hole in something people will read.
PLACEHOLDER_RX = re.compile(r"TODO\(human\)|REPLACE-WITH|FIXME|\{\{|\$\{")


# --------------------------------------------------------------- front matter ----
def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """The subset of YAML this repo's pages actually use: `key: value`, quoted scalars and
    inline lists. Deliberately not a YAML parser — a 30-line reader with an error message you
    can act on beats a dependency, and the format is ours.

    Raises ValueError rather than guessing: half-parsed front matter produces a page whose title
    or description is silently wrong, which is the kind of defect nobody notices until a reader does.
    """
    if not text.startswith("---"):
        raise ValueError("no front matter block (expected the file to start with ---)")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("front matter block is never closed")
    raw = text[4:end].strip("\n")
    body = text[end + 4:].lstrip("\n")
    meta: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"front matter line without a key: {line!r}")
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items = [i.strip().strip("\"'") for i in inner.split(",")] if inner else []
            meta[key] = [i for i in items if i]
        elif (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            meta[key] = val[1:-1]
        else:
            if val.startswith("[") or val.endswith("]"):
                raise ValueError(f"{key}: unterminated list in front matter")
            quote = val[:1]
            if quote in ('"', "'") and not val.endswith(quote):
                # A quote opened and never closed. The value would otherwise keep a stray mark and
                # show up in the <title> tag, which is a defect a reader sees and a writer never
                # does — two files in this repo shipped that way before this line existed.
                raise ValueError(f"{key}: front matter value opens with {quote} and never closes it")
            if val[-1:] in ('"', "'") and val[-1:] != val[:1]:
                raise ValueError(f"{key}: front matter value ends with {val[-1]} and never opens it")
            meta[key] = val
    missing = [k for k in REQUIRED_KEYS if k not in meta]
    if missing:
        raise ValueError(f"front matter missing {', '.join(missing)}")
    slug = str(meta["slug"])
    if not re.fullmatch(r"/[a-z0-9][a-z0-9/-]*/", slug):
        raise ValueError(f"slug must look like /a-page/: {slug!r}")
    return meta, body


# ------------------------------------------------------------------ markdown ----
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_EM = re.compile(r"(?<![\*\w])\*([^*\n]+?)\*(?!\*)", re.S)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def _is_table_sep(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line)) and "-" in line


class LinkSink:
    """Collects every internal link the renderer emits, so the checker can resolve them
    without re-parsing HTML."""

    def __init__(self) -> None:
        self.internal: list[tuple[str, str]] = []      # (page slug, target slug)
        self.external: list[str] = []
        self.affiliate: list[tuple[str, str]] = []     # (page slug, url)


WORDS_RE = re.compile(r"[A-Za-z0-9'\u2019-]+")


def count_words(text: str) -> int:
    """How long is this? One answer, because `wf price`, the page estimate and the site's thinness
    warning all read the same number — and a second definition two words apart is how a document
    ends up explaining why its own figures disagree."""
    return len(WORDS_RE.findall(text))


def render_inline(src: str, page_slug: str, links: LinkSink, aff_hosts: tuple[str, ...]) -> str:
    """Inline markdown → HTML. Escaping happens first, so a stray `<` in prose stays prose."""
    out = html.escape(src, quote=False)
    # Images: resolved by the builder (it knows what exists on disk); here they become a
    # placeholder figure so a missing photo is visible instead of a torn icon.
    def img_sub(m: re.Match[str]) -> str:
        alt, target = m.group(1), m.group(2)
        return (f'<figure class="photo-slot" data-src="{html.escape(target, quote=True)}">'
                f'<span class="photo-note">photo to supply: {html.escape(target)}</span>'
                f'<figcaption>{alt}</figcaption></figure>')
    out = _IMG.sub(img_sub, out)
    out = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = _EM.sub(lambda m: f"<em>{m.group(1)}</em>", out)

    def link_sub(m: re.Match[str]) -> str:
        text, href = m.group(1), m.group(2)
        if href.startswith(("http://", "https://")):
            host = re.sub(r"^https?://", "", href).split("/")[0].lower()
            rel = ["noopener", "noreferrer"]
            if any(host == a or host.endswith("." + a) for a in aff_hosts):
                rel.append("sponsored")
                rel.append("nofollow")
                links.affiliate.append((page_slug, href))
            else:
                rel.append("nofollow")     # the site's own rule: never hand authority away
            links.external.append(href)
            return f'<a href="{href}" rel="{" ".join(rel)}" target="_blank">{text}</a>'
        if href.startswith("/"):
            target = href.strip("/") + "/"
            links.internal.append((page_slug, target))
            # Relative, always. `/slug/` breaks the moment the site lives under a subpath, and
            # GitHub Pages is one of the two free hosts this project recommends.
            return f'<a href="../{target if target != "index/" else ""}">{text}</a>'
        return f'<a href="{href}">{text}</a>'
    return _LINK.sub(link_sub, out)


def heading_anchor(text: str) -> str:
    """Heading text → id. Kept ASCII and short because it ends up in an EPUB nav and a
    URL fragment; an accented or punctuated id is legal but miserable to type."""
    t = re.sub(r"[*_`\[\]()#|]", " ", text)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return t or "section"


def render_body(body: str, page_slug: str, links: LinkSink, aff_hosts: tuple[str, ...],
                anchor_mode: str = "page") -> tuple[str, list[str]]:
    """Block-level markdown subset → HTML. Returns (html, headings) for the TOC/word counts."""
    lines = body.split("\n")
    out: list[str] = []
    heads: list[str] = []
    used_anchors: dict[str, int] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        strip = line.strip()
        if not strip:
            i += 1
            continue
        if strip in ("---", "***"):
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", strip)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            heads.append(text)
            if anchor_mode == "heading":
                # Unique per heading, so an e-reader's table of contents can point at each one.
                anchor = heading_anchor(text)
                n = used_anchors.get(anchor, 0)
                used_anchors[anchor] = n + 1
                if n:
                    anchor = f"{anchor}-{n + 1}"
            else:
                anchor = posixpath.basename(page_slug.strip("/")) or "top"
            out.append(f'<h{level} id="{anchor}">{render_inline(text, page_slug, links, aff_hosts)}</h{level}>')
            i += 1
            continue
        if strip.startswith("|") and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            header = [c.strip() for c in strip.strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{render_inline(c, page_slug, links, aff_hosts)}</th>" for c in header)
            trs = []
            for r in rows:
                r = (r + [""] * len(header))[:len(header)]
                trs.append("<tr>" + "".join(f"<td>{render_inline(c, page_slug, links, aff_hosts)}</td>"
                                            for c in r) + "</tr>")
            out.append('<table><thead><tr>' + th + "</tr></thead><tbody>" + "".join(trs) + "</tbody></table>")
            continue
        if strip.startswith("```"):
            # Fenced code. The book's fridge card and log grid are ASCII, and a reflowed
            # monospace box is not a cute failure — it is unreadable on a 600 px e-reader.
            lang = strip[3:].strip()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i].rstrip())
                i += 1
            i += 1                                    # consume the closing fence (or end of body)
            cls = f' class="lang-{html.escape(lang, quote=True)}"' if lang else ""
            out.append(f"<pre><code{cls}>" + html.escape("\n".join(buf), quote=False) + "</code></pre>")
            continue
        if strip.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner = render_inline(" ".join(q.strip() for q in quote), page_slug, links, aff_hosts)
            out.append(f"<blockquote><p>{inner}</p></blockquote>")
            continue
        m = re.match(r"^([-*]|\d+\.)\s+(.*)$", strip)
        if m:
            ordered = m.group(1)[0].isdigit()
            items = []
            while i < len(lines):
                mm = re.match(r"^([-*]|\d+\.)\s+(.*)$", lines[i].strip())
                if not mm:
                    break
                items.append(mm.group(2).strip())
                i += 1
            tag = "ol" if ordered else "ul"
            lis = "".join(f"<li>{render_inline(x, page_slug, links, aff_hosts)}</li>" for x in items)
            out.append(f"<{tag}>{lis}</{tag}>")
            continue
        para = [strip]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,4}\s|[-*]\s|\d+\.\s|\||>|!|```)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + render_inline(" ".join(para), page_slug, links, aff_hosts) + "</p>")
    return "\n".join(out), heads


# --------------------------------------------------------------------- model ----
@dataclass
class Page:
    slug: str                      # "sleep-after-night-shift/" (no leading slash)
    src: Path
    title: str
    description: str
    last_reviewed: str = ""
    published: str = ""            # optional; never guessed — see _jsonld
    kind: str = "article"          # article | page | index | tool
    internal_links: list[str] = field(default_factory=list)
    html: str = ""
    headings: list[str] = field(default_factory=list)
    words: int = 0

    @property
    def out_path(self) -> Path:
        return Path("index.html") if self.slug == "" else Path(self.slug) / "index.html"


@dataclass
class SiteResult:
    pages: list[Page] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    affiliate_pages: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    photos_needed: list[str] = field(default_factory=list)
    photos_used: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {"pages": [{"slug": p.slug, "src": str(p.src), "title": p.title, "kind": p.kind,
                           "words": p.words, "internal_links": p.internal_links,
                           "last_reviewed": p.last_reviewed} for p in self.pages],
                "errors": self.errors, "warnings": self.warnings,
                "affiliate_pages": sorted(set(self.affiliate_pages)),
                "photos_needed": sorted(set(self.photos_needed)),
                "config": self.config}


# ------------------------------------------------------------------- build ----
def load_config(root: Path, path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else root / "config" / "site.json"
    cfg: dict[str, Any] = {"base_url": "", "affiliate_hosts": list(AFFILIATE_DEFAULT)}
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    cfg["_config_path_exists"] = p.exists()
    return cfg


def _read_pages(root: Path, links: LinkSink, aff: tuple[str, ...],
                errors: list[str]) -> list[Page]:
    """Every markdown source becomes a page — or a named error. A file whose front matter does not
    parse must not be skipped quietly: that is how a page silently stops existing."""
    pages: list[Page] = []
    for d in SOURCE_DIRS:
        for src in sorted((root / d).glob("*.md")):
            try:
                meta, body = parse_front_matter(src.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                errors.append(f"{src}: not built — {exc}")
                continue
            # Front matter is hand-edited; a missing slug falls back to the filename so the
            # build degrades to a slightly uglier URL instead of crashing on a KeyError.
            slug = str(meta.get("slug") or "").strip("/") or re.sub(
                r"^\d+[-_]", "", src.stem).replace("_", "-").lower()
            slug = slug.strip("/") + "/"
            rendered, heads = render_body(body, slug, links, aff)
            words = count_words(body)
            # Same rule the ebook builder applies: a block type the renderer does not understand must
            # not disappear from a public page. Letters-only, so the digits a list marker contributes
            # in markdown and not in <ol> do not read as lost prose.
            visible = len(re.findall(r"[A-Za-z][A-Za-z'\u2019-]{1,}", re.sub(r"<[^>]+>", " ", rendered)))
            seen = len(re.findall(r"[A-Za-z][A-Za-z'\u2019-]{1,}", body))
            if seen - visible > max(40, int(0.015 * seen)):
                errors.append(f"{src}: {seen - visible} of {seen} words never reached the page — the "
                              "renderer met a block it does not know and dropped it")
            pages.append(Page(slug=slug, src=src, title=str(meta["title"]),
                              description=str(meta["description"]),
                              last_reviewed=str(meta.get("last_reviewed", "")),
                              published=str(meta.get("published", "")),
                              kind="page" if "pages" in str(src) else "article",
                              internal_links=[str(x).strip("/") + "/" for x in meta.get("internal_links", [])],
                              html=rendered, headings=heads, words=words))
    return pages


CSS = """:root{--bg:#0b1220;--fg:#e8eefc;--mut:#9fb0cc;--card:#111c31;--line:#22304b;--amber:#f2a33c}
@media (prefers-color-scheme:light){:root{--bg:#fff;--fg:#12203a;--mut:#4d5f80;--card:#f6f8fc;--line:#dde4f0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:17px/1.65 ui-sans-serif,system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:46rem;margin:0 auto;padding:1.25rem 1.1rem 3rem}
nav.top{display:flex;gap:.9rem;flex-wrap:wrap;align-items:center;padding:.6rem 0;border-bottom:1px solid var(--line);font-size:.95rem}
nav.top a{color:var(--amber);text-decoration:none}nav.top a:hover{text-decoration:underline}
h1{font-size:1.9rem;line-height:1.2;margin:1.4rem 0 .4rem}h2{font-size:1.35rem;margin:2rem 0 .5rem}
h3{font-size:1.1rem;margin:1.5rem 0 .4rem}p,li{overflow-wrap:break-word}
a{color:var(--amber)}hr{border:0;border-top:1px solid var(--line);margin:2rem 0}
.meta{color:var(--mut);font-size:.88rem;margin:0 0 1.2rem}
table{border-collapse:collapse;width:100%;margin:1.1rem 0;font-size:.93rem;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:.45rem .55rem;text-align:left;vertical-align:top}
th{background:var(--card)}code{background:var(--card);padding:.1rem .3rem;border-radius:4px;font-size:.9em}
blockquote{margin:1.1rem 0;padding:.7rem .9rem;background:var(--card);border-left:3px solid var(--amber);border-radius:0 6px 6px 0}
ul,ol{padding-left:1.3rem}li{margin:.3rem 0}
.cards{display:grid;gap:.8rem;grid-template-columns:1fr}@media(min-width:640px){.cards{grid-template-columns:1fr 1fr}}
.card{display:block;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.85rem .95rem;text-decoration:none;color:inherit}
.card b{display:block;font-size:1.02rem;margin-bottom:.2rem}.card span{color:var(--mut);font-size:.9rem}
footer{margin-top:2.5rem;padding-top:1rem;border-top:1px solid var(--line);color:var(--mut);font-size:.88rem}
.aff-note{border:1px solid var(--amber);border-radius:8px;padding:.55rem .75rem;margin:0 0 1.2rem;font-size:.92rem}
figure.photo img{width:100%;height:auto;display:block;border-radius:10px;border:1px solid var(--line)}
  .photo-slot{border:1px dashed var(--line);border-radius:10px;padding:1.4rem 1rem;margin:1.2rem 0;text-align:center;color:var(--mut)}
.photo-note{display:block;font-size:.82rem;opacity:.85;margin-bottom:.35rem}
img{max-width:100%;height:auto;border-radius:10px}
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
{meta}{jsonld}{canonical}<link rel="stylesheet" href="../styles.css">
<!-- built by `wf site` from 04-seo/. No analytics, no cookies, no external requests. -->
</head><body><div class="wrap">
<nav class="top">{nav}</nav>
<main>
{affline}<article>{body}</article></main>
{related}<footer>{footer}</footer>
</div></body></html>
"""

INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
{meta}{jsonld}{canonical}<link rel="stylesheet" href="styles.css">
<!-- built by `wf site` from 04-seo/. No analytics, no cookies, no external requests. -->
</head><body><div class="wrap">
<h1>{h1}</h1>
<p class="meta">{lede}</p>
<main>
<div class="cards">
{cards}
</div>
{extra}</main>
<footer>{footer}</footer>
</div></body></html>
"""


_ANCHOR = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<inner>.*?)</a>", re.S | re.I)
_HREF = re.compile(r"""href\s*=\s*(?P<q>["'])(?P<t>.*?)(?P=q)""", re.I)


def rewrite_tool_links(html: str, by_target: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """Make the standalone planner page fit inside the site: point its links at pages.

    The tool is written to sit in the repo next to the markdown it cites, so its links are repo
    paths. On a deployed site those paths do not exist, and a 404 is worse than a mention: a path
    that resolves to a page becomes that page's URL, a path that does not (the keyword map, the
    compliance checklist) loses its anchor and stays as plain text.
    """
    found: list[str] = []
    dropped: list[str] = []

    def resolve(target: str) -> str | None:
        t = target.split("#")[0].strip("./")
        for key in (t, str(Path(t)), Path(t).name):
            if key in by_target:
                return by_target[key]
        head = t.strip("/").split("/")[0]
        if head and f"{head}/" in by_target.values():
            return f"{head}/"
        return None

    def sub(m: re.Match[str]) -> str:
        attrs, inner = m.group("attrs"), m.group("inner")
        hm = _HREF.search(attrs)
        if not hm:
            return m.group(0)
        target = hm.group("t").strip()
        if target.startswith(("http://", "https://", "mailto:", "#", "data:")):
            return m.group(0)                       # external or in-page: the tool's own call
        if target.endswith(("index.html", "/")) or not Path(target.split("#")[0]).suffix:
            href = "../"                             # back to the site home
        else:
            hit = resolve(target)
            if not hit:
                dropped.append(target)
                return inner.strip()                 # unwrap the anchor, keep the label
            found.append(hit)
            href = f"../{hit}"
        new_attrs = _HREF.sub(lambda _m: f'href="{href}"', attrs, count=1)
        return f"<a{new_attrs}>{inner}</a>"

    return _ANCHOR.sub(sub, html), sorted(set(found)), sorted(set(dropped))


def _canonical(base_url: str, slug: str) -> str:
    if not base_url:
        return ""
    return f'<link rel="canonical" href="{base_url.rstrip("/")}/{slug}">\n'


def _iso(value: str) -> str:
    v = str(value or "").strip()
    return v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else ""


def _brand_meta(cfg: dict[str, Any], base_url: str, page: "Page", *, up: str = "../",
                have_og: bool = False, feed: bool = False, icon: bool = True) -> str:
    """Open Graph, Twitter card and favicon for one page.

    Absolute URLs only where the spec demands them: `og:url` and `og:image` are useless as
    relative paths, so they appear once `base_url` exists, and `og:image` additionally needs the
    exported PNG — a social card pointing at a file that does not exist looks worse in a LinkedIn
    preview than no image tag at all.
    """
    esc = lambda v: html.escape(str(v), quote=True)
    loc = base_url.rstrip("/")
    out = ([f'<link rel="icon" href="{up}favicon.svg" type="image/svg+xml">'] if icon else []) + [
           '<meta name="theme-color" content="#0b1220">',
           f'<meta property="og:site_name" content="{esc(cfg.get("site_name", "Shift Reset"))}">',
           f'<meta property="og:type" content="{"website" if page.kind in ("index", "tool") else "article"}">',
           f'<meta property="og:title" content="{esc(page.title)}">',
           f'<meta property="og:description" content="{esc(page.description)}">']
    if loc:
        out.append(f'<meta property="og:url" content="{loc}/{page.slug}">')
        out.append(f'<meta name="twitter:card" content="{"summary_large_image" if have_og else "summary"}">')
        if have_og:
            out.append(f'<meta property="og:image" content="{loc}/og-card.png">')
            out.append(f'<meta name="twitter:image" content="{loc}/og-card.png">')
        if feed:
            out.append(f'<link rel="alternate" type="application/rss+xml" '
                       f'title="{esc(cfg.get("site_name", "Shift Reset"))}" href="{loc}/feed.xml">')
    else:
        out.append('<meta name="twitter:card" content="summary">')
    return "\n".join(out) + "\n"


def _jsonld(cfg: dict[str, Any], base_url: str, page: "Page", slugs: set[str]) -> str:
    """Structured data, and nothing in it that the page cannot support.

    Deliberately absent: `datePublished` unless the front matter states it (back-dating a guess is
    a lie with a timestamp), `aggregateRating` (no reviews exist), a `SearchAction` (there is no
    search to point at), and any author credentials — the pen name carries no qualification, in the
    schema as anywhere else, because that is the exact fabrication this project refuses.
    """
    esc = lambda v: json.dumps(str(v), ensure_ascii=False)
    loc = base_url.rstrip("/")
    node: dict[str, Any] = {"@context": "https://schema.org"}
    url = f"{loc}/{page.slug}" if loc else ""
    if page.kind == "index":
        node["@type"] = "WebSite"
        node["name"] = str(cfg.get("site_name", "Shift Reset"))
        node["description"] = str(cfg.get("site_description", ""))
    elif page.kind == "tool":
        node["@type"] = "WebApplication"
        node["name"] = page.title
        node["description"] = page.description
        node["applicationCategory"] = "UtilitiesApplication"
        node["isAccessibleForFree"] = True
        node["featureList"] = ["anchor nap and wake windows", "caffeine dose and curfew math",
                              "meal and light timing", "printable fridge card", ".ics export"]
    elif page.kind == "article":
        node["@type"] = "Article"
        node["headline"] = page.title
        node["description"] = page.description
        node["inLanguage"] = str(cfg.get("language", "en"))
        node["isAccessibleForFree"] = True
        node["author"] = {"@type": "Person", "name": str(cfg.get("author", "J. Arek Thorne"))}
        node["publisher"] = {"@type": "Organization", "name": str(cfg.get("site_name", "Shift Reset"))}
        pub = _iso(page.published)
        if pub:
            node["datePublished"] = pub
        rev = _iso(page.last_reviewed)
        if rev:
            node["dateModified"] = rev
        if url:
            node["mainEntityOfPage"] = {"@type": "WebPage", "@id": url}
    else:
        node["@type"] = "WebPage"
        node["name"] = page.title
        node["description"] = page.description
        rev = _iso(page.last_reviewed)
        if rev:
            node["lastReviewed"] = rev
    if url:
        node["url"] = url
    parts = [node]
    if url and page.slug:
        parts.append({"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{loc}/"},
            {"@type": "ListItem", "position": 2, "name": page.title, "item": url}]})
    if len(parts) == 1:
        graph = parts[0]
    else:
        # Nested nodes inherit @context from the envelope; repeating it inside each one is how
        # generators ship a graph that validates and still reads as noise.
        for node in parts:
            node.pop("@context", None)
        graph = {"@context": "https://schema.org", "@graph": parts}
    return f'<script type="application/ld+json">{json.dumps(graph, ensure_ascii=False)}</script>\n'


#: The four destinations the site shell offers, in the order they should read. Kept as data so the
#: nav can be built from what a build actually produced: a hard-coded nav is how a site whose tool
#: folder was deleted ends up shipping a link to /planner/ on every page — the same class of bug
#: the related-links check already refuses, one level higher up.
NAV_ITEMS = (("planner/", "Free planner"), ("book/", "The book"), ("about/", "About"),
             ("editorial-policy/", "Corrections"))


def _nav(slugs: "set[str] | frozenset[str]", up: str = "../") -> str:
    items = [f'<a href="{up}">Home</a>']
    items += [f'<a href="{up}{slug}">{label}</a>' for slug, label in NAV_ITEMS if slug in slugs]
    return " · ".join(items)


def _footer(cfg: dict[str, Any], slugs: "set[str] | frozenset[str]" = frozenset()) -> str:
    author = cfg.get("author", "J. Arek Thorne")
    # A contact route is not decoration: an affiliate account and a store listing both expect a
    # reachable address, and a reader who spots an error needs somewhere to put it. Empty means it
    # is simply absent — an invented inbox would be a lie in the one place people look for proof.
    contact = str(cfg.get("contact", "")).strip()
    reach = f" Contact: {html.escape(contact)}." if contact else ""
    # The link is only written when the page exists, for the same reason the nav is built from `slugs`:
    # the footer is the one line whose job is to say the site can be trusted, so it must not contain
    # a dead link. Without the guard, a build that has no editorial-policy page still promises one on
    # every page it writes.
    policy = ('<a href="../editorial-policy/">editorial policy</a>' if "editorial-policy/" in slugs
              else "editorial policy")
    return (f"Educational content, not medical advice — see the {policy} for sourcing, disclosure and "
            f"how corrections are logged. Written by {author} (a pen name; no clinical credentials "
            f"are claimed).{reach}")


HOME_REVIEWED = ""     # the home page is a generated index, so it carries no review date of its own


def build(root: Path, out_dir: str | Path = DEFAULT_OUT, config_path: str | Path | None = None,
          check: bool = False) -> SiteResult:
    root = Path(root)
    out = Path(out_dir)
    if not out.is_absolute():
        out = root / out
    cfg = load_config(root, config_path)
    aff = tuple(h.lower() for h in cfg.get("affiliate_hosts", AFFILIATE_DEFAULT))
    base_url = str(cfg.get("base_url", "")).strip()
    res = SiteResult(config={k: v for k, v in cfg.items() if not k.startswith("_")})
    links = LinkSink()
    undated: list[str] = []      # articles with no `published:` — reported once, not per page

    # A social card is the only place where a *raster* is required: scrapers do not read SVG.
    # The repo ships the vector original (04-seo/images/og-card.svg); og-card.png is the operator's
    # 2-minute export, and until it exists the tag is omitted instead of pointing at nothing.
    img_root = root / "04-seo" / "images"
    have_og = (img_root / "og-card.png").exists()
    # Same rule as the social card: a page may not link an asset the build did not copy, so the
    # icon tag exists only when the file does. Otherwise every page in the site would ship a
    # request that answers 404, which is the bug this builder is supposed to catch in other people's copy.
    have_icon = (img_root / "favicon.svg").exists()
    pages = _read_pages(root, links, aff, res.errors)
    tool_src = root / TOOL_DIR / "index.html"
    if tool_src.exists():
        # Registered before slugs is computed, so every "/planner/" link in the prose resolves.
        pages.append(Page(slug="planner/", src=tool_src, title="Shift Reset Planner — free browser tool",
                          description="Enter a shift start and end; get the anchor nap, meal timing, "
                                      "caffeine curfew and light plan. No sign-up, nothing uploaded.",
                          kind="tool", html="tool"))
    slugs = {p.slug for p in pages}
    claimed: dict[str, Path] = {}
    for p in pages:
        if p.slug in claimed:
            # Two sources writing to the same URL: the second silently wins, which is how a page
            # disappears without any check failing. Say so instead.
            res.errors.append(f"duplicate URL /{p.slug}: {claimed[p.slug].name} and {p.src.name}")
        else:
            claimed[p.slug] = p.src

    # The tool is already a web page: copy it, and drop a canonical that points at a domain we
    # do not know yet rather than shipping an invented one.
    tool_html = ""
    if tool_src.exists():
        tool_html = tool_src.read_text(encoding="utf-8")
        by_target = {str(p.src.relative_to(root)): p.slug for p in pages if p.src.exists()}
        by_target.update({p.src.name: p.slug for p in pages if p.src.exists()})
        for p in pages:                                   # "/slug/" links, spelled either way
            by_target.setdefault(p.slug.strip("/"), p.slug)
            by_target.setdefault(f"/{p.slug}", p.slug)
        tool_html, tool_links, tool_dropped = rewrite_tool_links(tool_html, by_target)
        for t in tool_dropped:
            res.warnings.append(f"{TOOL_DIR}/index.html: link to {t} is not a site page — "
                                "kept as text, not a link")
        if not base_url:
            tool_html = re.sub(r'<link rel="canonical" href="https?://[^"]+">\n?', "", tool_html)
            res.warnings.append(f"{TOOL_DIR}/index.html: canonical dropped — set `base_url` in "
                                "config/site.json once the domain exists")
        else:
            tool_html = re.sub(r'<link rel="canonical" href="[^"]+">',
                               f'<link rel="canonical" href="{base_url.rstrip("/")}/planner/">',
                               tool_html, count=1)
        # The planner is the most shared page on the site, and it is copied verbatim rather than
        # wrapped in the article shell — so its social metadata and its schema are injected here.
        # Without this, the WebApplication node below would be code that looks like a feature.
        tool_page = Page(slug="planner/", src=tool_src, title="Shift Reset Planner — free night shift "
                         "sleep, caffeine and meal timing calculator",
                         description="Enter a shift start and get the anchor nap, meal timing, caffeine "
                                     "curfew and light plan. Free, no sign-up, nothing uploaded.",
                         kind="tool")
        head_block = _brand_meta(cfg, base_url, tool_page, have_og=have_og, feed=True,
                                 icon=False) + _jsonld(cfg, base_url, tool_page, slugs)
        tool_html = tool_html.replace("</head>", head_block + "</head>", 1)
        if "../styles.css" not in tool_html:
            tool_html = tool_html.replace("</head>", '  <!-- served alongside the site styles -->\n</head>', 1)
        for tp in pages:
            if tp.slug == "planner/":
                tp.internal_links = tool_links

    for p in pages:
        if p.kind == "tool" or not p.html:
            continue
        has_aff = any(slug == p.slug for slug, _ in links.affiliate)
        if has_aff:
            res.affiliate_pages.append(p.slug)
        # The wording trips the same keyword list it is disclosing, so the exemption is written
        # into the line itself: a reader of the HTML sees why the gate did not flag it.
        affline = ('<!-- gate:allow RULE the disclosure line is the requirement, not a promotion -->\n'
                   '    <p class="aff-note"><strong>Disclosure:</strong> some links below are affiliate '
                   'links — if you buy through them the site may earn a commission at no cost to you. '
                   'It does not change what the page recommends, and no brand reviewed here paid for '
                   'placement.</p>\n') if has_aff else ""
        if has_aff and "affiliate" not in p.html.lower():
            res.errors.append(f"{p.src}: contains an affiliate link; the generated disclosure is in "
                              "place, but the page text should say it in the author's own words too")
        if not _iso(p.last_reviewed):
            res.errors.append(f"{p.src}: no valid `last_reviewed: YYYY-MM-DD`. The visible review line "
                              "and the schema's dateModified are the same promise, and a page dated by "
                              "guess is worse than an undated one — put the date you actually reviewed it")
        if p.kind == "article" and not _iso(p.published):
            undated.append(p.slug)
        rel = [x for x in p.internal_links if x not in slugs]
        res.errors += [f"{p.src}: internal_links target /{x} has no page" for x in rel]
        related = ""
        good = [x for x in p.internal_links if x in slugs and x != p.slug]
        if good:
            items = "".join(f'<li><a href="../{x}">{x.strip("/").replace("-", " ")}</a></li>' for x in good)
            related = f'<p class="meta" style="margin-top:2rem">More in this cluster:</p><ul>{items}</ul>'
        p.html = PAGE.format(title=html.escape(p.title), desc=html.escape(p.description),
                             nav=_nav(slugs, "../"),
                             meta=_brand_meta(cfg, base_url, p, up="../", have_og=have_og,
                                              feed=bool(base_url), icon=have_icon),
                             jsonld=_jsonld(cfg, base_url, p, slugs),
                             canonical=_canonical(base_url, p.slug), affline=affline,
                             body=p.html, related=related, footer=_footer(cfg, slugs))
        if p.last_reviewed:
            p.html = p.html.replace("<main>\n", f"<main>\n<p class=\"meta\">Last reviewed "
                                                f"{html.escape(p.last_reviewed)}</p>\n", 1)

    # Index: every prose page, sorted by kind then slug, and no orphans allowed.
    prose = [p for p in pages if p.kind in ("article", "page")]
    order = {"article": 0, "page": 1}
    prose.sort(key=lambda p: (order.get(p.kind, 9), p.slug))
    cards = "\n".join(
        f'<a class="card" href="{p.slug}"><b>{html.escape(p.title)}</b>'
        f'<span>{html.escape(p.description[:150])}</span></a>' for p in prose)
    extra = ""
    if "planner/" in slugs:
        extra = ('<p><a class="card" href="planner/"><b>The free planner</b><span>Turn your own shift '
                 'times into the nap, caffeine and light schedule — one file, works offline.</span></a></p>')
    index_html = INDEX.format(
        title=html.escape(str(cfg.get("site_name", "Shift Reset — sleep, food and light for night work"))),
        # The visible headline is not the <title>: "Shift Reset / Shift Reset" is what a template
        # writes when the two are the same variable, and the headline is the one line a first-time
        # reader actually reads.
        h1=html.escape(str(cfg.get("home_h1", "Sleep, food and light for people who work overnights"))),
        desc=html.escape(str(cfg.get("site_description", "Original, practical guidance for people who work "
                                                          "overnights. Free planner, five long guides, one book."))),
        meta=_brand_meta(cfg, base_url, Page(slug="", src=root, title=str(cfg.get("site_name", "Shift Reset")),
                                             description=str(cfg.get("site_description", "")), kind="index",
                                             last_reviewed=HOME_REVIEWED), up="", have_og=have_og, feed=True,
                                             icon=have_icon),
        jsonld=_jsonld(cfg, base_url, Page(slug="", src=root, title=str(cfg.get("site_name", "Shift Reset")),
                                           description=str(cfg.get("site_description", "")), kind="index"), slugs),
        canonical=_canonical(base_url, ""), lede=html.escape(str(cfg.get("lede", ""))) or
        "Written by someone who has done the shifts, not by a clinic. Start with the free planner.",
        cards=cards, extra=extra,
        footer=(f"Educational content, not medical advice. <a href=\"about/\">About</a> · "
                f"<a href=\"editorial-policy/\">Editorial policy and corrections</a> · "
                f"<a href=\"book/\">The book</a>"))
    pages.append(Page(slug="", src=root / "04-seo", title=str(cfg.get("site_name", "Shift Reset")),
                      description="", kind="index", html=index_html))

    # ---- the checks that make this worth running before a deploy
    for src, target in links.internal:
        if target not in slugs:
            res.errors.append(f"/{src} links to /{target} — no page with that slug exists")
    linked = {t for _s, t in links.internal}
    for p in prose:
        if p.slug not in linked and p.slug not in ("", "index/"):
            # A page nothing links to is a page nobody finds; the index links everything, so
            # this catches a page that is linked by nothing *in prose* and is not on the ladder.
            if p.kind == "page" and p.slug not in {"about/", "editorial-policy/", "book/"}:
                res.warnings.append(f"{p.src}: no prose page links to /{p.slug}")
    for p in pages:
        if PLACEHOLDER_RX.search(p.html or ""):
            res.errors.append(f"{p.src}: placeholder text reached the built page "
                              f"({PLACEHOLDER_RX.search(p.html).group(0)!r})")
    if undated:
        res.warnings.append(f"{len(undated)} article(s) have no `published:` date, so datePublished is "
                            "omitted from their schema and the feed uses the review date: "
                            f"{', '.join(sorted(undated)[:5])}. Add the real date at publish time — do "
                            "not back-date it from the file's git history")
    if not cfg.get("_config_path_exists"):
        res.warnings.append("config/site.json missing — defaults used (no base_url, so no canonicals)")
    elif not base_url:
        res.warnings.append("base_url is empty — pages build without canonical URLs. Set it when "
                            "the domain exists; do not invent one before then")
    for p in prose:
        rep = gate_mod.evaluate_text(p.src.read_text(encoding="utf-8"), str(p.src))
        for e in rep.errors:
            res.errors.append(f"{p.src}: gate {e.rule_id} — {e.title}")
    # Images: an <img> where the file exists, a labelled slot where it does not. Both states are
    # deliberate — a missing photo has to be visible in the artefact instead of a torn icon, and
    # the day the operator drops a JPEG in 04-seo/images/ the next build uses it without anyone
    # re-opening the prose.
    img_dir = root / "04-seo" / "images"
    want = sorted(set(re.findall(r'data-src="([^"]+)"', "".join(p.html for p in pages))))
    have: dict[str, Path] = {}
    for ref in want:
        cand = [img_dir / Path(ref).name, root / "04-seo" / ref, root / "03-tool" / ref]
        found = next((c for c in cand if c.exists()), None)
        if found:
            have[ref] = found
        else:
            res.photos_needed.append(ref)
    for p in pages:
        if not p.html or "photo-slot" not in p.html:
            continue
        prefix = "" if p.slug in ("", "index/") else "../"
        for ref, src in sorted(have.items()):
            slot = re.compile(r'<figure class="photo-slot" data-src="' + re.escape(ref) +
                              r'"><span class="photo-note">[^<]*</span>(<figcaption>.*?</figcaption>)</figure>',
                              re.S)
            cap = slot.search(p.html)
            alt = html.escape(re.sub(r"<[^>]+>", "", cap.group(1)).replace("figcaption", "").strip(),
                              quote=True) if cap else ""
            p.html = slot.sub(lambda m: f'<figure class="photo"><img src="{prefix}images/'
                                         f'{src.name}" alt="{alt}" loading="lazy">{m.group(1)}</figure>',
                              p.html)
            res.photos_used.append(f"{p.slug}:{src.name}")
    # Not suppressed in --check mode, unlike the "file not written" notes: missing photos are a
    # finding about the content, and `--check` is the path CI reads. Warnings never fail the build —
    # an asset the operator has not photographed yet is pending human work, not a broken site.
    if res.photos_needed:
        res.warnings.append(f"{len(res.photos_needed)} photo slot(s) rendered as labelled "
                            f"placeholders: {', '.join(sorted(set(res.photos_needed))[:4])}")

    # ---- write (sorted, fixed content: byte-identical rebuilds are the point)
    lang = str(cfg.get("language", "en") or "en")
    if not re.fullmatch(r"[a-z]{2}(-[A-Za-z0-9]{2,8})*", lang):
        # The value is copied into every page's lang attribute, the JSON-LD inLanguage and the feed's
        # <language>, so "English" is not a cosmetic slip — it is a site-wide defect with a typo in it.
        res.errors.append(f'config/site.json: language {lang!r} is not a language tag — expected '
                          f'"en-GB", "en-US", "pt-BR"')

    def with_lang(text: str) -> str:
        # `lang` on the document, not only in the schema: a screen reader picks its pronunciation
        # from here, and so does the browser's hyphenation. `<html lang="en">` is a shrug.
        return text.replace('<html lang="en">', f'<html lang="{lang}">', 1)

    if not check:
        out.mkdir(parents=True, exist_ok=True)
        for ref, src in sorted(have.items()):
            (out / "images").mkdir(parents=True, exist_ok=True)
            (out / "images" / src.name).write_bytes(src.read_bytes())
            res.written.append(Path("images") / src.name)
        for p in pages:
            target = out / p.out_path
            target.parent.mkdir(parents=True, exist_ok=True)
            text = tool_html if p.kind == "tool" else p.html
            target.write_text(with_lang(text), encoding="utf-8")
            res.written.append(p.out_path)
        (out / "styles.css").write_text(CSS, encoding="utf-8")
        res.written.append(Path("styles.css"))
        locs = sorted({p.slug for p in pages if p.slug})
        loc = f"{base_url.rstrip('/')}" if base_url else ""
        sitemap = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
                   '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
                   f"<url><loc>{loc}/</loc></url>"]
        for s in locs:
            sitemap.append(f"<url><loc>{loc}/{s}</loc></url>")
        sitemap.append("</urlset>")
        if base_url:
            (out / "sitemap.xml").write_text("\n".join(sitemap) + "\n", encoding="utf-8")
            res.written.append(Path("sitemap.xml"))
        else:
            res.warnings.append("sitemap.xml not written: it needs absolute URLs, and inventing a "
                                "domain is worse than omitting the file. Set base_url and rebuild")
        (out / "robots.txt").write_text("User-agent: *\nAllow: /\n"
                                        + (f"Sitemap: {base_url.rstrip('/')}/sitemap.xml\n" if base_url else ""),
                                        encoding="utf-8")
        (out / "netlify.toml").write_text(
            "# Generated by `wf site`. Publishes the folder as-is; there is no build step.\n"
            "[build]\n  command = \"# no build: run ./wf site and commit docs/\"\n  publish = \"docs\"\n"
            "\n[[headers]]\n  for = \"/*\"\n  [headers.values]\n"
            "    X-Content-Type-Options = \"nosniff\"\n    Referrer-Policy = \"no-referrer-when-downgrade\"\n"
            "    Permissions-Policy = \"camera=(), microphone=(), geolocation=()\"\n", encoding="utf-8")
        # A 404 that offers the good pages instead of a host default: most traffic to a dead URL
        # on a young site comes from a link someone typo'd, and both free hosts serve this file.
        lost = "".join(f'<li><a href="{p.slug}">{html.escape(p.title)}</a></li>'
                       for p in sorted(prose, key=lambda q: q.slug) if p.slug)
        page404 = PAGE.format(
            title=f"Page not found — {cfg.get('site_name', 'Shift Reset')}",
            nav=_nav(slugs, ""),
            desc="That address is not part of this site.", canonical="", affline="",
            meta=(f'<link rel="icon" href="favicon.svg" type="image/svg+xml">\n' if have_icon else '')
                 + '<meta name="theme-color" content="#0b1220">\n'
                 # GitHub Pages and Netlify do answer a dead URL with this file, and with a real 404
                 # status — but the same folder on another host, or a local preview, serves it as an
                 # ordinary 200 page. Without this line a crawler that has already found the site can
                 # index "No page here" plus its list of links as though it were content. Links stay
                 # followed, so nothing that does exist is hidden by this.
                 '<meta name="robots" content="noindex,follow">\n', jsonld="",
            body="<h1>No page here</h1><p class=\"meta\">Either a link was mistyped or this site "
                 "renamed something. Everything that exists is listed below — or start at the "
                 "<a href=\"./\">home page</a>.</p>"
                 f"<ul>{lost}</ul>",
            related="", footer=_footer(cfg, slugs))
        # The shell is written for pages one folder deep (/slug/index.html), so a root-level
        # file has to drop the "../" prefix or its stylesheet and links point above the site.
        (out / "404.html").write_text(with_lang(page404).replace('="../', '="')
                                      .replace('href=""', 'href="./"'), encoding="utf-8")
        # favicon + the vector original of the social card: copied because they are assets the site
        # links to, not prose, and the export step is easier when the source file is right there.
        for asset in ("favicon.svg", "og-card.svg", "og-card.png"):
            src = img_root / asset
            if src.exists():
                (out / asset).write_bytes(src.read_bytes())
                res.written.append(Path(asset))
        if not (img_root / "favicon.svg").exists():
            res.warnings.append("no 04-seo/images/favicon.svg — the icon link is omitted site-wide; browsers "
                                "get a 404 (harmless, avoidable)")
        if have_og:
            res.written.append(Path("og-card.png"))
        elif base_url:
            res.warnings.append("og:image not emitted: export 04-seo/images/og-card.svg to og-card.png "
                                "(1200×630, any free exporter) and rebuild — until then social posts "
                                "show the title and description without a card")
        if base_url:
            items = []
            # Newest first; the slug is the tie-breaker so a rebuild of the same sources produces the
            # same bytes, which is what makes CI's docs/ diff worth having.
            for p_ in sorted((x for x in prose if _iso(x.last_reviewed)),
                             key=lambda q: (_iso(q.published) or _iso(q.last_reviewed), q.slug),
                             reverse=True)[:10]:
                date = _iso(p_.published) or _iso(p_.last_reviewed)
                try:
                    from datetime import datetime as _dt
                    rd = _dt.strptime(date, "%Y-%m-%d").strftime("%a, %d %b %Y 09:00:00 +0000")
                except ValueError:
                    continue
                link = f"{base_url.rstrip('/')}/{p_.slug}"
                items.append(
                    "<item>"
                    f"<title>{html.escape(p_.title)}</title>"
                    f"<link>{link}</link>"
                    f'<guid isPermaLink="true">{link}</guid>'
                    f"<pubDate>{rd}</pubDate>"
                    f"<description>{html.escape(p_.description)}</description>"
                    "</item>")
            feed = ['<?xml version="1.0" encoding="UTF-8"?>',
                    '<rss version="2.0"><channel>',
                    f"<title>{html.escape(str(cfg.get('site_name', 'Shift Reset')))}</title>",
                    f"<link>{base_url.rstrip('/')}/</link>",
                    f"<description>{html.escape(str(cfg.get('site_description', '')))}</description>",
                    f"<language>{html.escape(str(cfg.get('language', 'en')))}</language>"]
            if items:
                feed.append(f"<lastBuildDate>{items[0].split('<pubDate>')[1].split('</pubDate>')[0]}"
                            "</lastBuildDate>")
            feed += items + ["</channel></rss>"]
            (out / "feed.xml").write_text("\n".join(feed) + "\n", encoding="utf-8")
            res.written.append(Path("feed.xml"))
        (out / ".nojekyll").write_text("", encoding="utf-8")
        res.written += [Path("robots.txt"), Path("netlify.toml"), Path(".nojekyll"), Path("404.html")]
    # What is switched on, in one line of the report. The host-level facts (canonicals, sitemap,
    # feed, absolute og:url) all hinge on one config value, so an operator deciding "am I ready to
    # submit this to Search Console?" should not have to infer it from five prose warnings.
    res.metadata = {"base_url": bool(base_url), "sitemap": bool(base_url),
                    "feed": bool(base_url), "og_image": have_og, "favicons": (img_root / "favicon.svg").exists(),
                    "pages_missing_published_date": len(undated)}
    res.pages = pages
    return res


def report_markdown(res: SiteResult, out_dir: str = DEFAULT_OUT) -> str:
    L = ["# Site build report", ""]
    n = len([p for p in res.pages if p.kind in ("article", "page")])
    L.append(f"**{n} prose page(s)** · {len(res.written)} file(s) written · "
             f"{len(res.errors)} error(s), {len(res.warnings)} warning(s)")
    L += ["", "| page | kind | words | internal links |", "|---|---|---|---|"]
    for p in sorted(res.pages, key=lambda x: (x.kind, x.slug)):
        L.append(f"| `/{p.slug}` | {p.kind} | {p.words} | {len(p.internal_links)} |")
    if res.affiliate_pages:
        L += ["", f"**Affiliate-disclosure banner injected on:** "
                  + ", ".join(f"`/{s}`" for s in sorted(set(res.affiliate_pages)))]
    if res.photos_needed:
        L += ["", "**Photos still to supply** (rendered as labelled placeholders, not broken icons):"]
        L += [f"- `{x}`" for x in sorted(set(res.photos_needed))]
    if res.metadata:
        m = res.metadata
        state = lambda b: "on" if b else "off"
        L += ["", f"**Metadata:** schema and Open Graph on every page · canonical/sitemap/feed "
                  f"{state(m.get('base_url'))} (needs `base_url`) · social card image "
                  f"{state(m.get('og_image'))} (needs `04-seo/images/og-card.png`) · favicon "
                  f"{state(m.get('favicons'))} · {m.get('pages_missing_published_date', 0)} page(s) "
                  f"without a `published:` date"]
    if res.errors:
        L += ["", "## Errors", ""] + [f"- {e}" for e in res.errors]
    if res.warnings:
        L += ["", "## Warnings", ""] + [f"- {w}" for w in res.warnings]
    L += ["", f"_Rebuild with `./wf site` — the folder under `{out_dir}/` is generated, so edit the "
              f"markdown in `04-seo/` instead._"]
    return "\n".join(L) + "\n"
