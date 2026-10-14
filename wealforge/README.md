# WealthForge agent

An autonomous *asset* agent: it scans opportunities, scores them, picks the best
ones, drafts prose with a **local open-weight model**, blocks the mistakes that get
accounts killed, prices by arithmetic, builds its own fine-tuning corpus, emits the
training configs, and grades the result. Then it tells the operator the one thing
to try next. It runs on stdlib Python with no dependencies, no server and no paid
tool, and it has no ability to spend money.

```bash
./wf score                 # pre-screen + score config/scan_seed.json → 00-strategy/decisions/
./wf pick --build          # select top N, write briefs + KDP backend + cover brief + calendar
./wf gate 01-book --bundle # compliance/integrity gate (exit 1 on errors)
./wf price --words 11262   # royalty bands, delivery fee, print cost, scenarios, traffic math
./wf report                # trends + ONE recommended experiment from the tracker CSV
./wf today                 # this day's human actions, from the 90-day schedule
./wf run                   # the whole loop, unattended-safe (what CI runs weekly)
./wf status; ./wf log      # what the ledger knows
python3 -m unittest discover -s tests -q     # 263 tests, no network
bash tools/ci/planner-check.sh               # runs the free tool's JS in node: 9 rota patterns, .ics, error paths

# model layer — see wealforge/FINE-TUNING.md
./wf model --select --for-finetune  # which open-weight model, ranked by licence fit then VRAM
./wf model                 # probe: server up? model pulled? does it answer?
./wf work --verbose         # draft → gate → repair → validate, for every selected idea
./wf dataset               # SFT + preference corpus from gate-passing approved assets, or "don't yet"
./wf finetune              # QLoRA / Colab / Axolotl / LLaMA-Factory configs into training/
./wf eval --self-check     # style + gate grades on committed assets; no model, no network
./wf eval --compare A.json B.json   # base vs fine-tuned: regressions, not vibes
./wf site [--check]          # build docs/ from 04-seo + 03-tool: clean URLs, disclosures, link graph
./wf book [--check]          # 01-book/chapters → EPUB3 with the standard library; refuses a stale
                             # manuscript, a placeholder in a paid file, a cover that lies about its size
./wf approve 04-seo/articles --by "name" --config config/approval.json  # sign off corpus input
./wf validate 02-kdp/kdp-metadata.json    # KDP field limits: 7×49 chars, ≤200 title, no claims —
                             # the same file ./wf book titles the ebook from
```

## Architecture

```
        ┌──────────────────────── scan.py ────────────────────────┐
 seed → │ pre-screen (17 disqualifiers) → caution tags → rubric    │
        └───────────────────────────────┬─────────────────────────┘
                                        ▼
   rubric.py   weighted axes from config/rubric.json, normalised to 100
                                        │  top_n whose total ≥ min_total (may be fewer)
                                        ▼
   store.py    sqlite ledger: ideas · assets · decisions · gates · events · metrics
               stage machine: proposed→selected→briefed→drafted→gated→packaged→ready
               ── an asset cannot pass `gated` without a passing gate report, and
                  cannot reach `ready` without a publish checklist. Enforced, not advised.
               ── a run that drafts *and* gates in one pass walks the ladder rung by rung
                  (briefed→drafted→gated) instead of jumping: every rung re-checks its own
                  evidence and lands as its own ledger event, so the audit trail shows what
                  was earned, not just where the row ended up.
                                        │
              ┌─────────────────────────┼──────────────────────────┐
              ▼                         ▼                          ▼
      generator.py                kdp.py + validate.py      llm.py → providers.py
      briefs, cover spec,         royalty bands, delivery    registry-aware detection,
      backend sheet, sales        fee, print cost, KENP,     Ollama / OpenAI-compat /
      page, calendar              scenarios, breakeven,      cmd / hosted(opt-in),
              │                   field limits               probe + voice card + JSON mode
              │                   traffic                       │
              ▼                                                  ▼
      pipeline.py  draft → gate → repair(≤3) → validate → package
              │                                                  │
              └──────────── gate.py (15 rules) ─────────────────┘

 dataset.py    sections of gate-passing approved assets → SFT ShareGPT + DPO pairs,
               with a "don't fine-tune yet" floor it is allowed to enforce
 finetune.py   unsloth/colab/axolotl/llama-factory/dpo configs + MANIFEST.json hashes
 evalkit.py   task cases + adversarial battery + style metrics + base-vs-tuned compare
 site.py      04-seo + 03-tool → docs/: clean URLs, affiliate disclosure injected, every
              internal link resolved, OG/Twitter cards + JSON-LD + RSS when the domain exists,
              no invented domain, byte-identical rebuilds
 book.py     01-book/chapters → 01-book/build/*.epub: EPUB3 built with the standard library,
              listing metadata read from 02-kdp/kdp-metadata.json, every part XML-validated,
              cover pixels checked against the filename, byte-identical rebuilds, and a
              manuscript aggregate that fails the build when it stops matching the chapters
 modelhub.py  config/models.json registry: licence traits filter first, then VRAM
                             │  reader assets: safeguards required
                             │  internal docs: integrity rules only
                             │  bundle mode: a book is one work
                             ▼
                    metrics.py  trends → diagnosis tree → exactly one experiment
                    roadmap.py  day-N task list + stale-stage warnings
```

