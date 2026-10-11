"""Step 1 of the loop: generate or accept candidates, pre-screen, score, rank.

The pre-screen is the part worth coding. A human scanning 10 ideas forgets the
disqualifiers on idea 7 because idea 7 looked exciting; a rules list does not
forget. Everything on the reject list is there because it either costs money,
breaks platform terms, or hurts a reader — and each rejection carries the reason
into the ledger so the decision is auditable rather than a vibe.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import models, rubric as rubric_mod

from .gate import HEALTH_TRIGGER as _HEALTH

_HEALTH_SRC = _HEALTH.pattern + r"|\b(diet|weight loss|nutrition plan)\b"


#: Hard disqualifiers. Matched against title+niche+thesis+angles, case-insensitive.
REJECT: tuple[tuple[str, str, str], ...] = (
    ("public-domain", r"public domain|reprint of|unabridged original|project gutenberg",
     "Republishing public-domain texts is permitted but worthless and policy-flagged as bulk content."),
    ("trademark", r"\b(disney|marvel|pokemon|harry potter|winnie|swatch|lego|barbie|grinch)\b",
     "Trademarked characters in titles, covers or keywords: takedown plus infringement exposure."),
    ("medical-advice", r"((cure|cures|reverse|reverses|treat|treats|heal|heals|eliminate)\b.{0,40}"
                       r"(cancer|diabetes|dementia|hiv|depression|bipolar|epilepsy|insomnia|apnoea|apnea|"
                       r"disorder|syndrome|arthritis|obesity|hypertension|addiction|autism))"
                       r"|prescrib|diagnos(e|sis|tic) (your|yourself|at home)|"
                       r"(dosing|dose) (protocol|guide|schedule|plan) (for|to)|"
                       r"(take|inject|administer) \d+\s?(mg|mcg|iu)",
     "Individualised diagnosis, treatment or dosing is where real harm and legal exposure live. This is "
     "not a niche you can serve without a licence, and no margin justifies finding out the hard way."),
    ("supplement-stack", r"(supplement|nootropic|stack) (protocol|regimen|guide) (that|to)? ?(cures|fixes|guarantees)",
     "Selling ingestible protocols to sleep-deprived people on a platform you don't control: no."),
    ("adult", r"\b(adult only|nsfw|erotic roleplay|porn|onlyfans content)\b", "Excluded by constraint."),
    ("gambling", r"(betting system|guaranteed (odds|win)|arbitrage (sure|guaranteed)|casino (strategy that wins))",
     "Betting content aimed at vulnerable readers, plus most affiliate programmes forbid it."),
    ("finance-advice", r"(guaranteed (return|yield)|insider|avoid (taxes|audits) (completely|100%)|"
                       r"manipulate (credit score|the system)|no doc loan)",
     "Financial advice that could cause harm, plus securities/tax licensing problems. Education about "
     "concepts is fine; 'guaranteed' is not."),
    ("political-manip", r"(swing (voters|state) micro-target|astroturf|suppress (voter|turnout)| opposition research dump)",
     "Political manipulation is excluded outright."),
    ("review-fraud", r"(fake review|seed\w*.{0,14}review|review (farm|blast|swap|exchange|group|club)|"
                     r"manipulat\w+.{0,12}(rating|review)|upvote (our|my|this)|vote (for|on) (our|my) "
                     r"|(five|5)[- ]star (for|in exchange))",
     "Consumer-protection violation, account-fatal on every platform, and it is the specific behaviour "
     "that gets the whole category treated as fraud by storefronts."),
    ("spam-outreach", r"(cold email (blast|at scale|10,?000)|scraped (email|contact) list|bulk dms?|"
                      r"unsubscrib\w* ignore)",
     "CAN-SPAM/GDPR exposure, and it's the behaviour that gets domains blacklisted."),
    ("phishing", r"(clone (the )?login|credential harvest|typosquat)", "Outright fraud."),
    ("copyright", r"(summarise (the )?entire (book|course)|transcri\w+ (course|paywall)|leaked (course|pdf)|"
                  r"course (csv|solutions) (repackaged|for sale))",
     "Copyright theft and, for course materials, contract breach."),
    ("scraping", r"(scrape\w*\s+.{0,25}(amazon|google|reddit|goodreads|review|listing|keyword)"
                 r"|circumvent|ignore robots|bypass (the )?(login|captcha|paywall|rate ?limit)"
                 r"|against the (tos|terms of service))",
     "TOS violation and, for some data classes, worse. Autocomplete reasoning plus a 15-minute manual "
     "check is enough to pick a niche; the scraped version is also the version a platform detects."),
    ("upfront-cost", r"(buy (the )?(course|domain|listing) first|requires (paid ads|inventory|a paid tool)|"
                     r"dropship with (paid )?ads|minimum (order|spend))",
     "Not zero-capital. This project's constraint is a feature: it forces the moat to be judgement."),
    ("guaranteed-income", r"(guaranteed \$|earn \$\d[\d,]* (per|a) (day|week) guaranteed|"
                          r"get rich quick|no effort|money for nothing|passive income (that )?(prints|guaranteed))",
     "Unsubstantiated earnings claims: FTC territory and instantly distrustful to readers."),
    ("thin-ai", r"(100 prompts (about|for) (anything|everything)|generate \d+ (books?|journals?|log books?) per (day|week)|"
                r"low[- ]content (mill|factory)|\d+ ?page blank journal)",
     "Bulk low-content manufacturing is exactly what the platforms are purging. Zero margin, high ban risk."),
)

#: Niches where a required safeguard cannot be met by an unlicensed author → downgrade, not reject.
CAUTION: tuple[tuple[str, str], ...] = (
    # health pattern imported from the gate so screen and gate cannot disagree about
    # what counts as health-adjacent.
    ("health", _HEALTH_SRC,
     "Health-adjacent: must carry a 'not medical advice' line, a 'see a clinician if…' list, and no dosing. "
     "The gate enforces these; expect to lose any claim that needs a licence to make."),
    ("finance", r"\b(tax|investing|credit|loan|retirement|bookkeeping)\b",
     "Financial-adjacent: educational only, no personalised advice, and date-stamp every rule that changes."),
    ("legal", r"\b(contract|legal|lawyer|tenant|immigration)\b",
     "Legal-adjacent: templates are fine, jurisdiction-specific advice is not."),
    ("minors", r"\b(kids|children|teens|student)\b",
     "Audience may include minors: no data collection, no personalised outreach, COPPA applies to the tool."),
)


@dataclass
class Candidate:
    raw: dict[str, Any]
    idea: models.Idea
    matched: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return bool(self.matched)


def _haystack(d: dict[str, Any]) -> str:
    return " ".join(str(d.get(k, "")) for k in ("title", "niche", "thesis", "audience", "platform")) + " " + \
        " ".join(d.get("angles", []) or [])


def prescreen(candidate: dict[str, Any]) -> tuple[list[str], list[str]]:
    hay = _haystack(candidate)
    rej = [f"{tag}: {why}" for tag, pat, why in REJECT if re.search(pat, hay, re.I)]
    cau = [f"{tag}: {why}" for tag, pat, why in CAUTION if re.search(pat, hay, re.I)]
    return rej, cau


def evaluate(candidates: Iterable[dict[str, Any]], rb: rubric_mod.Rubric,
             score_fn: Any | None = None) -> list[Candidate]:
    """Attach rubric scores + pre-screen verdicts.

    Missing axes are not auto-filled with 3s. A silent midpoint default would
    make every unjudged idea look average and quietly pollute the ranking, so the
    operator either supplies scores in the seed file or scores interactively.
    """
    out: list[Candidate] = []
    for c in candidates:
        rej, cau = prescreen(c)
        scores = dict(c.get("scores") or {})
        if score_fn and not scores and not rej:
            scores = score_fn(c, rb)
        need_missing = [k for k in rb.axis_keys if k not in scores]
        idea = models.Idea.new(c["title"], c.get("niche", ""), c.get("platform", "kdp"),
                               thesis=c.get("thesis", ""), audience=c.get("audience", ""),
                               angles=list(c.get("angles") or []), blocked=rej,
                               meta={"cautions": cau, "competition": c.get("competition", "medium"),
                                     "time_note": c.get("time_note", ""),
                                     "seed_needs_scores": need_missing})
        if not rej and not need_missing:
            res = rb.score(scores)
            idea.scores, idea.total, idea.raw_total, idea.flags = (
                res["axes"], res["total"], res["raw_total"], res["flags"])
        elif rej:
            idea.flags = ["pre-screen rejected: " + "; ".join(r.split(":")[0] for r in rej)]
        else:
            idea.flags = [f"unscored — missing {', '.join(need_missing)}"]
        out.append(Candidate(raw=c, idea=idea, matched=rej, cautions=cau))
    return out


def render(cands: list[Candidate], rb: rubric_mod.Rubric, show_rejected: bool = True) -> str:
    lines = ["# Scorecard", "",
             f"Rubric v{rb.version} · axes: {', '.join(a.label for a in rb.axes)} · "
             f"selection: top {rb.top_n} ≥ {rb.min_total}", "",
             "| total | idea | platform | " + " | ".join(a.key for a in rb.axes) + " | verdict |",
             "|---" * (5 + len(rb.axes)) + "|"]
    scored = [c for c in cands if not c.rejected]
    for c in sorted(scored, key=lambda x: -x.idea.total):
        axes = " | ".join(str(c.idea.scores.get(k, "—")) for k in rb.axis_keys) or "—"
        verdict = "PICK" if c.idea.total >= rb.min_total else ("below bar" if not c.idea.flags else c.idea.flags[0])
        lines.append(f"| **{c.idea.total}** | {c.idea.title} | {c.idea.platform} | {axes} | {verdict} |")
    if show_rejected:
        for c in cands:
            if c.rejected:
                lines.append(f"| — | ~~{c.idea.title}~~ | {c.idea.platform} | — | **REJECTED** — {c.matched[0]} |")
    lines += ["", "## Cautions (not rejections — safeguards required)", ""]
    cautions = [f"- **{c.idea.title}** — {n}" for c in cands if not c.rejected for n in c.cautions]
    lines += cautions or ["- none in this pool"]
    lines += ["", "## Why this ordering beats a hunch", "",
              "Selection is `top_n` of the items clearing `min_total`, so a weak field yields *fewer* "
              "picks, never a filler pick. Two mediocre assets cost more than one good one: they split "
              "the same audience, halve the review count each, and teach you nothing about which lever "
              "moved the number.", ""]
    return "\n".join(lines) + "\n"


def load_seed(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("ideas", [])
    if not isinstance(data, list):
        raise ValueError("seed file must be a JSON list, or an object with an 'ideas' list")
    return data


def interactive_score(candidate: dict[str, Any], rb: rubric_mod.Rubric) -> dict[str, int]:
    """Prompt for the five axes. Used by `wf scan --ask`; harmless in CI (returns
    'needs scores' rather than blocking on a closed stdin)."""
    import sys
    if not sys.stdin or not sys.stdin.isatty():
        raise SystemExit("scan --ask needs a terminal. Put scores in the seed JSON instead.")
    scores: dict[str, int] = {}
    print(f"\n{candidate.get('title')}")
    for a in rb.axes:
        while True:
            raw = input(f"  {a.label} (1-5) — {a.guide[:60]}… > ").strip()
            if raw.isdigit() and 1 <= int(raw) <= 5:
                scores[a.key] = int(raw)
                break
            print("    1-5 only")
    return scores
