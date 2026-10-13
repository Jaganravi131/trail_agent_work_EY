"""Model registry + selection, because "best open-source model" is a *license*
question before it is a benchmark question.

A zero-capital operation has to ship commercially on a permissive licence and must
be allowed to use outputs for training — otherwise the fine-tune in `dataset.py` is
a licence violation, not an optimisation. Several popular families carry clauses
exactly like that. So `select()` filters on licence constraints first, then ranks
on quality-per-VRAM, and always prints the reason.

The registry is data (config/models.json) with an `as_of` date, because this space
moves monthly and a stale recommendation is worse than none.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .providers import Backend, ollama, openai_compat

AS_OF = "2026-10-11"

#: Licence properties that change what we may *do*, not just what we may ship.
TRAITS = ("commercial", "redistribute_finetune", "outputs_may_train_other_models", "osi_approved")


@dataclass
class ModelSpec:
    id: str
    family: str
    params_total: str
    params_active: str
    license: str
    vram_gb_4bit: float
    context_k: int
    vram_gb_train: float = 0.0        # QLoRA needs headroom beyond inference weights
    strengths: list[str] = field(default_factory=list)
    traits: dict[str, bool] = field(default_factory=dict)
    ollama_tag: str = ""
    hf_repo: str = ""
    verifiable_on_free_tier: bool = False      # fits a free Colab T4 (16 GB) at 4-bit
    notes: str = ""

    @property
    def runnable_here(self) -> bool:
        return self.vram_gb_4bit <= 16

    @property
    def trainable_here(self) -> bool:
        return (self.vram_gb_train or self.vram_gb_4bit * 1.4) <= 16

    def train_cost(self, vram_gb: float) -> bool:
        """True when this model cannot be *trained* (not merely run) on the host."""
        return (self.vram_gb_train or round(self.vram_gb_4bit * 1.4, 1)) > vram_gb

    def licence_ok_for(self, need_training_on_outputs: bool = True) -> tuple[bool, str]:
        if not self.traits.get("commercial", False):
            return False, f"{self.license} does not grant unrestricted commercial use"
        if self.traits.get("redistribute_finetune") is False:
            return False, f"{self.license} restricts redistributing fine-tunes"
        if need_training_on_outputs and self.traits.get("outputs_may_train_other_models") is False:
            return False, (f"{self.license} forbids using model outputs to train other models — "
                           "fine-tuning a successor on distils from it would breach the terms")
        return True, "licence permits commercial use, fine-tuning and reuse of outputs"


def default_registry() -> list[dict[str, Any]]:
    """Shortlist as of AS_OF. Verify each card at download time; this list is a
    starting point with reasons, not gospel, and every entry says which licence
    claim matters. Sizes/VRAM are approximate published figures."""
    return [
        {"id": "qwen3.6-27b", "family": "Qwen3.6", "params_total": "27B", "params_active": "27B dense",
         "license": "Apache-2.0", "vram_gb_4bit": 15.0, "vram_gb_train": 24.0, "context_k": 262,
         "strengths": ["best quality that still fits a 24GB card", "thinking mode", "long context"],
         "traits": dict.fromkeys(TRAITS, True), "ollama_tag": "qwen3.6:27b",
         "hf_repo": "Qwen/Qwen3.6-27B", "verifiable_on_free_tier": False,
         "notes": "primary drafting model when the operator has 24GB+"},

        {"id": "qwen3.6-35b-a3b", "family": "Qwen3.6", "params_total": "35B", "params_active": "3B MoE",
         "license": "Apache-2.0", "vram_gb_4bit": 20.0, "vram_gb_train": 26.0, "context_k": 256,
         "strengths": ["cheapest per token", "fast decode"],
         "traits": dict.fromkeys(TRAITS, True), "ollama_tag": "qwen3.6:35b-a3b",
         "hf_repo": "Qwen/Qwen3.6-35B-A3B", "verifiable_on_free_tier": False,
         "notes": "throughput play; quality close to the dense 27B on instruction tasks"},

        {"id": "qwen3.6-8b", "family": "Qwen3.6", "params_total": "8B", "params_active": "8B dense",
         "license": "Apache-2.0", "vram_gb_4bit": 5.5, "vram_gb_train": 7.0, "context_k": 128,
         "strengths": ["fits a free Colab T4 for QLoRA", "the fine-tune target", "10-20× faster loop"],
         "traits": dict.fromkeys(TRAITS, True), "ollama_tag": "qwen3.6:8b",
         "hf_repo": "Qwen/Qwen3.6-8B", "verifiable_on_free_tier": True,
         "notes": "THE fine-tune base: Apache-2.0, LoRA/QLoRA ecosystem, 6-8GB training VRAM"},

        {"id": "gemma4-26b-a4b", "family": "Gemma 4", "params_total": "26B MoE", "params_active": "3.8B",
         "license": "Apache-2.0", "vram_gb_4bit": 14.0, "vram_gb_train": 22.0, "context_k": 256,
         "strengths": ["best capability-to-hardware ratio for local use", "moved off the old Gemma terms"],
         "traits": dict.fromkeys(TRAITS, True), "ollama_tag": "gemma4:26b-a4b",
         "hf_repo": "google/gemma-4-26b-a4b", "verifiable_on_free_tier": False,
         "notes": "check the card: older Gemma releases carry usage guidelines, Gemma 4 is reported Apache-2.0"},

        {"id": "gpt-oss-20b", "family": "gpt-oss", "params_total": "21B", "params_active": "3.6B MoE",
         "license": "Apache-2.0 + usage policy", "vram_gb_4bit": 16.0, "vram_gb_train": 24.0, "context_k": 128,
         "strengths": ["strong reasoning", "native MXFP4", "structured outputs"],
         "traits": {**dict.fromkeys(TRAITS, True), "osi_approved": False}, "ollama_tag": "gpt-oss:20b",
         "hf_repo": "openai/gpt-oss-20b", "verifiable_on_free_tier": False,
         "notes": "requires the harmony response format on some servers; verify parser flags"},

        {"id": "mistral-small4-22b", "family": "Mistral Small 4", "params_total": "22B", "params_active": "22B",
         "license": "Apache-2.0", "vram_gb_4bit": 13.0, "vram_gb_train": 20.0, "context_k": 128,
         "strengths": ["budget production", "European languages"],
         "traits": dict.fromkeys(TRAITS, True), "ollama_tag": "mistral-small:24b",
         "hf_repo": "mistralai/Mistral-Small-4-22B-Instruct", "verifiable_on_free_tier": False, "notes": ""},

        {"id": "llama4-scout", "family": "Llama 4", "params_total": "109B", "params_active": "17B MoE",
         "license": "Llama 4 Community License", "vram_gb_4bit": 60.0, "vram_gb_train": 80.0, "context_k": 10000,
         "strengths": ["huge context"],
         "traits": {"commercial": True, "redistribute_finetune": True,
                    "outputs_may_train_other_models": False, "osi_approved": False},
         "ollama_tag": "llama4:scout", "hf_repo": "meta-llama/Llama-4-Scout",
         "verifiable_on_free_tier": False,
         "notes": "KEPT IN THE LIST AS THE TRAP: not OSI-approved, MAU cap, and outputs may not be "
                   "used to train competing models — which disqualifies the distil-then-fine-tune path"},

        {"id": "smollm3-3b", "family": "SmolLM3", "params_total": "3B", "params_active": "3B",
         "license": "Apache-2.0", "vram_gb_4bit": 2.4, "vram_gb_train": 4.0, "context_k": 128,
         "strengths": ["trains in 20 minutes on a free T4", "good enough for narrow formatters"],
         "traits": dict.fromkeys(TRAITS, True), "ollama_tag": "smollm3:3b",
         "hf_repo": "HuggingFaceTB/SmolLM3-3B-Instruct", "verifiable_on_free_tier": True,
         "notes": "the right base when the task is formatting + constraint obedience, not reasoning"},
    ]


def load(path: str | Path | None = None) -> list[ModelSpec]:
    raw = default_registry()
    p = Path(path) if path else Path("config/models.json")
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        raw = data.get("models", raw)
    return [ModelSpec(**r) for r in raw]


def by_id(specs: list[ModelSpec], mid: str) -> ModelSpec | None:
    return next((s for s in specs if s.id == mid), None)


def score(spec: ModelSpec, vram_gb: float, need_finetune: bool) -> tuple[float, list[str]]:
    """Quality-per-VRAM, filtered by licence and by what the operator's hardware can hold."""
    reasons: list[str] = []
    ok, why = spec.licence_ok_for(need_training_on_outputs=need_finetune)
    if not ok:
        return -1.0, [f"DISQUALIFIED — {why}"]
    reasons.append(why)
    if spec.vram_gb_4bit > vram_gb:
        return -1.0, [f"DISQUALIFIED — needs ~{spec.vram_gb_4bit:g}GB at 4-bit, host has ~{vram_gb:g}GB"]
    reasons.append(f"fits: ~{spec.vram_gb_4bit:g}GB of {vram_gb:g}GB")
    if need_finetune and spec.train_cost(vram_gb):
        return -1.0, [f"DISQUALIFIED for tuning — inference fits (~{spec.vram_gb_4bit:g}GB) but QLoRA "
                      f"training needs ~{spec.vram_gb_train:g}GB and this host has {vram_gb:g}GB. "
                      "Inference and fine-tuning have different budgets; conflating them is the most "
                      "common way a 'free' tune turns into a failed job at minute nine."]
    headroom = (vram_gb - spec.vram_gb_4bit) / max(vram_gb, 1)
    base = {"27B": 96, "35B": 94, "26B": 93, "21B": 90, "22B": 88, "8B": 80, "3B": 68,
            "109B": 97}.get(spec.params_total, 82)
    score_v = base - headroom * 6 + (8 if spec.verifiable_on_free_tier and need_finetune else 0)
    if need_finetune:
        reasons.append(f"tunable here: ~{spec.vram_gb_train or round(spec.vram_gb_4bit*1.4,1):g}GB for "
                       "QLoRA, which fits this host")
    return round(score_v, 1), reasons


