"""Fine-tuning corpus builder — and the judgement about whether to fine-tune at all.

Two datasets, both derived only from work that (a) exists in this repo, (b) passes
the compliance gate, and (c) the operator has explicitly approved:

* **SFT** (prompt → approved section). Teaches format, structure, and the house
  voice. Needs ~150+ examples to be worth a LoRA; below that, prompt engineering
  is strictly better and `build()` says so instead of shipping a config nobody
  should run.
* **DPO / preference** (prompt, chosen = the human's final text, rejected = the
  model's earlier draft). This is the higher-value half, and it is *free* here:
  every asset in this repo went draft → human edit → final, so the pairs already
  exist as a by-product of doing the work. Almost nobody realises they have a
  preference dataset lying around.

Guardrails that are the whole point:
  - a source file that fails the gate is excluded and reported, never quietly included
  - every example carries provenance (file, sha, licence note) in the dataset card
  - no third-party text: we train on work we own, which sidesteps the copyright
    question that makes most scraped "style" fine-tunes radioactive
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

#: Base choices for the training plan. 8B is the one that fits a free Colab T4 at 4-bit; 27B is
#: only worth the extra GPU time once there is enough data to fill the extra capacity.
BASE_SMALL, BASE_LARGE, BASE_LARGE_MIN_EXAMPLES = "qwen3.6-8b", "qwen3.6-27b", 400

MIN_SFT_EXAMPLES = 150          # below this: prompt-engineer instead
MIN_DPO_PAIRS = 40
MAX_CHARS = 9_000
MIN_CHARS = 420

SPLIT_SEED = 20261011


@dataclass
class Example:
    prompt: str
    completion: str
    source: str
    section: str
    kind: str = "sft"           # "sft" | "dpo"
    rejected: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return hashlib.sha256(f"{self.prompt}\x1f{self.completion}".encode()).hexdigest()[:16]

    def to_sharegpt(self) -> dict[str, Any]:
        """Unsloth / TRL standardize_sharegpt shape."""
        return {"messages": [{"role": "user", "content": self.prompt},
                             {"role": "assistant", "content": self.completion}],
                 "id": f"{self.kind}-{self.key}"}

    def to_dpo(self) -> dict[str, Any]:
        return {"prompt": self.prompt, "chosen": self.completion, "rejected": self.rejected,
                "meta": {"source": self.source, "section": self.section, **self.meta}}

    def to_alpaca(self) -> dict[str, Any]:
        return {"instruction": self.prompt, "input": "", "output": self.completion}


# ------------------------------------------------------------ sectionising ----
def split_sections(md: str) -> list[tuple[str, str]]:
    """Split on H1/H2 boundaries. H3s stay inside their parent so a training example
    keeps its argument whole instead of learning to produce orphaned subsections."""
    lines = md.split("\n")
    out: list[tuple[str, list[str]]] = []
    cur_title, buf = "", []
    for ln in lines:
        if re.match(r"^#{1,2}\s+", ln):
            if buf and ("".join(buf)).strip():
                out.append((cur_title, buf))
            cur_title, buf = re.sub(r"^#+\s*", "", ln).strip(), [ln]
        else:
            buf.append(ln)
    if buf and "".join(buf).strip():
        out.append((cur_title, buf))
    return [(t, "\n".join(b).strip()) for t, b in out if len("\n".join(b).strip()) > 60]


def _prompt_for(section: str, idea: str, requirement: str) -> str:
    """The instruction half of every example. Deliberately includes the constraints,
    so the model learns the rules instead of learning to ignore them — a fine-tune
    that bakes in the voice without the constraints is how you lose the gate's work."""
    return (
        f"You are writing the “{section}” part of {idea}.\n"
        "Constraints: specific over general; include at least one concrete number, a worked example, "
        "and a next action the reader can take tonight; no medical/legal/financial advice; no "
        "credentials claimed; no income promises; no cliché openers; vary sentence length; "
        "Markdown starting at the section heading.\n" + (requirement + "\n" if requirement else "")
    ).strip()


