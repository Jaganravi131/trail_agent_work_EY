"""Tests for the site builder: the thing that turns markdown into a deployable folder.

Two kinds of test live here on purpose. The fixture tests prove the mechanism (link rewriting,
the disclosure, placeholders, determinism) against a tiny tree I control. The last test class
runs the builder over *this repository* and asserts the real site is clean, which is the check
that stops the built folder and the markdown from drifting apart once a human edits a page.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wealforge import site  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


ART_A = """---
title: "Alpha"
slug: /alpha/
description: "The first page."
internal_links: ["/beta/"]
last_reviewed: 2026-10-14
---

# Alpha

Body text with a [link to beta](/beta/).

Educational content, not medical advice.
"""

ART_B = """---
title: "Beta"
slug: /beta/
description: "The second page."
internal_links: ["/alpha/"]
last_reviewed: 2026-10-14
---

# Beta

Beta talks about a [thing that is a page](/alpha/) and nothing else.

Not medical advice.
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="wf-site-"))
        _write(self.root, "04-seo/articles/01-alpha.md", ART_A)
        _write(self.root, "04-seo/articles/02-beta.md", ART_B)
        _write(self.root, "config/site.json", json.dumps({"site_name": "Test", "base_url": ""}))
        self.out = self.root / "docs"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def build(self, check=False):
        return site.build(self.root, out_dir=self.out, check=check)

    # ---- the basics
    def test_it_produces_clean_urls_not_files(self):
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        for slug in ("alpha", "beta"):
            self.assertTrue((self.out / slug / "index.html").exists(), slug)
        self.assertTrue((self.out / "index.html").exists())
        self.assertTrue((self.out / "styles.css").exists())

    def test_links_are_relative_so_a_subpath_host_still_works(self):
        # GitHub Pages serves from /repo/, Netlify from / — an absolute `/beta/` would break the
        # first one, which is the host the zero-capital plan recommends.
        self.build()
        html = (self.out / "alpha" / "index.html").read_text()
        self.assertIn('href="../beta/"', html)
        self.assertNotIn('href="/beta/"', html)

    def test_output_is_byte_identical_on_rebuild(self):
        # CI's drift check is `git diff --exit-code docs/` after a rebuild, so a build that
        # embeds a timestamp would fail every single run for no reason.
        self.build()
        first = {p: p.read_bytes() for p in sorted(self.out.rglob("*")) if p.is_file()}
        shutil.rmtree(self.out)
        self.build()
        second = {p: p.read_bytes() for p in sorted(self.out.rglob("*")) if p.is_file()}
        self.assertEqual(first, second)

    def test_check_mode_reports_photo_gaps_although_it_reports_no_written_files(self):
        # The write-only findings (sitemap, feed) disappear in --check, but a photo the operator has
        # not taken is a finding about the content, and --check is the step CI reads.
        _write(self.root, "04-seo/articles/03-img.md",
               ART_B.replace("Beta", "Image").replace("slug: /beta/", "slug: /photo/") +
               "\n![My room](images/my-room.jpg)\n")
        res = self.build(check=True)
        self.assertTrue(any("photo slot" in w for w in res.warnings), res.warnings)
        self.assertFalse(any("sitemap" in w for w in res.warnings), res.warnings)
        self.assertEqual(res.written, [])
        self.assertTrue(res.ok, "pending human work is a warning, never a red build")

    def test_check_mode_writes_nothing(self):
        res = self.build(check=True)
        self.assertTrue(res.ok, res.errors)
        self.assertFalse(self.out.exists(), "--check must not touch the deploy folder")

    def test_prose_the_renderer_drops_is_a_build_error(self):
        # Every block type the little renderer does not know is a paragraph that silently vanishes
        # from a public page. The check is on the render, not on the file, so a future renderer
        # change cannot quietly lose a section.
        # Above the rule's floor: a 60-word fixture page cannot lose 40 words and a check with a
        # floor has to be tested with a corpus above the floor, not by shrinking the floor to fit.
        long = ART_A.replace("slug: /alpha/", "slug: /long/").replace("# Alpha", "# Long") + "\n" + \
            ("The quick brown fox jumps over the lazy log. " * 18)
        _write(self.root, "04-seo/articles/03-long.md", long.replace("link to beta", "link"))
        real = site.render_body
        try:
            site.render_body = lambda body, slug, links, aff, **kw: ("<p>A fraction</p>", ["A"])
            res = site.build(self.root, out_dir=self.out)
        finally:
            site.render_body = real
        self.assertTrue(any("never reached the page" in e for e in res.errors), res.errors)
        self.assertTrue(any("03-long" in e for e in res.errors), res.errors)

    def test_a_link_to_a_page_that_does_not_exist_is_an_error(self):
        _write(self.root, "04-seo/articles/03-gamma.md",
               ART_B.replace("Beta", "Gamma").replace("/alpha/", "/no-such-page/"))
        res = self.build()
        self.assertFalse(res.ok)
        self.assertTrue(any("no page with that slug" in e for e in res.errors), res.errors)

    def test_front_matter_targets_are_checked_too(self):
        # A page can promise a "related" link that goes nowhere; that is a 404 in the nav.
        _write(self.root, "04-seo/articles/03-gamma.md",
               ART_B.replace('internal_links: ["/alpha/"]', 'internal_links: ["/ghost/"]'))
        res = self.build()
        self.assertTrue(any("has no page" in e for e in res.errors), res.errors)

    # ---- monetisation chrome
    def test_affiliate_link_gets_disclosure_and_rel_attributes(self):
        _write(self.root, "04-seo/articles/03-shop.md", """---
title: "Shop"
slug: /shop/
description: "Gear."
internal_links: ["/alpha/"]
---

# Shop

The [mask we would buy](https://www.amazon.com/dp/example) is this one. More on
[alpha](/alpha/).

Not medical advice.
""")
        res = self.build()
        page = (self.out / "shop" / "index.html").read_text()
        self.assertIn("Disclosure:", page)
        self.assertIn("rel=\"sponsored nofollow\"", page.replace('rel="noopener noreferrer sponsored nofollow"',
                                                                "rel=\"sponsored nofollow\""))
        self.assertIn("sponsored", page)
        self.assertIn("nofollow", page)
        self.assertIn("shop/", res.affiliate_pages, res.affiliate_pages)

    def test_a_page_without_affiliate_links_gets_no_disclosure_banner(self):
        self.build()
        self.assertNotIn("Disclosure:", (self.out / "alpha" / "index.html").read_text())

    # ---- images
    def test_missing_photo_becomes_a_labelled_slot_not_a_broken_icon(self):
        _write(self.root, "04-seo/articles/03-img.md",
               ART_B.replace("Beta", "Image").replace("slug: /beta/", "slug: /photo/") +
               "\n![My room](images/my-room.jpg)\n")
        self.build()
        page = (self.out / "photo" / "index.html").read_text()
        self.assertIn('class="photo-slot"', page)
        self.assertIn("photo to supply: images/my-room.jpg", page)
        self.assertNotIn("<img ", page)

    def test_a_supplied_photo_is_copied_and_used(self):
        (self.root / "04-seo" / "images").mkdir(parents=True, exist_ok=True)
        _write(self.root, "04-seo/images/my-room.jpg", "not-really-a-jpeg")
        _write(self.root, "04-seo/articles/03-img.md",
               ART_B.replace("Beta", "Image").replace("slug: /beta/", "slug: /photo/") +
               "\n![My room](images/my-room.jpg)\n")
        res = self.build()
        self.assertTrue((self.out / "images" / "my-room.jpg").exists())
        page = (self.out / "photo" / "index.html").read_text()
        self.assertIn('<img src="../images/my-room.jpg"', page)
        self.assertNotIn("photo-slot", page)
        self.assertTrue(any("my-room.jpg" in x for x in res.photos_used), res.photos_used)

    def test_the_nav_only_offers_pages_the_build_actually_made(self) -> None:
        """A hard-coded nav is a site-wide 404 the moment a folder is not there.

        The prose pages already refuse a `related` link that goes nowhere. The shell's nav is the
        same promise written by hand, so this is the test that keeps it honest.
        """
        res = self.build()
        alpha = (self.out / "alpha" / "index.html").read_text(encoding="utf-8")
        # No 03-tool/index.html in this fixture, so no /planner/ link — and the two pages that do
        # exist are still offered.
        self.assertNotIn("planner/", alpha, res.errors and "" or "the nav invented a page")
        self.assertIn('href="../beta/"', alpha)     # a real internal link, from the article body
        self.assertIn('href="../"', alpha)
        page404 = (self.out / "404.html").read_text(encoding="utf-8")
        self.assertNotIn("planner/", page404)
        # With the tool present, the link reappears on its own — nothing is added by hand.
        _write(self.root, "03-tool/index.html",
               '<!doctype html><html><head><title>Planner</title></head><body><p>tool</p></body></html>')
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        self.assertIn('href="../planner/"', (self.out / "alpha" / "index.html").read_text(encoding="utf-8"))

    # ---- the files a host serves that no page links to
    def test_the_404_page_is_unfindable_by_search_and_links_only_to_real_pages(self) -> None:
        self.build()
        page = (self.out / "404.html").read_text(encoding="utf-8")
        self.assertIn("No page here", page)
        # GitHub Pages and Netlify answer a dead URL with a real 404 status; a folder copied onto
        # any other host (or opened from a preview server) answers 200. Without this tag, the
        # page of "these links exist" becomes an indexed page of links.
        self.assertIn('<meta name="robots" content="noindex,follow">', page)
        # Written at the root, not one folder deep, so the shell's "../" prefix has to be gone and
        # the home link must not collapse to href="" — that resolves to the current page.
        self.assertNotIn('"../', page)
        hrefs = re.findall(r'href="([^"]*)"', page)
        self.assertIn("./", hrefs, "the home link must point at ./, not at itself")
        for href in hrefs:
            if href.startswith(("http", "#", "mailto:", "data:")):
                continue
            rel = href.split("#")[0].rstrip("/")
            target = (self.out / rel).resolve()
            self.assertTrue(target.exists() or (target / "index.html").exists(),
                            f"the 404 page links to {href!r}, which does not exist in the build")

    def test_robots_txt_advertises_a_sitemap_only_when_one_was_written(self) -> None:
        self.build()
        robots = (self.out / "robots.txt").read_text(encoding="utf-8")
        self.assertIn("User-agent: *", robots)
        self.assertIn("Allow: /", robots)
        # No base_url in this fixture → no sitemap.xml → no pointer to a file that is not there.
        self.assertNotIn("Sitemap:", robots)
        self.assertFalse((self.out / "sitemap.xml").exists())
        # .nojekyll: without it, GitHub Pages runs Jekyll and silently drops folders that begin
        # with an underscore, which is a 404 nobody can explain.
        self.assertTrue((self.out / ".nojekyll").exists())
        netlify = (self.out / "netlify.toml").read_text(encoding="utf-8")
        self.assertIn('publish = "docs"', netlify)
        self.assertIn('X-Content-Type-Options = "nosniff"', netlify)

    # ---- the planner
    def test_the_standalone_tool_becomes_a_page_with_working_links(self):
        _write(self.root, "03-tool/index.html", """<!doctype html>
<html><head><title>Planner</title>
<link rel="canonical" href="https://invented.example/planner/"></head>
<body><a href="04-seo/articles/02-beta.md">beta article</a>
<a href="04-seo/01-KEYWORD-MAP.md">keyword map</a>
<a href="index.html">back</a></body></html>
""")
        res = self.build()
        page = (self.out / "planner" / "index.html").read_text()
        self.assertIn('href="../beta/"', page)
        # A repo file with no page loses its anchor rather than shipping a 404.
        self.assertNotIn("01-KEYWORD-MAP.md", page)
        self.assertIn("keyword map", page)
        # Canonicals only exist once the domain exists; a made-up one is worse than none.
        self.assertNotIn("invented.example", page)
        self.assertIn('href="../"', page)
        self.assertTrue(any("canonical" in w for w in res.warnings), res.warnings)

    def test_base_url_turns_on_canonicals_and_sitemap(self):
        _write(self.root, "config/site.json",
               json.dumps({"site_name": "Test", "base_url": "https://example.org/site/"}))
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        self.assertTrue((self.out / "sitemap.xml").exists())
        sm = (self.out / "sitemap.xml").read_text()
        self.assertIn("https://example.org/site/alpha/", sm)
        self.assertIn('<link rel="canonical" href="https://example.org/site/alpha/">',
                      (self.out / "alpha" / "index.html").read_text())
        self.assertFalse(any("base_url is empty" in w for w in res.warnings))
        # The other half of the rule: with a domain, robots.txt must point at the sitemap it just wrote.
        self.assertIn("Sitemap: https://example.org/site/sitemap.xml",
                      (self.out / "robots.txt").read_text(encoding="utf-8"))

    def test_no_sitemap_is_written_without_a_domain(self):
        self.build()
        self.assertFalse((self.out / "sitemap.xml").exists())
        self.assertFalse((self.out / "alpha" / "canonical").exists())

    # ---- the placeholders a generated draft likes to leave behind
    def test_a_placeholder_in_the_source_fails_the_build(self):
        _write(self.root, "04-seo/articles/03-todo.md",
               ART_B.replace("Beta", "Todo") + "\nSee {{ price }} and $12.99 TODO(human).\n")
        res = self.build()
        self.assertTrue(any("placeholder text reached the built page" in e for e in res.errors),
                        res.errors)

    def test_gate_errors_in_a_page_are_reported_with_its_path(self):
        # This fixture copy is deliberately the real thing: a cure claim must not be buildable.
        _write(self.root, "04-seo/articles/03-bad.md", ART_B.replace("Beta", "Bad") +
               "\n\nThis cures shift work sleep disorder and guarantees deeper sleep.\n")
        res = self.build()
        self.assertFalse(res.ok)
        self.assertTrue(any("gate M1" in e for e in res.errors), res.errors)


    def test_two_pages_claiming_one_url_is_an_error(self):
        _write(self.root, "04-seo/articles/03-dupe.md",
               ART_B.replace("Beta", "Beta copy"))
        res = self.build()
        self.assertFalse(res.ok)
        self.assertTrue(any("duplicate URL" in e for e in res.errors), res.errors)

    def test_a_quote_that_never_closes_is_a_build_error(self):
        # The exact defect two real files shipped with: `description: "…` with no closing quote
        # used to reach the page as a stray character inside the <title> tag.
        broken = ART_B.replace('description: "The second page."', 'description: "The second page.')
        _write(self.root, "04-seo/articles/04-quoted.md", broken)
        with self.assertRaises(ValueError) as cm:
            site.parse_front_matter(broken)
        self.assertIn("never closes", str(cm.exception))
        res = self.build()
        self.assertTrue(any("never closes" in e for e in res.errors), res.errors)

    def test_unparsable_front_matter_is_a_named_error_not_a_crash_or_a_silence(self):
        # The unterminated-quote bug this guards against produced a *crash* before this path
        # existed; a build that fails with the file named is the useful behaviour.
        text = ART_B.replace("Beta", "No-slug").replace("slug: /beta/\n", "")
        _write(self.root, "04-seo/articles/04-no-slug.md", text)
        res = self.build()
        self.assertFalse(res.ok)
        self.assertTrue(any("not built" in e and "slug" in e for e in res.errors), res.errors)


