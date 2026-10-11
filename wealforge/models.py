"""Domain objects: Idea, Asset, and the stage machine.

The stage machine is the only bit of 'enforcement' the agent has, so it is
strict: an asset cannot reach `packaged` without a passing compliance gate, and
cannot reach `ready` without a publish checklist having been emitted. Everything
else here is plain data.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------- stages ----
STAGES: tuple[str, ...] = (
    "proposed",     # scored, not yet chosen
    "selected",     # chosen by the loop for this cycle
    "briefed",      # brief + asset plan written; no prose yet
    "drafted",      # prose exists on disk
    "gated",        # compliance gate run (and passed)
    "packaged",     # build outputs + backend/metadata sheets written
    "ready",        # human-facing publish checklist emitted
    "published",    # operator says it's live (agent cannot do this itself)
    "measured",     # tracker row recorded and diagnosis run
)

_ALLOWED: dict[str, set[str]] = {
    "proposed": {"selected", "dropped"},
    "selected": {"briefed", "proposed", "dropped"},
    "briefed": {"drafted", "selected", "dropped"},
    "drafted": {"gated", "briefed", "dropped"},
    "gated": {"packaged", "drafted", "dropped"},
    "packaged": {"ready", "gated", "dropped"},
    "ready": {"published", "packaged", "dropped"},
    "published": {"measured", "published", "dropped"},
    "measured": {"published", "briefed", "drafted", "dropped"},
    "dropped": set(),
}

#: Transitions that require evidence, checked by Store.set_stage.
_REQUIRED_EVIDENCE: dict[str, str] = {
    "gated": "gate_report",
    "packaged": "build_artifact",
    "ready": "publish_checklist",
    "published": "live_url",
}


class StageError(RuntimeError):
    pass


def can_move(frm: str, to: str) -> bool:
    if frm not in _ALLOWED:
        raise StageError(f"unknown stage {frm!r}")
    return to in _ALLOWED[frm]


# ----------------------------------------------------------------- ideas ----
def slugify(text: str, maxlen: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:maxlen].rstrip("-") or "untitled"


@dataclass
class Idea:
    """A candidate asset, plus the rubric verdict that got it picked."""
    id: str
    title: str
    niche: str
    platform: str                      # kdp | seo-site | digital-product | code | audience
    thesis: str = ""                    # one sentence: why anyone pays attention
    audience: str = ""                  # who, concretely enough to write for them
    angles: list[str] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)
    total: float = 0.0
    raw_total: int = 0
    flags: list[str] = field(default_factory=list)
    rubric_version: str = "1"
    stage: str = "proposed"
    blocked: list[str] = field(default_factory=list)   # auto-rejected at scan time
    created_utc: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, title: str, niche: str, platform: str, **kw: Any) -> "Idea":
        base = slugify(title)
        used = kw.pop("id", None)
        return cls(id=used or f"{platform}:{base}", title=title, niche=niche, platform=platform,
                   created_utc=utc_now(), **kw)

    @property
    def slug(self) -> str:
        return slugify(self.title)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked)


@dataclass
class Asset:
    """A produced file set for an idea: manuscript, article, tool, product…"""
    id: str
    idea_id: str
    kind: str                          # book | article | tool | product | kit
    path: str                          # repo-relative output dir
    stage: str = "briefed"
    word_count: int = 0
    content_sha: str = ""
    evidence: dict[str, str] = field(default_factory=dict)
    updated_utc: str = ""

    @classmethod
    def new(cls, idea_id: str, kind: str, path: str, **kw: Any) -> "Asset":
        return cls(id=f"{idea_id}::{kind}", idea_id=idea_id, kind=kind, path=path,
                   updated_utc=utc_now(), **kw)


# ----------------------------------------------------------------- util ----
def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: str | Path) -> str:
    from pathlib import Path as _P
    p = _P(path)
    if not p.exists():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def count_words(text: str) -> int:
    """Prose words only: fenced code, tables and HTML tags are excluded so that a
    word count is an honest measure of reading volume, not of formatting."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = "\n".join(l for l in text.splitlines() if not re.match(r"^\s*\|?[\s:-]*\|", l))
    return len(re.findall(r"[A-Za-z0-9'’\-]+", text))
