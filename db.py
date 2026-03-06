import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "malpractice.db")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                image_path TEXT NOT NULL,
                batch INTEGER,
                labels_json TEXT,
                max_conf REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_detections_created_at ON detections(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_detections_image_path ON detections(image_path)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                batch INTEGER,
                receiver TEXT,
                attachments_count INTEGER,
                status TEXT NOT NULL,
                error_text TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_sent_at ON alerts(sent_at)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def get_config(*, key: str, default: Optional[str] = None) -> Optional[str]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = ?",
            (key,),
        ).fetchone()
    return str(row["value"]) if row else default


def set_config(*, key: str, value: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO app_config(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, _utc_now_iso()),
        )


def get_receiver_email(*, default: Optional[str] = None) -> Optional[str]:
    return get_config(key="receiver_email", default=default)


def set_receiver_email(*, receiver_email: str) -> None:
    set_config(key="receiver_email", value=receiver_email)


def run_readonly_query(*, sql: str, limit: int = 200) -> Dict[str, Any]:
    """Run a *read-only* SQL query and return a JSON-friendly result.

    Intended for a local-only SQL console.
    """

    init_db()

    if sql is None:
        return {"ok": False, "error": "Missing SQL"}

    sql_clean = str(sql).strip()
    if not sql_clean:
        return {"ok": False, "error": "Empty SQL"}

    # Block multi-statement input.
    if ";" in sql_clean:
        return {"ok": False, "error": "Only single statements are allowed (no ';')."}

    first_token = sql_clean.split(None, 1)[0].upper() if sql_clean.split() else ""
    allowed_first_tokens = {"SELECT", "WITH", "PRAGMA"}
    if first_token not in allowed_first_tokens:
        return {"ok": False, "error": "Only SELECT/WITH/PRAGMA queries are allowed."}

    # Open the DB in read-only mode.
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(sql_clean)
            rows = cur.fetchmany(max(1, int(limit)))
            columns = [d[0] for d in (cur.description or [])]
        finally:
            conn.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    result_rows: List[List[Any]] = []
    for r in rows:
        result_rows.append([r[c] for c in columns])

    return {
        "ok": True,
        "columns": columns,
        "rows": result_rows,
        "row_count": len(result_rows),
        "limit": int(limit),
    }


def log_detection(
    *,
    image_path: str,
    batch: Optional[int] = None,
    labels: Optional[List[Dict[str, Any]]] = None,
    max_conf: Optional[float] = None,
    created_at: Optional[str] = None,
) -> int:
    init_db()
    created_at = created_at or _utc_now_iso()
    labels_json = json.dumps(labels) if labels is not None else None

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO detections (created_at, image_path, batch, labels_json, max_conf)
            VALUES (?, ?, ?, ?, ?)
            """,
            (created_at, image_path, batch, labels_json, max_conf),
        )
        return int(cur.lastrowid)


def log_alert(
    *,
    folder_path: str,
    batch: Optional[int] = None,
    receiver: Optional[str] = None,
    attachments_count: Optional[int] = None,
    status: str,
    error_text: Optional[str] = None,
    sent_at: Optional[str] = None,
) -> int:
    init_db()
    sent_at = sent_at or _utc_now_iso()

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO alerts (sent_at, folder_path, batch, receiver, attachments_count, status, error_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sent_at, folder_path, batch, receiver, attachments_count, status, error_text),
        )
        return int(cur.lastrowid)


def get_latest_images(*, limit: int = 200) -> List[str]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT image_path FROM detections ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [str(r["image_path"]) for r in rows]


def get_stats() -> Dict[str, Any]:
    init_db()
    with _connect() as conn:
        total_detections = conn.execute("SELECT COUNT(*) AS c FROM detections").fetchone()["c"]
        total_alerts = conn.execute("SELECT COUNT(*) AS c FROM alerts").fetchone()["c"]
        alerts_sent = conn.execute(
            "SELECT COUNT(*) AS c FROM alerts WHERE status = 'sent'"
        ).fetchone()["c"]
        alerts_failed = conn.execute(
            "SELECT COUNT(*) AS c FROM alerts WHERE status = 'failed'"
        ).fetchone()["c"]
        last_detection_at = conn.execute(
            "SELECT created_at FROM detections ORDER BY id DESC LIMIT 1"
        ).fetchone()

    return {
        "total_detections": int(total_detections),
        "total_alerts": int(total_alerts),
        "alerts_sent": int(alerts_sent),
        "alerts_failed": int(alerts_failed),
        "last_detection_at": str(last_detection_at["created_at"]) if last_detection_at else None,
        "db_path": DB_PATH,
    }
