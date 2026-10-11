"""SQLite ledger: what was considered, what was picked, why, and what happened.

Three tables that matter, one design opinion:

* `ideas` + `decisions` — the scorecard and the *selection*, stored together with
  the rubric version, so "why did the agent ship this and not that" is answerable
  in month four instead of being a vibe.
* `assets` — every asset carries its stage, its content hash and its gate verdict.
  A stage bump that isn't allowed by the state machine raises, so nothing can be
  quietly marked "ready" without having been gated.
* `events` — append-only. This is the file the diagnosis step reads.

The db lives in the repo at `.wealforge/ledger.sqlite3` and is git-ignored by
default: it is operator state, not published work.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from . import models

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, niche TEXT, platform TEXT,
  thesis TEXT, audience TEXT, angles TEXT, scores TEXT, total REAL, raw_total INTEGER,
  flags TEXT, rubric_version TEXT, stage TEXT, blocked TEXT, meta TEXT, created_utc TEXT
);
CREATE TABLE IF NOT EXISTS assets(
  id TEXT PRIMARY KEY, idea_id TEXT, kind TEXT, path TEXT, stage TEXT,
  word_count INTEGER, content_sha TEXT, evidence TEXT, updated_utc TEXT
);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, rubric_version TEXT,
  picked TEXT, rejected TEXT, rationale TEXT, created_utc TEXT
);
CREATE TABLE IF NOT EXISTS gates(
  id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT, audience TEXT, passed INTEGER,
  errors INTEGER, warnings INTEGER, words INTEGER, report TEXT, created_utc TEXT
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, ref TEXT, payload TEXT, created_utc TEXT
);
CREATE TABLE IF NOT EXISTS metrics(
  week_start TEXT PRIMARY KEY, payload TEXT, updated_utc TEXT
);
"""