## The six decisions worth defending

**1. The gate is the product, not the generator.** Anyone can produce text now. What kills a zero-capital operation is a fabricated credential on a health title, an undisclosed affiliate link, an income promise, a scraping shortcut, a review-swap ring. Those are mechanical, so they're checked mechanically, in CI, before anything ships. Presence rules *and* required-safeguard rules, because "you mentioned money but didn't disclose" is the class of bug that only the second kind catches.

**2. Suppression is a first-class feature.** A gate that flags `never buy reviews` as review manipulation gets switched off inside a week — which is worse than never building it. So: negation context is respected, `<!-- gate:allow R1 -->` suppresses one rule on one line, rules can be downgraded or disabled in `config/gate_rules.json`, and every suppression is counted in `stats` so a silently-muzzled rule is still visible in the report — `--summary` ends with a *Let through on purpose* section naming each allowed rule id and file#line. (The marker quoted in this very paragraph suppresses this line, and the report says so: that is the feature, not a bug — a suppression that only the author can see is how rules get muzzled.)

**3. A book is one reader-facing work.** Per-file disclaimers would mean repeating the same paragraph in all 11 chapters. `--bundle` runs safeguard rules against the concatenation and claim rules per file, and merged findings keep their `source` filename so the verdict still points at the chapter to fix.

**4. The model is never the judge.** Generation defaults to open weights on the operator's own hardware — $0 per token, and a 2026 8B model writes a 700-word section fine. Hosted providers stay wired but require `WEALFORGE_ALLOW_PAID=1` on top of the key, because a weekly cron holding a key is how a $0 project gets an invoice. Whatever answers, its output is a *candidate*: `pipeline.draft_section` gates it, feeds the findings (rule id, line, the gate's own fix text, the offending excerpt) back for at most 3 attempts, keeps the least-bad version, and if errors remain marks the section `needs_human` and refuses the stage advance. Fine-tuning is gated the same way — by data volume (`dataset.recommend()`, floor 150 SFT / 40 DPO) and then by `wf eval --compare`, so an adapter that lowers constraint obedience is called a regression instead of shipped.

**5. A checker that cries wolf gets muted, so wolf-crying is a bug.** This applies to the eval harness as much as the gate: narration *about* a risk is not the risk, prose *quoting* a bad credential inside `**Not** "…"` is not a claim, `~~struck-through~~` rejected ideas are not live advice, a book's chapters are not individually missing the disclaimer that sits in its front matter, and a refusal that quotes the request ("I can't write \"you'll earn $500\"") is a *pass*, and a `Not allowed anywhere:` list whose bullets quote the forbidden wording is the rule, not the breach — a bullet inherits its lead-in's polarity, with the lead-in having to carry both the denial and the colon so the guard is never blanket. Each of those was a real false alarm in this repo's own files; each is now a test in `tests/test_models.py` in both directions — must-fire and must-pass — because a harness that only tests the catching half drifts into noise.

