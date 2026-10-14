# Site build report

**12 prose page(s)** · 21 file(s) written · 0 error(s), 5 warning(s)

| page | kind | words | internal links |
|---|---|---|---|
| `/4-on-4-off-sleep/` | article | 1798 | 6 |
| `/blackout-curtains-eye-mask/` | article | 1248 | 3 |
| `/caffeine-on-night-shift/` | article | 1223 | 4 |
| `/days-off-reset/` | article | 1894 | 5 |
| `/earplugs-day-sleeping/` | article | 1830 | 4 |
| `/nap-before-shift/` | article | 1779 | 5 |
| `/no-microwave-meal-prep/` | article | 1330 | 4 |
| `/sleep-after-night-shift/` | article | 1927 | 5 |
| `/what-to-eat-on-a-night-shift/` | article | 1414 | 4 |
| `/` | index | 0 | 0 |
| `/about/` | page | 527 | 0 |
| `/book/` | page | 1022 | 5 |
| `/editorial-policy/` | page | 730 | 0 |
| `/planner/` | tool | 0 | 0 |

**Photos still to supply** (rendered as labelled placeholders, not broken icons):
- `images/batch-cook-five-containers.jpg`
- `images/blackout-curtains-daylight-test.jpg`
- `images/breakroom-coffee-3am.jpg`
- `images/bridge-day-calendar.jpg`
- `images/cover-front.jpg`
- `images/day-bedroom-dark-setup.jpg`
- `images/day-sleep-kit.jpg`
- `images/foam-plugs-and-fan.jpg`
- `images/no-microwave-lunch-bag.jpg`
- `images/pre-shift-nap.jpg`

**Metadata:** schema and Open Graph on every page · canonical/sitemap/feed off (needs `base_url`) · social card image off (needs `04-seo/images/og-card.png`) · favicon on · 9 page(s) without a `published:` date

## Warnings

- 03-tool/index.html: canonical dropped — set `base_url` in config/site.json once the domain exists
- 9 article(s) have no `published:` date, so datePublished is omitted from their schema and the feed uses the review date: 4-on-4-off-sleep/, blackout-curtains-eye-mask/, caffeine-on-night-shift/, days-off-reset/, earplugs-day-sleeping/. Add the real date at publish time — do not back-date it from the file's git history
- base_url is empty — pages build without canonical URLs. Set it when the domain exists; do not invent one before then
- 10 photo slot(s) rendered as labelled placeholders: images/batch-cook-five-containers.jpg, images/blackout-curtains-daylight-test.jpg, images/breakroom-coffee-3am.jpg, images/bridge-day-calendar.jpg
- sitemap.xml not written: it needs absolute URLs, and inventing a domain is worse than omitting the file. Set base_url and rebuild

_Rebuild with `./wf site` — the folder under `docs/` is generated, so edit the markdown in `04-seo/` instead._
