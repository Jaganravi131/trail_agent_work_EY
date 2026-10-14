# trail_agent_work_EY

Working asset library produced by **WealthForge** — a zero-capital income operation. Everything in here cost $0 to produce and needs no paid tool, no inventory and no ads. What remains is a human's time: accounts, uploads, and a fact-check pass.

**Portfolio 001 — *The Night Shift Reset*.** A specific reader (night-shift workers), one proprietary system, four monetisation surfaces from one manuscript.

## Start here

| File | What it is |
|---|---|
| `00-strategy/01-IDEA-SCORECARD.md` | 10 ideas scored, why these 2 won, what was rejected and why |
| `00-strategy/02-REVENUE-MODEL.md` | conservative / base / optimistic, with assumptions you can falsify |
| `00-strategy/03-90-DAY-ROADMAP.md` | the human operator's schedule, day by day, with copy-paste account instructions |
| **`01-book/manuscript.md`** | **the finished book, ~11,600 words, EPUB-ready** |
| `01-book/chapters/` | the same manuscript as 11 chapter files (edit here, then rebuild) |
| `01-book/build.sh` | pandoc → EPUB + print PDF + DOCX |
| `02-kdp/01-COVER-BRIEF.md` | art-director spec: layout, hex colours, sizes, back cover, spine |
| `02-kdp/02-KDP-BACKEND-UPLOAD.md` | title, subtitle, description HTML, 7 keywords, categories, pricing, AI-declaration guidance |
| `02-kdp/03-LAUNCH-SEQUENCE.md` | the 30-day launch, priced pulses, KDP Select mechanics, what to do on day 1 vs day 30 |
| `03-tool/index.html` | **a working free web app.** Single file, no dependencies, no tracking, offline-capable |
| `04-seo/01-KEYWORD-MAP.md` | 20+ clusters, tiered, with the 12-page site architecture |
| `04-seo/articles/` | 3 finished pillar articles, ~1,700 words each, with meta + internal links |
| `05-marketing/01-CONTENT-CALENDAR.md` | 30 days, **every post written and paste-ready** |
| `06-tracking/` | the weekly CSV + how to read it |
| `07-risks/` | compliance rules, and the diagnosis tree for when numbers don't move |

## The free tool

`03-tool/index.html` — open it in a browser, or serve it:

```bash
cd 03-tool && python3 -m http.server 8000 --bind 0.0.0.0
```

Enter a shift start and end. It returns the anchor nap, meal timing, a caffeine curfew with the half-life decay table (including *why* the curfew moved if your shift is long), the 3-2-1-0 countdown, sleep architecture, and a printable fridge card. It also exports nine `.ics` alarms so the plan lands in a phone calendar. It is a static file: GitHub Pages or Netlify Drop serves it free, with HTTPS.

Tested headlessly across 9 rota patterns (12h nights, 4-on-4-off, 16h, day shifts, blank input, sub-3h shifts). All plan invariants pass: curfew never falls after the sleep time, decay is monotonic, ICS timestamps validate, and short/invalid shifts fail with a human-readable message instead of a NaN.

## Build the book

```bash
bash 01-book/build.sh            # needs pandoc locally
```
or push and let `.github/workflows/build-book.yml` do it with the `pandoc/core:3` container, then download the `night-shift-reset-book-files` artifact — no local toolchain, no cost.

## The agent itself — `wealforge/`

Portfolio 001 above is what the loop *produced*. `wealforge/` is the loop, as software:
stdlib-only Python, no dependencies, no server, no paid tool, no ability to spend money.
Generation runs on a local open-weight model (Ollama or any OpenAI-compatible server) by
default; hosted APIs need an explicit `WEALFORGE_ALLOW_PAID=1`.

```bash
./wf score          # pre-screen 17 disqualifiers → score the pool → 00-strategy/decisions/
./wf pick --build   # select top N; write brief + cover spec + KDP backend + calendar
./wf gate 01-book --bundle    # compliance/integrity gate — exit 1 on any error
./wf price --words 11566      # royalty bands, delivery fee, print cost, scenarios, traffic math
./wf report         # trends → diagnosis tree → exactly ONE experiment to run
./wf today          # this day's human actions from the 90-day schedule
./wf run            # the whole loop (what CI runs weekly)
./wf model --select --for-finetune   # which open-weight model, ranked by licence fit then VRAM
./wf work           # draft → gate → repair → validate, on every selected idea
./wf dataset        # fine-tuning corpus from gate-passing approved assets — or the "not yet" verdict
./wf finetune       # QLoRA/Colab/Axolotl/LLaMA-Factory configs into training/
./wf eval --self-check              # style + gate grades on the committed assets, no model needed
./wf site                           # build docs/ — the folder you actually publish (./wf site --check to verify only)
python3 -m unittest discover -s tests -q     # 158 tests, no network
```

