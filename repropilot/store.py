"""SQLite is the source of truth; state transitions are compare-and-swap."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB, RUNTIME


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          prompt TEXT NOT NULL, plan TEXT NOT NULL, plan_hash TEXT NOT NULL,
          planner TEXT NOT NULL, status TEXT NOT NULL, recovery_demo INTEGER NOT NULL,
          summary TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
          time TEXT NOT NULL, tool TEXT NOT NULL, status TEXT NOT NULL,
          message TEXT NOT NULL, details TEXT NOT NULL
        );
        """)


def plan_hash(plan):
    return hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def create(prompt, plan, planner, recovery_demo=False):
    run_id = uuid.uuid4().hex[:12]
    timestamp = now()
    with connect() as db:
        db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            run_id, timestamp, timestamp, prompt, json.dumps(plan, ensure_ascii=False),
            plan_hash(plan), planner, "awaiting_approval", int(recovery_demo), None, None))
    event(run_id, "planner", "complete", "分析方案已生成，等待研究者确认。", {"plan_hash": plan_hash(plan)})
    return get(run_id)


def get(run_id):
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["plan"] = json.loads(result["plan"])
        result["summary"] = json.loads(result["summary"]) if result["summary"] else None
        result["events"] = [dict(e) for e in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,))]
        for e in result["events"]:
            e["details"] = json.loads(e["details"])
        return result


def list_runs():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT id,created_at,prompt,status,planner FROM runs ORDER BY created_at DESC LIMIT 50")]


def transition(run_id, expected, target, **fields):
    allowed = {"summary", "error"}
    if fields.keys() - allowed:
        raise ValueError("Unsupported update")
    assignments = ["status=?", "updated_at=?"]
    args = [target, now()]
    for key, value in fields.items():
        assignments.append(f"{key}=?")
        args.append(json.dumps(value, ensure_ascii=False) if key == "summary" else value)
    args.extend([run_id, expected])
    with connect() as db:
        changed = db.execute(f"UPDATE runs SET {','.join(assignments)} WHERE id=? AND status=?", args).rowcount
    return changed == 1


def event(run_id, tool, status, message, details=None):
    with connect() as db:
        db.execute("INSERT INTO events(run_id,time,tool,status,message,details) VALUES (?,?,?,?,?,?)", (
            run_id, now(), tool, status, message, json.dumps(details or {}, ensure_ascii=False)))


def recover_interrupted():
    with connect() as db:
        rows = db.execute("SELECT id FROM runs WHERE status IN ('queued','running')").fetchall()
        for row in rows:
            db.execute("UPDATE runs SET status='failed',error=?,updated_at=? WHERE id=?", (
                "服务重启中断了执行；可以从已验证的检查点重试。", now(), row["id"]))
    for row in rows:
        event(row["id"], "executor", "failed", "检测到服务重启，执行标记为中断。")
