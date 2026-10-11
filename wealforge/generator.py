"""Asset composition: briefs, metadata sheets, sales pages, checklists, calendars.

Everything here is deterministic string building from the idea record + the money
math in `kdp.py`. No generator in this module invents claims: where a human must
decide, the output contains `TODO(human: …)` rather than plausible filler, because
a filled template is indistinguishable from a finished asset until someone reads it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import kdp, models

TODO = "TODO(human)"

# ---------------------------------------------------------------- brief ----
def brief_for(idea: dict[str, Any], rubric_axes: dict[str, int] | None = None) -> str:
    title, niche, plat = idea["title"], idea.get("niche", ""), idea.get("platform", "kdp")
    thesis = idea.get("thesis") or TODO
    audience = idea.get("audience") or TODO
    angles = idea.get("angles") or []
    scores = idea.get("scores") or {}
    wordcount = {"kdp": 11_000, "seo-site": 6_500, "digital-product": 2_500,
                 "code": 0, "audience": 4_000}.get(plat, 6_000)
    comp = idea.get("competition", "medium")
    plan = kdp.price_new_title(wordcount or 8_000, comp)
    lines = [
        f"# Brief — {title}", "",
        f"- **Niche:** {niche}",
        f"- **Platform:** {plat}",
        f"- **Audience:** {audience}",
        f"- **Thesis (why anyone pays attention):** {thesis}",
        f"- **Rubric total:** {idea.get('total', 0)}/100"
        + (f" — axes {json.dumps(scores)}" if scores else ""),
        "",
        "## The mechanism (this is the asset)", "",
        "An asset in 2026 earns attention only if it contains a *named, number-bearing "
        "procedure* the reader can't get from a listicle. Fill all five lines before drafting "
        "a single word of prose; if you can't, the idea is not ready and the loop should "
        "return to scoring.",
        "",
        "| Slot | Your answer |",
        "|---|---|",
        f"| The one-line mechanism | {TODO} e.g. \"Descent → Hold → Reentry: a shift is three problems, not one\" |",
        f"| The number(s) it reduces to | {TODO} e.g. 3-2-1-0, 30 g, 400 mg, 10 min |",
        f"| What it replaces | {TODO} the vague advice people were given before |",
        f"| Why it is defensible | {TODO} what a competitor needs to spend to copy it |",
        f"| The proof it can show | {TODO} worked example, own log, or named public source |",
        "",
        "## Contents / structure", "",
    ]
    lines += _structure_for(plat, title)
    lines += [
        "", "## Production targets", "",
        f"- Word budget: **{wordcount or 'n/a (code asset)'}** words of reader-facing prose.",
        f"- Files to write: see the manifest below. Every one must pass `wf gate` before it is packaged.",
        f"- Pricing (computed by `wealforge/kdp.py`, not guessed): ebook ${plan.ebook_price:.2f} "
        f"→ net **${plan.ebook.net:.2f}/sale**; promo ${plan.promo_price:.2f} for {plan.promo_days} days; "
        f"paperback ${plan.paperback_price:.2f} → net ${plan.paperback['net']:.2f}.",
        f"- First-dollar expectation: {idea.get('time_note', '30–90 days organic; a launch push can beat that once, not twice')}.",
        "",
        "## Non-negotiables for this asset", "",
        "- No invented credentials, no clinical claims, no supplement dosing.",
        "- If health-adjacent: 'not medical advice' line, a 'see a clinician if…' list, and — if "
        "fatigue and driving both appear — the never-drive-drowsy rule with a safe alternative.",
        "- Any affiliate link: disclosure above the fold, on the same page as the link.",
        "- Income figures appear only with assumptions attached, in a range, with the boring timeline.",
        "", "## Manifest", "", "```",
        f"out/{models.slugify(title)}/\n"
        + "\n".join(f"  {f}" for f in _manifest_for(plat)),
        "```", "",
    ]
    return "\n".join(lines)


def _structure_for(plat: str, title: str) -> list[str]:
    if plat == "kdp":
        return ["Front matter: title, copyright, **disclaimer**, contents, \"how to use\", the numbers table.",
                "", "| # | Chapter | Job of the chapter |", "|---|---|---|",
                "| 1 | The mechanism | Explain the two-systems model; hand over the numbers early |",
                "| 2–6 | One rule per chapter | Each: scene → mechanism → numbers → worked example → do-this-tonight |",
                "| 7 | Context chapter | The thing a system can't fix: other people, rosters, households |",
                "| 8 | Protocol | Day-by-day, one change at a time |",
                "| 9 | Worksheets | Printable, self-explanatory, no app required |",
                "| — | Back matter | Honest bio, free tool link, review ask, corrections policy |",
                "", "Fiction variant: 3-chapter hook inside 10% of the book, series promise, no cliffhanger in book 1."]
    if plat == "seo-site":
        return ["12 pages maximum. Each page answers one question a person asks out loud.",
                "", "```", "/  home: tool + pillar cards", "/<tool>/  the free utility (the link magnet)",
                "/pillar-1/ … /pillar-3/  1,500–2,000 words, primary keyword in title + H1",
                "/about/ + /editorial-policy/  E-E-A-T floor for a new domain", "```"]
    if plat == "digital-product":
        return ["One product, one page. Contents: what it is, the 8 bullets, delivery, guarantee, price, FAQ.",
                "The file itself must be more finished than the sales page describes it."]
    if plat == "code":
        return ["README with a screenshot or GIF of it working, LICENSE, and zero required dependencies.",
                "Monetisation is an upsell inside a tool people already use, never a paywall on the code."]
    return ["3–5 pieces/week, one niche, one channel each, same handle everywhere.",
            "A post is a complete thought; a thread is a post cut into teeth."]


def _manifest_for(plat: str) -> list[str]:
    return {
        "kdp": ["chapters/00-front-matter.md", "chapters/01..0N-*.md", "chapters/99-worksheets.md",
                "chapters/NN-back-matter.md", "manuscript.md (assembled)", "style.css", "build.sh",
                "../02-kdp/COVER-BRIEF.md", "../02-kdp/BACKEND.md", "../02-kdp/LAUNCH.md"],
        "seo-site": ["keyword-map.md", "articles/*.md (front-matter with title/meta/links)",
                     "index.html or site/ static build", "sitemap.xml"],
        "digital-product": ["product/ (the files)", "sales-page.md", "listing.md", "delivery-copy.md"],
        "code": ["index.html or src/", "README.md", "LICENSE", "tests/"],
        "audience": ["posts/week-01.md … ", "profile-bio.md", "cross-links.md"],
    }.get(plat, ["README.md"])


# --------------------------------------------------------- kdp backend ----
def kdp_backend(idea: dict[str, Any], word_count: int, pages: int | None = None,
                competition: str = "medium") -> str:
    title = idea["title"]
    subtitle = idea.get("subtitle") or f"{TODO} — put the audience nouns here; the subtitle is search real estate"
    plan = kdp.price_new_title(word_count, competition, has_worksheets=True, pages=pages)
    kw = idea.get("keywords") or [TODO] * 7
    cats = idea.get("categories") or [TODO, TODO]
    lines = [f"# KDP backend — {title}", "",
             "**Verify every rate in the live KDP app before publishing.** Numbers below are computed "
             f"against the schedule recorded in `wealforge/kdp.py` (checked {kdp.VERIFY_AS_OF}).", "",
             "## Title block", "",
             f"**Title**\n```\n{title}\n```\n",
             f"**Subtitle**\n```\n{subtitle}\n```\n",
             f"**Series:** {idea.get('series') or TODO}",
             f"**Author:** {idea.get('author') or TODO}",
             "", "Do not add a credential the author cannot substantiate. Full stop.", "",
             "## Description (paste into the KDP editor)", "", "```html",
             idea.get("description") or f"<p>{TODO}</p>", "```", "",
             "## Categories", ""]
    for i, c in enumerate(cats, 1):
        lines.append(f"{i}. {c}")
    lines += ["", "Request a third via KDP support after publication (free, commonly granted).", "",
              "## 7 backend keywords (≤49 chars, no title words, no brands, no clinician names)", ""]
    for i, k in enumerate(kw[:7], 1):
        lines.append(f"{i}. `{k}`")
    lines += ["", "## Pricing (computed)", "",
              "| Format | Price | Royalty math | Net/sale |", "|---|---|---|---|",
              f"| Ebook | ${plan.ebook_price:.2f} | {plan.ebook.band}% − ${plan.ebook.delivery_fee:.2f} delivery | **${plan.ebook.net:.2f}** |",
              f"| Paperback {plan.pages} pp | ${plan.paperback_price:.2f} | 60% − ${plan.paperback['print_cost']:.2f} print | ${plan.paperback['net']:.2f} |",
              f"| Launch days 1–{plan.promo_days} | ${plan.promo_price:.2f} | 35% band (outside 70%) | ${plan.promo.net:.2f} |",
              f"| KENP | — | ~{kdp.kenp_earnings(int(plan.counted_pages)):.2f} per full read | — |",
              "",
              f"> Delivery fee estimated from {plan.file_kb} KB. Re-check after upload: a file with "
              "images moves bands and changes the arithmetic.", "",
              "## Declarations", "",
              "- AI content: answer factually for the actual workflow. If a human reviewed and takes "
              "responsibility for every claim, that is **AI-assisted**. Publishing unreviewed output "
              "on a topic touching health is not a posture this pipeline endorses.",
              "- Rights: \"I own all copyright\", all territories, primary marketplace US.",
              "- Price: KDP's own minimum applies for the paperback; `min_viable_list_price` above is a "
              "sanity check, not the authority.", "",
              "## Pre-publish checklist", ""]
    lines += [f"- [ ] {c}" for c in _publish_checklist(idea, plan)]
    return "\n".join(lines) + "\n"


def _publish_checklist(idea: dict[str, Any], plan: kdp.Plan) -> list[str]:
    return [
        "Read the whole manuscript once as a stranger, on a phone, at 400-nit brightness. Cut filler.",
        "Verify every number and citation against a primary source *today*; soften anything you cannot defend in 10 minutes.",
        "Confirm no diagnosis/treatment claims, no supplement dosing, no individual advice.",
        "Confirm no fabricated credentials in title, subtitle, bio, cover, author page.",
        "Cover: legible at 100 px. Screenshot a real results page and paste it in at thumbnail size.",
        "Title, subtitle, series, 2 categories, 7 keywords, description pasted and proof-read.",
        "EPUB validated: opens in a reader, TOC present, tables not broken, 800×600 preview checked.",
        "Paperback: digital proof approved ($0) before any physical proof order.",
        f"Price ${plan.ebook_price:.2f}, promo ${plan.promo_price:.2f} scheduled for {plan.promo_days} days, then raised on a stated date.",
        "Author Central profile live with the honest bio; book claimed.",
        "Compliance gate run and passing: `wf gate <paths>` → zero errors. Attach the report to the release note.",
        "Free-tool URL in the back matter is real and returns 200.",
    ]


# -------------------------------------------------------- cover brief ----
def cover_brief(idea: dict[str, Any]) -> str:
    t = idea["title"]
    words = re.sub(r"[^A-Za-z0-9 ]", "", t).split()
    stack = "\n".join(f"  `{w.upper()}`" for w in words[:3])
    return f"""# Cover brief — {t}

