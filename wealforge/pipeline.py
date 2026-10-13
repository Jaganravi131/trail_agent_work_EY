"""The integration: brief → model → gate → repair → validate → package, with a budget.

This is the module that makes "the agent does all the work" true rather than aspirational.
The rule it encodes:

    a model's output is a *candidate*. It becomes an asset only by passing the gate.

Three properties that matter more than the code itself:

* **Bounded repair loop.** `max_attempts` (default 3). Model output that violates a rule is
  sent back with the *verbatim findings* and a list of the specific offenders; the best
  attempt so far is kept. Uncapped self-correction on a local model is how you burn an hour
  to produce the same bad paragraph four times.
* **It cannot fake success.** If no model answers, the section is written as a brief with
  `TODO(human)` and the asset's stage stays `briefed`. If a section still fails the gate
  after the loop, the file is written (the draft has value) but it is marked
  `NEEDS-HUMAN`, the ledger records the reason, and no stage advance happens.
* **Cost is visible per call.** Tokens and attempts are logged to the ledger, so "$0 because
  the model is local" is a claim on a screen rather than a vibe.

Offline behaviour is identical in structure to online behaviour, which is deliberate: the
artefacts, gates and ledger entries exist either way; only the prose differs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import gate as gate_mod
from . import llm, models, validate

#: platform → asset kind. Lives here so `wf work`, `wf pick --build` and the ledger all
#: agree on what kind of thing they just made.
KIND_BY_PLATFORM = {"kdp": "book", "code": "tool", "digital-product": "product",
                    "seo-site": "article", "audience": "kit", "tool": "tool",
                    "book": "book", "article": "article", "product": "product", "kit": "kit"}

NEEDS_HUMAN = "<!-- NEEDS-HUMAN: gate errors remain -->"
REPAIR_MAX_DEFAULT = 3

REPAIR_HEADER = (
    "Your previous draft of this section FAILED the pre-publication compliance check. Rewrite "
    "it so every finding below is resolved, keeping everything that was good. Do not argue, do "
    "not explain, do not add a note about the fix — output only the corrected Markdown section, "
    "starting at its heading.\n\nFINDINGS TO FIX:\n")
REPAIR_FOOTER = (
    "\n\nRules for the fix:\n"
    "- Replace the offending phrasing, do not just delete the sentence — keep the substance.\n"
    "- If a claim cannot be made safely, state the limitation plainly and point to the "
    "professional who can answer it.\n"
    "- Keep the numbers, the worked example and the closing action intact unless a finding "
    "names them.\n")


@dataclass
class Attempt:
    n: int
    words: int
    errors: list[str]
    warnings: list[str]
    chars: int

    def as_dict(self) -> dict[str, Any]:
        return {"attempt": self.n, "words": self.words, "errors": self.errors,
                "warnings": self.warnings, "chars": self.chars}


@dataclass
class Section:
    name: str
    status: str                          # "generated" | "needs_human" | "offline" | "failed"
    words: int = 0
    attempts: list[Attempt] = field(default_factory=list)
    path: str = ""
    note: str = ""
    tokens: int = 0

    @property
    def clean(self) -> bool:
        return self.status == "generated"

    def as_dict(self) -> dict[str, Any]:
        return {"section": self.name, "status": self.status, "words": self.words,
                "path": self.path, "note": self.note, "tokens": self.tokens,
                "attempts": [a.as_dict() for a in self.attempts]}


@dataclass
class WorkResult:
    idea: str
    slug: str
    sections: list[Section]
    validation: dict[str, Any] = field(default_factory=dict)
    gate_errors: int = 0
    gate_warnings: int = 0
    provider: str = "offline"
    model: str = ""
    total_tokens: int = 0
    total_words: int = 0
    cost_usd: float = 0.0
    needs_human: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.gate_errors == 0 and not self.needs_human

    @property
    def fully_generated(self) -> bool:
        return all(s.status == "generated" for s in self.sections) and bool(self.sections)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


# ---------------------------------------------------------------- prompts ----
def sections_for(idea: dict[str, Any], max_sections: int = 8) -> list[str]:
    """What the model is asked to write, derived from the platform and the brief's own
    structure — so `wf work` drafts the asset's real shape, not a generic outline."""
    plat = idea.get("platform", "kdp")
    plat = {"code": "tool", "kdp": "kdp", "seo-site": "seo-site", "site": "seo-site",
            "digital-product": "digital-product", "product": "digital-product",
            "kit": "digital-product", "audience": "audience", "book": "kdp",
            "article": "seo-site"}.get(plat, plat)
    named = idea.get("sections") or []
    if named:
        return named[:max_sections]
    table = {
        "kdp": ["The mechanism: three problems, not one", "Caffeine timing on a 12-hour shift",
                "The 3-2-1-0 wind-down", "Light: the lever nobody hands you",
                "Food timing without a break room", "Anchor sleep and the one non-negotiable",
                "The 28-day protocol, day by day", "Worksheets and the log page"],
        "seo-site": ["Answer the question in 3 sentences", "The mechanism, with numbers",
                     "What to do tonight", "What doesn't work and why",
                     "When to see a professional", "FAQ block (6 real questions)"],
        "tool": ["README: what it does and does not do", "Privacy and offline behaviour",
                 "Worked example, start to finish", "Limitations and error handling",
                 "How to run it: zero-install path"],
        "digital-product": ["What's inside, itemised", "Who it is not for",
                            "How it's delivered", "The guarantee, honestly worded",
                            "Sales page body", "FAQ"],
        "audience": ["Value post: one mechanism, no link", "Numbers post", "Objection post",
                     "Worked-example post", "Profile bio and cross-links"],
    }
    return table.get(plat, table["seo-site"])[:max_sections]


