"""`wf` — the operator's interface to the loop.

One rule for every subcommand: it either writes an artefact into the repo or it
prints a decision with its reasons. It never edits a published asset in place,
and it never claims to have done something a human must do (open an account,
push a button, accept a platform's terms).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import (__version__, dataset, evalkit, finetune, gate, generator, kdp, llm, metrics, modelhub, site,
               models, pipeline, roadmap, rubric as rubric_mod, scan, store, validate)
from . import commands

CONFIG = Path("config")


def _rubric(args: argparse.Namespace) -> rubric_mod.Rubric:
    return rubric_mod.Rubric.load(getattr(args, "rubric", None) or (CONFIG / "rubric.json"))


def _seed_path(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "seed", None) or (CONFIG / "scan_seed.json"))


# ----------------------------------------------------------------- score ----
def cmd_score(args: argparse.Namespace) -> int:
    rb = _rubric(args)
    seed = _seed_path(args)
    if not seed.exists():
        print(f"no seed file at {seed}. Create one (list of ideas) or pass --seed.", file=sys.stderr)
        return 2
    cands = scan.evaluate(scan.load_seed(seed), rb,
                         scan.interactive_score if args.ask else None)
    out = args.out or "00-strategy/decisions/scorecard-latest.md"
    md = scan.render(cands, rb, show_rejected=not args.no_rejected)
    generator.write(Path(out).parent, Path(out).name, md)
    with store.open_store(args.db) as st:
        for c in cands:
            st.put_idea(c.idea)
        st.event("score", str(seed), {"count": len(cands),
                "rejected": sum(1 for c in cands if c.rejected),
                "rubric": rb.version})
    print(md)
    print(f"scorecard → {out}   ledger → {st.path}")
    return 0


# ------------------------------------------------------------------ pick ----
def cmd_pick(args: argparse.Namespace) -> int:
    rb = _rubric(args)
    with store.open_store(args.db) as st:
        rows = [i for i in st.ideas() if not i.get("blocked")]
        if not rows:
            print("ledger is empty — run `wf score` first.", file=sys.stderr)
            return 2
        for r in rows:
            r["scores"] = r.get("scores") or {}
        picked = rb.select(rows)
        if args.top:
            rb = rubric_mod.Rubric(**{**{k: v for k, v in vars(rb).items() if k != "raw"},
                                      "top_n": args.top})
            picked = rb.select(rows)
        if not picked:
            print(f"nothing cleared min_total={rb.min_total}. Correct outcome: do not pick filler.\n"
                  f"Highest was {max((r['total'] for r in rows), default=0)}. Re-scan or lower the bar "
                  f"deliberately, in config, not in the moment.", file=sys.stderr)
            return 1
        rejected = [r["id"] for r in rb.rank(rows) if r not in picked]
        rationale = "; ".join(
            f"{r['title']} = {r['total']} ({', '.join(f'{k}{v}' for k, v in (r['scores'] or {}).items())})"
            for r in picked)
        for r in picked:
            st.set_stage("ideas", r["id"], "selected")
            st.event("pick", r["id"], {"total": r["total"]})
        st.record_decision(date.today().isoformat(), rb.version,
                           [r["id"] for r in picked], rejected, rationale)
        print(f"Picked {len(picked)} of {len(rows)} scored ideas "
              f"(top_n={rb.top_n}, min_total={rb.min_total}):\n")
        for r in picked:
            print(f"  • {r['title']}  [{r['total']}/100]  {r['platform']}")
            if args.build:
                _build(st, r, rb, args)
        if args.emit_markdown:
            Path(args.emit_markdown).parent.mkdir(parents=True, exist_ok=True)
            Path(args.emit_markdown).write_text(
                f"# Selection — {date.today().isoformat()}\n\n**{rationale}**\n\n"
                f"- rejected: {', '.join(rejected) or 'none'}\n"
                f"- rubric v{rb.version}, bar {rb.min_total}, top_n {rb.top_n}\n")
            print(f"\ndecision note → {args.emit_markdown}")
    return 0


KIND_BY_PLATFORM = pipeline.KIND_BY_PLATFORM   # one map, shared with `wf work`


def _build(st: store.Store, idea: dict[str, Any], rb: rubric_mod.Rubric, args: argparse.Namespace) -> int:
    kind = args.kind or KIND_BY_PLATFORM.get(idea.get("platform", ""), "book")
    outdir = Path(args.out_dir or "out") / models.slugify(idea["title"])
    st.set_stage("ideas", idea["id"], "selected") if idea.get("stage") == "proposed" else None
    idea.setdefault("slug", models.slugify(idea["title"]))
    brief = generator.brief_for(idea, rb)
    files = {"brief.md": brief}
    if kind == "book":
        plan = kdp.price_new_title(11_000, idea.get("meta", {}).get("competition", "medium"))
        files["02-kdp/BACKEND.md"] = generator.kdp_backend(idea, 11_000, plan.pages,
                                                            idea.get("meta", {}).get("competition", "medium"))
        files["02-kdp/COVER-BRIEF.md"] = generator.cover_brief(idea)
    if kind in ("product", "kit"):
        files["sales-page.md"] = generator.sales_page(idea, json.loads(Path(args.product_file).read_text())
                                                      if args.product_file and Path(args.product_file).exists()
                                                      else {"name": idea["title"] + " — printable kit", "price": 9})
    files["calendar.md"] = generator.calendar(idea, args.days)
    paths = {name: generator.write(outdir, name, body) for name, body in files.items()}

    asset = models.Asset.new(idea["id"], kind, str(outdir))
    if args.draft:
        # One drafting path for the whole CLI: the gate-repairing pipeline. A second,
        # ungated "quick draft" here would be the path people actually use, and the
        # one that ships unreviewed prose.
        prov = llm.Provider.detect(args.provider)
        wr = pipeline.work_idea(st, idea, prov, out_root=Path(args.out_dir or "out"),
                                max_sections=args.sections,
                                word_target={"book": 1400, "article": 1500, "kit": 600,
                                            "tool": 400, "product": 500}[kind],
                                log=lambda k, m: print(f"    [{k}] {m[:120]}"))
        if wr.fully_generated and wr.gate_errors == 0:
            asset.stage = "drafted"
        elif wr.total_words:
            asset.stage = "drafted"
        asset.word_count = wr.total_words
        print(f"  drafted {sum(1 for s_ in wr.sections if s_.status == 'generated')}/"
              f"{len(wr.sections)} sections, {wr.total_words} words, {wr.total_tokens} tokens, "
              f"{wr.gate_errors} gate error(s)")
        for s_ in wr.sections:
            if s_.status in ("needs_human", "failed", "offline"):
                print(f"    ! {s_.name}: {s_.status} — {s_.note or 'no model'}")
        if not prov.available:
            print(f"    (offline: {prov.probed.get('reason', 'no provider')})", file=sys.stderr)
    else:
        generator.write(outdir, "draft/HOW-TO.md",
                        "Brief written; no prose yet. Re-run with --draft (needs a provider) or "
                        "--emit-prompt for the zero-cost path.\n")
    for rel, p in paths.items():
        st.event("write", str(p), {"bytes": p.stat().st_size})
    brief_path = paths.get("brief.md")
    if brief_path:
        asset.content_sha = models.sha256_of(brief_path)
    st.put_asset(asset)
    print(f"\n  → {len(paths)} file(s) in {outdir}/  (asset stage: {asset.stage})")
    if not args.no_gate:
        targets = list(paths.values()) + ([str(outdir / "draft")] if (outdir / "draft").exists() else [])
        rep = gate.evaluate_paths(targets)
        errs = sum(len(r.errors) for r in rep)
        print(f"  gate: {len(rep)} file(s), {errs} error(s), {sum(len(r.warnings) for r in rep)} warning(s)")
        for r in rep:
            if r.findings:
                print(r.render())
        if errs and asset.stage == "drafted":
            print("  stage stays 'drafted' until the gate passes. Run:  wf gate " + str(outdir))
        elif not errs and asset.stage == "drafted":
            st.set_stage("assets", asset.id, "gated", {"gate_report": "clean"})
            print("  → advanced to 'gated'")
    return 0


# ----------------------------------------------------------------- gate ----
def cmd_gate(args: argparse.Namespace) -> int:
    rules, skip = gate.load_rules(args.rules)
    reports = gate.evaluate_paths(args.paths, rules, skip, bundle=args.bundle)
    if args.json:
        print(json.dumps([r.as_dict() for r in reports], indent=1, default=str))
    else:
        for r in reports:
            print(r.render())
        if args.summary:
            print(gate.summary_markdown(reports))
    errs = sum(len(r.errors) for r in reports)
    warns = sum(len(r.warnings) for r in reports)
    with store.open_store(args.db) as st:
        for r in reports:
            st.record_gate(r)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(gate.summary_markdown(reports), encoding="utf-8")
        print(f"report → {args.out}")
    label = f"{len(reports)} file(s): {errs} error(s), {warns} warning(s)"
    if args.out:
        Path(args.out).with_suffix(".json").write_text(
            json.dumps([r.as_dict() for r in reports], indent=1, default=str), encoding="utf-8")
    if errs:
        print(f"GATE FAIL — {label}", file=sys.stderr)
        return 1
    if args.strict and warns:
        print(f"GATE FAIL (strict) — {label}", file=sys.stderr)
        return 1
    print(f"GATE PASS — {label}")
    return 0


# ---------------------------------------------------------------- price ----
def cmd_price(args: argparse.Namespace) -> int:
    plan = kdp.price_new_title(args.words, args.competition, not args.no_worksheets, args.pages)
    print(f"Word count {args.words:,} · competition '{args.competition}' · "
          f"~{plan.pages} print pages · ~{plan.file_kb} KB file\n")
    print("  ebook    " + plan.ebook.render())
    for n in plan.ebook.notes:
        print("            ! " + n)
    print(f"  promo    ${plan.promo_price:.2f} for {plan.promo_days} days → {plan.promo.render()}"
          f"   (buying rank and reviews, not revenue)")
    pb = plan.paperback
    print(f"  paperback ${plan.paperback_price:.2f} → net ${pb['net']:.2f} "
          f"(print cost ${pb['print_cost']:.2f}; min viable list ${pb['min_viable_list_price']:.2f})")
    print(f"  KENP     ~${kdp.kenp_earnings(int(plan.counted_pages)):.2f} per full read "
          f"({plan.counted_pages} counted pages × {kdp.KENP_RATE_DEFAULT})")
    if pb.get("thin_warning"):
        print(f"\n  \u26a0 {pb['thin_warning']}")
    print(f"\n  {pb['verified']}")
    print(f"  All rates need confirming in the live KDP app before you commit to a price.\n")
    rows = kdp.scenario_table(plan.pages, plan.file_kb, plan.ebook_price,
                              (args.conservative, args.base, args.optimistic))
    print("  month-1 scenarios (30-day window):")
    for r in rows:
        print(f"    {r['scenario']:12} {r['units_30d']:>4} units → ${r['month_net_usd']:>7.2f}   "
              f"[{r['assumption']}]")
    be = kdp.breakeven_reviews(max(20, int(args.base * 30)), args.conversion, 0)
    print(f"\n  traffic reality check: {be['units_target']} units at a {be['assumed_conversion_pct']}% "
          f"page-conversion rate needs {be['store_visits_needed']:,} store visits "
          f"(~{be['visits_per_day_for_30_days']}/day for a month). {be['note']}.")
    return 0


# ---------------------------------------------------------------- report ----
def cmd_report(args: argparse.Namespace) -> int:
    weeks = metrics.load_tracker(args.tracker)
    start = Path(".wealforge/start.txt")
    days = (date.today() - date.fromisoformat(start.read_text().strip())).days + 1 if start.exists() else args.days_live
    diag = metrics.diagnose(weeks, days, cover_tested=args.cover_tested)
    md = metrics.report_markdown(weeks, days, diag)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"report → {args.out}")
    else:
        print(md)
    with store.open_store(args.db) as st:
        for w in weeks:
            st.put_metrics(w.week_start, w.values)
        st.event("report", args.tracker, {"days_live": days, "stage": diag["stage"]})
    return 0


# ----------------------------------------------------------------- log ----
def cmd_status(args: argparse.Namespace) -> int:
    with store.open_store(args.db) as st:
        s = st.stats()
        print("ledger " + str(st.path) + "\n  " + "  ".join(f"{k}={v}" for k, v in s.items()))
        if s["ideas"]:
            print("\nideas:")
            for i in st.ideas()[: args.limit]:
                flag = " ".join(i.get("flags") or [])
                print(f"  {i['total']:5}  {i['stage']:9}  {i['title'][:52]:52}  {flag[:44]}")
        assets = st.assets()
        if assets:
            print("\nassets:")
            for a in assets[: args.limit]:
                print(f"  {a['stage']:9}  {a['kind']:8}  {a['path']:34}  "
                      f"{a['word_count']} words  sha={a['content_sha'] or '—'}")
        dec = st.decisions(3)
        if dec:
            print("\nselections:")
            for d in dec:
                print(f"  {d['day']} (rubric v{d['rubric_version']}): {d['rationale'][:100]}")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    with store.open_store(args.db) as st:
        for e in st.events(args.limit, args.kind):
            print(f"{e['created_utc']}  {e['kind']:8}  {e['ref'][:60]:60}  "
                  f"{json.dumps(json.loads(e['payload']), default=str)[:90]}")
    return 0


# ------------------------------------------------------------- roadmap ----
def cmd_today(args: argparse.Namespace) -> int:
    sch = roadmap.load(args.schedule)
    today = date.fromisoformat(args.date) if args.date else date.today()
    day = roadmap.for_date(sch, today)
    print(day.render())
    return 0


def cmd_roadmap(args: argparse.Namespace) -> int:
    sch = roadmap.load(args.schedule)
    start = roadmap.set_start()
    print(roadmap.roadmap_markdown(sch, start))
    return 0


# ----------------------------------------------------------------- run ----
def cmd_run(args: argparse.Namespace) -> int:
    """The whole loop, unattended-safe: scan → pick → brief → gate → report.

    Deliberately excludes: publishing, spending, and drafting prose without a
    provider. A cron job that can only write files and fail loudly is the correct
    shape for autonomous infrastructure at $0.
    """
    print(f"wealforge v{__version__} — loop run {date.today().isoformat()}\n")
    prov = llm.Provider.detect(args.provider, probe=False)
    print(f"model: {prov.mode}{':' + prov.model if prov.model else ''} "
          f"({'usable, $0 local' if prov.is_local else 'offline — drafting stays a human/prompt step'})\n")
    rc = 0
    ns = argparse.Namespace(db=args.db, rubric=None, seed=args.seed, ask=False, out=None, no_rejected=False)
    rc |= cmd_score(ns)
    print("\n" + "=" * 72 + "\n")
    rc |= cmd_pick(argparse.Namespace(db=args.db, rubric=None, seed=args.seed, top=None,
                                       build=True, kind=args.kind, out_dir=args.out_dir, draft=args.draft,
                                       sections=2, days=30, no_gate=False, product_file=None,
                                       emit_markdown="00-strategy/decisions/latest-selection.md",
                                       provider=args.provider))
    print("\n" + "=" * 72 + "\n")
    for group, bundled in ((["01-book"], True), (["03-tool", "04-seo"], False)):
        if args.paths:
            group, bundled = args.paths, args.bundle
        rc |= cmd_gate(argparse.Namespace(paths=group, bundle=bundled, rules=None, json=False,
                                           strict=False, summary=not bundled,
                                           out="00-strategy/decisions/gate-latest.md", db=args.db))
    if args.with_model:
        print("\n" + "=" * 72 + "\n\n— drafting (model → gate → repair)\n")
        rc |= commands.cmd_work(argparse.Namespace(
            db=args.db, idea=None, max_ideas=args.max_ideas, sections=3, words=args.words,
            max_attempts=pipeline.REPAIR_MAX_DEFAULT, out_dir=args.out_dir or "out",
            provider=args.provider, no_probe=False, allow_fail=True, verbose=True))
        print("\n" + "=" * 72 + "\n")
    # Corpus + eval are free and offline-safe, so they run every cycle: the loop has to
    # keep measuring whether the agent's output is good, not just whether it exists.
    print("— rebuilding the fine-tuning corpus\n")
    rc2 = commands.cmd_dataset(argparse.Namespace(db=args.db, out="dataset", include=None,
                                                   format="sharegpt", approvals=None, no_gate=False,
                                                   dry_run=False))
    rc |= 0 if args.allow_fail else (rc2 and 0)      # dataset build can legitimately find nothing
    print("\n— eval self-check (style + gate on committed assets; no model called)\n")
    rc |= commands.cmd_eval(argparse.Namespace(
        db=args.db, cases="config/eval_prompts.json", grade=None, pattern=["**/*.md"],
        provider="offline", self_check=True, no_repo_grade=False, strict=False, json=False,
        out="00-strategy/decisions/eval-latest.md", compare=None, tolerance=2.0))
    print("\n— rebuild the deployable site (docs/) — what CI publishes must match its sources\n")
    rc3 = commands.cmd_site(argparse.Namespace(db=args.db, out=site.DEFAULT_OUT, check=False,
                                               config=None, allow_fail=args.allow_fail,
                                               report="00-strategy/decisions/site-latest.md"))
    rc |= 0 if args.allow_fail else rc3
    # The ebook is the paid product, so it gets the same treatment: rebuilt, and its report kept
    # next to the site's. A chapter edit that never reaches the manuscript is how a stale book gets
    # uploaded, and `--check` in CI is what stops that being a surprise on the customer's side.
    print("\n— rebuild the ebook (EPUB) from 01-book/chapters/\n")
    rc4 = commands.cmd_book(argparse.Namespace(db=args.db, out=None, check=False,
                                                allow_fail=args.allow_fail, json=False,
                                                report="00-strategy/decisions/book-latest.md"))
    rc |= 0 if args.allow_fail else rc4
    print("\n" + "=" * 72 + "\n")
    rc |= cmd_report(argparse.Namespace(tracker=args.tracker, out="00-strategy/decisions/report-latest.md",
                                         days_live=30, cover_tested=False, db=args.db))
    print(f"\nloop complete. Artefacts in 00-strategy/decisions/ and {args.out_dir or 'out/'}. "
          f"Exit {rc}.")
    return 0 if args.allow_fail else rc


def cmd_build(args: argparse.Namespace) -> int:
    with store.open_store(args.db) as st:
        idea = st.get_idea(args.idea)
        if not idea:
            print(f"no idea {args.idea!r} in ledger — `wf score` first.", file=sys.stderr)
            return 2
        if args.emit_prompt:
            p = llm.prompt_to_file(args.emit_prompt, generator.brief_for(idea), args.section or "chapter 1",
                                   args.words)
            print(f"prompt → {p}\nPaste it into any chat UI or pipe it to a local model, then "
                  f"`wf build --idea {args.idea} --from-file <output>`.")
            return 0
        if args.from_file:
            text = Path(args.from_file).read_text(encoding="utf-8")
            out = Path(args.out_dir or "out") / models.slugify(idea["title"]) / "draft" / Path(args.from_file).name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            rep = gate.evaluate_text(text, str(out))
            with store.open_store(args.db) as st2:
                st2.record_gate(rep)
                st2.event("intake", str(out), {"words": rep.words, "passed": rep.passed})
            print(rep.render())
            print(f"intake → {out}  ({rep.words} words, {'clean' if rep.passed else 'errors found'})")
            return 0 if rep.passed else 1
        return _build(st, idea, _rubric(args), args)


# ------------------------------------------------------------------ cli ----
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wf", description="WealthForge loop — zero-capital asset agent")
    p.add_argument("--db", help="sqlite ledger path (default: .wealforge/ledger.sqlite3)")
    p.add_argument("--version", action="version", version=f"wealforge {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--rubric", help="rubric JSON (default config/rubric.json)")

    sp = sub.add_parser("score", help="pre-screen + score the idea pool into the ledger")
    sp.add_argument("--seed"); sp.add_argument("--ask", action="store_true", help="prompt for axis scores")
    sp.add_argument("--out"); sp.add_argument("--no-rejected", action="store_true")
    common(sp); sp.set_defaults(fn=cmd_score)

    sp = sub.add_parser("pick", help="select the top N (nothing below the bar gets picked)")
    sp.add_argument("--words", type=int, default=1400, help="per-section word target for --draft")
    sp.add_argument("--top", type=int); sp.add_argument("--build", action="store_true",
                    help="also write briefs/metadata for each pick")
    sp.add_argument("--kind", choices=["book", "article", "tool", "kit", "product"],
                    help="default: inferred from the idea's platform")
    sp.add_argument("--out-dir"); sp.add_argument("--draft", action="store_true",
                    help="generate prose (needs a provider; never faked)")
    sp.add_argument("--sections", type=int, default=3); sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--product-file"); sp.add_argument("--no-gate", action="store_true")
    sp.add_argument("--provider", help="registry id (qwen3.6:8b), ollama:<tag>, openai:<base_url>, "
                                       "cmd:<prog>, anthropic, openai, offline (default: auto-detect)")
    sp.add_argument("--emit-markdown", dest="emit_markdown")
    common(sp); sp.set_defaults(fn=cmd_pick)

    sp = sub.add_parser("build", help="write briefs/metadata for one idea, or ingest drafted prose")
    sp.add_argument("--idea", required=True); sp.add_argument("--kind", choices=["book","article","tool","kit","product"])
    sp.add_argument("--out-dir"); sp.add_argument("--draft", action="store_true")
    sp.add_argument("--sections", type=int, default=3); sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--product-file"); sp.add_argument("--no-gate", action="store_true")
    sp.add_argument("--provider"); sp.add_argument("--emit-prompt", dest="emit_prompt")
    sp.add_argument("--from-file", dest="from_file"); sp.add_argument("--section")
    sp.add_argument("--words", type=int, default=1400)
    common(sp); sp.set_defaults(fn=cmd_build)

    sp = sub.add_parser("gate", help="compliance + integrity gate over files or dirs")
    sp.add_argument("paths", nargs="+"); sp.add_argument("--rules", help="JSON rule overrides")
    sp.add_argument("--json", action="store_true"); sp.add_argument("--strict", action="store_true",
                    help="treat warnings as failures")
    sp.add_argument("--summary", action="store_true"); sp.add_argument("--out")
    sp.add_argument("--bundle", action="store_true",
                    help="treat each path as one reader-facing work: safeguards may appear in any "
                         "file of the bundle (use for a book directory), claims still run per file")
    sp.set_defaults(fn=cmd_gate)

    sp = sub.add_parser("price", help="KDP royalty + scenario math")
    sp.add_argument("--words", type=int, default=11_000); sp.add_argument("--pages", type=int)
    sp.add_argument("--competition", default="medium", choices=["low", "medium", "high"])
    sp.add_argument("--no-worksheets", action="store_true", dest="no_worksheets")
    sp.add_argument("--conservative", type=float, default=0.3)
    sp.add_argument("--base", type=float, default=1.0)
    sp.add_argument("--optimistic", type=float, default=5.0)
    sp.add_argument("--conversion", type=float, default=0.9)
    sp.set_defaults(fn=cmd_price)

    sp = sub.add_parser("report", help="trends + diagnosis from the tracker CSV")
    sp.add_argument("--tracker", default="06-tracking/01-tracker.csv")
    sp.add_argument("--out"); sp.add_argument("--days-live", type=int, default=30)
    sp.add_argument("--cover-tested", action="store_true", dest="cover_tested")
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("today", help="this day's human actions + stale stages")
    sp.add_argument("--date"); sp.add_argument("--schedule")
    sp.set_defaults(fn=cmd_today)

    sp = sub.add_parser("roadmap", help="print the 90-day schedule and set day 1 to today")
    sp.add_argument("--schedule"); sp.set_defaults(fn=cmd_roadmap)

    sp = sub.add_parser("status", help="ledger summary"); sp.add_argument("--limit", type=int, default=12)
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("log", help="event log"); sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--kind"); sp.set_defaults(fn=cmd_log)

    commands.add_commands(sub)

    sp = sub.add_parser("run", help="full loop: score → pick → build → gate → dataset → eval → report")
    sp.add_argument("--seed"); sp.add_argument("--kind", choices=["book","article","tool","kit","product"])
    sp.add_argument("--out-dir"); sp.add_argument("--draft", action="store_true")
    sp.add_argument("--provider"); sp.add_argument("--paths", nargs="*")
    sp.add_argument("--tracker", default="06-tracking/01-tracker.csv")
    sp.add_argument("--allow-fail", action="store_true", help="always exit 0 (for weekly cron noise)")
    sp.add_argument("--bundle", action="store_true")
    sp.add_argument("--with-model", dest="with_model", action="store_true",
                    help="also draft prose through the gate-repair pipeline (needs a reachable model)")
    sp.add_argument("--max-ideas", type=int, default=3)
    sp.add_argument("--words", type=int, default=900)
    sp.set_defaults(fn=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except models.StageError as e:
        print(f"stage machine refused that: {e}", file=sys.stderr)
        return 3
    except FileNotFoundError as e:
        print(f"missing file: {e}", file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
