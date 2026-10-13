"""Training configs for the fine-tune — generated, not hand-written, so they can never
drift from the dataset that actually exists.

What lands in `training/`:

  unsloth_qlora.py     Colab / single-GPU QLoRA (4-bit base + LoRA) — the $0 path.
  colab-qlora.ipynb    the same thing as cells, for a browser with a free T4 attached.
  axolotl.yaml         multi-GPU / serious runs, `axolotl train training/axolotl.yaml`.
  llamafactory.yaml    the web-UI crowd.
  dpo_tune.py          preference tuning on draft→final pairs, if any exist.
  train.sh             local entry point that checks the prerequisites first.
  MANIFEST.json        dataset sha256s + base model + hyper-parameters: which adapter
                       came from which corpus, answerable a month later.
  README.md            cost, time, rollback, and the licence rule.

Nothing here runs training in this repo — there is no GPU in the sandbox and pretending
otherwise is how you ship a config that fails at minute nine. It emits the exact files,
prints the command, and the eval harness decides afterwards whether the adapter is real
progress: `wf eval --compare`.

The generated `.py` files are compiled at test time (`tests/test_finetune.py`) so a
template bug fails the suite instead of failing someone's 40-minute run.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from string import Template
from typing import Any

BASE_DEFAULT = "Qwen/Qwen3.6-8B"
TRAIN_SCRIPT_NAME = "unsloth_qlora.py"
DPO_MIN_PAIRS = 40


@dataclass
class Hyperparams:
    base_model: str = BASE_DEFAULT
    method: str = "QLoRA (4-bit NF4 base + LoRA adapters)"
    r: int = 16
    alpha: int = 16
    dropout: float = 0.0
    target_modules: tuple[str, ...] = ("q_proj", "k_proj", "v_proj", "o_proj",
                                      "gate_proj", "up_proj", "down_proj")
    max_seq_length: int = 2048
    batch_size: int = 2
    grad_accum: int = 4
    epochs: float = 3.0
    lr: str = "2e-4"
    optimizer: str = "paged_adamw_8bit"
    weight_decay: float = 0.01
    schedule: str = "cosine"
    warmup: float = 0.03
    dpo_beta: float = 0.1
    dpo_lr: str = "5e-5"
    chat_template: str = "chatml"
    notes: list[str] = field(default_factory=lambda: [
        "r=16/alpha=16 on all attention+MLP projections: the standard Unsloth starting point.",
        "epochs 3 on ~1k examples is the usual sweet spot; more epochs on a small corpus buys "
        "repetition of house tics, not capability.",
        "max_seq_length 2048 matches the dataset builder's section cap — a longer window costs "
        "VRAM you could spend on batch size.",
    ])

    @property
    def steps_per_epoch(self) -> int:
        return 0

    def est_minutes(self, n_examples: int) -> tuple[int, int]:
        """Wall-clock on a T4, including the weight download. A previous version of this
        printed '15–45 min' for a 74-example corpus, which is a guess dressed as a plan:
        the honest number is a few minutes of setup and about a minute of stepping."""
        import math
        steps = self.total_steps(n_examples)
        setup = 4                                    # image + ~5 GB base weights + tokenize
        return (max(1, int(setup + steps * 0.4 / 60)),
                max(2, math.ceil(setup + steps * 1.6 / 60)))

    def total_steps(self, n_examples: int) -> int:
        per = max(1, n_examples // max(1, self.batch_size * self.grad_accum))
        return max(10, int(per * self.epochs))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "missing"


# ------------------------------------------------------------------ bodies ----
_UNSLOTH = Template('''"""QLoRA fine-tune of $base on the WealthForge house-style corpus.

Run it where a GPU is: a free Colab/Kaggle T4 (16 GB) or any 24 GB card.
    python training/$script
or open training/colab-qlora.ipynb and press run. Nothing in this repo trains during
`wf run` — this file is the artefact, and `wf eval --compare` is the verdict.

