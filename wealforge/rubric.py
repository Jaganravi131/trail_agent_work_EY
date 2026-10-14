"""Scoring rubric: weighted axes, loaded from config/rubric.json.

Data-driven on purpose. When the operator disagrees with a weight, the fix is
a JSON edit and a re-run, not a code change — and the ledger stores the rubric
version used for each score so old decisions stay explainable after the weights
change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_AXES: tuple[dict[str, Any], ...] = (
    {"key": "feasibility", "label": "Feasibility at $0", "weight": 3,
     "guide": "5 = genuinely free to ship: no ads, no inventory, no paid tool. 1 = needs capital."},
    {"key": "speed", "label": "Time to first dollar", "weight": 2,
     "guide": "5 = plausible sale inside 30 days. 1 = 6+ months before revenue is meaningful."},
    {"key": "defensibility", "label": "Defensibility", "weight": 3,
     "guide": "5 = a lazy competitor cannot clone it in an afternoon. 1 = a prompt does it."},
    {"key": "quality", "label": "Output quality vs. average human in niche", "weight": 2,
     "guide": "5 = a practitioner would call it good. 1 = generic listicle."},
    {"key": "compounding", "label": "Compounding / scalability", "weight": 2,
     "guide": "5 = asset #1 makes asset #20 materially cheaper (shared audience, tooling, list)."})


@dataclass(frozen=True)
class Axis:
    key: str
    label: str
    weight: int
    guide: str


@dataclass(frozen=True)
class Rubric:
    """Weighted 1-5 axes normalised to a 0-100 total.

    Normalisation matters: without it, changing an axis weight silently changes
    every historical total, and 'min_score' thresholds become meaningless.
    """
    version: str = "1"
    axes: tuple[Axis, ...] = tuple(Axis(**a) for a in DEFAULT_AXES)
    top_n: int = 2
    min_total: float = 70.0
    tie_break: tuple[str, ...] = ("speed", "defensibility", "quality")
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # ---- construction -----------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Rubric":
        if path is None:
            return cls()
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"rubric config not found: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        axes = tuple(Axis(**a) for a in data.pop("axes", DEFAULT_AXES))
        return cls(axes=axes, notes=data.pop("notes", ""), raw=data, **{
            k: v for k, v in data.items() if k in {"version", "top_n", "min_total", "tie_break"}})

    # ---- queries ----------------------------------------------------------
    @property
    def axis_keys(self) -> tuple[str, ...]:
        return tuple(a.key for a in self.axes)

    @property
    def max_raw(self) -> int:
        return 5 * sum(a.weight for a in self.axes)

    def normalise(self, raw_total: int) -> float:
        if self.max_raw == 0:
            raise ValueError("rubric has no axes with weight > 0")
        return round(100.0 * raw_total / self.max_raw, 1)

    def score(self, axis_scores: dict[str, int]) -> dict[str, Any]:
        """Score one idea. Unknown keys and missing axes are errors, not warnings:
        a silently-misspelled axis key would quietly change the ranking."""
        unknown = sorted(set(axis_scores) - set(self.axis_keys))
        if unknown:
            raise KeyError(f"unknown rubric axes {unknown}; valid: {list(self.axis_keys)}")
        missing = sorted(set(self.axis_keys) - set(axis_scores))
        if missing:
            raise KeyError(f"idea is missing rubric axes {missing}")
        parts: dict[str, int] = {}
        raw_total = 0
        for axis in self.axes:
            val = int(axis_scores[axis.key])
            if not 1 <= val <= 5:
                raise ValueError(f"{axis.key}={val} out of range 1..5")
            parts[axis.key] = val
            raw_total += val * axis.weight
        total = self.normalise(raw_total)
        flags = [f"below min_total {self.min_total}"] if total < self.min_total else []
        return {"axes": parts, "raw_total": raw_total, "total": total, "flags": flags}

    def rank(self, scored: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sort by total desc, then by tie-break axes, then title for stability.

        Without the final title key two equal-scoring ideas would reorder between
        runs, and the operator would rightly distrust the 'top pick'.
        """
        def key(item: dict[str, Any]):
            axes = item.get("scores", {})
            return (-float(item.get("total", 0.0)),
                    tuple(-int(axes.get(k, 0)) for k in self.tie_break),
                    str(item.get("title", "")))
        return sorted(scored, key=key)

    def select(self, scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Top N that clear min_total. Deliberately may return fewer than N —
        'pick the best two of ten' must not become 'publish two mediocre things'."""
        ranked = self.rank(scored)
        return [i for i in ranked if float(i.get("total", 0)) >= self.min_total][: self.top_n]

    def as_markdown(self) -> str:
        lines = [f"# Rubric v{self.version}", ""]
        if self.notes:
            lines += [self.notes, ""]
        lines += ["| Axis | Weight | 1 means …  /  5 means … |", "|---|---|---|"]
        lines += [f"| {a.label} | ×{a.weight} | {a.guide} |" for a in self.axes]
        lines += ["",
                  f"Total = Σ(score × weight), normalised to 100 (max raw {self.max_raw}). "
                  f"Selection: top {self.top_n} with total ≥ {self.min_total}. "
                  f"Tie-break order: {', '.join(self.tie_break)}.", ""]
        return "\n".join(lines)