## Sizes
| Asset | Spec |
|---|---|
| Ebook | 2,560 × 1,600 px (1.6:1), JPEG/PNG, sRGB, <50 MB |
| Paperback | KDP Covers Calculator for trim {idea.get('trim','6×9')} + page count → exact spine width |
| Promo | 1,600 × 1,600 square crop, centre-weighted |

## Type stack (top → bottom, % of cover height)
- 0–7% clear
- 9%: kicker `{(idea.get('kicker') or 'THE 28-DAY SYSTEM').upper()}` — amber, small caps. This line is the differentiator; never delete it.
- 16–46%: TITLE, {len(words[:3])} stacked lines, flush left at 8% margin, all-caps condensed bold:
{stack}
- 50%: 1.5 pt rule, 30% opacity, left margin → 78% width
- 53–69%: subtitle, 2 lines, 40% of title size — audience nouns, not poetry:
  `{idea.get('subtitle') or TODO}`
- 88–95%: author, letterspaced +6%, 55% of title size
- Bottom right 92%: value badge — `{idea.get('badge') or 'includes printable planner + free web tool'}`

## Colour system
Field `{idea.get('bg') or '#0B1220'}`; accent `{idea.get('accent') or '#F2A33C'}`; type `{idea.get('ink') or '#F4F1EA'}` (not pure white: it glows at thumbnail size).
Rationale must survive a challenge: {idea.get('colour_psych') or 'state why these colours fit the reader and the genre convention'}

