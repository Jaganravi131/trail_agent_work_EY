"""Measurement + diagnosis: the half of the loop that decides what happens next.

Two jobs:
1. Read the tracker the operator already fills in (one CSV, Sunday, 15 minutes) and
   turn it into trends — not a dashboard.
2. Walk the diagnosis tree and emit exactly **one** experiment. "Change one
   variable per 14 days" is the whole difference between iteration and noise, so
   `diagnose()` returns a single action and refuses to return a list.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

NUMERIC_HINT = re.compile(r"(units|visits|clicks|impressions|sales|subscribers|reviews|pages|adds|prints|downloads)")


@dataclass
class Week:
    week_start: str
    values: dict[str, float] = field(default_factory=dict)

    def get(self, k: str, default: float = 0.0) -> float:
        return self.values.get(k, default)


def load_tracker(path: str | Path) -> list[Week]:
    rows: list[Week] = []
    p = Path(path)
    if not p.exists():
        return rows
    with p.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            vals: dict[str, float] = {}
            for k, v in r.items():
                if k in (None, "week_start", "notes", "next_single_experiment", "top performing post"):
                    continue
                try:
                    vals[k] = float(str(v).strip().replace("$", "").replace(",", "") or 0)
                except ValueError:
                    continue
            if r.get("week_start"):
                rows.append(Week(r["week_start"], vals))
    rows.sort(key=lambda w: w.week_start)
    return rows


def parse_week(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def series(weeks: list[Week], key: str) -> list[tuple[str, float]]:
    return [(w.week_start, w.get(key)) for w in weeks]


def trend(points: list[tuple[str, float]]) -> dict[str, Any]:
    """Linear slope per week over the non-zero tail, plus 'is this real' guard.

    A two-point trend on a tracker with one filled row is noise dressed as data,
    so it is reported as insufficient rather than as a number.
    """
    ys = [y for _, y in points]
    n = len(ys)
    if n < 3:
        return {"ok": False, "slope": 0.0, "n": n, "verdict": "insufficient data (need ≥3 weeks)"}
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    pct = (ys[-1] - ys[0]) / ys[0] * 100 if ys[0] else float("inf")
    verdict = "growing" if slope > max(0.05, abs(my) * 0.03) else \
              "declining" if slope < -max(0.05, abs(my) * 0.03) else "flat"
    return {"ok": True, "slope": round(slope, 2), "n": n, "first": ys[0], "last": ys[-1],
            "change_pct": (None if pct == float("inf") else round(pct, 1)), "verdict": verdict}


def funnel(weeks: list[Week]) -> dict[str, Any]:
    """Stage-by-stage conversion, whatever the operator has filled in."""
    def tot(k: str) -> float:
        return sum(w.get(k) for w in weeks)
    out: dict[str, Any] = {}
    imp, clk = tot("site_impressions_gsc") + tot("kdp_impressions"), tot("site_clicks_gsc") + tot("kdp_clicks_to_store")
    units = tot("units_ebook") + tot("units_paperback")
    subs, sales = tot("email_subscribers"), tot("gumroad_sales")
    views = tot("tool_visits")
    if imp:
        out["impressions→clicks_pct"] = round(100 * clk / imp, 2)
    if clk:
        out["clicks→units_pct"] = round(100 * units / clk, 2)
    if views:
        out["visits→email_pct"] = round(100 * subs / views, 2)
        out["visits→units"] = round(units / views, 4)
    out["units"] = units
    out["gumroad_sales"] = sales
    out["reviews_total"] = tot("reviews_total")
    out["net_usd"] = round(tot("kdp_net_usd") + tot("gumroad_net_usd"), 2)
    return out


def diagnose(weeks: list[Week], days_live: int, reviews_total: int | None = None,
             cover_tested: bool = False) -> dict[str, Any]:
    """The iteration playbook, as code. Returns ONE experiment."""
    if len(weeks) < 2:
        return {"stage": "no-data", "confidence": "none",
            "finding": "Fewer than two filled tracker weeks.",
            "experiment": "Fill the tracker for 2 more weeks. Zero weeks of data has killed more "
                          "projects than any market force — the ramp looks like failure without it.",
            "do_not": "Do not change price, cover or niche yet. You would be tuning on noise."}

    f = funnel(weeks)
    last = weeks[-1]
    reach = last.get("site_impressions_gsc") + last.get("kdp_impressions") + last.get("tool_visits")
    clicks = last.get("site_clicks_gsc") + last.get("kdp_clicks_to_store")
    units = last.get("units_ebook") + last.get("units_paperback")
    rev = reviews_total if reviews_total is not None else last.get("reviews_total")
    pos = last.get("avg_position")

    # Ordered: reach → click → conversion → read → money. Stop at the first failure.
    if reach < 150:
        return {"stage": "reach", "confidence": "high",
                "finding": f"{reach:.0f} impressions+visits in the latest week. Nobody is seeing the thing.",
                "experiment": ("Fix packaging first: cover legible at 100 px and audience nouns in the "
                               "subtitle. " +
                               ("Cover has not been tested at thumbnail size — that is the 20-minute fix."
                                if not cover_tested else
                                "Cover tested, so this is distribution: ship 10 calendar posts this week "
                                "before touching the asset.")),
                "do_not": "Do not write new assets. New assets do not fix invisibility."}
    if clicks and clicks / max(reach, 1) < 0.006 and days_live > 21:
        return {"stage": "click", "confidence": "medium",
                "finding": f"Seen {reach:.0f}×, clicked {clicks:.0f}× (<0.6%). Snippet problem, not product.",
                "experiment": ("Rewrite the title/H1 + first 100 words to answer the query verbatim, and "
                               f"{'get to 10 reviews via 10 personal asks' if (rev or 0) < 10 else 're-test the cover'}."),
                "do_not": "Do not lower the price to buy clicks. That converts a packaging problem into a margin problem."}
    if clicks and units and units / clicks < 0.01:
        return {"stage": "convert", "confidence": "high",
                "finding": f"{clicks:.0f} store clicks → {units:.1f} units (<1%).",
                "experiment": (f"Reviews: {rev or 0} on file. " +
                               ("Ten individual asks, personal, no conditions. This is the lever." if (rev or 0) < 10
                                else "Description's first two lines must carry a mechanism; add the table of contents to Look Inside.")),
                "do_not": "Do not change the niche at day " + str(days_live) + ". Conversion is a packaging variable."}
    if pos and 12 <= pos <= 30:
        return {"stage": "ranking", "confidence": "medium",
                "finding": f"Average position {pos:.0f} — page 2. Clicks are suppressed by position, not content.",
                "experiment": "Internal links from the tool page + one new backlink (guest-value post). "
                              "On page 2, a title/meta rewrite usually beats more content.",
                "do_not": "Do not publish 10 new articles; a thin site with no authority ranks nowhere."}
    if units and last.get("kkenp_pages", 0) / max(units, 1) < 40:
        return {"stage": "read-through", "confidence": "low",
                "finding": "Bought but not finished (KENP pages per sale < 40).",
                "experiment": "Move the usable numbers into chapter 1 and put a worksheet in the middle. Prose is not the fix.",
                "do_not": "Do not add length. Length is not the same as density."}

    if days_live >= 60 and units * max(len(weeks), 1) < 15 and (rev or 0) < 10:
        return {"stage": "kill-or-packaging", "confidence": "high",
                "finding": f"{days_live} days, {units * len(weeks):.0f} units total, {rev or 0} reviews.",
                "experiment": "One full packaging redo (cover + subtitle + description), then 30 days. "
                              "If still nothing at day 100: keep the tool, the site and the list; retire "
                              "the paid product and re-point the audience at the $9 item.",
                "do_not": "Do not abandon the machine — that's the part that compounds."}
    return {"stage": "healthy", "confidence": "medium",
            "finding": f"No failure in the funnel. Latest: {units:.1f} units, {f.get('clicks→units_pct', 0)}% click→unit.",
            "experiment": "Grow assortment, not iteration: next asset in the same niche (book 2 / the kit). "
                          "Same reader, ~20% of the cost.",
            "do_not": "Do not diversify into a second niche while the first is compounding."}


def report_markdown(weeks: list[Week], days_live: int, diagnosis: dict[str, Any] | None = None) -> str:
    keys = sorted({k for w in weeks for k in w.values if NUMERIC_HINT.search(k)})
    lines = [f"# Metrics report — {len(weeks)} weeks, day {days_live}", ""]
    if weeks:
        lines += ["| week | " + " | ".join(k for k in keys[:8]) + " |",
                  "|---" * (len(keys[:8]) + 1) + "|"]
        for w in weeks[-8:]:
            lines.append(f"| {w.week_start} | " + " | ".join(f"{w.get(k):g}" for k in keys[:8]) + " |")
        lines += ["", "## Trends", ""]
        for k in keys[:8]:
            t = trend(series(weeks, k))
            if t.get("ok"):
                lines.append(f"- **{k}**: {t['first']:g} → {t['last']:g} "
                             f"({t['change_pct']}%), slope {t['slope']}/wk — *{t['verdict']}*")
            else:
                lines.append(f"- **{k}**: {t['verdict']}")
        lines += ["", "## Funnel", "", "```"] + [f"{k}: {v}" for k, v in funnel(weeks).items()] + ["```"]
    else:
        lines.append("_No tracker rows parsed._")
    d = diagnosis or diagnose(weeks, days_live)
    lines += ["", "## Diagnosis", "", f"**Stage:** `{d['stage']}` (confidence {d.get('confidence')})", "",
              f"- Finding: {d['finding']}", f"- Experiment (one, 14 days): {d['experiment']}",
              f"- Explicitly not doing: {d['do_not']}", ""]
    return "\n".join(lines) + "\n"