class HeadMetadata(unittest.TestCase):
    """OG/Twitter cards, JSON-LD, the feed and the brand assets.

    These matter because of how the site gets its first readers: every post in
    `05-marketing/01-CONTENT-CALENDAR.md` shares a link, and a shared link with no title, no
    description and a broken image is how good writing gets skipped.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="wf-site-meta-"))
        _write(self.root, "04-seo/articles/01-alpha.md", ART_A)
        _write(self.root, "04-seo/articles/02-beta.md", ART_B)
        _write(self.root, "config/site.json", json.dumps({"site_name": "Test", "base_url": "",
                                                           "author": "A. N. Other", "language": "en-gb"}))
        # The brand asset has to exist for its link to exist. It used to be assumed, which is how
        # a test can pass while the builder emits a request the built folder cannot answer.
        _write(self.root, "04-seo/images/favicon.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>\n')
        self.out = self.root / "docs"
        site.build(self.root, out_dir=self.out)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _ld(self, rel):
        page = (self.out / rel).read_text()
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
        self.assertTrue(m, f"no structured data on {rel}")
        return json.loads(m.group(1)), page

    def _rebuild_with_config(self, **over):
        """Fresh tree, one config key changed — the config is read at build time, so a test that
        changes it cannot reuse setUp's output."""
        root = Path(tempfile.mkdtemp(prefix="wf-site-cfg-"))
        try:
            _write(root, "04-seo/articles/01-alpha.md", ART_A)
            _write(root, "04-seo/articles/02-beta.md", ART_B)
            cfg = {"site_name": "Test", "base_url": "", "language": "en-GB"}
            cfg.update(over)
            _write(root, "config/site.json", json.dumps(cfg))
            out = root / "docs"
            return site.build(root, out_dir=out), out
        finally:
            self._tmp = root

    def test_the_language_in_the_config_reaches_the_document_not_just_the_schema(self):
        # `lang=` is what a screen reader uses to pick a pronunciation and what the browser uses to
        # hyphenate. A key that only reaches JSON-LD would be half a setting.
        res, out = self._rebuild_with_config(language="pt-BR")
        self.assertTrue(res.ok, res.errors)
        page = (out / "alpha" / "index.html").read_text()
        self.assertIn('<html lang="pt-BR">', page)
        self.assertIn('"inLanguage": "pt-BR"', page)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_a_language_that_is_not_a_language_tag_fails_the_build(self):
        res, _ = self._rebuild_with_config(language="English")
        self.assertFalse(res.ok)
        self.assertTrue(any("not a language tag" in e for e in res.errors), res.errors)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_every_built_page_carries_the_card_tags(self):
        for rel in ("alpha/index.html", "beta/index.html", "index.html"):
            head = (self.out / rel).read_text().split("</head>")[0]
            for tag in ("og:site_name", "og:title", "og:description", "twitter:card"):
                self.assertIn(tag, head, f"{rel} missing {tag}")

    def test_relative_pages_and_root_page_point_at_the_same_favicon(self):
        # The site is one folder deep for content and one level shallower for the home page; a
        # hardcoded href would 404 the icon on half the site.
        self.assertIn('href="../favicon.svg"', (self.out / "alpha" / "index.html").read_text())

    def test_no_absolute_url_tags_without_a_domain(self):
        head = (self.out / "alpha" / "index.html").read_text()
        self.assertNotIn("og:url", head)
        self.assertNotIn("og:image", head)
        self.assertFalse((self.out / "feed.xml").exists())

    def test_schema_is_valid_json_and_dates_from_the_review_line(self):
        d, _ = self._ld("alpha/index.html")
        self.assertEqual(d["@type"], "Article")
        self.assertEqual(d["dateModified"], "2026-10-14")
        self.assertEqual(d["inLanguage"], "en-gb")
        self.assertTrue(d["isAccessibleForFree"])
        # `published:` is not in the fixture, so the field must be absent rather than guessed.
        self.assertNotIn("datePublished", d)

    def test_the_author_node_claims_nothing_beyond_a_name(self):
        d, _ = self._ld("alpha/index.html")
        self.assertEqual(sorted(d["author"].keys()), ["@type", "name"])
        self.assertEqual(d["author"]["name"], "A. N. Other")

    def test_a_graph_is_a_graph_and_not_a_duplicated_node(self):
        # A graph only forms once there are absolute URLs to relate, so this one builds with a domain.
        _write(self.root, "config/site.json", json.dumps(
            {"site_name": "Test", "base_url": "https://example.org/site", "author": "A. N. Other"}))
        site.build(self.root, out_dir=self.out)
        d, _ = self._ld("alpha/index.html")
        # The envelope holds only the context and the list. Some generators paste the first node at
        # the top level *and* inside the graph, which validators accept and parsers trip on.
        self.assertEqual(sorted(d.keys()), ["@context", "@graph"])
        self.assertEqual([n["@type"] for n in d["@graph"]], ["Article", "BreadcrumbList"])
        self.assertNotIn("@context", d["@graph"][0], "inner nodes inherit the envelope's @context")
        # And with no domain there is nothing to breadcrumb *to*: the node stands alone, and a
        # half-empty graph is not shipped in its place.
        _write(self.root, "config/site.json", json.dumps({"site_name": "Test", "base_url": ""}))
        site.build(self.root, out_dir=self.out)
        d2, _ = self._ld("alpha/index.html")
        self.assertEqual(d2["@type"], "Article")
        self.assertNotIn("@graph", d2)

    def test_feed_autodiscovery_is_on_every_page_once_a_domain_exists(self):
        _write(self.root, "config/site.json", json.dumps(
            {"site_name": "Test", "base_url": "https://example.org/site"}))
        site.build(self.root, out_dir=self.out)
        for rel in ("alpha/index.html", "beta/index.html", "index.html"):
            self.assertIn('rel="alternate" type="application/rss+xml"',
                          (self.out / rel).read_text(), rel)

    def test_no_ratings_no_search_and_no_fake_stats_in_the_schema(self):
        blob = " ".join((self.out / r / "index.html").read_text() for r in ("alpha", "beta"))
        for forbidden in ("aggregateRating", "SearchAction", "usersCount", "interactionCount",
                          "speakable", "medicalSpecialty"):
            self.assertNotIn(forbidden, blob, f"{forbidden} would be a claim the site cannot support")

    def test_a_page_without_a_review_date_does_not_build(self):
        root2 = Path(tempfile.mkdtemp(prefix="wf-site-undated-"))
        try:
            _write(root2, "04-seo/articles/01-alpha.md", ART_A.replace("last_reviewed: 2026-10-14\n", ""))
            _write(root2, "04-seo/articles/02-beta.md", ART_B)
            res = site.build(root2, out_dir=root2 / "docs", check=True)
            self.assertFalse(res.ok)
            self.assertTrue(any("last_reviewed" in e for e in res.errors), res.errors)
        finally:
            shutil.rmtree(root2, ignore_errors=True)

    def test_a_garbage_review_date_is_rejected_too(self):
        root2 = Path(tempfile.mkdtemp(prefix="wf-site-baddate-"))
        try:
            _write(root2, "04-seo/articles/01-alpha.md", ART_A.replace("2026-10-14", "last week"))
            _write(root2, "04-seo/articles/02-beta.md", ART_B)
            res = site.build(root2, out_dir=root2 / "docs", check=True)
            self.assertTrue(any("last_reviewed" in e for e in res.errors), res.errors)
        finally:
            shutil.rmtree(root2, ignore_errors=True)

    def test_base_url_turns_on_urls_and_the_feed(self):
        _write(self.root, "config/site.json", json.dumps(
            {"site_name": "Test", "base_url": "https://example.org/site", "author": "A. N. Other"}))
        site.build(self.root, out_dir=self.out)
        head = (self.out / "alpha" / "index.html").read_text()
        self.assertIn('<meta property="og:url" content="https://example.org/site/alpha/">', head)
        self.assertIn('rel="alternate" type="application/rss+xml"', head)
        feed = (self.out / "feed.xml").read_text()
        self.assertIn("<title>Test</title>", feed)
        self.assertIn("https://example.org/site/alpha/", feed)
        self.assertIn("14 Oct 2026", feed)      # RFC-822 form of the review date, not a guess
        self.assertRegex(feed, r"<pubDate>[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} 09:00:00 \+0000</pubDate>")

    def test_og_image_appears_only_when_the_png_exists(self):
        _write(self.root, "config/site.json", json.dumps(
            {"site_name": "Test", "base_url": "https://example.org/site"}))
        site.build(self.root, out_dir=self.out)
        self.assertNotIn("og:image", (self.out / "alpha" / "index.html").read_text())
        self.assertEqual("summary", re.search(r'twitter:card" content="(\w+)"',
                                              (self.out / "alpha" / "index.html").read_text()).group(1))
        (self.root / "04-seo" / "images").mkdir(parents=True, exist_ok=True)
        _write(self.root, "04-seo/images/og-card.png", "png-bytes")
        site.build(self.root, out_dir=self.out)
        head = (self.out / "alpha" / "index.html").read_text()
        self.assertIn('<meta property="og:image" content="https://example.org/site/og-card.png">', head)
        self.assertIn('summary_large_image', head)
        self.assertTrue((self.out / "og-card.png").exists())

    def test_brand_assets_are_carried_into_the_site(self):
        (self.root / "04-seo" / "images").mkdir(parents=True, exist_ok=True)
        _write(self.root, "04-seo/images/favicon.svg", "<svg/>")
        _write(self.root, "04-seo/images/og-card.svg", "<svg/>")
        site.build(self.root, out_dir=self.out)
        self.assertEqual((self.out / "favicon.svg").read_text(), "<svg/>")
        self.assertTrue((self.out / "og-card.svg").exists())

    def test_the_tool_page_gets_its_own_schema_and_one_icon(self):
        # The planner is copied verbatim, so its metadata has to be injected — and its inline icon
        # link must not be doubled.
        _write(self.root, "03-tool/index.html", '<!doctype html><html><head><title>Planner</title>\n'
                 '<link rel="icon" href="data:image/svg+xml,<svg/>"></head><body><p>tool</p></body></html>\n')
        res = site.build(self.root, out_dir=self.out)
        page = (self.out / "planner" / "index.html").read_text()
        self.assertEqual(len(re.findall(r'<link rel="icon"', page)), 1)
        self.assertIn("og:site_name", page)
        d = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
        self.assertEqual(d["@type"], "WebApplication")
        self.assertTrue(d["isAccessibleForFree"])


