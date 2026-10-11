# WealthForge agent

An autonomous *asset* agent: it scans opportunities, scores them, picks the best
ones, writes the surrounding production assets, blocks the mistakes that get
accounts killed, prices by arithmetic, and then tells the operator the one thing
to try next. It runs on stdlib Python with no dependencies, no server and no paid
tool, and it has no ability to spend money.

```bash
./wf score                 # pre-screen + score config/scan_seed.json → 00-strategy/decisions/
./wf pick --build          # select top N, write briefs + KDP backend + cover brief + calendar
./wf gate 01-book --bundle # compliance/integrity gate (exit 1 on errors)
./wf price --words 11566   # royalty bands, delivery fee, print cost, scenarios, traffic math
./wf report                # trends + ONE recommended experiment from the tracker CSV
./wf today                 # this day's human actions, from the 90-day schedule
./wf run                   # the whole loop, unattended-safe (what CI runs weekly)
./wf status; ./wf log      # what the ledger knows
python3 -m unittest discover -s tests -q     # 92 tests, no network
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
                                        │
              ┌─────────────────────────┼──────────────────────────┐
              ▼                         ▼                          ▼
      generator.py                kdp.py                     llm.py
      briefs, cover spec,         royalty bands, delivery     provider detection,
      backend sheet, sales        fee, print cost, KENP,      `cmd`/Anthropic/OpenAI,
      page, calendar              scenarios, breakeven        or honest "offline"
              │                   traffic                       │
              └──────────── gate.py (15 rules) ─────────────────┘
                             │  reader assets: safeguards required
                             │  internal docs: integrity rules only
                             │  bundle mode: a book is one work
                             ▼
                    metrics.py  trends → diagnosis tree → exactly one experiment
                    roadmap.py  day-N task list + stale-stage warnings
```

## The four decisions worth defending

**1. The gate is the product, not the generator.** Anyone can produce text now. What kills a zero-capital operation is a fabricated credential on a health title, an undisclosed affiliate link, an income promise, a scraping shortcut, a review-swap ring. Those are mechanical, so they're checked mechanically, in CI, before anything ships. Presence rules *and* required-safeguard rules, because "you mentioned money but didn't disclose" is the class of bug that only the second kind catches.

**2. Suppression is a first-class feature.** A gate that flags `never buy reviews` as review manipulation gets switched off inside a week — which is worse than never building it. So: negation context is respected, `<!-- gate:allow R1 -->` suppresses one rule on one line, rules can be downgraded or disabled in `config/gate_rules.json`, and every suppression is counted in `stats` so a silently-muzzled rule is still visible in the report.

**3. A book is one reader-facing work.** Per-file disclaimers would mean repeating the same paragraph in all 11 chapters. `--bundle` runs safeguard rules against the concatenation and claim rules per file, and merged findings keep their `source` filename so the verdict still points at the chapter to fix.

**4. Offline is a mode, not a failure.** With no provider configured, `--draft` writes `draft/HOW-TO.md` explaining the two free paths and *does not emit prose*. A `--emit-prompt` / `--from-file` round trip keeps provenance in the ledger. Nothing pretends.

## Extension points

| Want | Change |
|---|---|
| Different weights | `config/rubric.json` — totals renormalise; the ledger stores `rubric_version` per decision |
| A new disqualifier | `scan.REJECT` tuple (tag, regex, why). It shows up in the scorecard and the ledger |
| A new gate rule | `config/gate_rules.json` → `{"rules":[{"id":"Z1",...}]}`. No code change |
| A rule you disagree with | override `severity`, or `disable` it — and say why in the commit |
| A different marketplace | `kdp.py` is one module; the plan/price/checklist generator reads it |
| Prose generation | set `WEALFORGE_LLM_CMD` to any command that reads stdin → writes stdout (a local model works) |

## Deliberate omissions

No publishing (platform terms require a human account holder). No ad buying. No
scraping. No auto-posting to communities. No account creation. No dependency
installs. No cron that can write to a storefront. If a future version adds any of
those, it should be argued for in a commit message, not slipped in.
