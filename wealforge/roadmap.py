"""The 90-day schedule, as data the operator can disagree with.

`config/schedule.json` holds phases and per-day tasks. `wf today` works out which
day of the operation it is, prints that day's human actions, and flags any asset
whose stage has gone stale (drafted but never gated is the usual one).

Everything here is a *human* task list plus agent-runnable checks. The agent does
not create accounts, log in anywhere, or publish; those need the operator, by
design and by platform terms.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import models

DEFAULT_SCHEDULE: dict[str, Any] = {
    "start": None,
    "phases": [
        {"name": "ship", "days": [1, 7], "goal": "Assets live and reachable", "tasks": {
            "1": ["Serve/commit the free tool; put the real URL into its canonical + CTA",
                  "`wf gate 03-tool/ 04-seo/` → zero errors",
                  "Fact-check pass: verify every number and citation against a primary source today"],
            "2": ["Cover built from the brief; thumbnail test on a real results screenshot",
                  "Create the marketplace account (identity verification takes ~24h — start now)"],
            "3": ["Author profile + email publication + digital-product store, same handle everywhere",
                  "Upload ebook; answer the AI-content declaration truthfully; set price from `wf price`"],
            "4": ["Paperback via the digital proof ($0) and approve it", "Publish the 3 pillar articles with internal links"],
            "5": ["Submit sitemap to Search Console + Bing (free)", "`wf score --seed config/scan_seed.json` re-run so the ledger has today's ranking"],
            "6": ["Day-1 calendar post: tool first, sell second"],
            "7": ["Fill the tracker, even with zeros. Week of zeros is the baseline that makes later diagnosis possible."],
        }},
        {"name": "proof", "days": [8, 21], "goal": "Evidence that strangers care: 10 reviews, 50 tool visits/wk",
         "tasks": {
            "10": ["Format the low-content kit from the existing worksheets (formatting, not writing)"],
            "12": ["List the kit; add the book-owner discount line"],
            "14": ["Step price up to list, announced with the reason"],
            "16": ["10 personal review asks, individually, no conditions attached"],
            "19": ["Reply to every review, including the critical ones; no defensiveness"],
            "21": ["`wf report` → read the diagnosis before touching anything"],
         }},
        {"name": "distribute", "days": [22, 45], "goal": "Two channels working, not twenty tried",
         "tasks": {
            "24": ["5 community replies at full quality, no links unless asked"],
            "27": ["Free-promo window on the ebook if enrolled in the 90-day exclusivity programme"],
            "30": ["One PDF flyer to training coordinators / staff reps / department libraries"],
            "35": ["`wf report`; run exactly one experiment for 14 days"],
            "45": ["Decision gate: keep / repackage / pivot. Use `wf report` output, not a feeling."],
         }},
        {"name": "compound", "days": [46, 90], "goal": "Second asset at ~20% of the first one's cost",
         "tasks": {
            "50": ["`wf pick` on the scored ledger → next asset is chosen by the rubric, not by boredom"],
            "60": ["Draft asset 2 with `wf build`; the gate is the same gate"],
            "75": ["Bundle both assets; raise the low-price product to a system price"],
            "90": ["Full review: revenue vs the model, hours invested per $, and one pivot decision"],
         }},
    ],
}


@dataclass
class Day:
    number: int
    date_str: str
    phase: str
    goal: str
    tasks: list[str]
    stale: list[str]

    def render(self) -> str:
        head = f"Day {self.number} · {self.date_str} · phase **{self.phase}** — {self.goal}"
        body = "\n".join(f"- [ ] {t}" for t in self.tasks) or "- [ ] No scheduled task. Ship the next asset or take the day."
        stale = ("\n".join(f"  ! {s}" for s in self.stale)) if self.stale else ""
        return head + "\n\n" + body + (("\n\n**Stale:**\n" + stale) if stale else "") + "\n"


def load(path: str | Path | None = None) -> dict[str, Any]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    for cand in (Path("config/schedule.json"), Path.cwd() / "config" / "schedule.json"):
        if cand.exists():
            return json.loads(cand.read_text(encoding="utf-8"))
    return DEFAULT_SCHEDULE


def day_of(start: date, today: date) -> int:
    return (today - start).days + 1


def for_date(schedule: dict[str, Any], today: date, start: date | None = None) -> Day:
    start = start or _start_from_marker(today)
    n = max(1, day_of(start, today))
    phase = _phase_of(schedule, n)
    exact = phase.get("tasks", {}).get(str(n))
    todays = [exact] if isinstance(exact, str) else list(exact or [])
    return Day(n, today.isoformat(), phase.get("name", "unscheduled"), phase.get("goal", ""),
               todays or _nearest(phase, n),
               stale=_staleness_hint(phase, n))


def _start_from_marker(today: date) -> date:
    """Day 1 is remembered in the ledger dir so `wf today` is stable across calls."""
    marker = Path(".wealforge") / "start.txt"
    if marker.exists():
        try:
            return date.fromisoformat(marker.read_text().strip())
        except ValueError:
            pass
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(today.isoformat())
    return today


def set_start(today: date | None = None) -> date:
    today = today or date.today()
    Path(".wealforge").mkdir(exist_ok=True)
    (Path(".wealforge") / "start.txt").write_text(today.isoformat())
    return today


def _phase_of(schedule: dict[str, Any], n: int) -> dict[str, Any]:
    for ph in schedule.get("phases", []):
        lo, hi = ph.get("days", [0, 0])
        if lo <= n <= hi:
            return ph
    return {"name": "beyond-90", "goal": "Steady state: 1 asset/week + weekly review", "tasks": {}}


def _nearest(phase: dict[str, Any], n: int) -> list[str]:
    """No task scheduled for today → show the nearest upcoming one, so the operator
    isn't told 'nothing to do' on day 13 of a 90-day plan."""
    tasks = phase.get("tasks", {})
    if not tasks:
        return []
    future = sorted((int(k) for k in tasks if int(k) >= n))
    past = sorted((int(k) for k in tasks if int(k) < n), reverse=True)
    out: list[str] = []
    for k in (future[:1] + past[:1]):
        v = tasks[str(k)]
        for line in ([v] if isinstance(v, str) else list(v)):
            out.append(f"[day {k}] {line}")
    return out


def _staleness_hint(phase: dict[str, Any], n: int) -> list[str]:
    first = min((int(k) for k in phase.get("tasks", {})), default=n)
    return [f"phase started day {first}; day {n} is {(n - first)} days in — if nothing shipped, "
            f"the blocker is a task, not the plan"] if n - first > 5 else []


def roadmap_markdown(schedule: dict[str, Any], start: date | None = None) -> str:
    start = start or date.today()
    lines = ["# Roadmap", "", f"Day 1 = **{start.isoformat()}**. Human time only; the agent "
             "prepares assets and checks them, it does not publish.", "",
             "| day | phase | task |", "|---|---|---|"]
    for ph in schedule.get("phases", []):
        for k in sorted(ph.get("tasks", {}), key=int):
            lines.append(f"| {k} | {ph['name']} | {ph['tasks'][k]} |")
    lines += ["", "## What the agent can do unattended (free CI)", "",
              "`.github/workflows/agent.yml` runs weekly: re-score the idea pool, re-gate every "
              "reader-facing file, and open an issue only when something actually fails. That is "
              "the whole automation budget a zero-capital operation needs — the judgement calls stay "
              "with a person, because that's what keeps the account alive.", ""]
    return "\n".join(lines) + "\n"
