"""Shared JARVIS state (SQLite): pending confirmations, tool-result feed, audit log.

Written by the jarvis-google MCP server (spawned by Hermes) and read by JARVIS Core.

Safety invariant: outbound / destructive Google actions are never executed by the tool the
model calls. The tool only *proposes* the action here. Execution happens exclusively via
``execute_action`` which JARVIS Core calls after the user confirms in the UI (voice or click).
The model has no tool that can confirm its own proposal.
"""
from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

STATE_DIR = Path(os.environ.get("JARVIS_STATE_DIR", Path.home() / ".hermes/jarvis/state"))
DB_PATH = STATE_DIR / "jarvis.db"
AUDIT_PATH = STATE_DIR / "actions.jsonl"
ACTION_TTL_S = 15 * 60
FEED_KEEP = 500

# When set (a list), record_result appends feed items to it instead of writing the feed. The showcase uses this to
# fetch real data ahead of time and reveal each display on cue. A ContextVar so asyncio.to_thread carries it along.
_CAPTURE: contextvars.ContextVar[list | None] = contextvars.ContextVar("jarvis_feed_capture", default=None)


@contextmanager
def capture():
    items: list[dict] = []
    tok = _CAPTURE.set(items)
    try:
        yield items
    finally:
        _CAPTURE.reset(tok)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
  id TEXT PRIMARY KEY,
  created REAL NOT NULL,
  kind TEXT NOT NULL,
  account TEXT NOT NULL,
  params TEXT NOT NULL,
  summary TEXT NOT NULL,
  preview TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending|executed|cancelled|expired|failed
  result TEXT,
  resolved REAL
);
CREATE TABLE IF NOT EXISTS feed (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  tool TEXT NOT NULL,
  account TEXT,
  args TEXT NOT NULL,
  result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS own_drafts (   -- drafts JARVIS created; the model may only edit these
  draft_id TEXT PRIMARY KEY,
  account TEXT NOT NULL,
  created REAL NOT NULL
);
"""


@contextmanager
def db():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        yield conn
    finally:
        conn.close()


def _audit(entry: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a") as f:
        f.write(json.dumps({"ts": time.time(), **entry}, default=str) + "\n")


# ---------------------------------------------------------------- feed (visual cards)

def record_result(tool: str, account: str | None, args: dict, result: Any) -> int:
    cap = _CAPTURE.get()
    if cap is not None:  # a showcase is preparing its displays: hold them instead of publishing now
        cap.append({"seq": 0, "ts": time.time(), "tool": tool, "account": account,
                    "args": json.loads(json.dumps(args, default=str)),
                    "result": json.loads(json.dumps(result, default=str))})
        return 0
    with db() as c:
        cur = c.execute(
            "INSERT INTO feed(ts, tool, account, args, result) VALUES (?,?,?,?,?)",
            (time.time(), tool, account, json.dumps(args, default=str), json.dumps(result, default=str)),
        )
        seq = int(cur.lastrowid or 0)
        c.execute("DELETE FROM feed WHERE seq <= ?", (seq - FEED_KEEP,))
        return seq


def feed_since(seq: int) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM feed WHERE seq > ? ORDER BY seq", (seq,)).fetchall()
    return [
        {"seq": r["seq"], "ts": r["ts"], "tool": r["tool"], "account": r["account"],
         "args": json.loads(r["args"]), "result": json.loads(r["result"])}
        for r in rows
    ]


def remember_own_draft(account: str, draft_id: str) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO own_drafts(draft_id, account, created) VALUES (?,?,?)",
                  (draft_id, account, time.time()))


def is_own_draft(account: str, draft_id: str) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM own_drafts WHERE draft_id=? AND account=?",
                         (draft_id, account)).fetchone() is not None


def audit(entry: dict) -> None:
    _audit(entry)


def feed_head() -> int:
    with db() as c:
        row = c.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM feed").fetchone()
    return int(row["s"])


# ---------------------------------------------------------------- pending actions

def propose(kind: str, account: str, params: dict, summary: str, preview: dict) -> dict:
    action_id = "act_" + uuid.uuid4().hex[:12]
    with db() as c:
        c.execute(
            "INSERT INTO actions(id, created, kind, account, params, summary, preview) VALUES (?,?,?,?,?,?,?)",
            (action_id, time.time(), kind, account, json.dumps(params, default=str), summary,
             json.dumps(preview, default=str)),
        )
    _audit({"event": "proposed", "id": action_id, "kind": kind, "account": account, "summary": summary})
    return {
        "status": "awaiting_user_confirmation",
        "action_id": action_id,
        "summary": summary,
        "note": ("NOT executed. The user must confirm this in the JARVIS interface. Tell the user what "
                 "will happen and ask them to confirm. Do not claim it was done."),
    }


def _row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "created": r["created"], "kind": r["kind"], "account": r["account"],
        "params": json.loads(r["params"]), "summary": r["summary"], "preview": json.loads(r["preview"]),
        "status": r["status"], "result": json.loads(r["result"]) if r["result"] else None,
    }


def expire_stale() -> None:
    with db() as c:
        c.execute("UPDATE actions SET status='expired', resolved=? WHERE status='pending' AND created < ?",
                  (time.time(), time.time() - ACTION_TTL_S))


def get_action(action_id: str) -> dict | None:
    expire_stale()
    with db() as c:
        r = c.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    return _row(r) if r else None


def pending_actions() -> list[dict]:
    expire_stale()
    with db() as c:
        rows = c.execute("SELECT * FROM actions WHERE status='pending' ORDER BY created").fetchall()
    return [_row(r) for r in rows]


def _claim(action_id: str, new_status: str) -> dict | None:
    """Atomically move pending -> new_status. Returns the action if this caller won the claim."""
    expire_stale()
    with db() as c:
        cur = c.execute("UPDATE actions SET status=?, resolved=? WHERE id=? AND status='pending'",
                        (new_status, time.time(), action_id))
        if cur.rowcount != 1:
            return None
        return _row(c.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())


def cancel_action(action_id: str, source: str = "user") -> bool:
    ok = _claim(action_id, "cancelled") is not None
    if ok:
        _audit({"event": "cancelled", "id": action_id, "source": source})
    return ok


# executors are registered by jarvis_google.tools (kind -> fn(account, **params) -> result)
EXECUTORS: dict[str, Callable[..., Any]] = {}


def executor(kind: str):
    def deco(fn):
        EXECUTORS[kind] = fn
        return fn
    return deco


def execute_action(action_id: str, source: str) -> dict:
    """Run a confirmed action. ONLY JARVIS Core (after explicit user confirmation) calls this."""
    from . import tools  # noqa: F401  (registers executors)
    from . import travel
    travel.register_executors()  # idempotent; survives a reload of this module (tests)

    action = _claim(action_id, "executing")
    if action is None:
        current = get_action(action_id)
        return {"ok": False, "error": f"action is {current['status'] if current else 'unknown'}, not pending"}
    fn = EXECUTORS.get(action["kind"])
    try:
        if fn is None:
            raise RuntimeError(f"no executor for {action['kind']}")
        result = fn(action["account"], **action["params"])
        status, out = "executed", {"ok": True, "result": result}
    except Exception as exc:  # surfaced to the user verbatim
        status, out = "failed", {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    with db() as c:
        c.execute("UPDATE actions SET status=?, result=?, resolved=? WHERE id=?",
                  (status, json.dumps(out, default=str), time.time(), action_id))
    _audit({"event": status, "id": action_id, "kind": action["kind"], "account": action["account"],
            "source": source, "summary": action["summary"], "outcome": out})
    return out
