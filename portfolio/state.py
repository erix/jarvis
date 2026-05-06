"""Portfolio state: SQLite position tracking."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
DEFAULT_TOTAL_VALUE = 10_000_000.0


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables() -> None:
    """Create positions and portfolio_history tables if they don't exist."""
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            ticker TEXT PRIMARY KEY,
            shares REAL NOT NULL,
            entry_price REAL,
            current_price REAL,
            sector TEXT,
            beta REAL,
            factor_exposures TEXT,
            pnl REAL,
            pnl_pct REAL,
            approval_status TEXT,
            is_active INTEGER DEFAULT 1,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            action TEXT,
            shares REAL,
            price REAL,
            cost_basis REAL,
            total_value REAL,
            sector TEXT,
            reason TEXT
        )
    """)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(portfolio_history)").fetchall()}
    if "total_value" not in cols:
        conn.execute("ALTER TABLE portfolio_history ADD COLUMN total_value REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ph_date ON portfolio_history(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ph_ticker ON portfolio_history(ticker)")
    conn.commit()
    conn.close()


def get_current_positions() -> list[dict]:
    """Return all active positions as a list of dicts."""
    ensure_tables()
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM positions WHERE is_active=1"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_position(
    ticker: str,
    shares: float,
    price: float,
    sector: str | None = None,
    beta: float | None = None,
    factor_exposures: dict | None = None,
    approval_status: str = "pending",
    reason: str = "rebalance",
) -> None:
    """Upsert a position. Positive shares = long, negative = short."""
    ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    fe_json = json.dumps(factor_exposures) if factor_exposures else None

    conn = _conn()
    existing = conn.execute(
        "SELECT shares, entry_price FROM positions WHERE ticker=?", (ticker,)
    ).fetchone()

    if existing:
        # Update existing position
        old_shares = existing["shares"]
        old_price = existing["entry_price"] or price
        # Weighted average entry price for adds
        total_shares = old_shares + shares
        if abs(total_shares) > 0:
            avg_price = (old_price * abs(old_shares) + price * abs(shares)) / (
                abs(old_shares) + abs(shares)
            )
        else:
            avg_price = price

        conn.execute(
            """
            UPDATE positions SET
                shares=?, entry_price=?, current_price=?,
                sector=COALESCE(?,sector), beta=COALESCE(?,beta),
                factor_exposures=COALESCE(?,factor_exposures),
                pnl=((? - entry_price) * shares),
                pnl_pct=((? - entry_price) / NULLIF(entry_price,0) * 100),
                approval_status=?, is_active=1, updated_at=?
            WHERE ticker=?
            """,
            (
                total_shares, avg_price, price,
                sector, beta, fe_json,
                price, price,
                approval_status, now, ticker,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO positions
            (ticker, shares, entry_price, current_price, sector, beta,
             factor_exposures, pnl, pnl_pct, approval_status, is_active, updated_at)
            VALUES (?,?,?,?,?,?,?,0,0,?,1,?)
            """,
            (ticker, shares, price, price, sector, beta, fe_json, approval_status, now),
        )

    # Log to history
    action = _infer_action(shares, existing["shares"] if existing else 0)
    conn.execute(
        """
        INSERT INTO portfolio_history (date, ticker, action, shares, price, total_value, sector, reason)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (date.today().isoformat(), ticker, action, shares, price, _estimate_total_value(conn), sector, reason),
    )
    conn.commit()
    conn.close()


def close_position(ticker: str, price: float | None = None, reason: str = "close") -> None:
    """Mark a position as inactive."""
    ensure_tables()
    conn = _conn()
    pos = conn.execute("SELECT * FROM positions WHERE ticker=?", (ticker,)).fetchone()
    if pos:
        close_price = price or pos["current_price"] or pos["entry_price"]
        pnl = (close_price - (pos["entry_price"] or close_price)) * pos["shares"]
        pnl_pct = (
            (close_price - pos["entry_price"]) / pos["entry_price"] * 100
            if pos["entry_price"]
            else 0.0
        )
        conn.execute(
            """
            UPDATE positions SET is_active=0, current_price=?, pnl=?, pnl_pct=?,
            updated_at=? WHERE ticker=?
            """,
            (close_price, pnl, pnl_pct, datetime.now(timezone.utc).isoformat(), ticker),
        )
        action = "cover" if pos["shares"] < 0 else "sell"
        conn.execute(
            """
            INSERT INTO portfolio_history (date, ticker, action, shares, price, total_value, sector, reason)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                date.today().isoformat(), ticker, action,
                -pos["shares"], close_price, _estimate_total_value(conn), pos["sector"], reason,
            ),
        )
        conn.commit()
    conn.close()


def update_prices(prices: dict[str, float]) -> None:
    """Refresh current_price, pnl, and pnl_pct for all active positions."""
    ensure_tables()
    conn = _conn()
    for ticker, price in prices.items():
        conn.execute(
            """
            UPDATE positions SET
                current_price=?,
                pnl=((? - entry_price) * shares),
                pnl_pct=((? - entry_price) / NULLIF(entry_price,0) * 100),
                updated_at=?
            WHERE ticker=? AND is_active=1
            """,
            (price, price, price, datetime.now(timezone.utc).isoformat(), ticker),
        )
    conn.commit()
    conn.close()


def get_portfolio_summary() -> dict[str, Any]:
    """Return high-level portfolio summary stats."""
    positions = get_current_positions()
    if not positions:
        return {"positions": 0, "longs": 0, "shorts": 0, "total_pnl": 0.0}

    longs = [p for p in positions if p["shares"] > 0]
    shorts = [p for p in positions if p["shares"] < 0]
    total_pnl = sum(p["pnl"] or 0 for p in positions)

    return {
        "positions": len(positions),
        "longs": len(longs),
        "shorts": len(shorts),
        "total_pnl": total_pnl,
        "long_tickers": [p["ticker"] for p in longs],
        "short_tickers": [p["ticker"] for p in shorts],
    }


def _estimate_total_value(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT SUM(COALESCE(pnl, 0)) AS total_pnl FROM positions WHERE is_active=1"
    ).fetchone()
    total_pnl = row["total_pnl"] if row and row["total_pnl"] is not None else 0.0
    return DEFAULT_TOTAL_VALUE + float(total_pnl)


def _infer_action(new_shares: float, old_shares: float) -> str:
    if old_shares == 0:
        return "short" if new_shares < 0 else "buy"
    if new_shares > old_shares:
        return "buy"
    if new_shares < old_shares:
        return "sell" if old_shares > 0 else "cover"
    return "adjust"