## Constraints
- Zero photography unless you can name the licence and the commercial-use terms of every asset,
  including fonts. An abstract type-only cover removes that whole risk class.
- Fonts from an OFL/Apache family (Barlow, Archivo, Oswald, Lato) downloaded from the source — not
  from someone else's template file.
- No trademarked terms anywhere on the cover.
- Back cover: hook line in accent, 26–28 lines of body, 3 "who it's for" bullets, barcode zone
  reserved 2" × 1.2" bottom-right with 0.25" clear.

## Test before committing
Render three candidates at 300 px, paste them into a screenshot of a real results page for the
primary query, and read them at arm's length. The readable one wins — not the prettiest one.
"""


# ------------------------------------------------------- sales page ----
def sales_page(idea: dict[str, Any], product: dict[str, Any]) -> str:
    price = float(product.get("price", 9))
    net = round(price * 0.90 - 0.30, 2)
    return f"""# Sales page — {product.get('name', idea['title'])}

**Headline:** {product.get('headline') or TODO + ' — benefit + specificity, e.g. "The worksheets from the book, on your fridge by tonight"'}
**Subhead:** {product.get('subhead') or TODO}

**Problem.** {product.get('problem') or TODO + ' — the reader already knows the advice; name why it left their head'}

**What's inside** ({len(product.get('includes', []))} items — replace placeholders, keep each bullet concrete):
{chr(10).join(f"- {i}" for i in (product.get('includes') or [TODO + ' every page, itemised']))}

