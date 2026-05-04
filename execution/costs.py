"""Slippage and transaction cost tracker — reads from orders table."""
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")

_CREATE_ORDERS_TABLE = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_at TEXT,
    ticker TEXT,
    action TEXT,
    qty REAL,
    signal_price REAL,
    limit_price REAL,
    fill_price REAL,
    slippage_bps REAL,
    commission REAL,
    status TEXT,
    order_id TEXT,
    perm_id TEXT
)
"""


def ensure_orders_table(db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_ORDERS_TABLE)
    conn.commit()
    conn.close()


def track_slippage(order_record: dict) -> float:
    """Compute slippage in basis points relative to signal price.

    Positive bps = we paid more (bad for buys, bad for shorts if positive spread).
    Negative bps = we got a better price than signal (favorable).
    """
    fill = order_record.get("fill_price", 0.0)
    signal = order_record.get("signal_price", 0.0)
    if not signal:
        return 0.0
    action = order_record.get("action", "buy").lower()
    # For shorts: paying a higher price to borrow is also a cost
    raw_bps = (fill - signal) / signal * 10_000
    if action in ("short", "sell"):
        raw_bps = -raw_bps  # selling higher than signal = good
    return round(raw_bps, 2)


def record_order(order_record: dict, db_path: str = DB_PATH) -> None:
    """Save order fill to SQLite orders table."""
    ensure_orders_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO orders
           (submitted_at, ticker, action, qty, signal_price, limit_price,
            fill_price, slippage_bps, commission, status, order_id, perm_id)
           VALUES (:submitted_at, :ticker, :action, :qty, :signal_price, :limit_price,
                   :fill_price, :slippage_bps, :commission, :status, :order_id, :perm_id)""",
        order_record,
    )
    conn.commit()
    conn.close()


def get_slippage_metrics(days: int = 30, db_path: str = DB_PATH) -> dict:
    """Compute trailing slippage stats for dashboard."""
    ensure_orders_table(db_path)
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT ticker, action, fill_price, signal_price, slippage_bps, qty, commission "
        "FROM orders WHERE status='filled' AND submitted_at >= ?",
        (cutoff,),
    ).fetchall()
    conn.close()

    if not rows:
        return {
            "avg_bps": 0.0, "median_bps": 0.0, "p95_bps": 0.0,
            "total_cost_usd": 0.0, "worst_5": [], "count": 0,
        }

    bps_list = [r[4] for r in rows if r[4] is not None]
    cost_usd = sum((abs(r[4] or 0) / 10_000) * abs(r[5] or 0) * (r[2] or 0) + (r[6] or 0) for r in rows)

    import statistics
    bps_sorted = sorted(bps_list)
    p95_idx = int(len(bps_sorted) * 0.95)

    worst_5 = sorted(rows, key=lambda r: r[4] or 0, reverse=True)[:5]

    return {
        "avg_bps": round(statistics.mean(bps_list), 2) if bps_list else 0.0,
        "median_bps": round(statistics.median(bps_list), 2) if bps_list else 0.0,
        "p95_bps": round(bps_sorted[min(p95_idx, len(bps_sorted) - 1)], 2) if bps_sorted else 0.0,
        "total_cost_usd": round(cost_usd, 2),
        "count": len(rows),
        "worst_5": [
            {"ticker": r[0], "action": r[1], "fill": r[2], "signal": r[3], "slippage_bps": r[4]}
            for r in worst_5
        ],
    }