Why these settings: a 4-bit NF4 base plus LoRA on every attention and MLP projection
keeps the trainable set tiny, which is what makes a T4 enough and makes rollback trivial
— delete the adapter directory, the base weights were never touched.
"""
import torch
from datasets import load_dataset
from trl import SFTConfig, SFTTrainer
from unsloth import FastLanguageModel
from unsloth.chat_templates import get_chat_template, train_on_responses_only

BASE = "$base"
DATASET_DIR = "$dataset_dir"
MAX_SEQ = $max_seq
CHAT_TEMPLATE = "$chat_template"

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=BASE,
    max_seq_length=MAX_SEQ,
    dtype=None,                       # bf16 on Ampere+, fp16 otherwise
    load_in_4bit=True,
)

model = FastLanguageModel.get_peft_model(
    model,
    r=$r,
    lora_alpha=$alpha,
    lora_dropout=$dropout,
    target_modules=$targets,
    bias="none",
    use_gradient_checkpointing="unsloth",     # Unsloth kernel; use True elsewhere
    random_state=3407,
)

model = get_chat_template(model, chat_template=CHAT_TEMPLATE)
tokenizer.chat_template = model.tokenizer.chat_template

# The corpus is ShareGPT `messages` rows (see dataset/dataset-card.md for provenance).
ds = load_dataset("json", data_files={
    "train": f"{DATASET_DIR}/sft-train.jsonl",
    "validation": f"{DATASET_DIR}/sft-val.jsonl",
})


def to_text(batch):
    return {"text": [tokenizer.apply_chat_template(m, tokenize=False, add_generation_prompt=False)
                     for m in batch["messages"]]}


train_ds = ds["train"].map(to_text, batched=True, remove_columns=ds["train"].column_names)
val_ds = ds["validation"].map(to_text, batched=True, remove_columns=ds["validation"].column_names) \\
    if "validation" in ds else None

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=train_ds,
    eval_dataset=val_ds,
    dataset_text_field="text",
    max_length=MAX_SEQ,
    args=SFTConfig(
        output_dir="out/finetune/qlora",
        per_device_train_batch_size=$batch,
        gradient_accumulation_steps=$grad_accum,
        num_train_epochs=$epochs,
        learning_rate=$lr,
        optim="$optimizer",
        weight_decay=$weight_decay,
        lr_scheduler_type="$schedule",
        warmup_ratio=$warmup,
        logging_steps=10,
        save_strategy="steps",
        save_steps=$save_steps,
        eval_strategy="steps" if val_ds is not None else "no",
        eval_steps=$save_steps,
        fp16=False,
        bf16=torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8,
        report_to="none",
    ),
)

# Train on the assistant spans only. Without this the model spends capacity learning to
# reproduce the prompt, which is the classic way a small fine-tune gets worse, not better.
trainer = train_on_responses_only(
    trainer,
    instruction_part="<|start_header_id|>user<|end_header_id|>\\n\\n",
    response_part="<|start_header_id|>assistant<|end_header_id|>\\n\\n",
)

trainer.train()
trainer.save_model("out/finetune/adapter")     # ~50 MB: the thing to version and share

# Optional: a merged checkpoint for runtimes that cannot load adapters (Ollama Modelfile
# merge, GGUF conversion). Skip it if you only ever serve via llama.cpp --lora.
model.save_pretrained_merged("out/finetune/merged", tokenizer, save_method="merged_16bit")

print("""
Next, in this order:
  1. serve the adapter:  ollama create wf-house -f training/Modelfile   (or llama.cpp --lora)
  2. ./wf eval --compare   # base vs tuned, on the task cases + adversarial battery
  3. only then flip WEALFORGE_MODEL.
A lower constraint-obedience score than the base model means the adapter is a regression.
""")
''')

_AXOLOTL = Template("""# Axolotl config — same corpus, for when there is more than one GPU.
#   pip install "axolotl[deepspeed]"
#   accelerate launch -m axolotl.cli.train training/axolotl.yaml
base_model: $base
model_type: AutoModelForCausalLM
tokenizer_type: AutoTokenizer

datasets:
  - path: dataset/sft-train.jsonl
    type: sharegpt
    conversation: chatml
val_set_size: 0.05

load_in_4bit: true
adapter: lora
lora_r: $r
lora_alpha: $alpha
lora_dropout: $dropout
lora_target_modules:
$targets_yaml

sequence_len: $max_seq
sample_packing: true
pad_to_sequence_len: true

