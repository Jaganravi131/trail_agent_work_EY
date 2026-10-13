# Model integration and fine-tuning

How the agent generates, which model it generates with, and what it takes to make that
model better at *this* job. Read this with `wealforge/README.md`, which covers the loop
itself.

```
 idea pool → scan → rubric → pick → brief ──────────────┐
                                                        ├─→ llm.Provider (open weights first)
                                          providers.py  │     ├─ Ollama          /api/chat
                                          modelhub.py   │     ├─ OpenAI-compat   /v1/chat/completions
                                          config/models │     ├─ cmd:<prog>      stdin→stdout
                                          config/voice  │     └─ hosted (opt-in, WEALFORGE_ALLOW_PAID=1)
                                                        ↓
                                     draft → gate → repair (≤3) → validate → package
                                                        ↓
                                     dataset.py → dataset/*.jsonl → finetune.py → training/*
                                                        ↓
                                     evalkit.py + config/eval_prompts.json → wf eval --compare
```

## 1. Which model, and why that is a licence question first

`config/models.json` is the registry. `./wf model --select` ranks it and writes
`00-strategy/decisions/model-recommendation.md`. Ranking order is deliberate:

1. **Licence properties.** A zero-capital operation ships commercially, may need to
   redistribute an adapter, and — because this repo builds a fine-tuning corpus from
   model output — needs a base whose terms do not forbid training on its outputs.
   Families that forbid that (the Llama 4 Community License is the standing example, and
   it also carries an MAU cap and is not OSI-approved) are **disqualified as a fine-tune
   base**, however good their benchmarks. That is recorded in the recommendation output,
   not hidden in a footnote.
2. **Hardware fit, twice over.** `vram_gb_4bit` for inference and `vram_gb_train` for
   QLoRA are separate numbers, and conflating them is the most common way a "free"
   fine-tune dies at minute nine: a 27B model *runs* in ~15 GB and cannot be *trained* in
   16 GB. `./wf model --select --for-finetune` answers the second question only.
3. **Quality per GB**, then notes.

Detected capacity comes from the host (`nvidia-smi`, else `/proc/meminfo`), not from a
config that drifts. As of `2026-10-11` the shortlist is Apache-2.0/MIT families —
Qwen3.6 (dense 27B for 24 GB hosts, 35B-A3B for throughput, **8B as the tune base**),
Gemma 4 26B-A4B, gpt-oss-20b, Mistral Small 4, SmolLM3-3B for tiny narrow jobs — plus
GLM/DeepSeek-class MIT releases where an operator has the hardware. Every figure in that
file is a published approximation; confirm the model card at download time.

## 2. How generation actually works

`wealforge/llm.py::Provider.detect()` tries, in order: `WEALFORGE_MODEL` (a registry id,
`ollama:tag`, `openai:<base_url>` or `cmd:<prog>`) → `WEALFORGE_LLM_CMD` → a live Ollama
(probed with `GET /api/tags`, so an empty server is reported as "pull a model", not as a
working provider) → `WEALFORGE_OPENAI_BASE_URL` → hosted providers **only if
`WEALFORGE_ALLOW_PAID=1`**. Nothing found ⇒ `mode="offline"`, which is a supported mode:
briefs, prompts, gating, math, dataset and eval all still run, and `wf work` writes
unmistakable `TODO(human)` placeholders instead of pretending it drafted.

Two things every generation call carries, and one thing it never gets to decide:

* the **constraint block** (`SYSTEM_RULES`): no invented credentials, no clinical advice,
  no dosing, no income promises, no scraping/review manipulation, no copied text, no
  cliché openers, end with a next action, output only the requested section;
* the **voice card** (`config/voice.json`, defaulting to excerpts of the repo's own
  approved prose) — the $0 substitute for a style fine-tune;
* and no authority. Model output is a *candidate*: `pipeline.draft_section` gates every
  section, and only a section that passes becomes an asset stage advance.

### The repair loop, and why it is bounded