**How it's delivered.** {product.get('delivery') or 'Instant PDF. No app, no login, no subscription.'}

**Guarantee.** {product.get('guarantee') or 'Refund on request, no form. Only offer what you will actually honour.'}

**Price.** ${price:.2f}, one time.
{f"**Owners of the book:** reply with your order number for {product.get('owner_discount_pct', 50)}% off." if product.get('cross_link') else ""}

**Not for:** {product.get('not_for') or TODO + ' — say who should not buy. It reduces refunds and builds trust.'}

*Computed: at ${price:.2f} with a ~10% marketplace fee, net ≈ ${net:.2f}/sale. That number is an estimate — check the live fee schedule before publishing.*
"""


# ----------------------------------------------------------- calendar ----
def calendar(idea: dict[str, Any], days: int = 30, start: str = "") -> str:
    """Slot generator: platform, angle and the *job* of each post.

    It deliberately does not write the copy in offline mode. A 30-day calendar of
    auto-filled platitudes is the exact artefact that makes an operation look busy
    while producing nothing publishable.
    """
    channels = idea.get("channels") or ["linkedin", "reddit(value-only)", "x", "newsletter"]
    beats = ["mechanism", "numbers", "objection", "worked-example", "tool-demo", "safety",
             "reader-question", "correction", "before/after", "myth", "script-fragment",
             "checklist", "ask-for-review", "price-note", "wrap"]
    lines = [f"# {days}-day calendar — {idea['title']}", "",
             f"Channels, in rotation: {', '.join(channels)}.", "",
             "Rules: value first, link last. No undisclosed self-promotion in communities. No "
             "second accounts, no vote rings, no re-posting a removed item. If a moderator removes "
             "it, read the rule and adapt.", "", "| day | channel | beat | the one thing the post does |",
             "|---|---|---|---|"]
    for d in range(1, days + 1):
        ch = channels[(d - 1) % len(channels)]
        beat = beats[(d - 1) % len(beats)]
        rest = " (rest — read the numbers, change nothing)" if d % 7 == 0 else ""
        lines.append(f"| {d} | {ch} | {beat} | {TODO}: make one thing work for a reader{rest} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------ writing output ----
def write(outdir: str | Path, name: str, content: str) -> Path:
    p = Path(outdir) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p