trainer: sft
sft_trainer:
  epochs: $epochs
  batch_size: $batch
  gradient_accumulation_steps: $grad_accum
  learning_rate: $lr_float
  optimizer: adamw_bnb_8bit
  lr_scheduler: $schedule
  warmup_ratio: $warmup
  weight_decay: $weight_decay
  bf16: true
  gradient_checkpointing: true
  logging_steps: 10
  save_steps: $save_steps

output_dir: out/finetune/axolotl
hub_model_id: null
flash_attention: sdpa
""")

_LF = Template("""# LLaMA-Factory — the web-UI path (llamafactory-cli webui), same dataset.
### model
model_name_or_path: $base
quantization_bit: 4
template: $chat_template

### method
stage: sft
do_train: true
finetuning_type: lora
lora_rank: $r
lora_alpha: $alpha
lora_dropout: $dropout
lora_target: all

### dataset
dataset_dir: dataset
dataset: wf-sft
eval_dataset: wf-val
cutoff_len: $max_seq
preprocessing_num_workers: 2
max_samples: 100000
overwrite_cache: true

### train
num_train_epochs: $epochs
per_device_train_batch_size: $batch
gradient_accumulation_steps: $grad_accum
learning_rate: $lr_float
lr_scheduler_type: $schedule
warmup_ratio: $warmup
bf16: true
gradient_checkpointing: true
optim: paged_adamw_8bit
logging_steps: 10
save_steps: $save_steps

### output
output_dir: out/finetune/llamafactory
report_to: none

# dataset/ must also contain dataset_info.json for LLaMA-Factory to find the files:
#   {"wf-sft": {"file_name": "sft-train.jsonl", "formatting": "sharegpt",
#               "columns": {"messages": "messages"}},
#    "wf-val": {"file_name": "sft-val.jsonl", "formatting": "sharegpt",
#               "columns": {"messages": "messages"}}}
""")

_DPO = Template('''"""Preference tuning on draft→final pairs: the model learns what the human *changed*,
which is the only signal in this repo that actually encodes taste.

Corpus: dataset/dpo.jsonl — $n_pairs pairs, built by `wf dataset` from assets that went
model draft → human edit → gate-passing final.

    pip install "trl>=0.11" peft datasets accelerate
    python training/dpo_tune.py
"""
import torch
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import DPOConfig, DPOTrainer

BASE = "$base"
MIN_PAIRS = $min_pairs

raw = load_dataset("json", data_files={"pairs": "dataset/dpo.jsonl"})
keep = ("prompt", "chosen", "rejected")
ds = raw["pairs"].select_columns([c for c in raw["pairs"].column_names if c in keep])
if len(ds) < MIN_PAIRS:
    raise SystemExit(
        f"only {len(ds)} preference pairs — too few for DPO. Grow the corpus by publishing "
        f"assets (pairs come from real human edits, not synthetic ones), then re-run "
        f"`./wf dataset && ./wf finetune`.")

bnb = BitsAndBytesConfig(load_in_4bit=True,
                         bnb_4bit_compute_dtype=torch.bfloat16,
                         bnb_4bit_quant_type="nf4",
                         bnb_4bit_use_double_quant=True)
model = AutoModelForCausalLM.from_pretrained(BASE, quantization_config=bnb, device_map="auto")
tok = AutoTokenizer.from_pretrained(BASE)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token

trainer = DPOTrainer(
    model=model,
    args=DPOConfig(
        output_dir="out/finetune/dpo",
        beta=$beta,
        learning_rate=$dpo_lr_float,
        num_train_epochs=1,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        bf16=True,
        gradient_checkpointing=True,
        max_length=$max_seq,
        report_to="none",
    ),
    train_dataset=ds,
    peft_config=LoraConfig(r=$r, lora_alpha=$alpha, lora_dropout=$dropout,
                           target_modules=$targets, bias="none", task_type="CAUSAL_LM"),
    tokenizer=tok,
)
trainer.train()
trainer.save_model("out/finetune/dpo-adapter")
print("done — then ./wf eval --compare (a DPO run that lowers constraint obedience is a loss)")
''')

_SH = Template('''#!/usr/bin/env bash
# Pre-flight, then run whichever backend exists. Deliberately loud about what it checks,
# because "training failed" is usually "you have 3 GB of RAM" or "the dataset is 12 rows".
set -euo pipefail
cd "$(dirname "$0")/.."

need() {
  if command -v "$1" >/dev/null 2>&1; then echo "  ok      $1"; else echo "  MISSING $1 — $2"; exit 1; fi
}

echo "pre-flight"
[ -f dataset/sft-train.jsonl ] || { echo "  no dataset/sft-train.jsonl — run: ./wf dataset"; exit 1; }

python3 - <<'PY'
import json, pathlib, sys
p = pathlib.Path("dataset/stats.json")
s = json.loads(p.read_text()) if p.exists() else {}
rec = s.get("recommendation", {})
print(f"  dataset: {s.get('sft_examples', 0)} sft / {s.get('dpo_pairs', 0)} dpo pairs")
if not rec.get("fine_tune", True):
    print("  the dataset builder says DO NOT fine-tune yet —", rec.get("why", ""))
    sys.exit(4)
PY

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | sed 's/^/  gpu       /'
else
  echo "  no GPU visible here. Use a free-tier notebook: training/colab-qlora.ipynb"
  exit 3
fi
need python3 "python 3.10+"

backend="$${1:-unsloth}"
case "$backend" in
  unsloth)      need torch "pip install torch unsloth trl datasets"; python3 training/$script ;;
  axolotl)      need axolotl "pip install 'axolotl[deepspeed]'"; axolotl train training/axolotl.yaml ;;
  llamafactory) need llamafactory-cli "pip install llamafactory"; llamafactory-cli train training/llamafactory.yaml ;;
  dpo)          python3 training/dpo_tune.py ;;
  *) echo "usage: training/train.sh [unsloth|axolotl|llamafactory|dpo]"; exit 2 ;;
esac
''')

_MODELFILE = Template("""# Serve the tuned adapter as a first-class Ollama model.
#   ollama create wf-house -f training/Modelfile && ./wf eval --serve wf-house
FROM $base_gguf
ADAPTER out/finetune/adapter

