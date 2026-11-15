"""Long-term memory store (assignment R7): style memory + outcome memory, SQLite (stdlib, durable, zero services).

Separate from graph State and from the execution trace. Everything is OWNER-scoped (tenant isolation) and auditable (`memory_log`).
Writes go through two narrow doors only: `record_style_event` (a human's edit/approval) and `record_outcome` (a marketplace outcome);
promotion is computed by agent/memory/promotion_rules.py. Items are viewable (`list_items`) and deletable (`delete_item`, `forget_all`).
SQLite is deliberate: one file, WAL mode, safe for API + worker processes; the same method names can sit on Postgres later.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.policy.engine import matching_rules
from agent.settings import ROOT, load_config

from .promotion_rules import extract_signals, style_candidates
from .stats import posterior_rate

OUTCOME_RANK = {"ignored": 0, "replied": 1, "won": 2}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS style_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, persona TEXT NOT NULL, proposal_key TEXT NOT NULL,
  ts TEXT NOT NULL, status TEXT NOT NULL, signals TEXT NOT NULL, UNIQUE(owner, persona, proposal_key));
CREATE TABLE IF NOT EXISTS memory_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, persona TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,
  text TEXT NOT NULL, hits INTEGER NOT NULL, testable INTEGER NOT NULL, consistency REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(owner, persona, kind, key));
CREATE TABLE IF NOT EXISTS outcomes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, persona TEXT NOT NULL, proposal_key TEXT NOT NULL,
  strategy TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL, source TEXT NOT NULL, ts TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '', UNIQUE(owner, persona, proposal_key));
CREATE TABLE IF NOT EXISTS memory_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, owner TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _is_injection(text: str) -> bool:
    return bool(matching_rules(text, "job_post"))


class MemoryStore:
    def __init__(self, path: str | Path | None = None, cfg: dict[str, Any] | None = None):
        self.cfg = (cfg or load_config()).get("memory", {})
        raw = str(path) if path is not None else str(self.cfg.get("db_path", "data/memory/memory.sqlite"))
        if raw != ":memory:":
            p = Path(raw) if Path(raw).is_absolute() else ROOT / raw
            p.parent.mkdir(parents=True, exist_ok=True)
            raw = str(p)
        self._db = sqlite3.connect(raw, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            if raw != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            if "category" not in {r[1] for r in self._db.execute("PRAGMA table_info(outcomes)")}:  # databases created before categories existed
                self._db.execute("ALTER TABLE outcomes ADD COLUMN category TEXT NOT NULL DEFAULT ''")
                self._db.commit()

    def close(self) -> None:
        self._db.close()

    def _log(self, owner: str, action: str, detail: dict[str, Any]) -> None:
        self._db.execute("INSERT INTO memory_log(ts, owner, action, detail) VALUES (?,?,?,?)", (_now(), owner, action, json.dumps(detail)))

    # ---- style memory: the ONLY input is a human's edit/approval of a draft ------------------------------------------------------
    def record_style_event(self, owner: str, persona: str, proposal_key: str, original: str, edited: str | None, status: str) -> bool:
        """Idempotent per (owner, persona, proposal_key). `edited=None` means approved as is. Returns True if newly recorded."""
        if status not in ("approved", "edited"):
            return False  # a rejected draft says nothing about how the freelancer wants letters written
        sig = extract_signals(original, edited if edited is not None else original, self.cfg.get("style"))
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO style_events(owner, persona, proposal_key, ts, status, signals) VALUES (?,?,?,?,?,?)",
                                   (owner, persona, proposal_key, _now(), status, json.dumps(sig)))
            self._db.commit()
            return cur.rowcount == 1

    def promote_style(self, owner: str, persona: str) -> list[dict[str, Any]]:
        """Recompute which style rules qualify. Returns the items whose state changed. Idempotent; deleted items stay deleted."""
        with self._lock:
            rows = self._db.execute("SELECT signals FROM style_events WHERE owner=? AND persona=? ORDER BY id DESC LIMIT 200",
                                    (owner, persona)).fetchall()
            cands = style_candidates([json.loads(r["signals"]) for r in rows], self.cfg.get("style"), _is_injection)
            existing = {(r["kind"], r["key"]): r for r in self._db.execute(
                "SELECT * FROM memory_items WHERE owner=? AND persona=?", (owner, persona))}
            changed: list[dict[str, Any]] = []
            seen = set()
            now = _now()
            for c in cands:
                seen.add((c.kind, c.key))
                old = existing.get((c.kind, c.key))
                if old is not None and old["status"] == "deleted":
                    continue  # tombstone: the owner removed it; never silently bring it back
                if old is None:
                    self._db.execute("INSERT INTO memory_items(owner, persona, kind, key, text, hits, testable, consistency, status, created_at, updated_at)"
                                     " VALUES (?,?,?,?,?,?,?,?, 'active', ?, ?)",
                                     (owner, persona, c.kind, c.key, c.text, c.hits, c.testable, c.consistency, now, now))
                    changed.append({"action": "promoted", "kind": c.kind, "key": c.key, "text": c.text})
                else:
                    self._db.execute("UPDATE memory_items SET text=?, hits=?, testable=?, consistency=?, status='active', updated_at=? WHERE id=?",
                                     (c.text, c.hits, c.testable, c.consistency, now, old["id"]))
                    if old["status"] != "active":
                        changed.append({"action": "reactivated", "kind": c.kind, "key": c.key, "text": c.text})
            for (kind, key), old in existing.items():
                if (kind, key) not in seen and old["status"] == "active":
                    self._db.execute("UPDATE memory_items SET status='retired', updated_at=? WHERE id=?", (now, old["id"]))
                    changed.append({"action": "retired", "kind": kind, "key": key, "text": old["text"]})
            for ch in changed:
                self._log(owner, ch["action"], {"persona": persona, "kind": ch["kind"], "key": ch["key"]})
            self._db.commit()
            return changed

    def style_rules(self, owner: str, persona: str) -> list[str]:
        with self._lock:
            return [r["text"] for r in self._db.execute(
                "SELECT text FROM memory_items WHERE owner=? AND persona=? AND status='active' ORDER BY hits DESC, id", (owner, persona))]

    # ---- outcome memory: the ONLY input is a marketplace outcome of a sent proposal ---------------------------------------------
    def record_outcome(self, owner: str, persona: str, proposal_key: str, strategy: str | None, outcome: str, source: str = "marketplace",
                       category: str | None = None) -> None:
        """Upsert; an outcome only moves forward (ignored < replied < won), so a late poll cannot erase a win."""
        if outcome not in OUTCOME_RANK:
            raise ValueError(f"outcome must be one of {sorted(OUTCOME_RANK)}")
        with self._lock:
            old = self._db.execute("SELECT outcome FROM outcomes WHERE owner=? AND persona=? AND proposal_key=?",
                                   (owner, persona, proposal_key)).fetchone()
            if old is None:
                self._db.execute("INSERT INTO outcomes(owner, persona, proposal_key, strategy, outcome, source, ts, category) VALUES (?,?,?,?,?,?,?,?)",
                                 (owner, persona, proposal_key, strategy or "", outcome, source, _now(), category or ""))
            elif OUTCOME_RANK[outcome] > OUTCOME_RANK[old["outcome"]]:
                self._db.execute("UPDATE outcomes SET outcome=?, source=?, ts=?, category=CASE WHEN ?<>'' THEN ? ELSE category END"
                                 " WHERE owner=? AND persona=? AND proposal_key=?",
                                 (outcome, source, _now(), category or "", category or "", owner, persona, proposal_key))
            self._db.commit()

    def outcome_stats(self, owner: str, persona: str, strategy: str | None = None) -> dict[str, int]:
        q, args = "SELECT outcome, COUNT(*) c FROM outcomes WHERE owner=? AND persona=?", [owner, persona]
        if strategy is not None:
            q, args = q + " AND strategy=?", [*args, strategy]
        with self._lock:
            by = {r["outcome"]: r["c"] for r in self._db.execute(q + " GROUP BY outcome", args)}
        won, replied_only, ignored = by.get("won", 0), by.get("replied", 0), by.get("ignored", 0)
        return {"n": won + replied_only + ignored, "replied": won + replied_only, "won": won}

    def category_table(self, owner: str, persona: str | None = None) -> dict[str, dict[str, int]]:
        """Outcome counts per job category (all personas when `persona` is None). Outcomes recorded without a category are 'uncategorised'."""
        q, args = "SELECT category, outcome, COUNT(*) c FROM outcomes WHERE owner=?", [owner]
        if persona is not None:
            q, args = q + " AND persona=?", [*args, persona]
        table: dict[str, dict[str, int]] = {}
        with self._lock:
            rows = list(self._db.execute(q + " GROUP BY category, outcome", args))
        for r in rows:
            t = table.setdefault(r["category"] or "uncategorised", {"n": 0, "replied": 0, "won": 0})
            t["n"] += r["c"]
            t["replied"] += r["c"] if r["outcome"] in ("replied", "won") else 0
            t["won"] += r["c"] if r["outcome"] == "won" else 0
        return dict(sorted(table.items()))

    def strategy_table(self, owner: str, persona: str) -> dict[str, dict[str, int]]:
        """Per opener strategy counts (X2 proposal experiments, Phase 9)."""
        with self._lock:
            names = [r["strategy"] for r in self._db.execute(
                "SELECT DISTINCT strategy FROM outcomes WHERE owner=? AND persona=? AND strategy<>''", (owner, persona))]
        return {s: self.outcome_stats(owner, persona, s) for s in sorted(names)}

    def measured_rates(self, owner: str, persona: str, base_reply: float, base_win: float) -> dict[str, Any]:
        """Measured reply / win-given-reply rates, shrunk toward the assumed ones; None until enough real outcomes exist."""
        oc = self.cfg.get("outcome", {})
        s = self.outcome_stats(owner, persona)
        strength = float(oc.get("prior_strength", 10))
        reply = posterior_rate(s["replied"], s["n"], base_reply, strength) if s["n"] >= oc.get("min_samples", 20) else None
        win = posterior_rate(s["won"], s["replied"], base_win, strength) if s["replied"] >= oc.get("min_replies", 10) else None
        return {"samples": s["n"], "replied": s["replied"], "won": s["won"], "reply_rate": reply, "win_given_reply": win}

    # ---- viewer / delete (backs the memory screen + API in Phase 6) ----------------------------------------------------------------
    def list_items(self, owner: str, persona: str | None = None, include_inactive: bool = False) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM memory_items WHERE owner=?", [owner]
        if persona:
            q, args = q + " AND persona=?", [*args, persona]
        if not include_inactive:
            q += " AND status='active'"
        with self._lock:
            return [dict(r) for r in self._db.execute(q + " ORDER BY persona, hits DESC, id", args)]

    def delete_item(self, owner: str, item_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE memory_items SET status='deleted', updated_at=? WHERE id=? AND owner=? AND status<>'deleted'",
                                   (_now(), item_id, owner))
            if cur.rowcount:
                self._log(owner, "deleted", {"item_id": item_id})
            self._db.commit()
            return cur.rowcount == 1

    def forget_all(self, owner: str) -> dict[str, int]:
        """Hard delete everything stored for an owner (right to erasure). The audit log keeps only the fact that it happened."""
        with self._lock:
            counts = {t: self._db.execute(f"DELETE FROM {t} WHERE owner=?", (owner,)).rowcount for t in ("style_events", "memory_items", "outcomes")}
            self._log(owner, "forget_all", counts)
            self._db.commit()
            return counts

    def audit_log(self, owner: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT ts, action, detail FROM memory_log WHERE owner=? ORDER BY id DESC LIMIT ?",
                                                      (owner, limit))]

    # ---- what a run may read ---------------------------------------------------------------------------------------------------------
    def context(self, owner: str, persona: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only view put into state['memory_context'] at intake: promoted style rules + measured outcome rates."""
        p = (cfg or load_config()).get("pricing", {})
        return {"style": self.style_rules(owner, persona),
                "outcome": self.measured_rates(owner, persona, float(p.get("base_reply_rate", 0.3)), float(p.get("win_given_reply", 0.25)))}
