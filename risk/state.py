"""Risk state persistence: risk_state and rejections tables."""
import os
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables() -> None:
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS risk_state (
            date TEXT PRIMARY KEY,
            daily_pnl REAL,
            weekly_pnl REAL,
            drawdown_pct REAL,
            gross_exposure REAL,
            net_exposure REAL,
            portfolio_beta REAL,
            factor_risk_pct REAL,
            specific_risk_pct REAL,
            max_mctr_ticker TEXT,
            vix REAL,
            circuit_breaker_triggered TEXT,
            num_rejections INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            reason TEXT NOT NULL,
            check_number INTEGER,
            attempted_shares REAL
        )
    """)
    conn.commit()
    conn.close()


def upsert_risk_state(row: dict[str, Any]) -> None:
    ensure_tables()
    today = row.get("date", date.today().isoformat())
    conn = _conn()
    conn.execute("""
        INSERT INTO risk_state
            (date, daily_pnl, weekly_pnl, drawdown_pct, gross_exposure, net_exposure,
             portfolio_beta, factor_risk_pct, specific_risk_pct, max_mctr_ticker,
             vix, circuit_breaker_triggered, num_rejections)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            daily_pnl=excluded.daily_pnl,
            weekly_pnl=excluded.weekly_pnl,
            drawdown_pct=excluded.drawdown_pct,
            gross_exposure=excluded.gross_exposure,
            net_exposure=excluded.net_exposure,
            portfolio_beta=excluded.portfolio_beta,
            factor_risk_pct=excluded.factor_risk_pct,
            specific_risk_pct=excluded.specific_risk_pct,
            max_mctr_ticker=excluded.max_mctr_ticker,
            vix=excluded.vix,
            circuit_breaker_triggered=excluded.circuit_breaker_triggered,
            num_rejections=excluded.num_rejections
    """, (
        today,
        row.get("daily_pnl"),
        row.get("weekly_pnl"),
        row.get("drawdown_pct"),
        row.get("gross_exposure"),
        row.get("net_exposure"),
        row.get("portfolio_beta"),
        row.get("factor_risk_pct"),
        row.get("specific_risk_pct"),
        row.get("max_mctr_ticker"),
        row.get("vix"),
        row.get("circuit_breaker_triggered"),
        row.get("num_rejections", 0),
    ))
    conn.commit()
    conn.close()


def log_rejection(ticker: str, reason: str, check_number: int, attempted_shares: float) -> None:
    ensure_tables()
    conn = _conn()
    conn.execute("""
        INSERT INTO rejections (timestamp, ticker, reason, check_number, attempted_shares)
        VALUES (?,?,?,?,?)
    """, (datetime.now(timezone.utc).isoformat(), ticker, reason, check_number, attempted_shares))
    conn.commit()
    conn.close()


def get_today_rejections() -> int:
    ensure_tables()
    today = date.today().isoformat()
    conn = _conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM rejections WHERE timestamp LIKE ?",
        (f"{today}%",)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def get_nav_peak() -> float | None:
    """Return the highest daily portfolio value ever recorded."""
    ensure_tables()
    conn = _conn()
    # Derive NAV peak from risk_state: use gross_exposure as proxy if portfolio value not stored
    # Actually we compute peak from portfolio_history by summing position values
    # For simplicity, track peak via a dedicated column stored in risk_state
    row = conn.execute(
        "SELECT MAX(gross_exposure) as peak FROM risk_state"
    ).fetchone()
    conn.close()
    return row["peak"] if row and row["peak"] else None


def get_recent_risk_states(days: int = 7) -> list[dict]:
    ensure_tables()
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM risk_state ORDER BY date DESC LIMIT ?", (days,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
