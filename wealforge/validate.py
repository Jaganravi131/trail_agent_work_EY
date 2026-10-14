"""Field validators for platform metadata — the places where a model will happily
produce something 61 characters long and look completely fine doing it.

Limits here are the commonly documented ones, and every error message tells the
operator to confirm in the live app, because platforms change these without notice.
The point is not to be authoritative; the point is that a 49-char keyword field
should never be discovered by a publish button turning red at 11 pm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

AS_OF = "2026-10-11 — confirm in the live app; KDP has changed these limits before"


@dataclass
class Issue:
    field_name: str
    severity: str            # "error" | "warn"
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field_name, "severity": self.severity, "message": self.message}


@dataclass
class Validation:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warn"]

    def add(self, field_name: str, severity: str, message: str) -> None:
        self.issues.append(Issue(field_name, severity, message))

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": len(self.errors), "warnings": len(self.warnings),
                "issues": [i.as_dict() for i in self.issues], "as_of": AS_OF}

    def render(self) -> str:
        if self.ok and not self.warnings:
            return "VALIDATION PASS — every metadata field inside its documented limit"
        lines = [f"VALIDATION {'PASS (with warnings)' if self.ok else 'FAIL'} — "
                 f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"]
        for i in self.issues:
            lines.append(f"  [{i.severity:5}] {i.field_name}: {i.message}")
        lines.append(f"  limits as of {AS_OF}")
        return "\n".join(lines)


TODO_RE = re.compile(r"TODO\(human[^)]*\)|\bTBD\b|\bXXX\b", re.I)
#: HTML is the *format* KDP wants, so a placeholder detector must not mistake `<p>` for
#: a template hole. Only angle-bracket tokens with a space or underscore inside count.
PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}|\[[^\]]*insert[^\]]*\]|<(?:placeholder|todo|tbd)[^>]*>", re.I)
# Whole words, deliberately: `cure|heal|reverse` without boundaries reads "health" and "secure" as
# treatment claims, and a checker that fires on ordinary English gets ignored when it fires for real.
TREATMENT_RE = re.compile(
    r"\b(cure|cures|cured|curing|heal|heals|healed|healing|reverse|reverses|reversed|reversing)\b"
    r"|\btreats?\s+(insomnia|apnea|apnoea|diabetes|depression|cancer|addiction)\b"
    r"|\b(cures?|treatment for)\s+(insomnia|apnea|apnoea|shift work)\b", re.I)
# The banned thing is a money/outcome promise aimed at the buyer. "This will make the plan stick" is
# not that, so the object has to be money or a gain, not any noun in the sentence.
PROMISE_RE = re.compile(
    r"\b(guarantee|guaranteed|guarantees)\b"
    r"|\b(?:you(?:'|\u2019)?ll|you will|will)\s+(earn|make|save|double|net|clear)\b[^.]{0,24}?"
    r"(?:\$|usd|\b\d{2,}\s*(?:/|per|a)\s*(?:day|month|week)|\b(?:dollars|a month|a day|per month)\b)"
    r"|\b(?:earn|make|save)\s+\$\s*\d"
    r"|\b(?:make|get)\s+your\s+money\s+back\b", re.I)


BRAND_RE = re.compile(r"\b(amazon|kindle|apple|google|meta|tiktok|notion|reddit|costco)\b", re.I)
CLINICIAN_RE = re.compile(r"\b(mayo clinic|cleveland clinic|harvard health|nhs|who|cdc)\b", re.I)


def validate_kdp(fields: dict[str, Any], *, keywords: list[str] | None = None,
                 categories: list[str] | None = None) -> Validation:
    v = Validation()
    title, subtitle = str(fields.get("title", "")), str(fields.get("subtitle", ""))
    # KDP's own field is "description"; the JSON slot model returns is often
    # "description_html". Accept either so a validator miss never reads as a pass.
    desc = str(fields.get("description") or fields.get("description_html") or "")
    author = str(fields.get("author", ""))

    for name, val, lim in (("title", title, 200), ("subtitle", subtitle, 200),
                           ("description", desc, 4000), ("author", author, 150)):
        probe = re.sub(r"<[^>]+>", " ", val) if name == "description" else val
        if len(probe.strip()) < 40 and name == "description":
            v.add(name, "warn", f"{len(probe.strip())} visible chars — under ~200 words a listing reads empty "
                               "and converts badly; that is a craft note, not a platform limit")
        if len(val) > lim:
            v.add(name, "error", f"{len(val)} chars, limit is {lim} — shorten in the manuscript, not the listing")
        if not val.strip():
            v.add(name, "error", "empty — KDP will not accept a blank title/description")
        if TODO_RE.search(probe):
            v.add(name, "error", "contains an unfilled TODO; publish-blocking")
        if PLACEHOLDER_RE.search(probe):
            v.add(name, "warn", f"looks like a template placeholder: {PLACEHOLDER_RE.search(probe).group(0)!r}")

    kw = keywords if keywords is not None else (fields.get("keywords") or [])
    if len(kw) > 7:
        v.add("keywords", "error", f"{len(kw)} keywords supplied; the backend has 7 fields")
    if len(kw) < 7:
        v.add("keywords", "warn", f"{len(kw)} of 7 fields filled — unused fields are wasted search surface")
    for i, k in enumerate(kw[:7], 1):
        k = str(k)
        if TODO_RE.search(k):
            v.add(f"keyword[{i}]", "error", f"unfilled: {k!r}")
            continue
        if len(k) > 49:
            v.add(f"keyword[{i}]", "error", f"{len(k)} chars; the field caps at 49 — words past it are not indexed")
        if len(k.split()) > 6:
            v.add(f"keyword[{i}]", "warn", f"{len(k.split())} words; a phrase beats a sentence here")
        if title:
            # What this is actually checking: a field spent on words the title already carries.
            # "night shift sleep for nurses" repeats the title's "night shift" and is still a good
            # field, because it adds three words the title does not have — Amazon does not index a
            # phrase, it indexes words. A keyword that adds *nothing* new is the wasted one.
            known = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z']{2,}", f"{title} {author}")}
            new_words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z']{2,}", k) if w.lower() not in known}
            if not new_words and known:
                v.add(f"keyword[{i}]", "warn", f"adds no word the title and author do not already "
                                               f"carry ({k!r}) — that field buys you nothing")
        if BRAND_RE.search(k):
            v.add(f"keyword[{i}]", "error", f"contains a trademark ({BRAND_RE.search(k).group(0)}); Amazon rejects these")
        if re.search(r"\d+\s*star|best\s*seller|#1|free\s*shipping", k, re.I):
            v.add(f"keyword[{i}]", "error", "promotional/claim language is disallowed in keyword fields")

    cats = categories if categories is not None else (fields.get("categories") or [])
    if len(cats) > 2:
        v.add("categories", "error", f"{len(cats)} categories; the backend takes 2 (a third is by request)")
    if len(cats) < 2:
        v.add("categories", "warn", f"{len(cats)} of 2 chosen — browse-node fit matters more than keyword stuffing")
    for i, c in enumerate(cats, 1):
        if CLINICIAN_RE.search(str(c)):
            v.add(f"category[{i}]", "error", "do not name institutions or clinicians in categories")

    plain = f"{title} {subtitle} {author} {re.sub(chr(60) + '[^' + chr(62) + ']+' + chr(62), ' ', desc)}"
    if re.search(r"\b(rn|md|phd|dnp|board[- ]certified|nurse practitioner)\b", plain, re.I):
        v.add("credentials", "error",
              "a credential appears in the listing. Only the operator can authorise that, and only if "
              "the licence is real and current — for this portfolio the author is a pen name, so it must go.")
    if PROMISE_RE.search(desc):
        v.add("description", "error", f"reads like an income or outcome promise ({PROMISE_RE.search(desc).group(0)!r}); "
                                      "strip it and describe the method")
    if TREATMENT_RE.search(desc):
        v.add("description", "error", f"treatment claim in the description ({TREATMENT_RE.search(desc).group(0)!r}) "
                                      "— this is the claim that gets books suppressed")
    return v


def merge_validations(vs: list[Validation]) -> Validation:
    out = Validation()
    for v in vs:
        out.issues.extend(v.issues)
    return out
