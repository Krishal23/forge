"""SQLite storage for runs, events, artifacts, model calls, approvals and the ledger.

Events are hash-chained per run so edits to history show up in verify_chain.
Approvals store the hash of the artifact the person reviewed.
`once()` makes side effects run at most once for the same inputs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from forge.util import canonical_json, hash_obj, now_iso, sha256_hex

GENESIS = "0" * 64
log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    scenario_id TEXT,
    title TEXT,
    target TEXT NOT NULL,
    requirement TEXT NOT NULL,
    mode TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    stage TEXT,
    agent TEXT,
    data TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, name, version)
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    stage TEXT,
    agent TEXT,
    provider TEXT,
    model TEXT,
    attempt INTEGER,
    status TEXT,
    error TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms REAL,
    cost_usd REAL,
    cache_hit INTEGER,
    request_json TEXT,
    response_text TEXT,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    gate TEXT NOT NULL,
    decision TEXT NOT NULL,
    approver TEXT NOT NULL,
    comment TEXT,
    artifact_name TEXT,
    artifact_hash TEXT,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_run ON llm_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_appr_run ON approvals(run_id, gate);
"""


def _event_hash(prev_hash: str, body: dict) -> str:
    return sha256_hex(prev_hash + canonical_json(body))


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
        self._listeners: list[Callable[[dict], None]] = []

    def _q(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _x(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        """Register a listener for every appended event. Returns an unsubscribe function."""
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_run(
        self,
        run_id: str,
        target: str,
        requirement: str,
        mode: str,
        state: str,
        scenario_id: str | None = None,
        title: str | None = None,
        data: dict | None = None,
    ) -> dict:
        ts = now_iso()
        self._x(
            "INSERT INTO runs (id, scenario_id, title, target, requirement, mode, state, created_at, updated_at, data)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, scenario_id, title, target, requirement, mode, state, ts, ts, json.dumps(data or {})),
        )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict | None:
        rows = self._q("SELECT * FROM runs WHERE id = ?", (run_id,))
        return self._run_row(rows[0]) if rows else None

    def list_runs(self) -> list[dict]:
        return [self._run_row(r) for r in self._q("SELECT * FROM runs ORDER BY created_at DESC")]

    def update_run(self, run_id: str, state: str | None = None, **data_updates: Any) -> dict:
        """Update state and/or merge keys into the run's JSON data blob."""
        with self._lock:
            run = self.get_run(run_id)
            if run is None:
                raise KeyError(run_id)
            data = run["data"]
            data.update(data_updates)
            self._x(
                "UPDATE runs SET state = ?, data = ?, updated_at = ? WHERE id = ?",
                (state or run["state"], json.dumps(data, default=str), now_iso(), run_id),
            )
            return self.get_run(run_id)

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["data"] = json.loads(d["data"])
        return d

    def append_event(
        self, run_id: str, type: str, data: dict | None = None, stage: str | None = None, agent: str | None = None
    ) -> dict:
        with self._lock:
            last = self._q("SELECT seq, hash FROM events WHERE run_id = ? ORDER BY seq DESC LIMIT 1", (run_id,))
            seq = last[0]["seq"] + 1 if last else 1
            prev = last[0]["hash"] if last else GENESIS
            body = {
                "run_id": run_id,
                "seq": seq,
                "ts": now_iso(),
                "type": type,
                "stage": stage,
                "agent": agent,
                "data": data or {},
            }
            h = _event_hash(prev, body)
            self._x(
                "INSERT INTO events (run_id, seq, ts, type, stage, agent, data, prev_hash, hash)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, seq, body["ts"], type, stage, agent, canonical_json(body["data"]), prev, h),
            )
        event = {**body, "prev_hash": prev, "hash": h}
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:  # a dead SSE client must not break the run
                log.exception("event listener failed")
        return event

    def events(self, run_id: str, after_seq: int = 0) -> list[dict]:
        rows = self._q("SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after_seq))
        out = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    def verify_chain(self, run_id: str) -> tuple[bool, int | None]:
        """Recompute every hash. Returns (ok, first_bad_seq)."""
        prev = GENESIS
        for ev in self.events(run_id):
            body = {k: ev[k] for k in ("run_id", "seq", "ts", "type", "stage", "agent", "data")}
            if ev["prev_hash"] != prev or _event_hash(prev, body) != ev["hash"]:
                return False, ev["seq"]
            prev = ev["hash"]
        return True, None

    def put_artifact(self, run_id: str, name: str, content: Any) -> dict:
        """Store a new version of an artifact. Identical content to the latest version is not duplicated."""
        h = hash_obj(content)
        with self._lock:
            latest = self.get_artifact(run_id, name)
            if latest and latest["hash"] == h:
                return latest
            version = latest["version"] + 1 if latest else 1
            self._x(
                "INSERT INTO artifacts (run_id, name, version, content, hash, created_at) VALUES (?,?,?,?,?,?)",
                (run_id, name, version, canonical_json(content), h, now_iso()),
            )
        return {"run_id": run_id, "name": name, "version": version, "content": content, "hash": h}

    def get_artifact(self, run_id: str, name: str, version: int | None = None) -> dict | None:
        if version is None:
            rows = self._q(
                "SELECT * FROM artifacts WHERE run_id = ? AND name = ? ORDER BY version DESC LIMIT 1", (run_id, name)
            )
        else:
            rows = self._q(
                "SELECT * FROM artifacts WHERE run_id = ? AND name = ? AND version = ?", (run_id, name, version)
            )
        if not rows:
            return None
        d = dict(rows[0])
        d["content"] = json.loads(d["content"])
        return d

    def list_artifacts(self, run_id: str) -> list[dict]:
        rows = self._q(
            "SELECT name, MAX(version) AS version, hash, created_at FROM artifacts WHERE run_id = ?"
            " GROUP BY name ORDER BY created_at",
            (run_id,),
        )
        return [dict(r) for r in rows]

    def record_llm_call(self, **fields: Any) -> None:
        cols = [
            "run_id", "stage", "agent", "provider", "model", "attempt", "status", "error",
            "prompt_tokens", "completion_tokens", "latency_ms", "cost_usd", "cache_hit",
            "request_json", "response_text",
        ]
        values = [fields.get(c) for c in cols]
        self._x(
            f"INSERT INTO llm_calls ({', '.join(cols)}, ts) VALUES ({', '.join('?' * len(cols))}, ?)",  # nosec B608 - fixed column list
            (*values, now_iso()),
        )

    def llm_totals(self, run_id: str) -> tuple[int, float]:
        """(successful non-cached calls, estimated cost) used by the budget guard."""
        row = self._q(
            "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls"
            " WHERE run_id = ? AND status = 'ok' AND COALESCE(cache_hit, 0) = 0",
            (run_id,),
        )[0]
        return int(row["n"]), float(row["c"])

    def llm_calls(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM llm_calls WHERE run_id = ? ORDER BY id", (run_id,))]

    def usage_summary(self, run_id: str) -> dict:
        calls = self.llm_calls(run_id)
        ok = [c for c in calls if c["status"] == "ok"]
        by_agent: dict[str, dict] = {}
        for c in calls:
            a = by_agent.setdefault(
                c["agent"] or "?",
                {"calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "latency_ms": []},
            )
            a["calls"] += 1
            if c["status"] != "ok":
                a["errors"] += 1
                continue
            a["prompt_tokens"] += c["prompt_tokens"] or 0
            a["completion_tokens"] += c["completion_tokens"] or 0
            a["cost_usd"] += c["cost_usd"] or 0.0
            a["latency_ms"].append(c["latency_ms"] or 0.0)
        for a in by_agent.values():
            lat = sorted(a.pop("latency_ms"))
            a["p50_ms"] = _pct(lat, 50)
            a["p95_ms"] = _pct(lat, 95)
            a["cost_usd"] = round(a["cost_usd"], 6)
        return {
            "calls": len(calls),
            "errors": len(calls) - len(ok),
            "cache_hits": sum(1 for c in ok if c["cache_hit"]),
            "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in ok),
            "completion_tokens": sum(c["completion_tokens"] or 0 for c in ok),
            "cost_usd": round(sum(c["cost_usd"] or 0.0 for c in ok), 6),
            "latency_ms_total": round(sum(c["latency_ms"] or 0.0 for c in ok), 1),
            "providers": sorted({c["provider"] for c in ok if c["provider"]}),
            "by_agent": by_agent,
        }

    def record_approval(
        self,
        run_id: str,
        gate: str,
        decision: str,
        approver: str,
        comment: str = "",
        artifact_name: str | None = None,
        artifact_hash: str | None = None,
    ) -> dict:
        cur = self._x(
            "INSERT INTO approvals (run_id, gate, decision, approver, comment, artifact_name, artifact_hash, ts)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (run_id, gate, decision, approver, comment, artifact_name, artifact_hash, now_iso()),
        )
        return dict(self._q("SELECT * FROM approvals WHERE id = ?", (cur.lastrowid,))[0])

    def approvals(self, run_id: str, gate: str | None = None) -> list[dict]:
        if gate:
            rows = self._q("SELECT * FROM approvals WHERE run_id = ? AND gate = ? ORDER BY id", (run_id, gate))
        else:
            rows = self._q("SELECT * FROM approvals WHERE run_id = ? ORDER BY id", (run_id,))
        return [dict(r) for r in rows]

    def approval_valid(self, approval: dict) -> bool:
        """An approval only counts if the artifact it was given for is still the latest version."""
        if not approval.get("artifact_name"):
            return True
        latest = self.get_artifact(approval["run_id"], approval["artifact_name"])
        return latest is not None and latest["hash"] == approval["artifact_hash"]

    @staticmethod
    def ledger_key(run_id: str, action: str, inputs: Any) -> str:
        return hash_obj({"run": run_id, "action": action, "inputs": inputs})

    def ledger_get(self, key: str) -> Any | None:
        rows = self._q("SELECT result FROM ledger WHERE key = ?", (key,))
        return json.loads(rows[0]["result"]) if rows else None

    def ledger_put(self, key: str, run_id: str, action: str, result: Any) -> None:
        self._x(
            "INSERT OR IGNORE INTO ledger (key, run_id, action, result, ts) VALUES (?,?,?,?,?)",
            (key, run_id, action, canonical_json(result), now_iso()),
        )

    def once(self, run_id: str, action: str, inputs: Any, fn: Callable[[], Any], stage: str | None = None) -> Any:
        """Run a side-effecting action at most once per (run, action, inputs).

        A repeat returns the recorded result and appends a `duplicate_suppressed`
        event, which is how Forge handles "the workflow attempts to repeat an action".
        """
        key = self.ledger_key(run_id, action, inputs)
        cached = self.ledger_get(key)
        if cached is not None:
            self.append_event(
                run_id, "duplicate_suppressed", {"action": action, "key": key[:16]}, stage=stage
            )
            return cached["value"]
        result = fn()
        self.ledger_put(key, run_id, action, {"value": result})
        return result


def _pct(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    k = max(0, min(len(values) - 1, round(p / 100 * (len(values) - 1))))
    return round(values[k], 1)