PARAMETER temperature 0.4
PARAMETER num_ctx 8192
PARAMETER top_p 0.9
# Do not set a system here: wealforge/llm.py already sends the constraint block plus the
# voice card, and a second copy in the Modelfile is how the two drift apart.
""")


def _subs(hp: Hyperparams, n: int, save_steps: int, dataset_dir: str, script: str) -> dict[str, Any]:
    return {"base": hp.base_model, "script": script, "dataset_dir": dataset_dir,
            "chat_template": hp.chat_template, "max_seq": hp.max_seq_length,
            "r": hp.r, "alpha": hp.alpha, "dropout": hp.dropout,
            "targets": list(hp.target_modules),
            "targets_yaml": "\n".join(f"  - {m}" for m in hp.target_modules),
            "batch": hp.batch_size, "grad_accum": hp.grad_accum, "epochs": hp.epochs,
            "lr": hp.lr, "lr_float": f"{float(hp.lr):g}", "optimizer": hp.optimizer,
            "weight_decay": hp.weight_decay, "schedule": hp.schedule, "warmup": hp.warmup,
            "save_steps": save_steps, "n_pairs": n, "min_pairs": DPO_MIN_PAIRS,
            "beta": hp.dpo_beta, "dpo_lr_float": f"{float(hp.dpo_lr):g}",
            "base_gguf": "qwen3.6:8b"}


# ------------------------------------------------------------------ build ----
def build(dataset_dir: str | Path = "dataset", out_dir: str | Path = "training",
          hp: Hyperparams | None = None, stats: dict[str, Any] | None = None) -> dict[str, Path]:
    ds, out = Path(dataset_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    hp = hp or Hyperparams()
    if stats is None:
        sj = ds / "stats.json"
        stats = json.loads(sj.read_text(encoding="utf-8")) if sj.exists() else {}
    rec = stats.get("recommendation", {}) or {}
    n = int(stats.get("sft_examples", 0) or 0)
    nd = int(stats.get("dpo_pairs", 0) or 0)
    save_steps = max(10, hp.total_steps(n) // 6)

    paths = {
        "script": out / TRAIN_SCRIPT_NAME,
        "notebook": out / "colab-qlora.ipynb",
        "axolotl": out / "axolotl.yaml",
        "llamafactory": out / "llamafactory.yaml",
        "dpo": out / "dpo_tune.py",
        "modelfile": out / "Modelfile",
        "sh": out / "train.sh",
        "manifest": out / "MANIFEST.json",
        "readme": out / "README.md",
    }
    subs = _subs(hp, n, save_steps, str(ds), TRAIN_SCRIPT_NAME)
    paths["script"].write_text(_UNSLOTH.safe_substitute(subs), encoding="utf-8")
    paths["notebook"].write_text(_notebook(hp, n, subs), encoding="utf-8")
    paths["axolotl"].write_text(_AXOLOTL.safe_substitute(subs), encoding="utf-8")
    paths["llamafactory"].write_text(_LF.safe_substitute(subs), encoding="utf-8")
    paths["dpo"].write_text(_DPO.safe_substitute({**subs, "n_pairs": nd}), encoding="utf-8")
    paths["modelfile"].write_text(_MODELFILE.safe_substitute(subs), encoding="utf-8")
    paths["sh"].write_text(_SH.safe_substitute(subs), encoding="utf-8")
    paths["sh"].chmod(0o755)
    paths["manifest"].write_text(json.dumps({
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_model": hp.base_model, "method": hp.method, "chat_template": hp.chat_template,
        "dataset": {"dir": str(ds), "sft_train_sha256_16": _sha(ds / "sft-train.jsonl"),
                    "sft_val_sha256_16": _sha(ds / "sft-val.jsonl"),
                    "dpo_sha256_16": _sha(ds / "dpo.jsonl"),
                    "sft_examples": n, "dpo_pairs": nd},
        "hyperparameters": {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(hp).items()},
        "provenance_rule": "corpus = gate-passing, operator-approved repo assets only "
                           "(see dataset/dataset-card.md). Do not add scraped data or outputs from "
                           "models whose licence bars training on them.",
        "fine_tune_recommended": bool(rec.get("fine_tune", False)),
        "recommendation_note": rec.get("why", "no dataset stats found — run `wf dataset` first"),
        "est_total_steps": hp.total_steps(n),
    }, indent=2) + "\n", encoding="utf-8")
    paths["readme"].write_text(readme(hp, n, nd, rec, paths), encoding="utf-8")
    return paths


def _notebook(hp: Hyperparams, n: int, subs: dict[str, Any]) -> str:
    lo, hi = hp.est_minutes(n)
    body = _UNSLOTH.safe_substitute({**subs, "save_steps": subs["save_steps"]})
    start = body.index("import torch")
    end = body.index("trainer.train()")
    cells: list[dict[str, Any]] = [
        {"t": "markdown", "s": "### WealthForge QLoRA on a free T4\n\n"
                               f"Base `{hp.base_model}` · {n} SFT examples · 4-bit + LoRA r={hp.r}. "
                               "Runtime ▸ Change type ▸ T4 (free). Expect roughly "
                               f"**{lo}–{hi} min** for {hp.epochs:g} epochs at "
                               f"batch {hp.batch_size}×{hp.grad_accum}, under 8 GB VRAM. "
                               "Upload `dataset/` (zip is fine) to `/content` before running cell 3."},
        {"t": "code", "s": "!pip -q install \"unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git\"\n"
                          "!pip -q --no-deps trl peft accelerate bitsandbytes datasets"},
        {"t": "code", "s": "!ls dataset | head -20 || echo 'no dataset/ — upload dataset.zip and: !unzip dataset.zip'"},
        {"t": "code", "s": "#@title model + dataset\n" + body[start:end].rstrip()},
        {"t": "code", "s": "trainer.train()"},
        {"t": "markdown", "s": "### Save, then hand it back to the agent\n\nThe adapter is tens of MB. "
                               "The base weights were never touched, so rollback is deleting a directory — "
                               "that asymmetry is the whole reason LoRA is the default for a $0 operation."},
        {"t": "code", "s": "trainer.save_model('/content/adapter')\n"
                           "model.save_pretrained_merged('/content/merged', tokenizer, save_method='merged_16bit')\n"
                           "!zip -qr /content/adapter.zip /content/adapter"},
        {"t": "markdown", "s": "Then serve it (`ollama create wf-house -f training/Modelfile`, or llama.cpp "
                               "`--lora`) and run `./wf eval --compare` **before** changing any default. A "
                               "fine-tune that scores lower on constraint obedience than the base model is a "
                               "regression, not progress."},
    ]
    nb = {"cells": [{"cell_type": c["t"], "metadata": {},
                     "source": c["s"].splitlines(keepends=True),
                     **({"execution_count": None, "outputs": []} if c["t"] == "code" else {})}
                    for c in cells],
          "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
                       "accelerator": "GPU", "colab": {"gpuType": "T4"}},
          "nbformat": 4, "nbformat_minor": 0}
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"


def readme(hp: Hyperparams, n: int, nd: int, rec: dict[str, Any], paths: dict[str, Path]) -> str:
    L = ["# Fine-tuning the house-style adapter", "",
         "Generated by `wf finetune` — edit `wealforge/finetune.py`, not these files.", "",
         f"- base: `{hp.base_model}` · method: {hp.method}",
         f"- corpus: {n} SFT examples, {nd} preference pairs (provenance: `../dataset/dataset-card.md`)",
         f"- schedule: ~{hp.total_steps(n)} steps at batch {hp.batch_size}×{hp.grad_accum}, "
         f"{hp.epochs:g} epochs", "",
         "## The cost line, in full", "",
         "| item | value |", "|---|---|",
         "| money | **$0** — free-tier T4 (16 GB) or your own GPU |",
         f"| time | {hp.est_minutes(n)[0]}–{hp.est_minutes(n)[1]} min on a T4 for {n} examples "
         f"({hp.total_steps(n)} steps; includes the base-weight download) |",
         f"| VRAM | ~6–8 GB at 4-bit, r={hp.r}, batch {hp.batch_size}×{hp.grad_accum} |",
         "| rollback | delete the adapter directory; base weights untouched |",
         "| risk | style tics amplified; capability regressions — caught by `wf eval --compare` |", "",
         "## Run it", "", "```bash",
         "./wf dataset                       # rebuild the corpus from gate-passing approved assets",
         "./wf finetune                      # regenerate this directory",
         "training/train.sh unsloth          # pre-flight + local run, or open colab-qlora.ipynb",
         "./wf eval --compare                # base vs tuned; the switch is earned, not assumed",
         "```", "",
         "## Which file for which hardware", "",
         "| you have | use | why |", "|---|---|---|",
         "| free Colab/Kaggle T4 (16 GB) | `colab-qlora.ipynb` | 4-bit fits, zero setup |",
         "| 24 GB consumer card | `unsloth_qlora.py` | same recipe, bigger batches |",
         "| multi-GPU / want deepspeed | `axolotl.yaml` | config-first, reproducible |",
         "| prefer a UI | `llamafactory.yaml` | `llamafactory-cli webui` |",
         f"| fewer than {DPO_MIN_PAIRS} preference pairs | skip DPO | `dpo_tune.py` refuses below that |", "",
         "## Do not skip", "",
         "1. **Licence before benchmark.** The corpus is gate-passed work the operator owns, so training "
         "on it is clean. Do not add outputs scraped from models whose terms forbid using their outputs to "
         "train other models (the Llama 4-family licence and several 'modified MIT' releases do exactly "
         "that) — `wf model --select` filters those out for this reason, not as a preference.",
         "2. **Eval the adapter, don't admire it.** `wf eval` measures constraint obedience and "
         "specificity. Fixing prose style while quietly breaking instruction-following is a net loss, and "
         "only the battery shows it.",
         "3. **Small corpus = small claims.** This tunes voice and format. It cannot add facts, and it will "
         "faithfully amplify whatever tic appears thirty times in the training data.", "",
         "## What the dataset builder recommends right now", ""]
    if rec:
        L += [f"- fine_tune: **{'yes' if rec.get('fine_tune') else 'not yet'}** — {rec.get('why', '')}"]
        L += [f"  - instead: {x}" for x in (rec.get("do_instead") or [])]
        for k in ("base_model", "method", "expect", "eval_required", "revisit_when"):
            if rec.get(k):
                L.append(f"  - {k}: {rec[k]}")
    else:
        L.append("_no dataset stats found — run `./wf dataset` first_")
    L += ["", "## Files here", ""] + [f"- `{p.name}`" for p in paths.values()]
    L += ["", "_Hyper-parameter rationale lives in `wealforge/finetune.py::Hyperparams` so a change is "
          "a commit, not a memory._"]
    return "\n".join(L) + "\n"