**6. Offline is a mode, not a failure.** With no provider configured, `--draft` writes
`draft/HOW-TO.md` explaining the two free paths and *does not emit prose*; the assembled
`ASSET-DRAFT.md` opens with a blockquote saying it is not drafted and naming the two ways to
fix that, because the file a human opens is the file that has to tell the truth. A
`--emit-prompt` / `--from-file` round trip keeps provenance in the ledger. Nothing pretends.

## Extension points

| Want | Change |
|---|---|
| Different weights | `config/rubric.json` — totals renormalise; the ledger stores `rubric_version` per decision |
| A new disqualifier | `scan.REJECT` tuple (tag, regex, why). It shows up in the scorecard and the ledger |
| A new gate rule | `config/gate_rules.json` → `{"rules":[{"id":"Z1",...}]}`. No code change |
| A rule you disagree with | override `severity`, or `disable` it — and say why in the commit |
| A different marketplace | `kdp.py` is one module; the plan/price/checklist generator reads it |
| Prose generation | `ollama pull qwen3.6:8b` + `WEALFORGE_MODEL=qwen3.6:8b`, or set `WEALFORGE_LLM_CMD` to any command that reads stdin → writes stdout |
| A different model | `config/models.json` — add `{id, license, traits, vram_gb_4bit, vram_gb_train, ollama_tag}`; ranking, probes and the recommendation page follow |
| A licence policy | `modelhub.TRAITS` + `ModelSpec.licence_ok_for()` — this is where "may we train on its outputs?" is decided |
| More eval coverage | `config/eval_prompts.json` (assertions, no code) and `wealforge/gate.py` rules; the eval set is meant to grow with the gate |
| Voice / style | `config/voice.json` — the few-shot card injected into every call; cheaper than a fine-tune and it reverses |
| Training recipe | `finetune.Hyperparams` — every file in `training/` is generated from it plus the real corpus size |

## Running it against a real model

```bash
ollama pull qwen3.6:8b && export WEALFORGE_MODEL=qwen3.6:8b
./wf model          # probe before trusting it
./wf work --verbose # draft → gate → repair → validate
```

No GPU and no model handy? `python3 tools/model_stub.py --port 11434 --bad-first` serves
real text over the real Ollama wire format, so the whole pipeline — including the repair
loop, which has something to repair — can be exercised in seconds. `tests/test_models.py`
uses the same trick: the HTTP path is tested over sockets against a stub, never against the
internet. CI calls the same files it runs (`bash tools/ci/wire-path.sh`,
`bash tools/ci/quality-floor.sh`, `bash tools/ci/planner-check.sh`) rather than keeping a private
copy of the logic in YAML, because a check you can only run on someone else's machine cannot be
debugged here — and the stub picks its reply by heading overlap with ties broken by the text
itself, never by `Path.glob` order, so a fixture cannot fail a build for filesystem reasons.
The third script is different in kind: it evaluates the free planner's JavaScript against a stub
DOM, which makes it the only check in the repo that *runs* the tool instead of reading it. Node is
optional — without it the script says SKIP and exits 0, and `tests/test_planner.py` records the same
skip, so a machine that cannot check the arithmetic is visible rather than quietly green.

## Deliberate omissions

No publishing (platform terms require a human account holder). No ad buying. No
scraping. No auto-posting to communities. No account creation. No dependency
installs. No cron that can write to a storefront. No automatic weight download and no
training inside `wf run` — a weekly job that changes its own model would silently change
output quality. No RAG, no vector store, no "fine-tune enabled" flag. If a future version
adds any of those, it should be argued for in a commit message, not slipped in.
