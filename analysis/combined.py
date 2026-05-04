"""Combine quantitative L2 scores with Claude fundamental scores (60/40 blend)."""
import logging
import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")


def _signal_from_score(score: float) -> str:
    if score >= 80:
        return "STRONG_BUY"
    elif score >= 70:
        return "BUY"
    elif score >= 40:
        return "HOLD"
    elif score >= 30:
        return "SELL"
    else:
        return "STRONG_SELL"


def compute_combined_score(
    composite_score: Optional[float],
    claude_scores: dict[str, Optional[float]],
) -> tuple[Optional[float], Optional[float]]:
    """
    Compute claude_fundamental_avg and combined_score.

    Returns (claude_fundamental_avg, combined_score).
    If no Claude data, combined_score = composite_score (100% quant).
    """
    valid_claude = [v for v in claude_scores.values() if v is not None]
    claude_avg = sum(valid_claude) / len(valid_claude) if valid_claude else None

    if composite_score is None and claude_avg is None:
        return None, None

    if claude_avg is None:
        # 100% quant — no penalty
        return None, composite_score

    if composite_score is None:
        # 100% Claude
        return claude_avg, claude_avg

    combined = 0.60 * composite_score + 0.40 * claude_avg
    return claude_avg, round(combined, 2)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add claude_score, combined_score, signal columns to scores if missing."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(scores)").fetchall()}
    additions = {
        "claude_score": "REAL",
        "combined_score": "REAL",
        "signal": "TEXT",
    }
    for col, dtype in additions.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE scores ADD COLUMN {col} {dtype}")
    conn.commit()


def load_latest_scores(tickers: Optional[list] = None) -> list[dict]:
    """Load most recent score row per ticker from scores table."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if tickers:
            placeholders = ",".join("?" * len(tickers))
            rows = conn.execute(
                f"""SELECT s.* FROM scores s
                    INNER JOIN (
                        SELECT ticker, MAX(date) AS max_date FROM scores GROUP BY ticker
                    ) latest ON s.ticker = latest.ticker AND s.date = latest.max_date
                    WHERE s.ticker IN ({placeholders})""",
                tickers,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT s.* FROM scores s
                   INNER JOIN (
                       SELECT ticker, MAX(date) AS max_date FROM scores GROUP BY ticker
                   ) latest ON s.ticker = latest.ticker AND s.date = latest.max_date"""
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("Failed to load scores: %s", e)
        return []
    finally:
        conn.close()


def save_combined_scores(updates: list[dict]) -> int:
    """
    Update scores table with claude_score, combined_score, signal.

    Each update dict must have: ticker, date, claude_score, combined_score, signal.
    Returns number of rows updated.
    """
    if not updates:
        return 0

    conn = sqlite3.connect(_DB_PATH)
    try:
        _ensure_columns(conn)
        count = 0
        for u in updates:
            conn.execute(
                """UPDATE scores SET claude_score=?, combined_score=?, signal=?
                   WHERE ticker=? AND date=?""",
                (u.get("claude_score"), u.get("combined_score"), u.get("signal"),
                 u["ticker"], u["date"]),
            )
            count += conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        return count
    except Exception as e:
        logger.error("Failed to save combined scores: %s", e)
        return 0
    finally:
        conn.close()


def get_candidates(n_long: int = 20, n_short: int = 20) -> tuple[list[str], list[str]]:
    """Return top long and short candidates from most recent L2 scores."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT s.ticker FROM scores s
               INNER JOIN (
                   SELECT ticker, MAX(date) AS max_date FROM scores GROUP BY ticker
               ) latest ON s.ticker = latest.ticker AND s.date = latest.max_date
               WHERE s.is_long_candidate = 1
               ORDER BY s.composite_score DESC LIMIT ?""",
            (n_long,),
        ).fetchall()
        longs = [r["ticker"] for r in rows]

        rows = conn.execute(
            """SELECT s.ticker FROM scores s
               INNER JOIN (
                   SELECT ticker, MAX(date) AS max_date FROM scores GROUP BY ticker
               ) latest ON s.ticker = latest.ticker AND s.date = latest.max_date
               WHERE s.is_short_candidate = 1
               ORDER BY s.composite_score ASC LIMIT ?""",
            (n_short,),
        ).fetchall()
        shorts = [r["ticker"] for r in rows]

        return longs, shorts
    except Exception as e:
        logger.error("Failed to load candidates: %s", e)
        return [], []
    finally:
        conn.close()