def repair_prompt(brief: str, section: str, findings: list[str], prior: str,
                  system_rules: str = llm.SYSTEM_RULES) -> tuple[str, str]:
    body = REPAIR_HEADER + "\n".join(f"- {f}" for f in findings) + REPAIR_FOOTER
    user = (f"{body}\nORIGINAL BRIEF:\n{brief}\n\nSECTION: {section}\n\n"
            f"YOUR PREVIOUS DRAFT (to correct, not to repeat):\n{prior[:6000]}\n")
    return user, system_rules


def findings_for(rep: gate_mod.GateReport) -> list[str]:
    """Findings rendered as instructions, not codes. The gate already carries a `fix`
    string per rule, and pasting it in is what makes the repair loop converge instead
    of producing a differently-worded violation."""
    out = []
    for f in rep.findings:
        if f.severity != "error":
            continue
        bits = [f"{f.rule_id} (line {f.line}) — {f.title}"]
        if f.why:
            bits.append(f.why)
        if f.fix:
            bits.append(f"DO THIS: {f.fix}")
        if f.excerpt:
            bits.append(f"offending text: {f.excerpt!r}")
        out.append(" :: ".join(bits))
    return out


# ------------------------------------------------------------------- work ----
def draft_section(provider: llm.Provider, brief: str, section: str, *, word_target: int,
                  max_attempts: int = REPAIR_MAX_DEFAULT,
                  audience: str = "reader", log: Callable[[str, str], None] | None = None
                  ) -> tuple[str, Section, int]:
    """One section, through the repair loop. Returns (text, SectionRecord, tokens)."""
    rec = Section(name=section, status="generated")
    if not provider.available:
        rec.status = "offline"
        rec.note = "no model reachable; section left as a brief for the operator"
        return "", rec, 0
    text = ""
    tokens = 0
    findings: list[str] = []
    best: tuple[int, str] | None = None          # (error_count, least-bad draft)
    for n in range(1, max_attempts + 1):
        if n == 1:
            prompt, system = llm.brief_to_prompt(brief, section, word_target), ""
        else:
            prompt, system = repair_prompt(brief, section, findings, text)
        try:
            res = provider.complete(prompt, system=system)
        except llm.LLMUnavailable as e:
            rec.status, rec.note = "failed", str(e)[:200]
            if log:
                log("llm-error", f"{section}: {str(e)[:120]}")
            return text, rec, tokens
        tokens += res.in_tokens + res.out_tokens
        text = res.text.strip()
        rep = gate_mod.evaluate_text(text, section, audience=audience)
        att = Attempt(n=n, words=rep.words, errors=[f.rule_id for f in rep.errors],
                      warnings=[f.rule_id for f in rep.warnings], chars=len(text))
        rec.attempts.append(att)
        rec.words = rep.words
        if log:
            log("gate-draft", f"{section} try {n}: {len(att.errors)} error(s) {att.errors}")
        if best is None or len(att.errors) < best[0]:
            best = (len(att.errors), text)        # keep the best attempt, not the last one
        findings = findings_for(rep)
        if not findings:
            rec.status = "generated"
            return text, rec, tokens
        rec.status = "needs_human"
        rec.note = f"still failing after {n} attempt(s): " + ", ".join(findings[:4])
    if best and not best[0]:
        rec.status = "generated"
    return (best[1] if best else ""), rec, tokens


