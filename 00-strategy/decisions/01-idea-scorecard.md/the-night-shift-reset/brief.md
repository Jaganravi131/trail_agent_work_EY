# Brief — The Night Shift Reset

- **Niche:** shift-worker recovery
- **Platform:** kdp
- **Audience:** nurses, EMTs, warehouse and overnight desk crews on 12h or 4-on-4-off rotations
- **Thesis (why anyone pays attention):** a named, number-bearing system for eating/caffeine/light timing that a tired person can run tonight
- **Rubric total:** 88.3/100 — axes {"feasibility": 5, "speed": 3, "defensibility": 4, "quality": 5, "compounding": 5}

## The mechanism (this is the asset)

An asset in 2026 earns attention only if it contains a *named, number-bearing procedure* the reader can't get from a listicle. Fill all five lines before drafting a single word of prose; if you can't, the idea is not ready and the loop should return to scoring.

| Slot | Your answer |
|---|---|
| The one-line mechanism | TODO(human) e.g. "Descent → Hold → Reentry: a shift is three problems, not one" |
| The number(s) it reduces to | TODO(human) e.g. 3-2-1-0, 30 g, 400 mg, 10 min |
| What it replaces | TODO(human) the vague advice people were given before |
| Why it is defensible | TODO(human) what a competitor needs to spend to copy it |
| The proof it can show | TODO(human) worked example, own log, or named public source |

## Contents / structure

Front matter: title, copyright, **disclaimer**, contents, "how to use", the numbers table.

| # | Chapter | Job of the chapter |
|---|---|---|
| 1 | The mechanism | Explain the two-systems model; hand over the numbers early |
| 2–6 | One rule per chapter | Each: scene → mechanism → numbers → worked example → do-this-tonight |
| 7 | Context chapter | The thing a system can't fix: other people, rosters, households |
| 8 | Protocol | Day-by-day, one change at a time |
| 9 | Worksheets | Printable, self-explanatory, no app required |
| — | Back matter | Honest bio, free tool link, review ask, corrections policy |

Fiction variant: 3-chapter hook inside 10% of the book, series promise, no cliffhanger in book 1.

## Production targets

- Word budget: **11000** words of reader-facing prose.
- Files to write: see the manifest below. Every one must pass `wf gate` before it is packaged.
- Pricing (computed by `wealforge/kdp.py`, not guessed): ebook $3.99 → net **$2.73/sale**; promo $0.99 for 5 days; paperback $12.99 → net $6.41.
- First-dollar expectation: 30–90 days organic; a launch push can beat that once, not twice.

## Non-negotiables for this asset

- No invented credentials, no clinical claims, no supplement dosing.
- If health-adjacent: 'not medical advice' line, a 'see a clinician if…' list, and — if fatigue and driving both appear — the never-drive-drowsy rule with a safe alternative.
- Any affiliate link: disclosure above the fold, on the same page as the link.
- Income figures appear only with assumptions attached, in a range, with the boring timeline.

## Manifest

```
out/the-night-shift-reset/
  chapters/00-front-matter.md
  chapters/01..0N-*.md
  chapters/99-worksheets.md
  chapters/NN-back-matter.md
  manuscript.md (assembled)
  style.css
  build.sh
  ../02-kdp/COVER-BRIEF.md
  ../02-kdp/BACKEND.md
  ../02-kdp/LAUNCH.md
```