class Store:
    def __init__(self, db_path: str | Path = ".wealforge/ledger.sqlite3"):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.row_factory = sqlite3.Row
        self._con.executescript(SCHEMA)
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------ ideas ----
    def put_idea(self, idea: models.Idea) -> None:
        row = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v)
               for k, v in asdict(idea).items()}
        self._con.execute(
            """INSERT INTO ideas(id,title,niche,platform,thesis,audience,angles,scores,total,
               raw_total,flags,rubric_version,stage,blocked,meta,created_utc)
               VALUES(:id,:title,:niche,:platform,:thesis,:audience,:angles,:scores,:total,
               :raw_total,:flags,:rubric_version,:stage,:blocked,:meta,:created_utc)
               ON CONFLICT(id) DO UPDATE SET
                 scores=excluded.scores, total=excluded.total, raw_total=excluded.raw_total,
                 flags=excluded.flags, rubric_version=excluded.rubric_version,
                 stage=excluded.stage, blocked=excluded.blocked, meta=excluded.meta""",
            row)
        self._con.commit()

    def get_idea(self, idea_id: str) -> dict[str, Any] | None:
        r = self._con.execute("SELECT * FROM ideas WHERE id=?", (idea_id,)).fetchone()
        return _hydrate_idea(r) if r else None

    def ideas(self, stage: str | None = None, min_total: float | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM ideas", []
        wh = []
        if stage:
            wh.append("stage=?"); args.append(stage)
        if min_total is not None:
            wh.append("total>=?"); args.append(min_total)
        if wh:
            q += " WHERE " + " AND ".join(wh)
        q += " ORDER BY total DESC, title"
        return [_hydrate_idea(r) for r in self._con.execute(q, args)]

    # ----------------------------------------------------------- stages ----
    def set_stage(self, table: str, row_id: str, to: str, evidence: dict[str, str] | None = None) -> None:
        """Move an idea or asset through its stage machine, with evidence checks."""
        assert table in {"ideas", "assets"}
        col = "stage"
        cur = self._con.execute(f"SELECT {col} FROM {table} WHERE id=?", (row_id,))
        r = cur.fetchone()
        if r is None:
            raise models.StageError(f"{table} row {row_id!r} does not exist — no silent upserts")
        frm = r[col]
        if frm == to:
            return
        if table == "ideas":
            if to not in {"selected", "proposed", "dropped"}:
                raise models.StageError(f"ideas only move among proposed/selected/dropped, got {to!r}")
        else:
            if not models.can_move(frm, to):
                raise models.StageError(f"illegal asset transition {frm} → {to} "
                                        f"(allowed: {sorted(models._ALLOWED.get(frm, ()))})")
            need = models._REQUIRED_EVIDENCE.get(to)
            if need and not (evidence or {}).get(need):
                raise models.StageError(f"cannot move {frm} → {to} without evidence[{need!r}]. "
                                        f"Run the {need.replace('_', ' ')} step first.")
        if table == "assets":
            self._con.execute("UPDATE assets SET stage=?, updated_utc=? WHERE id=?",
                              (to, models.utc_now(), row_id))
        else:
            self._con.execute("UPDATE ideas SET stage=? WHERE id=?", (to, row_id))
        self.event("stage", f"{table}:{row_id}", {"from": frm, "to": to, "evidence": evidence or {}})

    # ----------------------------------------------------------- assets ----
    def put_asset(self, asset: models.Asset) -> None:
        row = {k: (json.dumps(v) if isinstance(v, dict) else v) for k, v in asdict(asset).items()}
        self._con.execute(
            """INSERT INTO assets(id,idea_id,kind,path,stage,word_count,content_sha,evidence,updated_utc)
               VALUES(:id,:idea_id,:kind,:path,:stage,:word_count,:content_sha,:evidence,:updated_utc)
               ON CONFLICT(id) DO UPDATE SET stage=excluded.stage, word_count=excluded.word_count,
                 content_sha=excluded.content_sha, evidence=excluded.evidence,
                 updated_utc=excluded.updated_utc""", row)
        self._con.commit()

    def assets(self, idea_id: str | None = None) -> list[dict[str, Any]]:
        if idea_id:
            rows = self._con.execute("SELECT * FROM assets WHERE idea_id=? ORDER BY kind", (idea_id,))
        else:
            rows = self._con.execute("SELECT * FROM assets ORDER BY updated_utc DESC")
        out = []
        for r in rows:
            d = dict(r)
            d["evidence"] = json.loads(d.get("evidence") or "{}")
            out.append(d)
        return out

    # -------------------------------------------------------- decisions ----
    def record_decision(self, day: str, rubric_version: str, picked: list[str],
                        rejected: list[str], rationale: str) -> None:
        self._con.execute(
            "INSERT INTO decisions(day,rubric_version,picked,rejected,rationale,created_utc) "
            "VALUES(?,?,?,?,?,?)",
            (day, rubric_version, json.dumps(picked), json.dumps(rejected), rationale, models.utc_now()))
        self._con.commit()

    def decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._con.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def record_gate(self, report: Any) -> None:
        self._con.execute(
            "INSERT INTO gates(target,audience,passed,errors,warnings,words,report,created_utc) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (report.target, getattr(report, "audience", "reader"), int(report.passed),
             len(report.errors), len(report.warnings), report.words,
             json.dumps(report.as_dict(), default=str), models.utc_now()))
        self._con.commit()

    def last_gate(self, target: str) -> dict[str, Any] | None:
        r = self._con.execute("SELECT * FROM gates WHERE target=? ORDER BY id DESC LIMIT 1",
                             (target,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["report"] = json.loads(d["report"])
        return d

    def gates_since(self, since: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._con.execute(
            "SELECT * FROM gates WHERE created_utc>=? ORDER BY id DESC", (since,))]

    # ----------------------------------------------------------- event ----
    def event(self, kind: str, ref: str, payload: dict[str, Any] | None = None) -> None:
        self._con.execute("INSERT INTO events(kind,ref,payload,created_utc) VALUES(?,?,?,?)",
                          (kind, ref, json.dumps(payload or {}, default=str), models.utc_now()))
        self._con.commit()

    def events(self, limit: int = 40, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            rows = self._con.execute("SELECT * FROM events WHERE kind=? ORDER BY id DESC LIMIT ?",
                                     (kind, limit))
        else:
            rows = self._con.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # --------------------------------------------------------- metrics ----
    def put_metrics(self, week_start: str, payload: dict[str, Any]) -> None:
        self._con.execute(
            "INSERT INTO metrics(week_start,payload,updated_utc) VALUES(?,?,?) "
            "ON CONFLICT(week_start) DO UPDATE SET payload=excluded.payload, updated_utc=excluded.updated_utc",
            (week_start, json.dumps(payload), models.utc_now()))
        self._con.commit()

    def metrics(self) -> list[dict[str, Any]]:
        out = []
        for r in self._con.execute("SELECT * FROM metrics ORDER BY week_start"):
            d = json.loads(r["payload"]); d["week_start"] = r["week_start"]
            out.append(d)
        return out

    # ------------------------------------------------------------ misc ----
    def stats(self) -> dict[str, int]:
        q = lambda sql: self._con.execute(sql).fetchone()[0]  # noqa: E731 - tiny helper, single use
        return {"ideas": q("SELECT COUNT(*) FROM ideas"),
                "assets": q("SELECT COUNT(*) FROM assets"),
                "gates": q("SELECT COUNT(*) FROM gates"),
                "decisions": q("SELECT COUNT(*) FROM decisions"),
                "events": q("SELECT COUNT(*) FROM events"),
                "metric_weeks": q("SELECT COUNT(*) FROM metrics")}


def _hydrate_idea(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("angles", "scores", "flags", "blocked", "meta"):
        d[k] = json.loads(d.get(k) or "[]") if k in ("angles", "flags", "blocked") else json.loads(d.get(k) or "{}")
    return d


@contextmanager
def open_store(path: str | Path | None = None) -> Iterator[Store]:
    st = Store(path or default_db())
    try:
        yield st
    finally:
        st.close()


def default_db() -> Path:
    import os
    env = os.environ.get("WEALFORGE_DB")
    if env:
        return Path(env)
    root = Path.cwd()
    for base in [root, *root.parents]:
        if (base / ".git").exists():
            return base / ".wealforge" / "ledger.sqlite3"
    return root / ".wealforge" / "ledger.sqlite3"
