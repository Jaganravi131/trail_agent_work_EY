"""Model, dataset, fine-tune, eval and work commands.

`build_parser()` is the wiring; the handlers live here so `cli.py` stays the readable
map of the tool. Every one of these runs with no model, no GPU and no network — they
either do their work offline or say exactly what they need and why.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from . import dataset, evalkit, finetune, gate, llm, modelhub, models, pipeline, site, store, validate


# ----------------------------------------------------------------- model ----
def cmd_model(args: argparse.Namespace) -> int:
    if args.select or args.markdown:
        md = modelhub.recommend_markdown(vram_gb=args.vram, need_finetune=args.for_finetune)
        out = args.out or "00-strategy/decisions/model-recommendation.md"
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(md, encoding="utf-8")
        print(md.rstrip())
        print(f"\n→ {out}")
        return 0
    if args.list:
        specs = modelhub.load(args.registry)
        print(f"{'id':22} {'license':34} {'~VRAM':>6} {'ctx':>6}  fine-tune-legal  ollama tag")
        for s in specs:
            ok, why = s.licence_ok_for(True)
            print(f"{s.id:22} {s.license:34} {s.vram_gb_4bit:6.1f} {s.context_k:6} "
                  f"{'yes' if ok else 'NO':>10}         {s.ollama_tag}")
            if not ok:
                print(f"{'':22} {'':34} {'':6} {'':6}   → {why}")
        return 0
    # default: probe what the agent would actually use, right now
    prov = llm.Provider.detect(args.provider if args.provider != "auto" else None, probe=not args.no_probe)
    payload = {"mode": prov.mode, "model": prov.model, "base_url": prov.base_url,
               "available": prov.available, "is_local": prov.is_local,
               "probe": prov.probed, "voice_samples": len(prov.voice.excerpts) if prov.voice else 0,
               "paid_gate": "open" if (__import__("os").environ.get("WEALFORGE_ALLOW_PAID") == "1")
                            else "closed (set WEALFORGE_ALLOW_PAID=1 to opt in)"}
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if prov.available else 1
    print(f"provider mode : {prov.mode}"
          + (f"  ({prov.model} @ {prov.base_url})" if prov.base_url else f"  ({prov.model})" if prov.model else ""))
    print(f"usable        : {'yes' if prov.available else 'NO'}")
    print(f"cost          : {'$0 — open weights on your hardware' if prov.is_local else 'see provider'}")
    print(f"voice card    : {payload['voice_samples']} sample(s), "
          f"{len(prov.voice.render()) if prov.voice else 0} chars injected per call")
    print(f"paid providers: {payload['paid_gate']}")
    if prov.available and prov.model:
        try:
            specs = modelhub.load()
            tag = prov.model
            spec = next((x for x in specs if x.ollama_tag == tag
                         or (x.ollama_tag and tag.startswith(x.ollama_tag.split(":")[0]))), None)
            if spec:
                vram = modelhub.detect_vram_gb()
                if spec.vram_gb_4bit > vram:
                    print(f"⚠ capacity      : {spec.id} wants ~{spec.vram_gb_4bit:g}GB at 4-bit but this host "
                          f"reports ~{vram:g}GB. Expect swapping or an OOM on the first real call; "
                          f"`./wf model --select` lists what fits.")
        except Exception:
            pass                                   # a sizing warning must never break detection
    if prov.probed:
        print(f"probe         : {'ok' if prov.probed.get('ok') else 'failed'} — "
              f"{prov.probed.get('reason', '')} ({prov.probed.get('latency_ms', 0)} ms)")
        if prov.probed.get("sample"):
            print(f"  reply        : {prov.probed['sample']!r}")
        if prov.probed.get("models_seen"):
            print(f"  models seen   : {', '.join(prov.probed['models_seen'][:8])}")
    if not prov.available:
        print("\nNothing answers, so `wf work` will write briefs and prompts but no prose.\n"
              "Free path on any machine with 8+ GB RAM:\n"
              "    curl -fsSL https://ollama.com/install.sh | sh\n"
              "    ollama pull qwen3.6:8b\n"
              "    export WEALFORGE_MODEL=qwen3.6:8b && ./wf model\n"
              "For the fine-tune base and licence reasoning: ./wf model --select --for-finetune")
        return 1
    return 0


# --------------------------------------------------------------- dataset ----
def cmd_dataset(args: argparse.Namespace) -> int:
    gate_mod = None if args.no_gate else gate
    res = dataset.collect(".", approvals_path=args.approvals, gate_module=gate_mod,
                          include_globs=tuple(args.include) if args.include else
                          ("01-book/chapters/*.md", "04-seo/articles/*.md"))
    if args.dry_run:
        print(json.dumps(res.to_dict(), indent=2)[:4000])
        return 0
    paths = dataset.write(res, args.out, fmt=args.format)
    print(f"SFT {len(res.sft)} examples · DPO {len(res.dpo)} pairs · {len(res.excluded)} excluded")
    for name, p in paths.items():
        print(f"  {name:10} → {p}")
    rec = res.recommendation
    print(f"\nfine-tune recommendation: {'YES' if rec.get('fine_tune') else 'NOT YET'} — {rec['why']}")
    for x in rec.get("do_instead", []) or []:
        print(f"  instead: {x}")
    if rec.get("fine_tune"):
        print(f"  next: ./wf finetune   (base {rec.get('base_model')}, {rec.get('method')})")
    with store.open_store(args.db) as st:
        st.event("dataset", args.out, {"sft": len(res.sft), "dpo": len(res.dpo),
                                       "excluded": len(res.excluded),
                                       "fine_tune": bool(rec.get("fine_tune"))})
    return 0


# -------------------------------------------------------------- finetune ----
def cmd_finetune(args: argparse.Namespace) -> int:
    stats_p = Path(args.dataset_dir) / "stats.json"
    if not stats_p.exists():
        print(f"no {stats_p} — run ./wf dataset first (the configs are generated from the real "
              f"corpus size, not a guess).", file=sys.stderr)
        return 2
    stats = json.loads(stats_p.read_text(encoding="utf-8"))
    hp = finetune.Hyperparams(base_model=args.base or stats.get("recommendation", {}).get("base_model",
                                                                                          finetune.BASE_DEFAULT))
    paths = finetune.build(args.dataset_dir, args.out, hp=hp, stats=stats)
    print(f"training configs → {args.out}/  ({len(paths)} files)")
    for n, p in paths.items():
        print(f"  {n:11} {p}")
    rec = stats.get("recommendation", {})
    if not rec.get("fine_tune"):
        print("\n⚠ the dataset builder does NOT recommend fine-tuning on this corpus yet.\n"
              f"  {rec.get('why', '')}\n  The configs are still written (they are the plan), but "
              "read training/README.md before spending a GPU hour.")
    else:
        print("\nrun:  training/train.sh unsloth   (or open training/colab-qlora.ipynb on a free T4)")
        print("then: ./wf eval --compare   — the adapter earns the switch, it does not assume it")
    with store.open_store(args.db) as st:
        st.event("finetune", args.out, {"base": hp.base_model, "files": len(paths),
                                        "recommended": bool(rec.get("fine_tune"))})
    return 0


# ------------------------------------------------------------------- eval ----
# ------------------------------------------------------------------- site ----
def cmd_site(args: argparse.Namespace) -> int:
    """Build (and optionally enforce) the deployable folder under `docs/`.

    `--check` is the part that earns its keep: a page that links to a page which does not
    exist is a 404 in production and a warning here, and an undisclosed affiliate link is an
    FTC problem that costs one line to prevent.
    """
    res = site.build(".", out_dir=args.out or site.DEFAULT_OUT, config_path=args.config,
                     check=args.check)
    md = site.report_markdown(res, args.out or site.DEFAULT_OUT)
    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(md, encoding="utf-8")
    print(f"site: {len([p for p in res.pages if p.kind in ('article', 'page')])} prose page(s), "
          f"{len(res.written)} file(s) → {args.out or site.DEFAULT_OUT}/  ·  "
          f"{len(res.errors)} error(s), {len(res.warnings)} warning(s)")
    for e in res.errors:
        print(f"  [ERROR] {e}", file=sys.stderr)
    for w in res.warnings:
        print(f"  [warn ] {w}")
    if res.affiliate_pages:
        print(f"  disclosure injected on: {', '.join(sorted(set(res.affiliate_pages)))}")
    with store.open_store(args.db) as st:
        st.event("site", args.out or site.DEFAULT_OUT,
                 {"pages": len(res.pages), "errors": len(res.errors), "warnings": len(res.warnings),
                  "affiliate_pages": len(set(res.affiliate_pages)), "written": len(res.written)})
    return 0 if (res.ok or args.allow_fail) else 1


def cmd_eval(args: argparse.Namespace) -> int:
    if args.compare:
        return _eval_compare(args)
    out = Path(args.out or "00-strategy/decisions/eval-latest.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    provider = None
    if not args.self_check:
        prov = llm.Provider.detect(args.provider if args.provider != "auto" else None, probe=True)
        provider = prov if prov.available else None
        if provider is None:
            print(f"model-dependent cases skipped: {prov.probed.get('reason', 'no provider')}\n",
                  file=sys.stderr)
    res = evalkit.run_all(args.cases, provider=provider)
    if args.self_check:
        res["mode"] = "self-check — graded the committed assets, called no model"
    elif provider is None:
        res["mode"] = f"{res.get('mode')} (probe failed, so no model was called)"
    graded = evalkit.grade_paths(args.grade, patterns=tuple(args.pattern)) if args.grade else []
    if not graded and not args.no_repo_grade:
        graded = evalkit.grade_paths(["01-book/chapters", "04-seo/articles"])
    json_p = out.with_suffix(".json")
    json_p.write_text(json.dumps({**res, "repo_grades": graded}, indent=2), encoding="utf-8")
    md = evalkit.report_markdown(res, graded)
    out.write_text(md, encoding="utf-8")
    fails = sum(1 for c in res.get("cases", []) + res.get("adversarial", []) if not c.get("passed"))
    hard_fails = sum(1 for c in res.get("adversarial", []) if not c.get("passed"))
    if args.json:
        # `--json` means "stdout is for a machine": the human report still goes to --out and the
        # .json sidecar, but stdout carries JSON alone, so `wf eval --json | jq` actually parses.
        print(json_p.read_text(encoding="utf-8"))
    else:
        print(md.rstrip())
        print(f"\n→ {out}")
        print(f"  json → {json_p}")
    with store.open_store(args.db) as st:
        st.event("eval", str(out), {"cases": len(res.get("cases", [])), "adversarial": len(res.get("adversarial", [])),
                                    "failures": fails, "refusal_failures": hard_fails,
                                    "provider": res.get("provider"), "mode": res.get("mode")})
    if hard_fails:
        print(f"\n🚨 {hard_fails} adversarial case(s) failed — a model that complies with these "
              "is not usable for this pipeline, regardless of how nice its prose is.", file=sys.stderr)
        return 1 if args.strict else 0
    if fails and args.strict:
        return 1
    return 0


def _eval_compare(args: argparse.Namespace) -> int:
    base_p, cur_p = Path(args.compare[0]), Path(args.compare[1])
    if not base_p.exists() or not cur_p.exists():
        print("usage: wf eval --compare BASE.json CURRENT.json (both from `wf eval --json` runs)",
              file=sys.stderr)
        return 2
    cmp_ = evalkit.compare(json.loads(base_p.read_text(encoding="utf-8")),
                           json.loads(cur_p.read_text(encoding="utf-8")), tol=args.tolerance)
    print(f"baseline pass {cmp_['baseline_pass']} → candidate pass {cmp_['current_pass']}")
    print(f"verdict: {cmp_['verdict']}")
    for x in cmp_["regressions"]:
        print(f"  🔴 {x}")
    for x in cmp_["wins"]:
        print(f"  🟢 {x}")
    if not cmp_["regressions"] and not cmp_["wins"]:
        print("  (no measurable difference — keep the simpler model)")
    with store.open_store(args.db) as st:
        st.event("eval-compare", f"{base_p.name}→{cur_p.name}",
                 {"verdict": cmp_["verdict"], "regressions": len(cmp_["regressions"]),
                  "wins": len(cmp_["wins"])})
    return 1 if cmp_["regressions"] else 0


# ------------------------------------------------------------------- work ----
def cmd_work(args: argparse.Namespace) -> int:
    prov = llm.Provider.detect(args.provider if args.provider != "auto" else None, probe=not args.no_probe)
    print(f"wealforge work — provider {prov.mode}"
          f"{':' + prov.model if prov.model else ''} · "
          f"{'$0 local' if prov.is_local else 'hosted'} · "
          f"{'READY' if prov.available else 'OFFLINE (briefs + prompts only, no faked prose)'}")
    if not prov.available:
        print(f"  why: {prov.probed.get('reason', 'nothing configured')}")
        print("  fix: ./wf model --select --for-finetune, or run offline: ./wf build --idea ID --emit-prompt f.txt")
    with store.open_store(args.db) as st:
        ideas = ([st.get_idea(args.idea)] if args.idea
                 else [i for i in st.ideas() if i.get("stage") in ("selected", "briefed", "drafted")])
        ideas = [i for i in ideas if i]
        if not ideas:
            print("no selected ideas in the ledger — run ./wf pick --build first.", file=sys.stderr)
            return 2
        rc = 0
        for idea in ideas[: args.max_ideas]:
            print(f"\n— {idea['title']}  ({idea.get('total', 0)})")
            wr = pipeline.work_idea(st, idea, prov, out_root=args.out_dir,
                                    max_attempts=args.max_attempts, max_sections=args.sections,
                                    word_target=args.words,
                                    log=(lambda k, m: print(f"    [{k}] {m[:110]}")) if args.verbose else None)
            flag = "PASS" if wr.passed else "NEEDS-HUMAN"
            print(f"  {flag} · {wr.total_words} words · {len(wr.sections)} sections · "
                  f"{wr.total_tokens} tokens · {wr.gate_errors} gate error(s) · "
                  f"{wr.validation.get('errors', 0)} metadata error(s)")
            print(f"  log → {Path(args.out_dir) / wr.slug / 'WORK-LOG.md'}")
            if not wr.passed and prov.available:
                rc |= 1                      # a real draft that still fails the gate is a failure
        return 0 if args.allow_fail or not prov.available else rc


# ----------------------------------------------------------- approve/validate ----
def cmd_approve(args: argparse.Namespace) -> int:
    out = Path(args.config) if args.config else Path(".wealforge/approvals.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"approved": {}}
    data.setdefault("approved", {})
    if args.list:
        print(json.dumps(data, indent=2))
        return 0
    if not args.paths:
        print("usage: wf approve FILE [FILE…] --by NAME   (records that YOU read it)", file=sys.stderr)
        return 2
    for f in args.paths:
        p = Path(f)
        if p.is_dir():
            files = [c for c in sorted(p.rglob("*.md"))]
        else:
            files = [p]
        for c in files:
            if not c.exists():
                print(f"  ! missing: {c}", file=sys.stderr)
                continue
            sha = hashlib.sha256(c.read_bytes()).hexdigest()[:16]
            key = c.as_posix()
            data["approved"][key] = {"sha": sha, "by": args.by or "operator", "on": date.today().isoformat()}
            print(f"  approved {key}  sha={sha}"
                  + ("  (re-signs: content changed since last approval)"
                     if args.revoke else ""))
    if args.revoke:
        for f in args.paths:
            data["approved"].pop(Path(f).as_posix(), None)
            print(f"  revoked {f}")
    data["note"] = ("Operator sign-off for the fine-tuning corpus. Editing a file after approval "
                    "invalidates it by sha. Remove entries with `wf approve FILE --revoke`.")
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"→ {out}  ({len(data['approved'])} approved file(s))")
    with store.open_store(args.db) as st:
        st.event("approve", str(out), {"count": len(data["approved"]), "by": args.by or "operator"})
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    fields = json.loads(Path(args.file).read_text(encoding="utf-8")) if args.file else {}
    v = validate.validate_kdp(fields, keywords=fields.get("keywords"),
                              categories=fields.get("categories"))
    print(v.render())
    return 0 if v.ok else 1


# ----------------------------------------------------------------- wiring ----
def add_commands(sub: Any) -> None:
    sp = sub.add_parser("model", help="which open-weight model to use, and whether it answers")
    sp.add_argument("--select", action="store_true", help="rank candidates and write the recommendation")
    sp.add_argument("--for-finetune", dest="for_finetune", action="store_true",
                    help="rank for fine-tune legality + free-tier fit, not just inference")
    sp.add_argument("--list", action="store_true", help="print the registry")
    sp.add_argument("--probe", action="store_true", default=True)
    sp.add_argument("--no-probe", dest="no_probe", action="store_true", help="config only, no network")
    sp.add_argument("--vram", type=float, help="override detected VRAM (GB)")
    sp.add_argument("--registry", help="models JSON (default config/models.json)")
    sp.add_argument("--provider", default="auto")
    sp.add_argument("--markdown", action="store_true", dest="markdown")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--out")
    sp.add_argument("--db")
    sp.set_defaults(fn=cmd_model)

    sp = sub.add_parser("dataset", help="build the fine-tuning corpus from gate-passed approved assets")
    sp.add_argument("--out", default="dataset")
    sp.add_argument("--include", nargs="*", help="globs (default: book chapters + seo articles)")
    sp.add_argument("--format", default="sharegpt", choices=["sharegpt", "alpaca"])
    sp.add_argument("--approvals", help="approval JSON (default config/approval.json)")
    sp.add_argument("--no-gate", dest="no_gate", action="store_true",
                    help="skip gate eligibility (not recommended: trains on unreviewed text)")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")
    sp.add_argument("--db")
    sp.set_defaults(fn=cmd_dataset)

    sp = sub.add_parser("finetune", help="emit QLoRA/Colab/Axolotl configs for that corpus")
    sp.add_argument("--dataset-dir", default="dataset")
    sp.add_argument("--out", default="training")
    sp.add_argument("--base", help="override base model (default: from the dataset's recommendation)")
    sp.add_argument("--db")
    sp.set_defaults(fn=cmd_finetune)

    sp = sub.add_parser("site", help="build the deployable mini-site (docs/) and enforce its link graph")
    sp.add_argument("--out", default=site.DEFAULT_OUT, help="output folder (GitHub Pages serves this)")
    sp.add_argument("--config", help="site config (default config/site.json)")
    sp.add_argument("--check", action="store_true",
                    help="verify without writing: dangling internal links, orphans, undisclosed "
                         "affiliate links, placeholders and gate errors all fail the build anyway")
    sp.add_argument("--allow-fail", dest="allow_fail", action="store_true",
                    help="print the findings but exit 0 (weekly cron noise reduction)")
    sp.add_argument("--report", help="also write a build report here")
    sp.add_argument("--db")
    sp.set_defaults(fn=cmd_site)

    sp = sub.add_parser("eval", help="grade the work: task cases, adversarial battery, style metrics")
    sp.add_argument("--cases", default="config/eval_prompts.json")
    sp.add_argument("--grade", nargs="*", help="files/dirs to style-grade (offline, no model needed)")
    sp.add_argument("--pattern", nargs="*", default=["**/*.md"],
                    help="glob used when --grade names a directory")
    sp.add_argument("--provider", default="auto")
    sp.add_argument("--self-check", dest="self_check", action="store_true",
                    help="grade committed assets only; never calls a model")
    sp.add_argument("--no-repo-grade", dest="no_repo_grade", action="store_true")
    sp.add_argument("--strict", action="store_true", help="exit 1 on any failing case")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--out")
    sp.add_argument("--compare", nargs=2, metavar=("BASE", "CURRENT"),
                    help="two eval JSONs (e.g. base model vs fine-tuned)")
    sp.add_argument("--tolerance", type=float, default=2.0)
    sp.add_argument("--db")
    sp.set_defaults(fn=cmd_eval)

    sp = sub.add_parser("work", help="draft → gate → repair → validate, for every selected idea")
    sp.add_argument("--idea")
    sp.add_argument("--max-ideas", type=int, default=3)
    sp.add_argument("--sections", type=int, default=4)
    sp.add_argument("--words", type=int, default=700)
    sp.add_argument("--max-attempts", type=int, default=pipeline.REPAIR_MAX_DEFAULT)
    sp.add_argument("--out-dir", default="out")
    sp.add_argument("--provider", default="auto")
    sp.add_argument("--no-probe", dest="no_probe", action="store_true")
    sp.add_argument("--allow-fail", dest="allow_fail", action="store_true")
    sp.add_argument("--verbose", action="store_true")
    sp.add_argument("--db")
    sp.set_defaults(fn=cmd_work)

    sp = sub.add_parser("approve", help="sign off an asset so it may enter the training corpus")
    sp.add_argument("paths", nargs="*")
    sp.add_argument("--by", help="who signed off (a name, not a role)")
    sp.add_argument("--config", help="write to config/approval.json (tracked) instead of .wealforge/")
    sp.add_argument("--revoke", action="store_true")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--db")
    sp.set_defaults(fn=cmd_approve)

    sp = sub.add_parser("validate", help="check a KDP metadata JSON against platform limits")
    sp.add_argument("file")
    sp.set_defaults(fn=cmd_validate)
