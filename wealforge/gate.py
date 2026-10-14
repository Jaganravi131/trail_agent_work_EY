"""Compliance gate — the part of the loop that is worth automating most.

Zero-capital content operations do not usually die from bad ideas. They die
from a fabricated credential on a health title, a missing affiliate disclosure,
an income promise, or a post in a subreddit that didn't disclose the author made
the thing. Those failures are mechanical and therefore checkable.

Design notes:
* Rules are declarative (`Rule`) so the operator can extend them in JSON
  (`gate_rules.json`, via `--rules`) instead of editing Python.
* Two rule types: *presence* (text matches → finding) and *required* (a trigger
  matches but the safeguard does not → finding). Most naive linters only do the
  first kind, which means they can't check "you mentioned money but didn't
  disclose".
* `error` blocks a stage transition, `warn` does not. Nothing here pretends to
  be legal advice; it is a checklist that catches the common, cheap, fatal
  mistakes before a platform does.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# ------------------------------------------------------------ vocabulary ----
#: One vocabulary, two consumers: `scan` uses this to attach a caution to an idea,
#: `gate` uses it to decide whether safeguards are required. Keeping it in one
#: place is the fix for "screen says health-adjacent, gate disagrees".
HEALTH_TRIGGER = re.compile(
    r"\b(sleep|insomnia|apnea|apnoea|melatonin|circadian|fatigue|anxiety|depress|medication|"
    r"supplement|dose|dosage|vitamin|magnesium|blood pressure|diabet|gut|reflux|caffeine|"
    r"therapy|disorder|syndrome|treat\b|cure|heal|prevent|"
    r"shift[- ]work|night shift|night[- ]worker|rotating shift|on[- ]call|3[- ]a\.m\.)\b", re.I)

AFFILIATE_TRIGGER = re.compile(
    r"(amazon\.com/[^)\s\"]*\?|amzn\.to/|&ft=association|/dp/[A-Z0-9]{10}|associates\.com|"
    r"bit\.ly/|tinyurl\.com|t\.co/|shareasale|impact\.com[^\s)]*|awin|cj\.com|dpbolvw|"
    r"go\.skimresources|anrdoezrs|linksynergy|jump\.linkshare|go\.redirecting\d)", re.I)
AFFILIATE_DISCLOSURE = re.compile(
    r"(affiliate|commission|associate\b.*(earn|link)|sponsored|ad\b|promotion disclosure)", re.I)

MEDICAL_DISCLOSURE = re.compile(
    r"(not\s+(medical|legal|financial)\s+advice|educational\s+(only|content|purposes)|"
    r"no\s+claim\s+to\s+(diagnose|treat|prevent)|not\s+a\s+substitute\s+for)", re.I)

#: Accepts any sentence that ties driving to drowsiness and gives an out — the copy
#: will be rewritten over the months, the requirement should not be phrasing-dependent.
DRIVING_SAFETY = re.compile(
    r"(drows\w*[^.]{0,70}(driv|wheel|commut)|(driv\w+|wheel|commut)[^.]{0,70}drows\w*|"
    r"pull over[^.]{0,45}(safe|20 ?min|alarm)|drowsy driving|fatigue[^.]{0,30}driving|"
    r"(never|don'?t|do not)[^.]{0,30}drive[^.]{0,40}(tired|exhaust|impaired))", re.I)

SEE_A_CLINICIAN = re.compile(
    r"((see|talk to|consult|ask|speak to)\s+(a\s+|your\s+|the\s+)?"
    r"(clinician|doctor|physician|pharmacist|gp|medical professional|health\s?care|nurse practitioner))|"
    r"(conversation (for|with) (a|your) (clinician|doctor|pharmacist))|"
    r"(that'?s a (clinician|doctor|pharmacist) (conversation|question))|"
    r"\b(clinician|doctor|pharmacist) conversation\b|"
    r"\b(conversation|question) (for|with|not) (a |an |the )?(blog|planner|spreadsheet|book)\b", re.I)

#: Matches a credential being *claimed* (post-nominals after a name, or "is a/an …
#: certified/licensed …"), never the word "clinician" appearing in advice that tells
#: the reader to see one. Detection of the generic nouns produced more false
#: positives than true positives, which is how a gate gets disabled.
CREDENTIAL_RE = re.compile(
    r"(\b(?:board[- ]certified|licensed|registered|diplomat\w*\s+of)\b[^.\n]{0,35}"
    r"(nurse|physician|therapist|dietitian|psychologist|sleep\s+specialist|somnologist|accountant|"
    r"attorney|lawyer|contractor)"
    r"|\b(registered|certified|licensed)\s+nurse\b"
    r"|\b(sleep\s+specialist|somnologist|board[- ]certified)\b"
    r"|as (a|an)\s+(your|our|the)\s+(doctor|clinician|physician|nurse))", re.I)

#: Post-nominals are matched **case-sensitively and separately**, because under `re.I`
#: ", do" in "if you do, then…" reads as a Doctor of Osteopathy and every second sentence
#: in an instructional book becomes a "credential claim". A degree suffix is spelled in
#: capitals in the real world; requiring that costs nothing and removes the whole class.
CREDENTIAL_POSTNOMINAL = re.compile(
    r"\b[A-Z][a-zA-Z.'\- ]{2,28},\s*(?:RN|BSN|MSN|MD|DO|PhD|PsyD|NP|PA-C|RD|CPC|DC|LCSW|LMFT|CFP|DNP)\b")


def credential_match(text: str) -> re.Match | None:
    """Either credential form. Kept as one function so the required-safeguard rule, the
    presence rule and the operator docs all ask the same question."""
    return CREDENTIAL_RE.search(text) or CREDENTIAL_POSTNOMINAL.search(text)
CREDENTIAL_SAFE = re.compile(r"(not a clinician|not\s+(a\s+)?(doctor|clinician|nurse|physician)|"
                             r"no\s+credentials|not\s+written\s+by\s+a\s+clinician|"
                             r"deliberately not\b|is not\s+a\s+(medical|legal)\s+advice)", re.I)

TREATMENT_CLAIM = re.compile(
    r"\b(cures?|heals?|treats?|fixes?|reverses?|eliminat\w+|resolv\w+)\b[^.]{0,45}\b"
    r"(insomnia|sleep apnea|apnoea|depression|anxiety|diabetes|reflux|narcolepsy|"
    r"shift work sleep disorder|crash|fatigue|adhd|hormones?|circadian rhythm)\b", re.I)
TREATMENT_CLAIM2 = re.compile(
    r"\b(prevents?|protects? (you )?from|reduces? (your )?risk of|eliminates? the risk)\b[^.]{0,40}\b"
    r"(cancer|diabetes|dementia|heart disease|crash|accident|stroke)\b", re.I)

SUPPLEMENT_DOSING = re.compile(
    r"\b(take|dose|supplement with)\b[^.\n]{0,40}\b"
    r"(\d+(\.\d+)?\s?(mg|mcg|g|grams?|iu|ml)\b[^.\n]{0,25}"
    r"(melatonin|magnesium|vitamin|ashwagandha|5-?htp|glycine|l-?theanine|taurine|citicoline|"
    r"rhodiola|valerian|kava|melatonin))", re.I)
SUPPLEMENT_NAMED = re.compile(r"\b(melatonin|ashwagandha|5-?htp|valerian|kava|l-?theanine|glycine|"
                              r"citicoline|rhodiola)\b", re.I)

INCOME_PROMISE = re.compile(
    r"(\$\s?\d[\d,]*\s?(per|/|a)?\s?(day|week|month)\b[^.]{0,30}(guarantee|easy|passive|replac\w+ your salary)|"
    r"guaranteed\s+(income|earnings|results|sales)|earn\b[^.]{0,20}\$\s?\d{3,}[^.]{0,20}(daily|monthly|guaranteed)|"
    r"(i|we) promise[d]? (you|to you)|promise[s]? (you|readers) (will|can|to) (make|earn|net)"
    r"|\b(will|you'?ll|you will)\s+(make|earn|net|clear|bring in|double)\s+(\$\s?\d|[a-z]+\s+\$\s?\d)"
    r"|\$\s?\d[\d,]*\s+(in|during|within) (your |the )?(first|\d+)\s+(week|month|30 days)"
    r"|double your income|financial freedom|quit your job (in|by)|replace your salary"
    r"|passive income (machine|system) (that|which)?"
    r"\s*(makes|prints)|get rich|six[- ]figure (month|year)|no effort|money for nothing)", re.I)

FAKE_ENGAGEMENT = re.compile(
    r"(buy(ed|ing)?\b[^.\n]{0,20}reviews?|swap(ping)?\b[^.\n]{0,20}reviews?|review (exchange|swap|farm|blast|group|club)|"
    r"incentivis\w+ reviews?|"
    r"leave a (5|five)[- ]star review (and|for)\s+i|free copy in exchange for (a |an )?honest review|"
    r"upvote (my|this) (post|thread)|vote for me|\bmass ?dm\b|"
    r"second account|alt account|multiple accounts|reply to my own (post|thread)|"
    r"seed (the )?reviews? with friends)", re.I)

SCRAPING = re.compile(
    r"(scrap(e|ed|ing|er)\b[^.\n]{0,40}(amazon|google|reddit|goodreads|reviews?|listings?|keyword)"
    r"|crawl(er|ing)?[^.\n]{0,30}(reviews?|product pages?)|"
    r"violat\w+\s+(the\s+)?tos|ignore robots\.txt|bypass (the )?(captcha|rate limit|login))", re.I)

TRADEMARKS = re.compile(
    r"\b(Keurig|Nespresso|Casper|Sleep ?Number|Tempur-?Pedic|Huggies|Pampers|Zzzquil|ZzzQuil|Advil|"
    r"Tylenol|Melatonin Gummies\b|Gatorade|Red Bull|Monster Energy|Starbucks|McDonald|Amazon Basics|"
    r"iPad|iPhone|Netflix|Spotify|Disney|Pok[eé]mon|Harry Potter|Nike|Lululemon|Peloton)\b")
TRADEMARK_COMPATIBLE = re.compile(r"\b(compatible with|works? with|alternative to|vs\.?|versus)\b", re.I)

AI_DISCLOSED = re.compile(r"(AI-(generated|assisted)|artificial intelligence (assist|generated)|"
                          r"written with (the help of|an) AI|drafted by an AI|AI.?assist)", re.I)

TEMPLATE_SLOP = re.compile(
    r"^(in today'?s (fast[- ]]?paced|digital|busy) world|"
    r"look no further|it'?s no secret that|in this (comprehensive )?ultimate guide|"
    r"at the end of the day|it'?s important to note that|furthermore, moreover|"
    r"unlock the secret|game.?changer|delve into|tapestry of)", re.I | re.M)

# ------------------------------------------------------ false-positive ctrl --
#: Copy that *prohibits* a behaviour mentions the same words as copy that does it.
#: A gate that flags "never upvote yourself" as review manipulation gets switched off
#: within a week, which is worse than no gate. So a match within this distance of a
#: prohibition marker is reported only if nothing in the sentence permits it.
NEGATION = re.compile(
    r"(^\s*[-*>]?\s*no\s|^\s*[-*>+]?\s*(?:nothing|nobody|no one|none of (?:it|this))\b|\bnever\b|\bdo not\b|\bdon'?t\b|\bavoid\b|\bno\s+(fake|paid|bought|incentivis|mass)|"
    r"\bnot\s+allowed\b|\bforbidden\b|\bprohibit\w*|\binstead of\b|\bwon'?t\b|\bmust not\b|"
    r"\bisn'?t\b|\bcannot\b|\brule against\b|\bagainst the rules\b|\bbanned\b|\bremoved\b|"
    r"\bfail(?:s|ure)? (?:to|when)|\bviolation\b|\btakedown\b|\bgets? you (?:banned|removed)|"
    r"\block-?out\b|\bsuspended?\b|\bnot\b"
    # Writing *about* a risk is the other big false-positive class, and it has tell words.
    r"|\bfatal\b|\bkills?\b|\bexcluded\b|\bdiscourag\w+|\bdeterred\b|\bpolicy\b|\btakedown\b"
    r"|\bwhat (gets|leads|causes)\b|\bconsequen\w+\b|\bwe (do not|don'?t|never)\b|\brule against\b)",
    re.I)
NEGATION_WINDOW = 160   # risk-narration sentences are long by nature

#: A denial is only read as a denial when it *opens* the sentence or bullet — "No scraping of
#: Amazon" and "Nothing was scraped from a competitor's listing" both clear; "Nothing stops you
#: scraping Amazon" would too, which is a deliberate trade: the anchored form is how compliance
#: prose actually reads, and the gate's job is prose it can trust, not prose it can't fool.
#: The same asymmetry the whole suppression layer uses: negation before the match, never after.

#: `<!-- gate:allow R1 -->` / `# gate:allow R1` suppresses one rule on one line.
ALLOW = re.compile(r"gate:allow\s+([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)*)")


def _allowed_on_line(text: str, line_no: int) -> set[str]:
    lines = text.splitlines()
    if 0 < line_no <= len(lines):
        m = ALLOW.search(lines[line_no - 1])
        if m:
            return set(m.group(1).split())
    m = ALLOW.search("\n".join(lines[:3]))          # file-level allow in the first 3 lines
    return set(m.group(1).split()) if m else set()


#: Leading list markers and emphasis, so "**Not** `X, RN`" is still read as a prohibition.
_SENTENCE_LEAD = r"^\s*(?:[-*>+]\s*)?(?:\*\*|__|`+|~~)*\s*"
_PROHIBITION_START = re.compile(_SENTENCE_LEAD + r"(no|never|not|don'?t|do not|cannot|can't|avoid|"
                                r"without|refus\w*|prohibit\w*|banned?|forbidden|reject\w*)\b", re.I)


#: `~~struck through~~` in this repo's decision docs means "we considered this and said no".
#: Grading a rejected item as a live violation is how a checker gets muted, so rejected spans
#: are skipped and counted — the count is the audit trail that something was skipped and why.
STRIKE_RE = re.compile(r"~~[^~]+~~", re.S)


def _struck_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in STRIKE_RE.finditer(text)]


def _in_struck(spans: list[tuple[int, int]], pos: int) -> bool:
    return any(a <= pos < b for a, b in spans)


#: Markdown bullets and table rows are their own statements in these documents, so a
#: prohibition written at the start of a bullet ("No scraping of Amazon reviews") must govern
#: the bullet it opens — not the whole preceding paragraph.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n\s*(?:[-*>+]\s+|\d+[.)]\s+)")

#: A line that is a list item, in any of the spellings these documents use.
_BULLET = re.compile(r"^(?:[-*>+]\s+|\d+[.)]\s+|\|)")


def _before_on_line(text: str, start: int) -> str:
    """Same line, up to the match. A quoted example of a bad credential is cited, not claimed,
    and the citation marker is always on the same line as the quote."""
    ls = text.rfind("\n", 0, start) + 1
    return text[ls:start]


def _lead_in_prohibits(text: str, start: int) -> bool:
    """Does this bullet sit under a lead-in that forbids what it says?

    A refusal list is how compliance copy is actually written: a line ending in a colon, then one
    bullet per forbidden thing, each quoting the wording it forbids ("Not allowed anywhere: / - Any
    claim to treat, cure or prevent a disease"). The bullets inherit that lead-in's polarity. Without
    this the gate flags the paragraph that states its own rules — and a checker that cries wolf on the
    compliance doc gets switched off, which is worse than no checker.

    The trade is the same one every guard here makes: a real violation smuggled into a refusal list is
    let through. That is why the suppression is counted in the report's audit trail, and why the lead-in
    has to be both a *denial* and a *list header* — not just any line that happens to contain "no".
    """
    lines = text.splitlines()
    idx = text.count("\n", 0, start)
    if idx >= len(lines) or not _BULLET.match(lines[idx].strip()):
        return False                       # only list items inherit a lead-in
    j = idx - 1
    while j >= 0:
        prev = lines[j].strip()
        if not prev:
            j -= 1
            continue
        if _BULLET.match(prev):
            j -= 1                         # still inside the same run of bullets
            continue
        return bool(prev.endswith(":") and (NEGATION.search(prev) or _PROHIBITION_START.search(prev)))
    return False


def _prohibited_context(text: str, start: int, end: int) -> bool:
    lo = max(0, start - NEGATION_WINDOW)
    window = text[lo:end]
    # Look at the current sentence, not the whole document.
    sentence = _SENTENCE_BOUNDARY.split(window)[-1]
    if _PROHIBITION_START.match(sentence):
        return True                      # imperative prohibition: "No fake reviews." / "**Not** X"
    if NEGATION.search(sentence):
        return True
    if _lead_in_prohibits(text, start):
        return True
    # Same-line prefix, checked last so it can only ever *widen* the guard by one line.
    # Deliberately looks only BEFORE the match: "cures insomnia … never drive drowsy" is a
    # violation plus a safety note in one line, and the safety note must not excuse it.
    return bool(NEGATION.search(_before_on_line(text, start)))


# --------------------------------------------------------------- ruleset ----
@dataclass(frozen=True)
class Rule:
    id: str
    severity: str                       # "error" | "warn"
    title: str
    pattern: str                        # regex source; empty string = presence-only rule
    why: str
    fix: str
    only_if: str = ""                   # skip unless this regex matches (context gate)

    def compile(self) -> tuple[re.Pattern | None, re.Pattern | None]:
        return (re.compile(self.pattern, re.I) if self.pattern else None,
                re.compile(self.only_if, re.I) if self.only_if else None)


BASE_RULES: tuple[Rule, ...] = (
    Rule("H1", "error", "Claim of clinical credential",
         r"__CREDENTIAL__", "A credential you do not hold, on a health-adjacent asset, is consumer fraud "
                            "and an instant takedown trigger on most platforms. This fires wherever the "
                            "claim appears — in a bio, a chapter, a sales page or an ad — not only in a "
                            "bio block, because that is where models put it when asked for authority.",
         "State lived experience instead, or add the real licence number you can substantiate.",
         r""),
    Rule("M1", "error", "Disease treatment or cure claim",
         r"__TREATMENT__", "Unsubstantiated treatment claims are the highest-severity content risk in "
                           "this niche and draw FTC as well as platform attention.",
         "Rewrite as an observation about comfort or energy, or cut the sentence.", r""),
    Rule("M2", "error", "Disease prevention claim",
         r"__TREATMENT2__", "Same as M1: a prevention claim is a health claim.",
         "Soften to what was measured, or drop it.", r""),
    Rule("M3", "error", "Supplement dosing instruction",
         r"__SUPP_DOSE__", "Telling a sleep-deprived, possibly medicated reader to take a dose of "
                           "something is medical advice and can interact with their medication.",
         "Remove the dose. Refer timing questions to a pharmacist.", r""),
    Rule("M4", "warn", "Supplement named without a clinician redirect",
         r"__SUPP_NAME__", "Not fatal, but a reader will act on a named substance.",
         "Add a 'ask your pharmacist about interactions' line.", r""),
    Rule("S1", "error", "Health content with no 'not medical advice' line",
         r"__NEED_MED_DISCLOSURE__", "Every asset touching sleep, food or medication carries this.",
         "Add the one-sentence disclaimer plus a 'see a clinician if…' list.", r""),
    Rule("S2", "error", "Fatigue content without the never-drive-drowsy rule",
         r"__NEED_DRIVING__", "The reader of a night-shift asset is often about to drive home. This is "
                              "the one place the copy is load-bearing for safety, not just for trust.",
         "Add the explicit rule plus the safe alternative (stop, caffeine, 20 minutes).", r""),
    Rule("S3", "warn", "Health content with no 'see a clinician if' redirect",
         r"__NEED_CLINICIAN__", "Distinguishes a helpful system from a substitute for care.",
         "Add the bullet list of when to stop optimising and get help.", r""),
    Rule("A1", "error", "Affiliate link without disclosure",
         r"__NEED_AFF_DISCLOSURE__", "FTC requires clear and conspicuous disclosure near the claim it "
                                     "qualifies; Amazon Associates can withhold accrued commissions.",
         "Plain-words disclosure at the top of the page, above the link, plus rel=\"sponsored\" on the anchor.", r""),
    Rule("R1", "error", "Buying, swapping or incentivising engagement",
         r"__FAKE__", "Review manipulation and vote ring rings are account-fatal and, in the review "
                      "case, a consumer-protection problem.",
         "Ask individuals for honest reviews, with no condition attached.", r""),
    Rule("X1", "error", "Scraping or TOS-violating data collection",
         r"__SCRAPE__", "Platform TOS and, for some data, computer-misuse law. Also unnecessary: "
                        "autocomplete reasoning plus a manual check is enough to pick a niche.",
         "Use public autocomplete observation and your own judgement; verify by hand.", r""),
    Rule("T1", "error", "Trademarked term used as a keyword or brand claim",
         r"__TRADEMARK__", "Keyword stuffing with brands is a takedown and a trademark problem.",
         "Use the generic noun ('a 3D-cup eye mask'), or comparative language in body text only if "
         "truthful and non-confusing.", r""),
    Rule("I1", "error", "Income guarantee or get-rich framing",
         r"__INCOME__", "Earnings claims require substantiation and are the classic FTC action. Also "
                        "the fastest way to lose reader trust in this niche.",
         "State assumptions and ranges, with the boring timeline, or cut the number.", r""),
    Rule("Q1", "warn", "Opening-line cliché (AI-tell / listicle smell)",
         r"__SLOP__", "Editors and readers pattern-match on these. They also correlate with the copy "
                      "Amazon removed at bulk.",
         "Open on a scene, a number, or a contradiction.", r""),
    Rule("D1", "warn", "AI-assisted content with no disclosure note in the package",
         r"__NEED_AI_DISCLOSURE__", "KDP asks explicitly at upload. Answering falsely there is "
                                    "account-permanent.",
         "Record whether a human reviewed and takes responsibility for every claim.", r""),
)

#: Rules whose pattern may be legitimately *quoted* by an asset that refuses the claim.
_SAFE_GUARDS: dict[str, "re.Pattern[str]"] = {"__CREDENTIAL__": CREDENTIAL_SAFE}

_PLACEHOLDERS = {
    "__TREATMENT__": TREATMENT_CLAIM.pattern,
    "__TREATMENT2__": TREATMENT_CLAIM2.pattern,
    "__SUPP_DOSE__": SUPPLEMENT_DOSING.pattern,
    "__SUPP_NAME__": SUPPLEMENT_NAMED.pattern,
    "__FAKE__": FAKE_ENGAGEMENT.pattern,
    "__SCRAPE__": SCRAPING.pattern,
    "__TRADEMARK__": TRADEMARKS.pattern,
    "__INCOME__": INCOME_PROMISE.pattern,
    "__CREDENTIAL__": CREDENTIAL_RE.pattern,
    "__SLOP__": TEMPLATE_SLOP.pattern,
}


@dataclass
class Finding:
    rule_id: str
    severity: str
    title: str
    why: str
    fix: str
    line: int = 0
    excerpt: str = ""
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule_id, "severity": self.severity, "title": self.title,
                "line": self.line, "excerpt": self.excerpt, "fix": self.fix, "why": self.why,
                **({"source": self.source} if self.source else {})}

    def render(self) -> str:
        loc = f" line {self.line}" if self.line else ""
        if self.source:
            loc += f" in {self.source}"
        ex = f"\n      «{self.excerpt}»" if self.excerpt else ""
        return f"  [{self.severity.upper():5}] {self.rule_id}{loc} {self.title}{ex}\n      fix: {self.fix}"


@dataclass
class GateReport:
    target: str
    findings: list[Finding] = field(default_factory=list)
    passed: bool = True
    stats: dict[str, int] = field(default_factory=dict)
    words: int = 0
    extra_rules: int = 0
    audience: str = "reader"
    summary_hint: str = ""

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warn"]

    def as_dict(self) -> dict[str, Any]:
        return {"target": self.target, "passed": self.passed, "words": self.words,
                "audience": self.audience,
                "errors": len(self.errors), "warnings": len(self.warnings),
                "stats": self.stats, "findings": [f.as_dict() for f in self.findings]}

    def render(self) -> str:
        tag = "" if self.audience == "reader" else f"  [{self.audience}: safeguards n/a]"
        head = f"GATE  {self.target}  ({self.words} words){tag}"
        if not self.findings:
            return head + "\n  PASS — clean against " + str(len(BASE_RULES) + self.extra_rules) + " rules\n"
        body = "\n".join(f.render() for f in self.findings)
        verdict = "FAIL" if not self.passed else "PASS (with warnings)"
        return f"{head}\n  {verdict}: {len(self.errors)} error(s), {len(self.warnings)} warning(s)\n{body}\n"


# -------------------------------------------------------------- evaluate ----
def _context_of(text: str) -> dict[str, bool]:
    return {
        "health": bool(HEALTH_TRIGGER.search(text)),
        "affiliate": bool(AFFILIATE_TRIGGER.search(text)),
        "credential_context": bool(re.search(r"\b(author|about (the|this)|bio|by |written by|profile)\b", text, re.I)),
        "mentions_driving": bool(re.search(r"\b(drive|driving|commute|car|road)\b", text, re.I)),
        "mentions_fatigue": bool(re.search(r"\b(drows|tired|exhaust|sleep[- ]depriv|shift work)\b", text, re.I)),
    }


def _nearest_line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _excerpt(text: str, start: int, end: int, width: int = 150) -> str:
    lo, hi = max(0, start - 30), min(len(text), end + 60)
    s = re.sub(r"\s+", " ", text[lo:hi]).strip()
    return (s[:width] + "…") if len(s) > width else s


def load_rules(extra_path: str | Path | None = None) -> tuple[list[Rule], dict[str, Any]]:
    """Base rules, optionally overridden/extended by a JSON file.

    A rule in the extra file with an existing id replaces that base rule
    (including `severity`), so an operator can downgrade a false-positive-prone
    rule to `warn` without forking the package.
    """
    rules = {r.id: r for r in BASE_RULES}
    config: dict[str, Any] = {}
    if extra_path:
        data = json.loads(Path(extra_path).read_text(encoding="utf-8"))
        config = data.get("skip", {})
        for item in data.get("rules", []):
            rule = Rule(**item)
            rules[rule.id] = rule
        for rid in data.get("disable", []):
            rules.pop(rid, None)
    return list(rules.values()), config


#: Path fragments that are operator-facing, not reader-facing. Safeguard-presence rules
#: (disclaimers, safety lines) are skipped here; integrity rules (income promises,
#: fake engagement, scraping, trademarks, credentials) still apply in full.
INTERNAL_HINTS = ("00-strategy", "02-kdp", "06-tracking", "07-risks", "wealforge/",
                  "tests/", "README", "CLAUDE", "AGENTS", "05-marketing", "keyword-map", "checklist",
                  "brief", "plan", "notes", "decisions",
                  # Planning and bookkeeping artefacts. A content calendar is not something a reader
                  # consumes, so demanding a medical disclaimer in it is a wolf-cry — and every wolf-cry
                  # costs trust in the whole gate.
                  "calendar", "work-log", "metadata", "dataset-card", "training/readme")


def audience_of(target: str) -> str:
    t = target.replace("\\", "/")
    return "internal" if any(h.lower() in t.lower() for h in INTERNAL_HINTS) else "reader"


def evaluate_text(text: str, target: str = "<text>", rules: list[Rule] | None = None,
                  skip: dict[str, Any] | None = None, words: int | None = None,
                  audience: str | None = None) -> GateReport:
    rules = rules or list(BASE_RULES)
    skip = skip or {}
    audience = audience or audience_of(target)
    rep = GateReport(target=target, audience=audience,
                     extra_rules=max(0, len(rules) - len(BASE_RULES)))
    rep.words = words if words is not None else len(re.findall(r"[A-Za-z0-9'\u2019\-]+", text))
    ctx = _context_of(text)
    strikes = _struck_spans(text)

    # 1. presence rules -----------------------------------------------------
    by_id = {r.id: r for r in rules}
    for rule in rules:
        if rule.id in skip.get("rules", []):
            continue
        pat, only_if = rule.compile()
        pattern_src = _PLACEHOLDERS.get(rule.pattern, rule.pattern)
        if not pattern_src or pattern_src.startswith("__"):
            continue                       # safeguard rules execute in step 2
        if rule.pattern == "__CREDENTIAL__":
            # one rule, two matchers (prose claim + capitalised post-nominal)
            cred_hits = []
            for rx in (CREDENTIAL_RE, CREDENTIAL_POSTNOMINAL):
                cred_hits += list(rx.finditer(text))
            for m in cred_hits:
                if _in_struck(strikes, m.start()):
                    rep.stats["strikethrough_skipped"] = rep.stats.get("strikethrough_skipped", 0) + 1
                    continue
                ln = _nearest_line(text, m.start())
                if rule.id in _allowed_on_line(text, ln) or rule.id in _allowed_on_line(text, 0):
                    rep.stats[f"allowed:{rule.id}"] = rep.stats.get(f"allowed:{rule.id}", 0) + 1
                    rep.stats.setdefault("allowed_by_marker", []).append(f"{rule.id} {rep.target}#{ln}")
                    continue
                if _prohibited_context(text, m.start(), m.end()):
                    rep.stats[f"negated:{rule.id}"] = rep.stats.get(f"negated:{rule.id}", 0) + 1
                    continue
                if CREDENTIAL_SAFE.search(text):
                    rep.stats[f"exempt:{rule.id}"] = rep.stats.get(f"exempt:{rule.id}", 0) + 1
                    continue
                if any(f.rule_id == rule.id and f.line == ln for f in rep.findings):
                    continue      # the two matchers can overlap on one span; one finding per line
                rep.findings.append(Finding(rule.id, rule.severity, rule.title, rule.why, rule.fix,
                                            ln, _excerpt(text, *m.span())))
            continue
        compiled = re.compile(pattern_src, re.I | re.M)
        if only_if and not only_if.search(text):
            continue
        allow = _allowed_on_line(text, 0)
        hits = 0
        for m in compiled.finditer(text):
            if _in_struck(strikes, m.start()):
                rep.stats["strikethrough_skipped"] = rep.stats.get("strikethrough_skipped", 0) + 1
                continue
            ln = _nearest_line(text, m.start())
            if rule.id in _allowed_on_line(text, ln) or rule.id in allow:
                rep.stats[f"allowed:{rule.id}"] = rep.stats.get(f"allowed:{rule.id}", 0) + 1
                rep.stats.setdefault("allowed_by_marker", []).append(f"{rule.id} {rep.target}#{ln}")
                continue
            if _prohibited_context(text, m.start(), m.end()):
                # Prose telling the reader/operator not to do the thing is not the thing.
                rep.stats[f"negated:{rule.id}"] = rep.stats.get(f"negated:{rule.id}", 0) + 1
                continue
            safe = _SAFE_GUARDS.get(rule.pattern)
            if safe and safe.search(text):
                # The asset explicitly disclaims the credential — "not a clinician" is a
                # different document from one claiming a licence it does not have.
                rep.stats[f"exempt:{rule.id}"] = rep.stats.get(f"exempt:{rule.id}", 0) + 1
                continue
            hits += 1
            rep.findings.append(Finding(rule.id, rule.severity, rule.title, rule.why, rule.fix,
                                        ln, _excerpt(text, *m.span())))

    # 2. required-safeguard rules (trigger present, safeguard absent) ------
    def require(rid: str, sev: str, title: str, why: str, fix: str,
                trigger: bool, safeguard: bool, sample: str = "") -> None:
        if rid in skip.get("rules", []) or audience != "reader":
            return          # safeguards are about protecting the *reader*; internal docs
                            # don't need a disclaimer addressed at nobody
        if rid in by_id:                   # operator may override severity/title/fix in JSON
            r = by_id[rid]
            sev, title, why, fix = r.severity, r.title, r.why, r.fix
        if trigger and not safeguard:
            rep.findings.append(Finding(rid, sev, title, why, fix, 1, sample))

    require("S1", "error", "Health content with no 'not medical advice' line",
            "Every asset touching sleep, food or medication carries this.",
            "Add the one-sentence disclaimer and a 'see a clinician if…' list.",
            ctx["health"] and rep.words > 250, bool(MEDICAL_DISCLOSURE.search(text)))
    require("S2", "error", "Fatigue content without the never-drive-drowsy rule",
            "This reader is often about to drive home.",
            "Add the explicit rule plus the safe alternative.",
            ctx["mentions_driving"] and ctx["mentions_fatigue"] and rep.words > 200,
            bool(DRIVING_SAFETY.search(text)))
    require("S3", "warn", "Health content with no 'see a clinician if' redirect",
            "Keeps a system from becoming a substitute for care.",
            "Add the when-to-stop list.",
            ctx["health"] and rep.words > 600, bool(SEE_A_CLINICIAN.search(text)))
    require("A1", "error", "Affiliate link without disclosure",
            "FTC: disclosure must be clear, conspicuous and near the link.",
            "Plain-words disclosure at the top of the page.",
            ctx["affiliate"], bool(AFFILIATE_DISCLOSURE.search(text)))
    require("H1b", "error", "Credential language in an author bio with no disclaiming line",
            "A fabricated or implied clinical credential on a health asset. (H1 catches the claim "
            "itself; H1b is the bio-context version for assets whose wording merely *implies* one.)",
            "Either state the real, substantiable credential, or say plainly the author is not a clinician.",
            ctx["credential_context"] and bool(credential_match(text)) and rep.words < 900
            and not _prohibited_context(text, credential_match(text).start(), credential_match(text).end()),
            bool(CREDENTIAL_SAFE.search(text)))

    # 3. quality heuristics -------------------------------------------------
    if rep.words > 200:
        dup = _duplicated_sentences(text)
        if dup and dup[0] >= 3:
            rep.findings.append(Finding("Q2", "warn", "Repeated sentence boilerplate",
                                        "Self-duplication at this rate reads as generated filler and is "
                                        "what 'thin content' looks like in a diff.",
                                        f"Vary or cut. Most-repeated line x{dup[0]}: «{dup[1][:90]}»",
                                        0, ""))
    if ctx["health"] and re.search(r"\b(cancer|dementia|heart disease|stroke|diabetes)\b", text, re.I) \
            and not (TREATMENT_CLAIM.search(text) or TREATMENT_CLAIM2.search(text)):
        rep.stats["disease_words_without_claim"] = 1

    rep.passed = not any(f.severity == "error" for f in rep.findings)
    return rep


#: Generated aggregates. A book folder contains both its chapter files and the assembled
#: `manuscript.md` that `wf book` writes from them. Gating the copy twice inflates the bundle's word
#: count ("22,543 words" for an 11,262-word book), duplicates every finding in the operator's report,
#: and — the part that matters — a `gate:allow` marker on a line in a chapter does not exist at that
#: line number in the aggregate, so a suppression that is correct becomes a finding. The aggregate is
#: *provably* the sources: `wf book --check` fails when it stops matching, which is the stronger check.
GENERATED_AGGREGATES = {"manuscript.md"}


def _files_under(p: Path) -> list[Path]:
    if p.is_file():
        return [p] if p.suffix.lower() in {".md", ".html", ".txt"} else []
    return [c for c in sorted(p.rglob("*"))
            if c.is_file() and c.suffix.lower() in {".md", ".html", ".txt"}
            and c.name not in GENERATED_AGGREGATES]


def evaluate_paths(paths: Iterable[str | Path], rules: list[Rule] | None = None,
                   skip: dict[str, Any] | None = None, bundle: bool = False) -> list[GateReport]:
    """Gate files. With bundle=True, each top-level path is treated as ONE
    reader-facing work: integrity/claim rules still run per file (a bad sentence is
    bad wherever it sits), but the *required-safeguard* rules run against the
    concatenation, because a book's disclaimer belongs in its front matter."""
    out = []
    for p in map(Path, paths):
        if not p.exists():
            r = GateReport(target=str(p), passed=False)
            r.findings.append(Finding("E0", "error", "Path not found", "Cannot gate what isn't there.",
                                       "Check the path.", 0, str(p)))
            out.append(r)
            continue
        files = _files_under(p)
        if not files:
            continue
        if bundle and len(files) > 1:
            whole = "\n\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in files)
            audit = evaluate_text(whole, f"{p} [bundle of {len(files)} files]", rules, skip)
            per = [evaluate_text(f.read_text(encoding="utf-8", errors="ignore"), str(f), rules, skip)
                   for f in files]
            safeguards = {"S1", "S2", "S3", "A1", "H1b", "D1"}
            merged = GateReport(target=audit.target, audience=audit.audience,
                                words=audit.words, extra_rules=audit.extra_rules)
            seen = set()
            for r in per:                                  # claims & integrity, per file
                for fd in r.findings:
                    if fd.rule_id not in safeguards:
                        fd.source = Path(r.target).name
                        merged.findings.append(fd)
                        seen.add((fd.rule_id, fd.line, fd.excerpt))
            merged.findings += [fd for fd in audit.findings if fd.rule_id in safeguards]
            merged.summary_hint = ("safeguards (S*/A1/H1b) are checked against the bundle; claims are "
                                    "checked per file")
            merged.findings.sort(key=lambda x: (x.severity != "error", x.rule_id))
            merged.passed = not any(f.severity == "error" for f in merged.findings)
            merged.stats = {**audit.stats, "bundled_files": len(files),
                            "safeguards_from_bundle": 1}
            out.append(merged)
            continue
        for f in files:
            out.append(evaluate_text(f.read_text(encoding="utf-8", errors="ignore"),
                                     str(f), rules, skip))
    return out


def _duplicated_sentences(text: str, min_len: int = 45) -> tuple[int, str] | None:
    from collections import Counter
    clean = re.sub(r"<[^>]+>", " ", text)
    sents = [re.sub(r"\s+", " ", s).strip().lower()
             for s in re.split(r"(?<=[.!?:])\s+", clean) if len(s.strip()) > min_len]
    if len(sents) < 6:
        return None
    c = Counter(sents)
    top, n = c.most_common(1)[0]
    return (n, top) if n >= 3 else None


def summary_markdown(reports: list[GateReport]) -> str:
    lines = ["# Compliance gate report", ""]
    errs = sum(len(r.errors) for r in reports)
    warns = sum(len(r.warnings) for r in reports)
    lines += [f"**Files scanned:** {len(reports)} · **errors:** {errs} · **warnings:** {warns}", ""]
    lines += ["| file | audience | words | errors | warnings | verdict |",
              "|---|---|---|---|---|---|"]
    for r in reports:
        v = "PASS" if r.passed else "FAIL"
        lines.append(f"| `{r.target}` | {r.audience} | {r.words} | {len(r.errors)} | "
                     f"{len(r.warnings)} | {v} |")
    if errs or warns:
        lines += ["", "## Findings", ""]
        for r in reports:
            for f in r.findings:
                lines.append(f"- `{r.target}`{'#'+str(f.line) if f.line else ''} "
                             f"**[{f.severity}] {f.rule_id} {f.title}** — {f.excerpt or f.why}")

    # Anything the gate chose NOT to report is listed too. A silent pass is how a checker gets
    # trusted and then muted: the reader should see that 4 matches were prose-about-the-risk,
    # 2 were struck through, and 1 was allowed by a marker — and where.
    allowed: dict[str, list[str]] = {}
    tallies = {"negated": 0, "exempt": 0, "strikethrough_skipped": 0}
    for r in reports:
        for k, v in r.stats.items():
            if k == "strikethrough_skipped":
                tallies["strikethrough_skipped"] += v
            elif k.startswith("allowed_by_marker"):
                for loc in v:
                    rid, _, where = loc.partition(" ")
                    allowed.setdefault(rid, []).append(where)
            elif k.startswith("allowed:"):
                pass
            elif k.startswith("negated:"):
                tallies["negated"] += v
            elif k.startswith("exempt:"):
                tallies["exempt"] += v
    if allowed or any(tallies.values()):
        lines += ["", "## Let through on purpose (audit trail)", ""]
        if allowed:
            for rid, locs in sorted(allowed.items()):
                where = ", ".join(f"`{loc}`" for loc in locs[:6])
                extra = "" if len(locs) <= 6 else f" (+{len(locs) - 6} more)"
                lines.append(f"- {len(locs)} match(es) for **{rid}** dropped by an explicit "
                             f"`<!-- gate:allow {rid} -->` marker: {where}{extra}")
        if tallies["negated"]:
            lines.append(f"- {tallies['negated']} match(es) were prose *about* the risk "
                         f"(negation or strike-through guarded the sentence), not the risk itself")
        if tallies["strikethrough_skipped"]:
            lines.append(f"- {tallies['strikethrough_skipped']} span(s) inside `~~struck "
                         f"through~~`, i.e. considered and rejected")
        if tallies["exempt"]:
            lines.append(f"- {tallies['exempt']} finding(s) cleared by a live safeguard line "
                         f"(e.g. a disclaimer present in the bundle)")
    return "\n".join(lines) + "\n"
