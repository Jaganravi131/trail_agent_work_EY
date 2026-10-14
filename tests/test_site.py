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

    def test_check_mode_writes_nothing(self):
        res = self.build(check=True)
        self.assertTrue(res.ok, res.errors)
        self.assertFalse(self.out.exists(), "--check must not touch the deploy folder")

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