class RepoSite(unittest.TestCase):
    """The promise, applied to the repo itself."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wf-site-repo-"))
        cls.res = site.build(REPO, out_dir=cls.tmp / "docs", check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_real_site_builds_with_no_errors(self):
        self.assertTrue(self.res.ok, "\n".join(self.res.errors))

    def test_every_promise_in_the_navigation_exists(self):
        pages = {p.slug for p in self.res.pages}
        for want in ("planner/", "book/", "about/", "editorial-policy/",
                     "sleep-after-night-shift/", "what-to-eat-on-a-night-shift/",
                     "caffeine-on-night-shift/", "nap-before-shift/", "4-on-4-off-sleep/",
                     "no-microwave-meal-prep/", "blackout-curtains-eye-mask/"):
            self.assertIn(want, pages, want)

    def test_hand_written_front_matter_is_valid(self):
        # Front matter is the one field people edit by hand, and an unterminated quote breaks the
        # YAML silently — two files in this repo did exactly that before this test existed.
        for f in sorted(list((REPO / "04-seo" / "articles").glob("*.md")) +
                        list((REPO / "04-seo" / "pages").glob("*.md"))):
            fm = f.read_text().split("---", 2)[1]
            for line in fm.splitlines():
                if ":" in line:
                    self.assertEqual(line.split(":", 1)[1].count('"') % 2, 0,
                                     f"{f.name}: unbalanced quote in `{line[:40]}`")

    def test_the_invented_domain_did_not_survive_into_the_build(self):
        # `03-tool/index.html` used to hard-code a canonical on a domain nobody owns.
        self.assertNotIn("shiftreset.tools", "".join(
            (REPO / rel).read_text() for rel in ("03-tool/index.html",)))

    def test_every_page_of_the_real_site_has_structured_data_and_a_card(self):
        # Rebuild into a temp folder and read the actual output: this is the artefact a host serves.
        tmp = Path(tempfile.mkdtemp(prefix="wf-site-meta-repo-"))
        try:
            site.build(REPO, out_dir=tmp)
            n = 0
            for f in sorted(tmp.rglob("index.html")):
                head = f.read_text().split("</head>")[0]
                self.assertIn("og:title", head, f"{f.relative_to(tmp)} has no social title")
                self.assertIn("application/ld+json", head, f"{f} has no structured data")
                n += 1
            self.assertGreaterEqual(n, 13, "every page plus the home page and the planner")
            self.assertTrue((tmp / "favicon.svg").exists())
            self.assertFalse((tmp / "feed.xml").exists(), "no base_url yet, so no absolute-URL feed")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_feed_is_valid_xml_and_dates_from_published_when_stated(self):
        import xml.etree.ElementTree as ET
        root = Path(tempfile.mkdtemp(prefix="wf-site-feed-"))
        try:
            _write(root, "04-seo/articles/01-alpha.md", ART_A.replace(
                "last_reviewed: 2026-10-14", 'last_reviewed: 2026-10-14\npublished: 2026-10-01'))
            _write(root, "04-seo/articles/02-beta.md", ART_B.replace(
                "last_reviewed: 2026-10-14", "last_reviewed: 2026-09-30"))
            _write(root, "config/site.json", json.dumps({
                "site_name": "Test & Co <x>", "base_url": "https://example.org/site/",
                "language": "en-gb"}))
            res = site.build(root, out_dir=root / "docs")
            self.assertTrue(res.ok, res.errors)
            feed = (root / "docs" / "feed.xml").read_text()
            ch = ET.fromstring(feed).find("channel")          # a feed that doesn't parse is worse than none
            self.assertEqual(ch.findtext("title"), "Test & Co <x>")   # escaped in, decoded out
            self.assertEqual(ch.findtext("language"), "en-gb")
            items = ch.findall("item")
            self.assertEqual([i.findtext("title") for i in items], ["Alpha", "Beta"])
            self.assertIn("01 Oct 2026", items[0].findtext("pubDate"))   # published, not reviewed
            self.assertIn("30 Sep 2026", items[1].findtext("pubDate"))   # review date as the fallback
            self.assertIn("https://example.org/site/alpha/", items[0].findtext("guid"))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_delivered_vector_assets_parse(self):
        import xml.etree.ElementTree as ET
        for rel in ("04-seo/images/favicon.svg", "04-seo/images/og-card.svg"):
            f = REPO / rel
            self.assertTrue(f.exists(), f"{rel} is the site's mark / social card source")
            node = ET.parse(f).getroot()
            self.assertTrue(node.tag.endswith("svg"), rel)
            self.assertTrue(node.get("width") and node.get("height"),
                          f"{rel} needs an intrinsic size; a viewBox-only SVG renders blank in some readers")

    def test_the_report_states_which_host_features_are_switched_on(self):
        md = site.report_markdown(self.res, "docs")
        self.assertIn("**Metadata:**", md)
        self.assertIn("canonical/sitemap/feed off (needs `base_url`)", md)
        self.assertIn("without a `published:` date", md)

    def test_report_markdown_is_readable_and_names_the_numbers(self):
        md = site.report_markdown(self.res, "docs")
        self.assertIn("site build", md.lower())
        self.assertIn("planner", md)
        self.assertGreater(len(md), 200)


class NoOrphans(unittest.TestCase):
    def test_a_page_nothing_links_to_is_warned_about(self):
        root = Path(tempfile.mkdtemp(prefix="wf-site-orphan-"))
        try:
            _write(root, "04-seo/articles/01-alpha.md", ART_A)
            _write(root, "04-seo/articles/02-beta.md", ART_B.replace(
                'internal_links: ["/alpha/"]', 'internal_links: []').replace(
                "a [thing that is a page](/alpha/) and nothing else", "nothing linked at all"))
            _write(root, "04-seo/pages/09-orphan.md", """---
title: "Orphan"
slug: /orphan/
description: "Nobody links here."
internal_links: ["/alpha/"]
---

# Orphan

This page is linked from nowhere. Not medical advice.
""")
            res = site.build(root, out_dir=root / "docs", check=True)
            self.assertTrue(any("no prose page links to" in w for w in res.warnings), res.warnings)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
