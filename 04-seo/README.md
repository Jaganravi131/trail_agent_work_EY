# `04-seo/` — the site's source text

Everything a reader sees on the hosted site is written here, and nothing here is edited after the
build except by editing it. `./wf site` turns these markdown files plus `../03-tool/index.html` into
`docs/`, which is the folder you actually publish.

| Path | What it is |
|---|---|
| `01-KEYWORD-MAP.md` | Query research → the asset that answers each one, the site tree, and the internal-linking rule. Not published; it is the plan the published pages are kept in sync with |
| `02-DEPLOY.md` | How to get `docs/` live in ~20 minutes for $0, the checks to run afterwards, and where the domain is configured |
| `articles/` | The pillar articles (five so far). Reader-facing, long, cited, and gate-scanned |
| `pages/` | Home-adjacent pages that aren't articles: `/about/`, `/editorial-policy/`, `/book/`, and the two commercial how-tos |
| `images/` | Does not exist yet, deliberately. Photo references point here; until a file lands, the build renders a labelled "photo to supply" box instead of a broken icon |

## The format a page has to follow

Front matter is the build's only metadata, and it is validated: a missing required key, a malformed
slug, an unterminated list or a quote that never closes all fail the build with the file and key
named. Extra keys are allowed and ignored, because a note to future-you in front matter is worth more
than a strict schema that gets worked around:

```yaml
---
title: "Question-shaped title, the way a person types it"
slug: /page-slug/                      # becomes docs/page-slug/index.html
description: "140–160 chars. The search-result line."
internal_links: ["/other-page/", "/planner/"]   # nav order; every entry must exist
last_reviewed: 2026-10-14              # printed on the page. Bump it only after a real review
---
```

Then in the body: one image line (`![what to photograph — replace with a real photo of your own
setup](images/whatever.jpg)`), `## ` sections, at least two internal links that also appear in
`internal_links`, the italic *Educational content, not medical advice.* line, and a closing
`**Next:**` that names one specific action. The compliance gate (`./wf gate 04-seo`) fails the build
without the disclaimer, and the drive-drowsy rule is required anywhere fatigue and driving meet.

## Rules worth knowing before you add a page

- **Don't hand-edit `docs/`.** It's generated, committed, and diffed by CI; a rebuild is
  byte-identical, which is what makes the diff meaningful.
- **Every internal link is checked.** `/planner/` resolves because `wf site` builds the tool into the
  site. A page that links to something unwritten is an error, not a warning — that's how "we'll add
  the earplug page later" turns into a live 404.
- **Affiliate links are handled, not remembered.** If a link's host is in `affiliate_hosts`
  (`config/site.json`), the builder injects the disclosure paragraph at the top of that page and adds
  `rel="sponsored nofollow"`. The disclosure line trips the site's own promotion keyword, so the
  injected HTML carries an explicit `gate:allow M1` marker rather than a config nobody reads.
- **`pages/` is not in the training corpus on purpose.** `dataset.py` includes `01-book/chapters/*.md`
  and `04-seo/articles/*.md`: book chapters and pillar articles are the voice. A sales page and an
  editorial policy are compliance copy — feeding them to a model teaches it to sell and to hedge, and
  `pages/` stays out so the decision is visible in the include list rather than buried in a prompt.
- **No numbers without a unit and no claims without a source.** The quality floor in CI measures
  quantities per 100 words; a paragraph of adjectives scores badly here, which is the point.

## Two folders, one rule

`articles/` and `pages/` are both prose, both gated, both built. The difference is intent: an article
answers a question and is the thing a search result sends someone to; a page supports the site (who
writes, what's for sale, how corrections work). When a page starts growing a 1,800-word explanation,
it wants to be an article.
