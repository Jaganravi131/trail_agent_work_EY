"""Preference tuning on draft→final pairs: the model learns what the human *changed*,
which is the only signal in this repo that actually encodes taste.

Corpus: dataset/dpo.jsonl — 0 pairs, built by `wf dataset` from assets that went
model draft → human edit → gate-passing final.

    pip install "trl>=0.11" peft datasets accelerate
    python training/dpo_tune.py
"""
import torch
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import DPOConfig, DPOTrainer

BASE = "qwen3.6-8b"
MIN_PAIRS = 40

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
        beta=0.1,
        learning_rate=5e-05,
        num_train_epochs=1,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        bf16=True,
        gradient_checkpointing=True,
        max_length=2048,
        report_to="none",
    ),
    train_dataset=ds,
    peft_config=LoraConfig(r=16, lora_alpha=16, lora_dropout=0.0,
                           target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'], bias="none", task_type="CAUSAL_LM"),
    tokenizer=tok,
)
trainer.train()
trainer.save_model("out/finetune/dpo-adapter")
print("done — then ./wf eval --compare (a DPO run that lowers constraint obedience is a loss)")
