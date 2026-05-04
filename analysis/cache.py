"""SQLite analysis result cache with TTL-based eviction."""
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            analyzer TEXT NOT NULL,
            artifact_hash TEXT,
            result_json TEXT NOT NULL,
            raw_response TEXT,
            created_at TEXT NOT NULL,
            ttl_hours INTEGER NOT NULL DEFAULT 168,
            UNIQUE(ticker, analyzer, artifact_hash)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_ticker ON analysis_cache(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_lookup ON analysis_cache(ticker, analyzer)")
    conn.commit()


def _artifact_hash(artifact: str) -> str:
    return hashlib.md5(artifact.encode()).hexdigest()[:16]


def get_cached(ticker: str, analyzer: str, artifact: str = "") -> Optional[dict]:
    """Return cached result if still within TTL, else None."""
    conn = _get_conn()
    _ensure_schema(conn)
    try:
        a_hash = _artifact_hash(artifact) if artifact else None
        row = conn.execute(
            """SELECT result_json, created_at, ttl_hours FROM analysis_cache
               WHERE ticker=? AND analyzer=?
               ORDER BY created_at DESC LIMIT 1""",
            (ticker, analyzer),
        ).fetchone()
        if not row:
            return None
        result_json, created_at_str, ttl_hours = row
        created_at = datetime.fromisoformat(created_at_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        expiry = created_at + timedelta(hours=ttl_hours)
        if datetime.now(timezone.utc) > expiry:
            logger.debug("Cache expired for %s/%s", ticker, analyzer)
            return None
        return json.loads(result_json)
    except Exception as e:
        logger.debug("Cache read error for %s/%s: %s", ticker, analyzer, e)
        return None
    finally:
        conn.close()


def set_cache(
    ticker: str,
    analyzer: str,
    result: dict,
    artifact: str = "",
    ttl: int = 168,
    raw_response: str = "",
) -> None:
    """Store analysis result in cache."""
    conn = _get_conn()
    _ensure_schema(conn)
    try:
        a_hash = _artifact_hash(artifact) if artifact else "default"
        conn.execute(
            """INSERT OR REPLACE INTO analysis_cache
               (ticker, analyzer, artifact_hash, result_json, raw_response, created_at, ttl_hours)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker, analyzer, a_hash,
                json.dumps(result),
                raw_response[:10_000] if raw_response else "",
                datetime.now(timezone.utc).isoformat(),
                ttl,
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning("Cache write error for %s/%s: %s", ticker, analyzer, e)
    finally:
        conn.close()


def invalidate(ticker: str, analyzer: str) -> None:
    conn = _get_conn()
    _ensure_schema(conn)
    try:
        conn.execute("DELETE FROM analysis_cache WHERE ticker=? AND analyzer=?", (ticker, analyzer))
        conn.commit()
    finally:
        conn.close()


def cache_stats() -> dict:
    conn = _get_conn()
    _ensure_schema(conn)
    try:
        total = conn.execute("SELECT COUNT(*) FROM analysis_cache").fetchone()[0]
        now = datetime.now(timezone.utc).isoformat()
        # SQLite datetime comparison
        fresh = conn.execute(
            """SELECT COUNT(*) FROM analysis_cache
               WHERE datetime(created_at, '+' || ttl_hours || ' hours') > datetime(?)""",
            (now,),
        ).fetchone()[0]
        return {"total": total, "fresh": fresh, "expired": total - fresh}
    finally:
        conn.close()
