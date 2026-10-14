# WealthForge — Opportunity Screen, Round 1

**Date:** 2026-10-11 · **Capital available:** $0 · **Human operator time:** 10–30 min per asset upload

## Scoring rubric

Ten ideas, scored 1–5 on each axis. Weighted total out of 100.

| Axis | Weight | Meaning |
|---|---|---|
| Feasibility at $0 | ×3 | Truly free to ship (no ads, no inventory, no paid tool required) |
| Time to first dollar | ×2 | Days until a real sale could plausibly land |
| Defensibility | ×3 | Why a lazy competitor can't clone it in an afternoon |
| My output quality vs. average human in niche | ×2 | Would a practitioner call this "good"? |
| Compounding / scalability | ×2 | Does asset #1 make asset #20 cheaper? |

## The scores

| # | Idea | Feas | Time | Def | Qual | Comp | Total |
|---|---|---|---|---|---|---|---|
| 1 | **Shift-worker recovery system: 12k-word KDP book + free web planner + $12 printable kit** | 5 | 3 | 4 | 5 | 5 | **92** |
| 2 | **Free single-file browser tools (5 of them) + SEO mini-site + paid template upsell** | 5 | 2 | 5 | 4 | 5 | **89** |
| 3 | Niche Excel/Sheets dashboard pack (freelancer cash-flow) on Gumroad | 5 | 4 | 2 | 4 | 3 | 78 |
| 4 | Cozy mystery novella series (40k words, 3-book arc) | 4 | 1 | 3 | 4 | 4 | 68 |
| 5 | Obsidian/Notion second-brain template pack | 5 | 3 | 1 | 3 | 3 | 64 |
| 6 | Generic air-fryer meal-prep book | 5 | 3 | 1 | 2 | 2 | 54 |
| 7 | Faceless YouTube automation (script + stock footage) | 3 | 1 | 2 | 3 | 4 | 53 |
| 8 | Open-source privacy analytics snippet → GitHub Sponsors | 5 | 1 | 3 | 3 | 3 | 53 |
| 9 | Medium Partner Program publication (10 essays) | 5 | 2 | 1 | 3 | 2 | 43 |
| 10 | Low-content journal (undifferentiated prompt book) | 5 | 4 | 1 | 1 | 2 | 42 |

## Two honest adjustments that changed the ranking

**KDP is not a cheat code, and AI-slop is being actively purged.** Amazon requires you to declare AI-*generated* content at upload (AI-*assisted* — you wrote it, AI helped refine — is declared differently). Amazon has also bulk-removed low-quality generated titles. This is fine: it raises the moat for anyone willing to ship a genuinely specific book with proprietary frameworks and real worksheets. It kills idea #6 and #10, which is exactly why they scored low on defensibility and quality rather than feasibility.

**Google does not want 10 AI articles on a new domain.** Post-2024 helpful-content enforcement, a fresh domain with 10 generic long-tail posts is a dead asset. So idea #2 is not "blog" — it is *tools that people bookmark and link to*, with the articles existing only to serve the tool. That reframe moved it from 74 to 89.

## Selection

**Primary → #1.** The shift-worker system. Why it wins:
- ~20% of the US workforce does any non-daytime work; nurses alone are 4M+ in the US, plus EMTs, warehouse, security, overnight IT support, 24h manufacturing. Buying while exhausted at 7am is a real behavior — this audience self-medicates with books.
- Almost all existing content is either clinical (unreadable) or generic "drink water, sleep in a dark room" (worthless). A specific *system with numbers* clears the bar.
- One asset, four monetization surfaces: KDP ebook + paperback, Gumroad printable kit, free web tool for links/traffic, email list.
- I can write this credibly without inventing credentials. See `07-risks/01-integrity-and-compliance.md` — that constraint is non-negotiable here because this topic brushes health.

**Secondary → #2.** Started as the free tool inside #1 (the Shift Reset Planner in `03-tool/`), then spun out into its own site. The tool doubles as the link magnet for the book. Assets shared, marginal cost near zero. This is what "compounding" is supposed to look like — not 20 separate ideas.

## What is *not* in scope, and why

| Rejected | Reason |
|---|---|
| "10 sleep tips" ebook | Generic. Zero reason to buy from an unknown author. |
| Supplement/nootropic stack recommendations | Health-harm risk, FTC exposure, unverifiable claims. Excluded by constraint. |
| "Cure shift work sleep disorder" positioning | Disease-treatment claim. Not made anywhere in the manuscript. <!-- gate:allow M1 M2 — this row records the refusal, it is not the claim --> |
| Scraping competitor reviews for keywords | TOS violation on Amazon. Simulated demand analysis only. <!-- gate:allow X1 — a rejected option, recorded so it stays rejected --> |
| Paid ads to validate faster | Costs money. |
| Buying a $97 "book launch" course | Costs money. Everything needed is in this folder. |

## Files in this build

```
00-strategy/01-IDEA-SCORECARD.md        you are here
00-strategy/02-REVENUE-MODEL.md         conservative / base / optimistic, with assumptions
00-strategy/03-90-DAY-ROADMAP.md         week-by-week operator schedule
01-book/                                 COMPLETE manuscript, ~12k words, EPUB-ready markdown
02-kdp/01-COVER-BRIEF.md                 art director spec for Canva free
02-kdp/02-KDP-BACKEND-UPLOAD.md          copy-paste title/keywords/categories/description
03-tool/index.html                       working free web app (single file, no build)
04-seo/01-KEYWORD-MAP.md                 30 clusters mapped
04-seo/articles/*.md                     3 finished anchor articles w/ meta + alt text
05-marketing/01-CONTENT-CALENDAR.md      30 days, every post written
06-tracking/01-tracker.csv               weekly measurement sheet
07-risks/01-integrity-and-compliance.md  what will get this banned, and how to not do that
07-risks/02-iteration-playbook.md        diagnosis tree when numbers don't move
```