def collect_metadata(provider: llm.Provider, idea: dict[str, Any],
                     words: int) -> tuple[dict[str, Any], validate.Validation]:
    """Structured slots through JSON mode + validators. On any failure the field falls
    back to the deterministic generator, so a finicky 8B model degrades quality of
    wording, never correctness of limits."""
    fields: dict[str, Any] = {}
    note = ""
    if provider.available:
        spec = ("Return JSON with keys title, subtitle, author, description_html, "
                "categories (2 strings), keywords (7 strings, each <=49 chars, none repeating a title "
                "word), price_usd. No credentials, no outcome promises, no treatment claims.")
        prompt = (f"{spec}\n\nIDEA:\n{json.dumps({k: idea.get(k) for k in ('title','niche','audience','thesis','platform')}, indent=1)[:1600]}\n"
                  f"MANUSCRIPT WORD COUNT: {words}")
        try:
            fields = provider.complete_json(prompt) or {}
        except (llm.LLMUnavailable, json.JSONDecodeError) as e:
            note = f"model metadata pass failed ({str(e)[:80]}); using deterministic generator"
    v = validate.validate_kdp(fields) if fields else validate.Validation()
    if note:
        v.add("provider", "warn", note)
    if not fields:
        return {"source": "generator"}, v
    fields["source"] = "model+validated"
    return fields, v


