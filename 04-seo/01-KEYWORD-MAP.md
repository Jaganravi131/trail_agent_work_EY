# Keyword Map — shift-worker sleep cluster

Generated from training-data simulation of autocomplete, "People Also Ask" and related-search patterns. **No paid tool, no scraping of Amazon or Google.** Verify the top 5 by hand in an incognito window for 15 minutes before publishing anything — autocomplete is regional and it changes.

## The intent ladder (why this order)

A new domain cannot win "shift work sleep disorder" (domain authority + medical content = YMYL-locked to established sources). It *can* win the "how do I actually…" middle where the searcher wants a person's procedure, not a clinic's pamphlet. So the site is built middle-out.

| Tier | Query | Est. difficulty | Intent | Target asset |
|---|---|---|---|---|
| **T1 head — do not chase yet** | shift work sleep disorder | very hard, YMYL | clinical | mention only, link out |
| T1 | how to sleep during the day | hard | how-to | art. 02 (pillar, 6 mo horizon) |
| T2 | night shift sleep tips / tricks | medium-hard | listicle | art. 02 section |
| **T2 the money zone** | | | | |
| T2 | how to sleep after a night shift | **low-med** | urgent how-to | **art. 01** |
| T2 | how much caffeine is too much in a day | low-med | informational | art. 03 §1 |
| T2 | should I nap before a night shift | **low** | decision | **art. 04** (/nap-before-shift/) |
| T2 | what to eat on a night shift | low-med | how-to | **art. 02** |
| T2 | 4 on 4 off sleep schedule | **low** | specific rota | **art. 05** (/4-on-4-off-sleep/) |
| T2 | why can't I sleep after night shift | low | problem | art. 01 §1 |
| T2 | is it bad to drink coffee at 4am | low | permission | art. 03 §2 |
| T3 long-tail — instant wins, low volume, high conversion | | | | |
| T3 | blackout curtains vs eye mask for day sleeping | low | commercial | art. 01 §4 + product link |
| T3 | caffeine half life 5 hours meaning | low | understanding | art. 03 §1 |
| T3 | can i drink coffee after midnight shift | low | permission | art. 03 |
| T3 | night shift meal prep no microwave | **very low** | commercial how-to | art. 02 §5 |
| T3 | 12 hour shift schedule health effects | low-med | informational | art. 01 §1 (defensible, cited) |
| T3 | 90 minute nap before a shift | **very low** | how-to | art. 04 §3 |
| T3 | sleep inertia after a nap | low | mechanism | art. 04 §2 (the 30–60 min trap) |
| T3 | 3 on 3 off night shift sleep | low | specific rota | art. 05 §the eight days (same arithmetic, different block) |
| T3 | forward vs backward shift rotation | low | mechanism | art. 05 §rotation direction |
| T3 | is 4 on 4 off better than 5 on 2 off | low | comparison | art. 05 §three schedules |
| T3 | best earplugs for day sleeping | low | commercial | art. 01 §4 |
| T3 | how to get out of night shift schedule | very low | decision | art. 03 §4 (the rota script) |
| T3 | night shift depression what to do | medium, YMYL | help-seeking | *not* monetised; signpost to clinicians |
| T3 | days off after night shift too short | very low | commutes | art. 03 |
| T3 | coffee nap how it works | low | how-to | art. 03 §3 |

**Rule for the whole site:** one page per *question a person actually asks at 07:40 on their way to bed*, never one page per keyword group. Google is reasonably good at grouping synonyms; humans are not forgiving of a page that answers a neighbouring question.

## Site architecture (12 pages, then stop)

```
shiftreset.tools/
├── /                       home: the tool + 3 pillar cards
├── /planner/               the free tool (index.html)  ← link magnet
├── /sleep-after-night-shift/        article 01  (pillar)
├── /what-to-eat-on-a-night-shift/   article 02  (pillar)
├── /caffeine-on-night-shift/        article 03  (pillar)
├── /4-on-4-off-sleep/               **shipped 2026-10-13** (was batch 2)
├── /blackout-curtains-eye-mask/     batch 2  (affiliate disclosure)
├── /earplugs-day-sleeping/          batch 2
├── /no-microwave-meal-prep/         batch 2
├── /days-off-reset/                 batch 2
├── /nap-before-shift/               **shipped 2026-10-13** (was batch 3)
├── /book/                           single sales page → Amazon
└── /about/ + /editorial-policy/     **shipped 2026-10-13** — E-E-A-T: who wrote it, what
    this isn't, and the corrections table that has to stay visible
```

12 pages, then re-evaluate. Not 60. A 60-page thin site in 2026 is a negative signal, and it's also 60 hours you'll never get back.

## Internal linking rule

Every pillar links to **two** other pillars + the tool + /book/. The tool links to /book/ and the three pillars. No orphans. Anchor text = the question in the target page's title, verbatim, once per link — not the keyword stuffed three ways.

## E-E-A-T package (do all five; costs nothing, and it's what a new domain lacks)

1. `/about/` — real name or an explicitly-labelled pen name, actual work history in the industry, **no invented credentials**.
2. `/editorial-policy/` — states plainly: not medical advice, no paid placements in editorial text, corrections logged with dates.
3. DateModified on every article + a visible "last reviewed" line.
4. Named primary sources for every physiological claim (sleep foundation, CDC/NIOSH shift-work guidance, a named caffeine study) linked with `rel="nofollow"` offsite where appropriate.
5. The original tool. A page that *does* something earns links a text page can't.

## What I did NOT do, and you shouldn't either

- No scraped Amazon reviews, no "found these keywords in competitor listings" claim — that violates TOS and I can't verify it anyway.
- No keyword-stuffed meta. Titles below are written for the human first.
- No AI-content laundering on the site: if a platform requires AI disclosure, disclose. It costs you almost nothing on an independent static site and costs you the account on Amazon.
- No fake author persona. "J. Arek Thorne" is fine **if** the bio in the back matter is true as written — lived experience, systems writer, not a clinician. The moment a bio says "board-certified" it becomes fraud.

## Publishing cadence tied to the calendar in `05-marketing/`

| Week | Ships |
|---|---|
| 1 | Tool live, 3 pillars live, /about/, /editorial-policy/ — **the last two shipped early, see `04-seo/pages/`** |
| 2 | 4 batch-2 pages, backlink outreach to 10 free/no-cost places |
| 3 | 4 more pages, first 5 Reddit/LinkedIn value posts |
| 4 | /nap-before-shift/ and /4-on-4-off-sleep/ — already shipped, so spend this week on the review-the-numbers pass and fixing the weakest 2 pages instead |

Weeks 5–12: one pillar-quality page per week, plus the weekly social rhythm. If a page hasn't earned a click in 8 weeks, rewrite its title and first 200 words before writing anything new.
