"""QLoRA fine-tune of qwen3.6-8b on the WealthForge house-style corpus.

Run it where a GPU is: a free Colab/Kaggle T4 (16 GB) or any 24 GB card.
    python training/unsloth_qlora.py
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

BASE = "qwen3.6-8b"
DATASET_DIR = "dataset"
MAX_SEQ = 2048
CHAT_TEMPLATE = "chatml"

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=BASE,
    max_seq_length=MAX_SEQ,
    dtype=None,                       # bf16 on Ampere+, fp16 otherwise
    load_in_4bit=True,
)

model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    lora_alpha=16,
    lora_dropout=0.0,
    target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'],
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
val_ds = ds["validation"].map(to_text, batched=True, remove_columns=ds["validation"].column_names) \
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
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        num_train_epochs=3.0,
        learning_rate=2e-4,
        optim="paged_adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=10,
        save_strategy="steps",
        save_steps=10,
        eval_strategy="steps" if val_ds is not None else "no",
        eval_steps=10,
        fp16=False,
        bf16=torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8,
        report_to="none",
    ),
)

# Train on the assistant spans only. Without this the model spends capacity learning to
# reproduce the prompt, which is the classic way a small fine-tune gets worse, not better.
trainer = train_on_responses_only(
    trainer,
    instruction_part="<|start_header_id|>user<|end_header_id|>\n\n",
    response_part="<|start_header_id|>assistant<|end_header_id|>\n\n",
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
