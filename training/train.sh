#!/usr/bin/env bash
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

backend="${1:-unsloth}"
case "$backend" in
  unsloth)      need torch "pip install torch unsloth trl datasets"; python3 training/unsloth_qlora.py ;;
  axolotl)      need axolotl "pip install 'axolotl[deepspeed]'"; axolotl train training/axolotl.yaml ;;
  llamafactory) need llamafactory-cli "pip install llamafactory"; llamafactory-cli train training/llamafactory.yaml ;;
  dpo)          python3 training/dpo_tune.py ;;
  *) echo "usage: training/train.sh [unsloth|axolotl|llamafactory|dpo]"; exit 2 ;;
esac
