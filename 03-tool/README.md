# `03-tool/` — the Shift Reset planner

One file: `index.html`, 431 lines of self-contained HTML, CSS and vanilla JS. No build step, no
dependencies, no CDN, no framework. It is the piece of this project that *does* something, which is
why it exists: a page that calculates your caffeine curfew earns more trust — and more links — than
another listicle about them.

## Preview it

```bash
python3 -m http.server 8000 --bind 0.0.0.0   # in this folder
# then open http://localhost:8000/index.html
```

Opening the file directly from disk also works — it makes no network calls, so there is no
`file://` CORS problem to hit.

## What it calculates

Enter a shift start, length and a couple of preferences; it derives, from the same constants the
book and articles use:

| Output | From |
|---|---|
| Anchor meal time | shift start minus ~1 h, so digestion is not peaking at lights-out |
| Caffeine doses and the curfew | 200 mg a dose, 400 mg/day ceiling, ~5 h half-life, last dose ≈5 h before bed |
| Nap window and length | 20 min if you wake inside ~30 min of the alarm, otherwise 90; 30–60 min is the inertia zone |
| Light plan | 10 min bright on waking, dim/warm after the shift's last third |
| Food cut-off | ~5 h before bed, earlier on a long shift |
| The 3-2-1-0 countdown | 3 h food, 2 h work, 1 h screens, 0 lights |
| A printable fridge card and `.ics` | plain text and a calendar file generated in-page |

The constants live in one object near the top of the `<script>` block. If a number changes in
`01-book/chapters/` and `04-seo/articles/`, it changes here too — nothing syncs them automatically,
and pretending otherwise would be the kind of quiet drift that makes a tool wrong.

## What is *not* in it

- **No network traffic at all.** No `fetch`, no XHR, no beacon, no web font, no analytics. Your shift
  times are stored in `localStorage` on your own device so a reload doesn't lose them, and that is
  the whole privacy story: nothing is uploaded because nothing could be.
- **No health claims.** The output is timing arithmetic with a visible "educational only — not medical
  or safety advice" line, plus the drowsy-driving rule on the print-out.
- **No invented domain.** This file used to ship `<link rel="canonical" href="https://shiftreset.tools/planner/">`.
  It no longer does: `wf site` writes the canonical from `base_url` in `config/site.json`, so the
  address exists in exactly one place and a placeholder can never reach a reader.

## How it becomes a web page

`./wf site` copies this file to `docs/planner/index.html` as part of the site build (see
`../04-seo/02-DEPLOY.md`). The copy keeps its own stylesheet — it is not wrapped in the site's shell,
because the tool's dark UI is deliberate and the site's prose pages are light — and any link it
carries gets rewritten by the builder: a link to a file that has a page becomes that page's URL, a
link to a repo document that has no page loses its anchor and stays as text, so the deployed folder
contains no 404s. Edit this file, never `docs/planner/index.html`; CI rebuilds and diffs `docs/` and
fails the run when the two disagree.

## Checks that already exist

- `./wf gate 03-tool` — the compliance rules run over this folder in CI (a reader-facing asset, not
  just prose).
- `tests/test_site.py` — the planner's link rewriting, the missing-canonical behaviour and the fact
  that `/planner/` exists as a page the rest of the site can link to.
- The `.ics`/fridge-card code is exercised by hand before a deploy, not by the test suite: it is
  browser-only JS and there is no headless browser in this sandbox. Anything a reader must trust for
  safety is written in prose in the articles, where the gate can actually read it.
