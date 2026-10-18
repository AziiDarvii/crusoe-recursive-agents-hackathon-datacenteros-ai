"""SQLite-backed memory: runs, event history, plan attempts and rejection reasons."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, scenario TEXT, fingerprint TEXT, provider TEXT, status TEXT,
    started_at TEXT, finished_at TEXT, summary TEXT, result_json TEXT
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT, seq INTEGER, json TEXT, PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS attempts (
    run_id TEXT, attempt_no INTEGER, depth INTEGER, strategy TEXT, author TEXT, signature TEXT,
    verdict TEXT, families_json TEXT, violations_json TEXT, plan_json TEXT, created_at TEXT,
    fingerprint TEXT, PRIMARY KEY (run_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS attempts_fp ON attempts (fingerprint, verdict);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise

    def start_run(self, run_id: str, scenario: str, fingerprint: str, provider: str) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO runs (id, scenario, fingerprint, provider, status, started_at) "
                      "VALUES (?, ?, ?, ?, 'RUNNING', ?)", (run_id, scenario, fingerprint, provider, _now()))

    def add_event(self, run_id: str, event: dict) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO events (run_id, seq, json) VALUES (?, ?, ?)",
                      (run_id, event["seq"], json.dumps(event, default=str)))

    def record_attempt(self, run_id: str, fingerprint: str, attempt: dict) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, attempt["attempt"], attempt["depth"], attempt["strategy"], attempt["author"],
                 attempt.get("signature"), attempt["verdict"], json.dumps(attempt.get("families", [])),
                 json.dumps(attempt.get("violations", [])), json.dumps(attempt.get("plan")), _now(), fingerprint))

    def finish_run(self, run_id: str, status: str, summary: str, result: dict) -> None:
        with self._tx() as c:
            c.execute("UPDATE runs SET status=?, finished_at=?, summary=?, result_json=? WHERE id=?",
                      (status, _now(), summary, json.dumps(result, default=str), run_id))

    def retrieve_failures(self, fingerprint: str, exclude_run: Optional[str] = None) -> List[dict]:
        """Previously rejected attempts for the same scenario fingerprint (most recent first)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, attempt_no, strategy, signature, verdict, families_json, violations_json "
                "FROM attempts WHERE fingerprint=? AND verdict != 'PASS' AND run_id != ? "
                "ORDER BY created_at DESC LIMIT 50", (fingerprint, exclude_run or "")).fetchall()
        return [{"run_id": r["run_id"], "attempt": r["attempt_no"], "strategy": r["strategy"],
                 "signature": r["signature"], "verdict": r["verdict"],
                 "families": json.loads(r["families_json"]), "violations": json.loads(r["violations_json"])}
                for r in rows]

    def list_runs(self, limit: int = 50) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, scenario, provider, status, started_at, finished_at, summary FROM runs "
                "ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._lock:
            run = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                return None
            events = self._conn.execute("SELECT json FROM events WHERE run_id=? ORDER BY seq",
                                        (run_id,)).fetchall()
        out = dict(run)
        out["result"] = json.loads(out.pop("result_json") or "null")
        out["events"] = [json.loads(e["json"]) for e in events]
        return out

    def list_attempts(self, limit: int = 100) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT a.*, r.scenario FROM attempts a JOIN runs r ON r.id = a.run_id "
                "ORDER BY a.created_at DESC, a.attempt_no DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            row = dict(r)
            for key in ("families_json", "violations_json", "plan_json"):
                row[key.removesuffix("_json")] = json.loads(row.pop(key) or "null")
            out.append(row)
        return out

    def clear(self) -> None:
        with self._tx() as c:
            c.executescript("DELETE FROM events; DELETE FROM attempts; DELETE FROM runs;")