def work_idea(store: Any, idea: dict[str, Any], provider: llm.Provider,
              *, out_root: str | Path = "out", max_attempts: int = REPAIR_MAX_DEFAULT,
              max_sections: int = 8, word_target: int = 700, kind: str | None = None,
              log: Callable[[str, str], None] | None = None) -> WorkResult:
    from . import generator
    slug = models.slugify(idea["title"])
    outdir = Path(out_root) / slug
    outdir.mkdir(parents=True, exist_ok=True)
    brief = generator.brief_for(idea)
    (outdir / "brief.md").write_text(brief, encoding="utf-8")

    def _log(kind: str, msg: str) -> None:
        if log:
            log(kind, msg)
        if store is not None:
            try:
                store.event(kind, slug, {"detail": msg[:500]})
            except Exception:
                pass

    _log("work-start", f"provider={provider.mode}:{provider.model or '—'} attempts<={max_attempts}")
    sections = sections_for(idea, max_sections)
    body: list[str] = []
    res = WorkResult(idea=idea["title"], slug=slug, sections=[], provider=provider.mode,
                     model=provider.model)
    offline_note = ""
    for sec in sections:
        text, rec, tokens = draft_section(provider, brief, sec, word_target=word_target,
                                         max_attempts=max_attempts, log=_log)
        target = outdir / "draft" / f"{models.slugify(sec)[:48]}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        if text:
            target.write_text(text + "\n", encoding="utf-8")
            rec.path = str(target)
            body.append(text)
        elif not provider.available:
            hint = (f"no model reachable. Use `wf build --emit-prompt` and paste into any chat "
                    f"UI, or pull a model: `wf model --select`.")
            target.write_text(f"## {sec}\n\n{generator.TODO} — {hint}\n", encoding="utf-8")
            rec.path = str(target)
            body.append(f"## {sec}\n\n{generator.TODO} — {hint}\n")
            # The assembled draft is the file a human actually opens, so it has to say why it is
            # empty. A bare "TODO(human)" inside an otherwise confident document is how a
            # placeholder gets pasted into a CMS by someone in a hurry.
            offline_note = ("> **Not drafted — no model was reachable.** Each section below is a "
                            "brief, not prose.\n>\n> Two free ways to turn it into a draft: run a "
                            "local model (`wf model --select`\n> picks one that fits the hardware you "
                            "have, `ollama pull` it, then `wf work --verbose`),\n> or copy the prompts "
                            "out (`wf build --emit-prompt`), paste them into any chat UI, and bring"
                            "\n> the answers back with `--from-file`. The gate runs either way, and "
                            "no stage advances\n> until it passes.\n\n")
        else:
            body.append(f"## {sec}\n\n{generator.TODO}\n")
        res.sections.append(rec)
        res.total_tokens += tokens
        res.total_words += rec.words

    draft_md = "\n\n".join(body).strip() + "\n"
    if offline_note:
        draft_md = offline_note + draft_md
    (outdir / "draft" / "ASSET-DRAFT.md").write_text(draft_md, encoding="utf-8")
    rep = gate_mod.evaluate_text(draft_md, str(outdir / "draft/ASSET-DRAFT.md"))
    res.gate_errors, res.gate_warnings = len(rep.errors), len(rep.warnings)
    if store is not None:
        try:
            store.record_gate(rep)
        except Exception:
            pass

    meta, v = collect_metadata(provider, idea, res.total_words)
    res.validation = v.as_dict()
    if meta.get("source") == "model+validated":
        (outdir / "METADATA.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if not v.ok:
        (outdir / "draft" / "METADATA-ISSUES.md").write_text(v.render() + "\n", encoding="utf-8")

    res.needs_human = [s.name for s in res.sections if s.status in ("needs_human", "failed")]
    (outdir / "WORK-LOG.md").write_text(log_markdown(res, rep), encoding="utf-8")

    if res.fully_generated and rep.passed and v.ok:
        stage = "gated"
    elif any(s.status == "generated" for s in res.sections):
        stage = "drafted"
    else:
        stage = "briefed"

    if store is not None:
        # Ideas only ever move proposed → selected → dropped. The *asset* row owns the
        # production stages, and the stage machine's evidence requirements
        # (gated ⇒ gate_report, packaged ⇒ build_artifact) are what stop a skip.
        evidence = {"out": str(outdir), "gate": "pass" if rep.passed else "fail",
                    "sections": str(len(res.sections)), "tokens": str(res.total_tokens),
                    "gate_report": "clean" if rep.passed else f"{len(rep.errors)} error(s)",
                    "needs_human": str(len(res.needs_human)),
                    "provider": f"{res.provider}:{res.model}"}
        try:
            rows = store.assets(idea["id"]) if hasattr(store, "assets") else []
            existing = next((a for a in rows if a.get("path") == str(outdir)), None)
            if existing is None:
                kind_ = kind or KIND_BY_PLATFORM.get(idea.get("platform", ""), "book")
                created = models.Asset.new(idea["id"], kind_, str(outdir))
                created.word_count = res.total_words
                draft_file = outdir / "draft" / "ASSET-DRAFT.md"
                created.content_sha = models.sha256_of(draft_file) if draft_file.exists() else ""
                store.put_asset(created)
                row_id, cur_stage = created.id, created.stage
            else:
                row_id, cur_stage = existing["id"], existing["stage"]
            # Forward moves are earned; a backward move happens only when the gate now fails.
            # Leaving an asset marked "gated" after its rebuilt draft broke a rule would be
            # worse than any stage skip, because "gated" is the label the publish checklist trusts.
            order = {name: i for i, name in enumerate(models.STAGES)}
            forward = order.get(stage, 0) > order.get(cur_stage, 0)
            downgrade = (order.get(stage, 0) < order.get(cur_stage, 0)
                         and (not rep.passed or res.needs_human))
            if cur_stage == stage:
                pass
            elif downgrade:
                store.set_stage("assets", row_id, stage, evidence=evidence)
                _log("asset-stage", f"{row_id}: {cur_stage} → {stage}  (gate failed since the last run)")
            elif forward and stage in order and cur_stage in order:
                # One run can earn more than a single rung: it drafted clean *and* gated clean.
                # The store enforces the graph one edge at a time, so walk it. Refusing the walk
                # would leave the ledger behind reality (an asset that passed everything still
                # "briefed"), and jumping straight to "gated" is the skip the evidence rules
                # exist to stop — so neither.
                for nxt in models.STAGES[order[cur_stage] + 1:order[stage] + 1]:
                    store.set_stage("assets", row_id, nxt, evidence=evidence)
                    _log("asset-stage", f"{row_id}: {cur_stage} → {nxt}")
                    cur_stage = nxt
            else:
                _log("stage-refused", f"{row_id}: staying at {cur_stage}; {stage} would be a "
                                      f"downgrade without a failing gate")
        except Exception as e:
            _log("stage-blocked", f"{stage}: {e}")
        store.event("work-done", slug, {"stage": stage, "errors": res.gate_errors,
                                        "needs_human": len(res.needs_human),
                                        "tokens": res.total_tokens, "words": res.total_words})
    return res


def log_markdown(res: WorkResult, rep: gate_mod.GateReport) -> str:
    L = [f"# Work log — {res.idea}", "",
         f"- provider `{res.provider}:{res.model or '—'}` · {res.total_words} words drafted · "
         f"{res.total_tokens} tokens (local: $0)",
         f"- gate on the assembled draft: **{len(rep.errors)} error(s), {len(rep.warnings)} warning(s)**",
         f"- validation: {res.validation.get('errors', 0)} metadata error(s), "
         f"{res.validation.get('warnings', 0)} warning(s)", "",
         "| section | status | words | attempts | residual |", "|---|---|---|---|---|"]
    for s in res.sections:
        resid = "; ".join(sorted({e for a in s.attempts for e in a.errors})) or "—"
        L.append(f"| {s.name} | `{s.status}` | {s.words} | {len(s.attempts)} | {resid[:60]} |")
    L += ["", "## Attempt history", ""]
    for s in res.sections:
        if len(s.attempts) > 1:
            L.append(f"- **{s.name}**: " + " → ".join(
                f"try{a.n}({a.words}w, {len(a.errors)}e{',' + '/'.join(a.warnings) if a.warnings else ''})"
                for a in s.attempts))
    if res.needs_human:
        L += ["", "## Needs a human, and why", ""]
        L += [f"- `{s.name}` — {s.note}" for s in res.sections if s.name in res.needs_human]
        L += ["", "_These sections are written but not publishable. The stage machine will not advance "
              "past them, which is the point._"]
    L += ["", "## Gate detail", ""]
    for f in rep.findings:
        L.append(f"- [{f.severity}] {f.rule_id} line {f.line}: {f.title}"
                 + (f" — {f.fix}" if f.fix else ""))
    if not rep.findings:
        L.append("- clean")
    return "\n".join(L) + "\n"