def detect_vram_gb() -> float:
    """Ask the host what it has, rather than trusting a config that drifts.
    torch may be absent (it should be — this package has no deps), so fall back to
    what the sandbox/OS reports for total memory."""
    try:
        import torch                                        # type: ignore
        if torch.cuda.is_available():
            return round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1)
    except Exception:
        pass
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.total",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=3)
        first = (r.stdout or "").strip().splitlines()
        if first and first[0].isdigit():
            return round(int(first[0]) / 1024, 1)
    except Exception:
        pass
    try:
        kb = int(Path("/proc/meminfo").read_text().splitlines()[0].split()[1])
        v = round(kb / 1024 / 1024, 1)
        return min(v, 8.0)          # system RAM as a heuristic for CPU inference; cap it
    except Exception:
        return 8.0


def select(specs: list[ModelSpec] | None = None, *, vram_gb: float | None = None,
           need_finetune: bool = False, top: int = 3) -> dict[str, Any]:
    specs = specs or load()
    vram = vram_gb if vram_gb is not None else detect_vram_gb()
    ranked = []
    for s in specs:
        sc, reasons = score(s, vram, need_finetune)
        ranked.append({"id": s.id, "family": s.family, "license": s.license,
                       "vram_gb_4bit": s.vram_gb_4bit, "vram_gb_train": s.vram_gb_train,
                       "trainable_here": not s.train_cost(vram), "score": sc, "reasons": reasons,
                       "ollama_tag": s.ollama_tag, "hf_repo": s.hf_repo,
                       "runnable_here": s.runnable_here, "notes": s.notes})
    runnable = [r for r in ranked if r["score"] >= 0]
    runnable.sort(key=lambda r: -r["score"])
    return {"as_of": AS_OF, "detected_vram_gb": vram, "need_finetune": need_finetune,
            "recommended": runnable[0] if runnable else None,
            "alternatives": runnable[1:top], "disqualified": [r for r in ranked if r["score"] < 0],
            "verdict": (runnable[0]["id"] if runnable else "none — no candidate fits this host")}


