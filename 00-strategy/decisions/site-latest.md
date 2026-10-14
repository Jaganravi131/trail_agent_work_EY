# Site build report

**10 prose page(s)** · 17 file(s) written · 0 error(s), 4 warning(s)

| page | kind | words | internal links |
|---|---|---|---|
| `/4-on-4-off-sleep/` | article | 1769 | 5 |
| `/caffeine-on-night-shift/` | article | 1223 | 4 |
| `/nap-before-shift/` | article | 1779 | 5 |
| `/sleep-after-night-shift/` | article | 1890 | 4 |
| `/what-to-eat-on-a-night-shift/` | article | 1414 | 4 |
| `/` | index | 0 | 0 |
| `/about/` | page | 527 | 0 |
| `/blackout-curtains-eye-mask/` | page | 1248 | 3 |
| `/book/` | page | 1022 | 5 |
| `/editorial-policy/` | page | 730 | 0 |
| `/no-microwave-meal-prep/` | page | 1330 | 4 |
| `/planner/` | tool | 0 | 0 |

**Photos still to supply** (rendered as labelled placeholders, not broken icons):
- `images/batch-cook-five-containers.jpg`
- `images/blackout-curtains-daylight-test.jpg`
- `images/breakroom-coffee-3am.jpg`
- `images/cover-front.jpg`
- `images/day-bedroom-dark-setup.jpg`
- `images/day-sleep-kit.jpg`
- `images/no-microwave-lunch-bag.jpg`
- `images/pre-shift-nap.jpg`

## Warnings

- 03-tool/index.html: canonical dropped — set `base_url` in config/site.json once the domain exists
- base_url is empty — pages build without canonical URLs. Set it when the domain exists; do not invent one before then
- 8 photo slot(s) rendered as labelled placeholders: images/batch-cook-five-containers.jpg, images/blackout-curtains-daylight-test.jpg, images/breakroom-coffee-3am.jpg, images/cover-front.jpg
- sitemap.xml not written: it needs absolute URLs, and inventing a domain is worse than omitting the file. Set base_url and rebuild

_Rebuild with `./wf site` — the folder under `docs/` is generated, so edit the markdown in `04-seo/` instead._
