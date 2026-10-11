# KDP Backend — copy-paste upload sheet

Manuscript source: `01-book/manuscript.md` (assemble with `01-book/build.sh`, or push and let the free GitHub Action do it — see `.github/workflows/build-book.yml`).

## Title block

**Field: Book title**
```
The Night Shift Reset
```

**Field: Subtitle** — 168 chars, keyword-bearing, no promises, no "the best"
```
The 28-Day System for Eating, Caffeine and Light Timing for Nurses, EMTs, Warehouse and Overnight Desk Crews Who Are Wired at 8 a.m.
```

**Series:** `Overnight Systems` (Book 1). Create the series now, even at one book — series pages are free also-bought real estate and every future title inherits it. Book 2 planned: *The Days Off Problem: Rotations, Relationships and the Bridge Day* (see roadmap).

**Author:** `J. Arek Thorne`
**Not** "J. Arek Thorne, RN" or any credential you don't hold. Fabricated clinical credentials on a health-adjacent title is the fastest path to a takedown and to someone getting hurt. The author bio in `01-book/chapters/10-back-matter.md` is written to be true as-is.

## Description (paste into KDP's editor; HTML tags below are supported)

```html
<h3>You can't fix a night shift with willpower.</h3>
<p>Nobody tells you what to actually do at 3 a.m. Not what to eat, not when to stop the coffee, not how to sleep through a Saturday morning your neighbours have filled with a lawnmower. The advice you get is generic: darken the room, drink water, go to bed at the same time. Same time as who? Your body doesn't own a clock that matches your roster.</p>
<p><i>The Night Shift Reset</i> replaces guesswork with five numbers and one sequence you can run on 12-hour nights, a 4-on-4-off pattern, or a rota you didn't choose.</p>
<p><b>Inside:</b></p>
<ul>
<li><b>The Descent / Hold / Reentry model</b> — why your shift is three short problems with different solutions, and why advice that treats it as one problem fails</li>
<li><b>The 3-2-1-0 countdown</b> — the last food, last caffeine, lights down and no-clock times, with the arithmetic that makes it stick</li>
<li><b>Caffeine as a scheduler, not a starter</b> — a half-life table showing what a 02:00 dose is still doing to you at noon, and why "I can fall asleep fine after coffee" is not evidence</li>
<li><b>The anchor-meal rule</b> — why your big meal belongs in hour one, plus menu templates for one microwave, no fridge at all, and an 8-minute break that may not come</li>
<li><b>The $0 bedroom rebuild</b> — towel, foil, duvet and a fan: what beats a purchased blackout setup, and the earplugs almost everyone inserts wrong</li>
<li><b>The drive home</b> — the non-negotiables, the coffee nap, and the 20-minute rule</li>
<li><b>The bridge day and the two-step pivot</b> for days off, plus the exact written script for asking your rota manager for a safer pattern (and the fallback that gets you a yes on something)</li>
<li><b>28 days, one rule per day</b>, with a 2-minute log that ends with a system tuned to your body instead of a study you'll never read</li>
</ul>
<p><b>Also free with the book:</b> the Shift Reset Planner web app — enter your start time, get your nap, light, caffeine and sleep alarms, printed as a fridge card. No account, nothing uploaded.</p>
<p><b>Not in this book:</b> supplements, sleep aids, medical claims, or anyone telling you to just go to bed earlier. Written by someone who has worked nights, not by a clinician — read the disclaimer in chapter 1 and take it seriously.</p>
<p><b>Start with one alarm tonight.</b> The rest of the sequence arrives over four weeks.</p>
```

## Categories (2, chosen for thin shelves)

| Pick | Path | Why |
|---|---|---|
| 1 | `Medical Books > Nursing > Nursing Skills & Practice` (browse: Nursing > Professional Issues) | Nurses are the highest-intent buyers of this and the shelf has few shift-sleep titles. Compete where it's empty. |
| 2 | `Business & Money > Industries > Labor` or `Health, Fitness & Dieting > Diseases & Physical Ailments > Sleep Disorders` | Second choice: prefer **Sleep Disorders** only if you accept it sits next to clinical texts; prefer **Labor/Workplace Health** if you want less competition and B2B/union library sales. |

Request a third via KDP support email after publication — this is free and commonly granted; ask for `Health, Fitness & Dieting > Wellness > Occupational Health`.

## 7 backend keywords (49-char limit each, no title words, no competitor names, no "free")

```
1  night shift sleep for nurses
2  shift work sleep disorder coping
3  12 hour shift schedule survival
4  rotating shift worker wellness
5  how to sleep during the day
6  emergency department nurse burnout
7  circadian rhythm reset adult
```

Deliberately absent: "insomnia cure", "cure", "bedtime hacks", trademarked terms, and any clinician name. Those either invite a takedown or compete with 100k results. Long-tail with 3–5 words and a *job title* in it is where an unknown author actually ranks.