def resolve(spec: str, registry_path: str | Path | None = None) -> Backend:
    """`wf`-facing factory. Accepts a registry id (`qwen3.6:27b` style also allowed),
    an explicit `ollama:tag`, `openai:http://host:port/v1` or `cmd:...`."""
    specs = load(registry_path)
    if spec.startswith("openai:"):
        url = spec.split(":", 1)[1]
        return openai_compat(os.environ.get("WEALFORGE_OPENAI_MODEL", "local"), url)
    if spec.startswith("cmd:"):
        return Backend("cmd", spec.split(":", 1)[1])
    hit = by_id(specs, spec) or by_id(specs, spec.replace(":", "-", 1) if ":" in spec else spec)
    if hit:
        return ollama(hit.ollama_tag or hit.id, **({"temperature": float(t)} if (t := os.environ.get("WEALFORGE_TEMPERATURE")) else {}))
    if ":" in spec:
        return ollama(spec)
    return ollama(spec)


def recommend_markdown(vram_gb: float | None = None, need_finetune: bool = False) -> str:
    r = select(vram_gb=vram_gb, need_finetune=need_finetune)
    lines = [f"# Model recommendation — registry as of {r['as_of']}", "",
             f"Detected capacity: **~{r['detected_vram_gb']:g} GB**. "
             f"Fine-tune target requested: **{r['need_finetune']}**.", ""]
    if r["recommended"]:
        rec = r["recommended"]
        lines += [f"**Pick: `{rec['id']}`** — {rec['family']}, {rec['license']}, "
                  f"~{rec['vram_gb_4bit']:g} GB at 4-bit.", "",
                  "Reasoning:"] + [f"- {x}" for x in rec["reasons"]]
        if rec["notes"]:
            lines.append(f"- {rec['notes']}")
        lines += ["", "```bash", f"ollama pull {rec['ollama_tag']}",
                  f"export WEALFORGE_MODEL={rec['id']}", "```"]
    else:
        lines.append("No candidate fits this host. Options: run inference on a machine with more "
                     "VRAM, use a free-tier notebook for the fine-tune (see `training/`), or keep the "
                     "agent in offline mode — briefs, math and gates all still work.")
    if r["alternatives"]:
        lines += ["", "## Alternatives", "", "| model | license | ~VRAM | score |", "|---|---|---|---|"]
        lines += [f"| `{a['id']}` | {a['license']} | {a['vram_gb_4bit']:g} GB | {a['score']} |"
                  for a in r["alternatives"]]
    if r["disqualified"]:
        lines += ["", "## Excluded, and why that is a real constraint not a preference", ""]
        for d in r["disqualified"]:
            lines.append(f"- `{d['id']}` ({d['license']}): {'; '.join(d['reasons'])}")
    lines += ["", "_Every number above is a published approximation. Confirm the model card and the "
              "licence terms at download time — the licence clauses, not the benchmarks, are what can "
              "cost you._", ""]
    return "\n".join(lines) + "\n"
