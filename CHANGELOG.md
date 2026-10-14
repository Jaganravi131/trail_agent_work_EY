# Changelog

No tags exist in this repository, and `wealforge.__version__` (`0.3.0`) is the only version string in
it — so this file is grouped by the work and the commits that carry it, not by releases that never
happened. Inventing a back-dated version history would be the same error as back-dating a `published:`
field on an article, and the repo has a rule against both.

Cutting the first tag is a human act. The checklist is at the bottom.

---

## Unreleased — everything since the version was declared (`1d8264b`, 2026-10-11)

### The site stopped being markdown and became a deployable folder (`e104697` `88d62ec` `0004a69`, 2026-10-14)

`./wf site` builds `docs/` from `04-seo/` and `03-tool/`: clean `/slug/` URLs, relative links so a
GitHub Pages subpath works, the FTC disclosure injected on any page carrying an affiliate link, and
every internal link resolved at build time so a 404 is a build error rather than a live URL. Nine
articles and three pages, byte-identical on rebuild, CI diffing the folder against its sources.

### Structured data, social cards and a feed (`b834618` `3e8dd86`, 2026-10-14)

JSON-LD per page (`Article` / `WebPage` / `WebApplication` / `WebSite` + breadcrumbs), Open Graph and
Twitter cards, `feed.xml`, `sitemap.xml`, canonicals, and a favicon and 1200×630 social-card source in
`04-seo/images/`. Absolute-URL features appear **only** once `config/site.json` has a domain, and
`og:image` only when the exported PNG also exists; `datePublished`, ratings, `SearchAction`, user
counts and author credentials are absent on purpose, with tests asserting the absence. `last_reviewed`
became a required `YYYY-MM-DD` because the visible review line and schema `dateModified` are one
promise.

### The ebook builds with no installs, and the listing became data (`1be335e` `7f15a87` `066b585`, 2026-10-14)

- `wealforge/book.py` + `./wf book`: `01-book/chapters/*.md` → a valid EPUB3 using only the standard
  library (mimetype stored first, container + OPF with a `dcterms:modified` taken from git, nav and
  NCX, the book's own stylesheet, chapter 9's ASCII cards preserved as `<pre>`). `./wf book --check`
  refuses a stale `manuscript.md`, a placeholder or an image reference in a paid file, a TOC anchor
  with no target, manifest ids that are not legal XML ids, and a cover whose measured pixels are not
  the size in its filename.
- `02-kdp/kdp-metadata.json` is now the source of the listing copy; `./wf validate` checks KDP's
  documented limits *and* runs the compliance gate over it, and `./wf book` titles the EPUB from the
  same values. The blurb gained its "educational, not medical advice" line and its never-drive-drowsy
  rule because the gate failed us first and was right.
- One English variety for the portfolio: `config/site.json` → `language` is a validated tag that reaches
  `<html lang>`, `inLanguage`, the feed and the ebook's `dc:language`, and a test audits the prose
  against the claim.
- The gate stopped reading a generated aggregate twice (`01-book/manuscript.md`), which had been
  reporting a 22,543-word book and could turn a correct line-scoped suppression into a finding.
- Both builders now compare the words they were given with the words they emitted, per file, so a block
  type the renderer does not understand cannot silently vanish from a page or a chapter.

### Fixed while getting there — each was a real defect a checker found

- `evalkit.NUMERIC` did not count length units or hyphenated compounds, so two pages were falsely
  flagged as thin prose (and the fix moved ch. 7 from 0.29 to 0.48 numbers per 100 words).
- The feed's autodiscovery link reached only the index and the tool page, not the articles where readers
  actually arrive; and the JSON-LD envelope duplicated its first node instead of wrapping it.
- Three validator rules cried wolf: `heal` matched "health" and `cure` matched "secure"; any "will make"
  read as an income promise; and a keyword that merely shares a word with the title was called wasted
  space, which is not how Amazon indexes.
- `01-book/build.sh` died on a missing cover file, and both it and `build-book.yml` stamped the
  manuscript as `en-US` while the copy is British English.
- Numbers had drifted where they were quoted by hand: the KDP sheet's page count, its KENP figure (which
  belonged to a 170-page book), and the sales page's "~11,600 words" for an 11,262-word manuscript. The
  sales-page figure is now checked against the build.

### Verified from a clean clone, and the last unbacked claim became an executable check (`0d687a9`→, 2026-10-14)

