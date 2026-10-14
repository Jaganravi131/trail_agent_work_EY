# Tracking: 15 minutes a week, and why these columns

**Open `01-tracker.csv` in Google Sheets (free) or Excel. Fill it every Sunday, including weeks that look like nothing happened.** Weeks of zeros are the dataset that lets you tell "bad product" from "bad packaging" — people who skip them can't diagnose anything, which is the main reason projects like this get abandoned at day 34.

## The only numbers that matter, and what they mean

| Metric | Where | Healthy | If it's wrong |
|---|---|---|---|
| KDP "book edits" / store views | KDP Reports → KENP + sales pages | trending up | not a ranking problem: it's a cover/subtitle problem |
| Units (ebook+paperback) | KDP Reports | 1–3/wk by day 45 | see diagnosis tree |
| Net/sale | KDP | ~$2.70 ebook, ~$5.14 paperback | if lower, you're outside the 70% band or priced < $2.99 |
| KENP pages read | KDP | >0.5 per sale | readers start and stop → ch. 3–5 sag |
| Tool visits | Pages + GSC | 10+/day by day 60 | distribution problem, not product |
| Tool prints / .ics downloads | the buttons themselves (see below) | 5–15% of visits | plan isn't landing → make the card less abstract |
| GSC clicks + avg position | Search Console | any keyword < 15 by day 60 | if clicks ≈ 0 but position < 12 → title/meta problem, rewrite in 30 min |
| Reviews | Amazon | 10 by day 45, 25 by day 90 | you didn't ask individually. Nothing else fixes this |
| Email list | Substack | +20/wk by day 60 | no visible subscribe box on the tool or every article |
| Gumroad sales | Gumroad | 2+/wk once a page ranks | price, or the 30-page PDF isn't obviously different from the free ch. 9 |

**Instrumenting the tool without an analytics provider:** there is no tracking script on the page — that's a feature. For print/.ics counts, either add a `mailto:` ask ("tell me it worked") or, if you want numbers without buying anything, add Plausible/GA4's free tier later and *then* update the privacy line in the footer to say what you collect. Do not silently add a tracker after promising "nothing uploaded"; that promise is on the page.

## Derived rows worth computing at week 4, 8, 12

- **Visit → email** % (target 1–3%). Below 1% → the subscribe prompt is buried or the offer is vague.
- **Email → sale** % on launch days (target 2–5%). Below → list isn't your audience.
- **Blended net per engaged unit** = total KDP net ÷ (units + KENP reads). Target ≥ $2.50.
- **Hours invested per $ earned** — the honest number, and the one that decides whether to keep spending 2 hrs/day. If it's still >$20/hr equivalent at day 90 with rising curves, keep. If it's flat at day 90, change packaging (cover/subtitle), not the niche.

## One rule

Add **one** new measurement per month at most. People who instrument 14 things at once end up reading dashboards instead of shipping asset 2.