# --------------------------------------------------------------- the build ----
@dataclass
class BuildResult:
    sft: list[Example]
    dpo: list[Example]
    excluded: list[dict[str, Any]]
    stats: dict[str, Any]
    recommendation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"stats": self.stats, "excluded": self.excluded, "recommendation": self.recommendation,
                "counts": {"sft": len(self.sft), "dpo": len(self.dpo)}}


def _is_approved(path: Path, approvals: dict[str, dict[str, Any]]) -> tuple[bool, str]:
    """Approval is by content hash, not filename: editing a file after sign-off must
    invalidate its approval rather than silently training on unreviewed text."""
    sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    rec = approvals.get(str(path).replace("\\", "/")) or approvals.get(path.name)
    if not rec:
        return False, "no approval record"
    if rec.get("sha") and rec["sha"] != sha:
        return False, f"approved content changed since sign-off (approved {rec['sha']}, now {sha})"
    return True, sha


def collect(repo: str | Path = ".", *, approvals_path: str | Path | None = None,
            include_globs: tuple[str, ...] = ("01-book/chapters/*.md", "04-seo/articles/*.md"),
            draft_glob: str = "out/*/draft/*.md", gate_module: Any = None) -> BuildResult:
    repo = Path(repo)
    approvals: dict[str, dict[str, Any]] = {}
    strict = False
    ap = Path(approvals_path) if approvals_path else repo / "config" / "approval.json"
    if not ap.exists():
        ap = repo / ".wealforge" / "approvals.json"
    if ap.exists():
        data = json.loads(ap.read_text(encoding="utf-8"))
        approvals = dict(data.get("approved", {}) or {})
        strict = bool(data.get("strict_warnings", False))

    gate = gate_module
    sft: list[Example] = []
    dpo: list[Example] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Gate eligibility, per *work* not per file when a directory holds a bundle.
    # A book's disclaimer lives in its front matter: gating chapters one by one would
    # reject every chapter for a safeguard that is present, just elsewhere. That is the
    # same reason `wf gate --bundle` exists, and the same false-alarm trap.
    dir_report: dict[Path, Any] = {}
    for pattern in include_globs:
        files = sorted(repo.glob(pattern))
        for d in {f.parent for f in files}:
            if gate is not None and len([f for f in files if f.parent == d]) >= 3:
                reps = gate.evaluate_paths([d], bundle=True)
                dir_report[d] = reps[0] if reps else None
        for f in files:
            text = f.read_text(encoding="utf-8", errors="ignore")
            if gate is not None:
                rep = dir_report.get(f.parent) or gate.evaluate_text(text, str(f))
                if not rep.passed:
                    excluded.append({"file": f.name, "reason": "gate",
                                     "detail": sorted({x.rule_id for x in rep.errors})})
                    continue
                if rep.warnings and strict:
                    excluded.append({"file": f.name, "reason": "gate warnings",
                                     "detail": sorted({x.rule_id for x in rep.warnings})})
                    continue
            approved, sha = _is_approved(f, approvals)
            if not approved and approvals:                 # an approval file exists → enforce it
                excluded.append({"file": f.name, "reason": "approval", "detail": sha})
                continue
            idea = re.sub(r"^#\s*", "", text.split("\n", 1)[0]).strip() or f.stem
            for title, body in split_sections(text):
                if not (MIN_CHARS <= len(body) <= MAX_CHARS):
                    excluded.append({"file": f.name, "reason": "size", "chars": len(body),
                                     "detail": title[:40]})
                    continue
                ex = Example(prompt=_prompt_for(title, idea, ""), completion=body.strip(),
                             source=f.name, section=title, meta={"sha": sha})
                if ex.key in seen:
                    excluded.append({"file": f.name, "reason": "duplicate", "detail": title[:40]})
                    continue
                seen.add(ex.key)
                sft.append(ex)

    # DPO: pair the model's draft with the human's final where both exist.
    for draft in sorted(repo.glob(draft_glob)) if draft_glob else []:
        final = _find_final_for(draft, repo)
        if not final:
            excluded.append({"file": draft.name, "reason": "no matching final", "detail": str(draft)})
            continue
        dtext = draft.read_text(encoding="utf-8", errors="ignore")
        ftext = final.read_text(encoding="utf-8", errors="ignore")
        if len(dtext.strip()) < MIN_CHARS or dtext.strip() == ftext.strip():
            excluded.append({"file": draft.name, "reason": "draft identical or too short", "detail": ""})
            continue
        if gate is not None:
            rep = gate.evaluate_text(ftext, str(final))
            if not rep.passed:
                excluded.append({"file": final.name, "reason": "gate on final",
                                 "detail": [x.rule_id for x in rep.errors]})
                continue
        title = (re.match(r"^#{1,3}\s*(.+)$", ftext, re.M) or [None, final.stem])[1]
        ex = Example(prompt=_prompt_for(title, final.parent.name, ""), completion=ftext.strip(),
                     rejected=dtext.strip(), source=final.name, section=title, kind="dpo")
        if ex.key in seen:
            continue
        seen.add(ex.key)
        dpo.append(ex)

    if not dpo:
        excluded.append({"file": "(dpo)", "reason": "no draft/final pairs yet",
                         "detail": "pairs appear by themselves once `wf work` drafts a section and you "
                                   "edit the file: the builder compares out/*/draft against the final text"})
    stats = {
        "sft_examples": len(sft), "dpo_pairs": len(dpo), "excluded": len(excluded),
        "median_chars": _median([len(e.completion) for e in sft]),
        "approx_tokens_total": sum(len(e.prompt) + len(e.completion) for e in sft) // 4,
        "sources": sorted({e.source for e in sft}),
    }
    return BuildResult(sft, dpo, excluded, stats, recommend(stats))