`wealforge/pipeline.py` feeds gate findings back verbatim — rule id, line, the gate's own
`fix` text, and the offending excerpt — then re-gates. Default cap: **3 attempts**, and
the *least-bad* attempt is kept rather than the last. Two reasons: uncapped
self-correction on a small local model mostly re-produces the same violation in different
words, and an unbounded loop in a weekly cron is an outage. If the cap is hit, the file is
still written, but it is marked `needs_human` in `WORK-LOG.md`, the ledger records the
residual rules, and **the asset row does not advance past `drafted`**. A backward move is
allowed only when the gate now fails — leaving an asset labelled `gated` after its rebuilt
draft broke a rule would be worse than any stage skip, because `gated` is the label the
publish checklist trusts.

Structured slots (KDP metadata) go through JSON mode with a tolerant parser
(`providers.extract_json` handles fences and prose wrappers) and then
`wealforge/validate.py`: 7 keywords ≤49 chars, ≤2 categories, title/subtitle ≤200,
description ≤4000, no trademarks, no credential strings, no outcome or treatment claims.
A field that fails validation is reported; it is never quietly filled.

## 3. Fine-tuning: the honest version

**Do it only when the corpus says so.** `./wf dataset` builds the corpus and prints a
recommendation with a floor (`MIN_SFT_EXAMPLES = 150`, `MIN_DPO_PAIRS = 40`). What it
prints right now (it moves with every asset, so treat the numbers as today's, not the
mechanism's):

```
fine-tune recommendation: NOT YET — 92 SFT examples and 0 preference pairs is below
the floor (150/40). A LoRA on this would mostly learn to imitate formatting while
degrading instruction-following, and you would have no way to tell the difference at
40 examples.
```

Two things that number means, and one it doesn't. It means the corpus is 92 gate-passing
sections, 58 short of the floor. It means DPO is empty because preference pairs need a
model draft *and* the human-corrected final for the same section, and nothing has been
through that loop yet — that is the reason the DPO half is 0, not a filter to relax. It
does **not** mean the training plan is premature in kind: `wf finetune` still writes
`training/`, because the plan is the deliverable and the numbers are what gate it.

That is the correct answer, and shipping a training run that the data does not support
would be the kind of move this whole repo exists to prevent.

**What the corpus is.** `wealforge/dataset.py` splits committed, **gate-passing** assets
into sections and writes ShareGPT rows. The include list is `01-book/chapters/*.md`
and `04-seo/articles/*.md`, deliberately: `04-seo/pages/` holds the about and
editorial-policy copy, which is a legal surface rather than a style exemplar, and mixing
it in would teach an adapter to write disclaimers instead of sections. Eligibility fails closed: a file that trips the
gate is excluded and counted (`dataset/excluded.json`, with rule ids), and if
`config/approval.json` has entries then only content-hash-matching, operator-signed files
are admitted — edit a file after sign-off and it drops out of the corpus rather than
training on unreviewed text. A *book* is gated as a bundle, because its disclaimer lives
in front matter; grading chapters one by one would reject all of them for a safeguard that
is present.

**The part almost nobody notices they have.** Every asset here went
`model draft → human edit → final`, so `dpo.jsonl` pairs the earlier draft (rejected) with
the published text (chosen) — a preference dataset that costs nothing because it is a
by-product of doing the work. `training/dpo_tune.py` refuses below 40 pairs.

**What a LoRA here can and cannot do.** It can move voice, section structure, formatting
and constraint obedience. It cannot add facts, and it will faithfully amplify any tic that
recurs in the corpus. It is per-niche: an adapter tuned on shift-work recovery is not a
general writer.

## 4. Running the tune

`./wf finetune` regenerates `training/` from the dataset that exists (never from a
guessed size):

| file | for |
|---|---|
| `colab-qlora.ipynb` | free-tier T4 in a browser — upload `dataset/`, press run |
| `unsloth_qlora.py` | any single GPU, 4-bit base + LoRA r=16, all proj layers, `train_on_responses_only` |
| `axolotl.yaml` | multi-GPU / config-first reproducibility |
| `llamafactory.yaml` | the web-UI path |
| `dpo_tune.py` | preference tuning on draft→final pairs |
| `Modelfile` | serving the adapter as `wf-house` in Ollama |
| `train.sh` | pre-flight (dataset size, GPU presence, backend installed) then dispatch |
| `MANIFEST.json` | dataset sha256s + hyper-parameters: which adapter came from which corpus |

Cost at the current corpus: **$0 and single-digit minutes** on a free T4 (the estimate is
computed from step count, not vibes). The base weights are never touched, so rollback is
deleting a directory — that asymmetry is why LoRA is the default for a $0 operation.

## 5. Proving it helped

A fine-tune is a hypothesis. `./wf eval` is the test, in three layers:

1. **task cases** (`config/eval_prompts.json`, 8 golden prompts with word bands and
   `must_contain`/`must_not` assertions) — do the real jobs get done;
2. **the adversarial battery** (8 requests an income-chasing model *wants* to satisfy:
   income guarantees, fake credentials, undisclosed promo posts, review swaps, scraping, <!-- gate:allow R1 -->
   melatonin protocols, paraphrasing a copyrighted book, invented testimonials) — a <!-- gate:allow M4 -->
   candidate must refuse **and** stay useful. A model that stonewalls scores badly too:
   *This list names prohibited conduct in order to test that the model refuses it, so the two
   lines carry a per-line `gate:allow` marker — the documented suppression, counted in the
   report rather than worked around by rewriting the prose. It is only ever this file's own
   test inventory: the same marker in a reader-facing asset would be a cover-up, and
   `--summary` prints how many rules were suppressed and where.*
   both directions are tested, and a correct refusal is verified never to trip the
   harness;
3. **style/specificity grades** (`evalkit.grade_text`) — numbers per 100 words, sentence
   length variation, second-person ratio, cliché hits, a real next action, plus the gate's
   own counts. The committed portfolio currently averages **87.4/100** with zero gate
   findings, and that check runs in CI without a model.

Then `./wf eval --compare BASE.json CURRENT.json` prints regressions and wins; a
non-empty regression list — and any adversarial case that flipped from refusal to
compliance — means keep the old model. The switch is earned.

## 6. What is *not* true in this sandbox

The verification environment for this code has **no GPU, 3 GB of RAM, and no route to
`huggingface.co` or an ollama registry**. Weights were not downloaded and nothing was
trained here. What is real and tested:

* the HTTP clients, `--model` resolution, probes, retries, JSON mode, token accounting —
  against a **stub server on 127.0.0.1 that speaks the actual Ollama and OpenAI wire
  formats** (`tests/test_models.py`, over real sockets);
* the gate-repair loop, including a demo where a violation is planted on attempt 1 and
  the loop converges on attempt 2 (`tools/model_stub.py --bad-first`);
* dataset building, dedupe, bundle gating, fail-closed approvals and the recommendation
  floor; the config emitters (generated Python is `compile()`d in tests, `train.sh`
  parses with `bash -n`, the notebook is valid nbformat);
* the eval harness and its own anti-false-positive tests.

What you must run yourself, in order, on a machine with a GPU:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3.6:8b                      # or the smallest registry entry that fits you
export WEALFORGE_MODEL=qwen3.6:8b
./wf model                                  # probe: server up, model pulled, reply OK
./wf work --verbose                         # draft → gate → repair → validate
./wf eval --out 00-strategy/decisions/eval-base.json --json   # baseline, before any tune
./wf dataset && ./wf finetune                # corpus + configs, with the recommendation
# on a free T4: upload dataset/, run training/colab-qlora.ipynb
./wf model --select --for-finetune           # registry sanity for the tuning step
./wf approve 04-seo/articles --by "your name" --config config/approval.json   # tighten to signed-off text
```

## 7. Deliberate omissions

No RAG. No vector store. No llama.cpp build scripts, no CUDA pinning, no
`requirements.txt` for the training path (the training files list their own installs).
No automatic weight download, no auto-training inside `wf run`, and no "fine-tune
enabled" flag that quietly changes output quality between runs: the model in use is
printed in every loop report, and switching it is a human decision backed by
`wf eval --compare`.
