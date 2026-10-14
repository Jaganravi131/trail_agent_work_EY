# Model recommendation — registry as of 2026-10-11

Detected capacity: **~3.8 GB**. Fine-tune target requested: **True**.

**Pick: `smollm3-3b`** — SmolLM3, Apache-2.0, ~2.4 GB at 4-bit.

Reasoning:
- licence permits commercial use, fine-tuning and reuse of outputs
- fits: ~2.4GB of 3.8GB
- tunable here: ~3.4GB for QLoRA, which fits this host
- the right base when the task is formatting + constraint obedience, not reasoning

```bash
ollama pull smollm3:3b
export WEALFORGE_MODEL=smollm3-3b
```

## Excluded, and why that is a real constraint not a preference

- `qwen3.6-27b` (Apache-2.0): DISQUALIFIED — needs ~15GB at 4-bit, host has ~3.8GB
- `qwen3.6-35b-a3b` (Apache-2.0): DISQUALIFIED — needs ~20GB at 4-bit, host has ~3.8GB
- `qwen3.6-8b` (Apache-2.0): DISQUALIFIED — needs ~5.5GB at 4-bit, host has ~3.8GB
- `gemma4-26b-a4b` (Apache-2.0): DISQUALIFIED — needs ~14GB at 4-bit, host has ~3.8GB
- `gpt-oss-20b` (Apache-2.0 + usage policy): DISQUALIFIED — needs ~16GB at 4-bit, host has ~3.8GB
- `mistral-small4-22b` (Apache-2.0): DISQUALIFIED — needs ~13GB at 4-bit, host has ~3.8GB
- `llama4-scout` (Llama 4 Community License): DISQUALIFIED — Llama 4 Community License forbids using model outputs to train other models — fine-tuning a successor on distils from it would breach the terms

_Every number above is a published approximation. Confirm the model card and the licence terms at download time — the licence clauses, not the benchmarks, are what can cost you._