Cloned to an empty directory and run as a stranger would: sixteen commands, every one exiting 0, the site
rebuild byte-identical, the ebook reproducible, `docs/` crawled with zero broken references. Then the claims
that no command could prove were made provable.

- `tools/ci/planner-check.mjs` + `tools/ci/planner-check.sh` + `tests/test_planner.py`: the free planner's
  JavaScript is now actually executed — evaluated as written against a stub DOM in node, clicking *Build my
  plan* through `go()` for nine rota patterns and asserting the plan's arithmetic, the rendered text, the
  exported `.ics` and the error paths. The README had claimed headless testing with nothing in the repo to
  run. Two real defects came out of it: readers who ticked *I can't nap* were getting a 90-minute nap alarm
  at their meal time, and `.ics` lines ran past 75 octets, which a strict calendar client rejects (RFC 5545
  §3.1) — now folded. `tests/test_planner.py` also breaks the page deliberately (curfew, calendar tail,
  folding rule) so the harness cannot pass by doing nothing, and skips with a visible reason where node is
  absent.
- The site builder stopped assuming its own assets exist: the favicon link is written only when
  `04-seo/images/favicon.svg` does, and the nav and the footer's policy link are built from the slugs the
  build produced. A shell that hard-codes `/planner/` in every page of a tree with no `03-tool/` is the same
  bug the `related`-links check already refuses, one level up.
- The built `404.html` carries `noindex,follow`: hosts answer a dead URL with that file, but a copied folder
  or a preview serves it at 200, and search engines will index "No page here" plus its twelve links.
- `tests/test_site.py` gained the root extras it had never covered — the 404 page's links and its robots
  tag, `robots.txt` advertising a sitemap only when a sitemap was written, `.nojekyll`, the Netlify header
  block. Suite: 254 tests → 263.
- The corpus is reproducible in the strictest sense available here: a clean clone running
  `./wf dataset` writes `sft-train.jsonl` / `sft-val.jsonl` byte-identical to this machine's
  (`1e0a8166…` / `a759c14e…`), and the committed `training/MANIFEST.json` records exactly those two
  hashes, so a config can be checked against the data it was written from on any machine.
- A green CI run now closes a blocking issue only when the failing commit is genuinely behind it
  (`0d687a9`). A red run that finished after a green one had left issue #3 open on a fixed branch, which is
  how an alert becomes wallpaper.

---

## v0.3.0 (declared, never tagged) — `1d8264b`, 2026-10-11

Generation through a locally resolved open-weight model, with model output treated as a candidate the
gate may reject; licence-aware model ranking (a licence that forbids training on outputs cannot be a
fine-tune base); `wf dataset` able to answer "not yet" against 150/40 corpus floors; `wf eval --compare`
as the gate an adapter must pass before any default flips.

## The agent as software — `5329888`, 2026-10-11

Twelve stdlib modules, 92 tests: the idea scorer with its 17 disqualifiers, the compliance gate with
its suppression machinery, the ledger-backed stage machine that cannot be skipped, the tracker reader and
its "exactly one experiment" diagnosis.

## Portfolio 001 — `e7a482d` `cfa888f`, 2026-10-11

*The Night Shift Reset*: 11 chapters, the shift-work sleep-window planner as one HTML file, the first
three anchor articles, the KDP cover brief and upload sheet, the 90-day roadmap, the revenue model, the
tracker, the risk register — and the EPUB structure check that caught pandoc 3.9's renamed flag.

---

## To cut the first tag

1. `python3 -m unittest discover -s tests -q` → `Ran 263 tests — OK`.
2. `./wf gate 01-book --bundle` and
   `./wf gate 03-tool 04-seo 02-kdp 05-marketing 06-tracking 07-risks --summary --strict` → both PASS.
3. `./wf site --check && ./wf book --check && ./wf validate 02-kdp/kdp-metadata.json` → clean, and
   `git status --porcelain docs/ 01-book/manuscript.md` empty.
4. Decide the number. `0.3.0` is what the code declares; `0.4.0` is the honest answer if you tag today,
   because a module and two commands were added after the declaration. If you pick `0.4.0`, edit
   `wealforge/__init__.py` in the same commit as the tag so the version and the release agree.
5. `git tag -a v0.4.0 -m "Portfolio 001 + deployable site + uploadable ebook" && git push origin v0.4.0`
   and open the release against it.

Nothing here should be tagged while a fact-check item in `04-seo/01-KEYWORD-MAP.md` is still open, or
before the twelve of them have a human's yes: a release is a claim that the thing is finished, and the
only unfinished parts are the parts that need a person who is accountable for the advice.