## Pricing

| Format | Price | Royalty maths | Notes |
|---|---|---|---|
| Ebook, KDP Select enrolled | **$3.99** | 70% − delivery fee ≈ **$2.70/sale** | 70% band is $2.99–$9.99. A text-only ~170-page file: delivery fee typically ~$0.02–0.07 at the "no images" tier — verify in the app at upload. |
| Paperback 6×9 | **$9.99** | 11,566 words ≈ **46 print pages** at ~340 pp/page + matter → print cost ≈ $1.40; 60% − cost ≈ **$4.59/sale** | **Correction:** an earlier draft of this sheet assumed ~170 pages / $12.99 — that page count belongs to a 45k-word book, not this one. A ~46-page paperback at $12.99 is a refund magnet and a 1-star waiting to happen. Two honest options: (a) price the booklet at $7.99–9.99, or (b) expand the manuscript to ~20k words by putting the worksheets and two worked examples inline, which supports ~$12.99 honestly. `wf price --words N` computes this; run it after any expansion. |
| Launch week (days 1–5) | **$0.99** | 35% ≈ $0.29/sale | 5 days, not 10. Goal is verified reviews + rank, not revenue. |
| KDP Select free days | Days 12–13 and days 26–27 | $0 | Pushes you into "also bought" feeds. Give the ebook free twice; never more. |

Enrol in KDP Select (90 days, exclusive ebook). Trade-off you're accepting: no ebook sales on Apple/Kobo/B&N for 90 days, in exchange forKENP page-read revenue (~$0.004/page → ~$0.60 for a 170-page read) and free-promo mechanics. For an unknown author in a discoverable niche, that trade is correct in quarter one. Re-evaluate at renewal if KENP is >30% of income.

## AI disclosure — answer these honestly at upload

KDP asks whether the content is AI-generated or AI-assisted.
- **This manuscript, as shipped in this repo: AI-assisted** if a human operator reviews, edits, fact-checks and takes responsibility for every claim (which is the required posture — you are the publisher, and you own the accuracy).
- **AI-generated** if you publish it without substantive review. That's not what to do here, both ethically and practically: this book contains numbers and safety statements that need a human who is accountable. Read chapters 1, 4 and 6 with a critical eye; if you wouldn't stake your name on a line, cut the line.
Answering falsely in that declaration is a policy violation with an account-permanent consequence. Do not do it.

## Manuscript formatting for upload

1. Page count reality: this manuscript is ~11.6k words ≈ 46 print pages. For a paperback, expand to ~20k (worksheets + two full worked examples inline) or accept booklet pricing.
2. EPUB built by `pandoc manuscript.md -o The-Night-Shift-Reset.epub` with a `style.css` (in `01-book/`) for headings, 1.15 line spacing, justified-off, and `page-break-before` on `h1`. Free; run it in the included GitHub Action if you have no local pandoc.
2. Do **not** upload a `.docx` with manual page breaks and a TOC you typed by hand. Let KDP generate the TOC from heading styles, and let the ebook reflow.
3. Fixed-layout only for the ASCII boxes in chapter 9 — if the art breaks on an e-ink preview, replace those boxes with a monospace-image PNG per page and set them as a "print replica" only for the paperback.
4. Paperback: upload PDF (6×9, 0.5" margins, mirror, 11 pt serif, 1.3 leading), front matter with an ISBN-free KDP ISBN, no bleed.
5. Preview every chapter on the Kindle previewer before hitting publish, at 800×600, because tables in KDP ebooks are the #1 cause of "unreadable formatting" reviews.

## Pre-publish checklist (25 minutes, in order)

- [ ] Read the whole manuscript once as a stranger, printing it or reading it on a phone at 400-nit brightness. Delete anything that reads like filler.
- [ ] Verify every number: 400 mg caffeine guidance, 5 h half-life, 18–20 °C, sleep-cycle length, the 400 mg study reference in ch. 4, the "one in five collisions" claim in ch. 6. Fix or soften anything that isn't defensible. Check the current public guidance for each figure today rather than trusting this file.
- [ ] Confirm no diagnosis/treatment claims, no supplement dosing, no drug advice. Ch. 1 and 6 disclaimers present.
- [ ] Confirm no fabricated credentials anywhere (title, bio, cover, author page).
- [ ] Cover uploaded at 2,560×1,600; thumbnail legible at 100 px.
- [ ] Title/subtitle/series/categories/7 keywords/description pasted.
- [ ] Price $3.99, rights "all territories", "I own all rights", AI declaration answered truthfully.
- [ ] Author Central profile created with the honest bio + a plain headshot or an initial mark, and the book claim-linked.
- [ ] Paperback proof ordered as a *digital* proof first ($0) and approved before committing to a physical proof.
- [ ] Live date set. Then start `05-marketing/01-CONTENT-CALENDAR.md`.