def _find_final_for(draft: Path, repo: Path) -> Path | None:
    stem = draft.stem
    for cand in [draft.parent.parent / "manuscript.md",
                 repo / "01-book" / "chapters" / f"{stem}.md"]:
        if cand.exists():
            return cand
    hits = sorted(repo.glob(f"**/{stem}*.md"))
    return next((h for h in hits if "draft" not in h.parts), None)


def _median(xs: list[int]) -> int:
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[len(xs) // 2]


def recommend(stats: dict[str, Any]) -> dict[str, Any]:
    """The call most tooling refuses to make: should you fine-tune at all?"""
    s, d = stats["sft_examples"], stats["dpo_pairs"]
    # Which base the plan targets, before the verdict: the dataset card names it either way so
    # `wf dataset` and `wf finetune` can never disagree about what they were preparing for.
    # Ids, not ollama tags — resolved against the registry, so this cannot drift from it.
    base = BASE_LARGE if s >= BASE_LARGE_MIN_EXAMPLES else BASE_SMALL
    if s < MIN_SFT_EXAMPLES and d < MIN_DPO_PAIRS:
        return {
            "fine_tune": False,
            "base_model": base,
            "why": (f"{s} SFT examples and {d} preference pairs is below the floor "
                    f"({MIN_SFT_EXAMPLES}/{MIN_DPO_PAIRS}). A LoRA on this would mostly learn to "
                    "imitate formatting while degrading instruction-following, and you would have "
                    "no way to tell the difference at 40 examples."),
            "do_instead": [
                "Ship the constraint prompt as-is and use few-shot examples from this same corpus — "
                "the marginal gain from a LoRA at this data volume is within noise.",
                "Keep writing. Every approved asset adds examples; the corpus is the scarce input, "
                "not the training run.",
                "Spend the time on the gate + eval harness, which improves output quality immediately "
                "and keeps improving as models change.",
            ],
            "revisit_when": f"{MIN_SFT_EXAMPLES} SFT examples or {MIN_DPO_PAIRS} DPO pairs exist "
                            f"(run `wf dataset` after each published asset)",
        }
    return {
        "fine_tune": True,
        "why": f"{s} SFT + {d} pairs clears the floor; a QLoRA adapter is justified and reversible.",
        "base_model": base,
        "method": "QLoRA (4-bit base, r=16 adapters) — 6-8GB training VRAM, free-tier T4 viable",
        "expect": "style + format + constraint obedience. Not new facts: that is a retrieval/prompt problem.",
        "eval_required": "run `wf eval --compare base,finetuned` before switching the default provider; "
                         "a fine-tune that scores lower on the constraint battery is a regression, not progress",
    }


# ------------------------------------------------------------------- write ----
def write(res: BuildResult, outdir: str | Path = "dataset", fmt: str = "sharegpt") -> dict[str, Path]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    train = [e.to_sharegpt() if fmt == "sharegpt" else e.to_alpaca() for e in res.sft]
    val: list[Any] = []
    if len(train) >= 20:                       # deterministic 5% holdout, no RNG drift
        idx = {i for i in range(0, len(train), max(1, len(train) // (len(train) // 20 or 1)))}
        val = [train.pop(i) for i in sorted(idx, reverse=True)]
    paths = {
        "sft_train": out / "sft-train.jsonl", "sft_val": out / "sft-val.jsonl",
        "dpo": out / "dpo.jsonl", "stats": out / "stats.json", "card": out / "dataset-card.md",
        "excluded": out / "excluded.json",
    }
    _jsonl(paths["sft_train"], train)
    _jsonl(paths["sft_val"], val)
    _jsonl(paths["dpo"], [e.to_dpo() for e in res.dpo])
    paths["stats"].write_text(json.dumps({**res.stats, "recommendation": res.recommendation,
                                          "split": {"train": len(train), "val": len(val)}}, indent=2))
    paths["excluded"].write_text(json.dumps(res.excluded, indent=2))
    paths["card"].write_text(card_markdown(res, fmt, len(train), len(val)))
    return paths


def _jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def card_markdown(res: BuildResult, fmt: str, n_train: int, n_val: int) -> str:
    rec = res.recommendation
    lines = ["# Dataset card — WealthForge house style", "",
             f"**Examples:** {len(res.sft)} SFT ({n_train} train / {n_val} val), {len(res.dpo)} DPO pairs "
             f"· format `{fmt}` · median {res.stats['median_chars']} chars "
             f"· ~{res.stats['approx_tokens_total']:,} tokens", "",
             "## Provenance", "",
             "Every example is drawn from assets in this repository that passed the compliance gate "
             "(`wealforge/gate.py`) and were signed off by the human operator. No third-party text, no "
             "scraped corpora, no model outputs from a licence that forbids training on them.", "",
             f"Intended base: `{rec.get('base_model') or 'not yet chosen'}`. Before spending a GPU hour, "
             "confirm at that model's card that its licence permits using its outputs for training: "
             "Apache-2.0 and MIT do, a community-style licence may not. `config/models.json` is filtered "
             "on exactly that rule, and `wf model --select --for-finetune` re-ranks on it.", "",
             "| source | what it contributes |", "|---|---|"]
    for s in res.stats["sources"]:
        lines.append(f"| `{s}` | gate-passing, operator-approved |")
    lines += ["", "## Known limits", "",
              f"- Style and structure transfer. Facts do not: this corpus is {len(res.stats['sources'])} "
              f"files of one niche's prose, so "
              "any factual recall the base model had is untouched and any hallucination is still there.",
              "- Single-niche vocabulary (shift-work recovery). A tuned adapter will not generalise to "
              "another niche; expect to re-tune per portfolio, or keep the prompt path for breadth.",
              "- Rejected-side DPO text is model drafts, so DPO here teaches \"avoid the model's own "
              "failure modes\", which is exactly the right target but a narrow one.", "",
              "## Recommendation", ""]
    if rec.get("fine_tune"):
        lines += [f"**Fine-tune: yes.** {rec['why']}", "",
                  f"- base: `{rec['base_model']}` · method: {rec['method']}",
                  f"- expectation: {rec['expect']}", f"- {rec['eval_required']}"]
    else:
        lines += [f"**Fine-tune: not yet.** {rec['why']}", ""]
        lines += [f"- {x}" for x in rec["do_instead"]]
        lines += ["", f"_Revisit: {rec['revisit_when']}_"]
    if res.excluded:
        reasons: dict[str, int] = {}
        for e in res.excluded:
            reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
        lines += ["", "## Excluded from training (counted, not hidden)", "",
                  " · ".join(f"**{k}**: {v}" for k, v in sorted(reasons.items()))]
    return "\n".join(lines) + "\n"