Nineteen modules; `wealforge/README.md` has the architecture and the extension points, and
`wealforge/FINE-TUNING.md` covers the model layer, the corpus rules and the eval harness. The
load-bearing behaviours, all covered by tests:

- **A stage machine that can't be skipped.** An asset cannot reach `gated` without a passing
  gate report, or `ready` without a publish checklist. `briefed → gated` raises.
- **Pre-screen before scoring.** Public-domain dumps, trademark characters, diagnosis/dosing
  protocols, review-swap rings, scraping, income guarantees, paid-ads-dependent ideas are
  rejected at scan time *with the reason in the ledger* — five such decoys sit in
  `config/scan_seed.json` to prove the screen bites.
- **Suppression is a feature.** Prose that *forbids* a behaviour is not flagged as committing it
  (`never buy reviews` passes); `<!-- gate:allow R1 -->` suppresses one rule on one line; rules are
  overridable in `config/gate_rules.json`; every suppression is counted in the report so a muzzled
  rule stays visible.
- **Bundle mode.** A book is one reader-facing work: the disclaimer in its front matter covers the
  volume, while claim rules still run per chapter and findings keep their source filename.
- **Offline is a mode, not a failure.** No provider configured ⇒ the agent writes briefs, math,
  checklists and verdicts and *refuses to emit prose*, pointing at `--emit-prompt` instead.
  A test asserts the offline path never opens a socket.
- **The model is never the judge.** Whatever answers the wire — a 2026 8B model or a frontier
  API — its output is a candidate that must pass the gate. Fail it and the loop feeds the findings
  back for at most 3 attempts, keeps the least-bad draft, marks the section `NEEDS-HUMAN`, and
  refuses the stage advance. Proven over real HTTP against a stub server.
- **A fine-tune is earned twice.** The corpus has a floor (150 SFT sections / 40 preference pairs);
  below it `wf dataset` tells you to keep writing instead of training, and refuses to emit a config
  that pretends otherwise. Above it, the adapter still has to beat the base model on
  `wf eval --compare` — where an adversarial case flipping from refusal to compliance is a hard
  regression, not a style preference.
- **Training data fails closed.** Only gate-passing files enter the corpus; if `config/approval.json`
  has entries, only content-hash-matching operator-signed files do. Edit an approved file after
  sign-off and it drops out rather than training the model on text nobody read.

`config/rubric.json` (weights), `config/scan_seed.json` (idea pool),
`config/models.json` (model registry and licence traits), `config/voice.json` (the style card
injected into every generation call), `config/eval_prompts.json` (golden prompts) and
`config/schedule.json` (90-day plan) are the files to edit when you disagree with it.

## Non-negotiables baked into these assets

- **No invented credentials.** The author bio says the author is not a clinician, and it must stay that way. See `07-risks/01-integrity-and-compliance.md`.
- **No medical claims.** No supplements, no dosing, no treatment of any condition. Every asset carries a "see a clinician if…" redirect and an absolute never-drive-drowsy rule, because a sleep-deprived reader is a person about to drive.
- **Truthful AI disclosure** at KDP upload, after a real human fact-check pass on the six flagged claims.
- **Affiliate links disclosed above the fold**, per FTC and the Amazon Associates agreement.
- **No scraping, no fake reviews, no undisclosed self-promotion in communities.**

## Realistic expectation

First dollar: weeks 2–6. Meaningful month: month 3–4. Base case ≈ $110 in month 1, $500–650/mo by month 6 — and if you're evaluating this as a career change at day 34, that's the wrong test; the tracker and the diagnosis tree exist so the decision gets made on data at day 60.

## Next in the queue (not built yet)

1. The $9 printable kit — 2 hours of formatting, content already written in `01-book/chapters/09-worksheets.md`
2. Book 2, *The Days Off Problem* — ~60% framework reuse
3. Four batch-2 SEO pages listed in the keyword map
4. Cover JPEG — needs a human with Canva access (spec is complete)
