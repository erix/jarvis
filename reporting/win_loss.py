"""Win/loss analytics per round-trip trade (FIFO matching)."""
import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")


def _load_filled_orders(db_path: str = DB_PATH) -> pd.DataFrame:
    if not os.path.exists(db_path):
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(
            "SELECT ticker, action, qty, fill_price, submitted_at "
            "FROM orders WHERE status='filled' ORDER BY submitted_at",
            conn,
        )
        conn.close()
        df["submitted_at"] = pd.to_datetime(df["submitted_at"])
        return df
    except Exception as exc:
        logger.warning("Could not load orders: %s", exc)
        return pd.DataFrame()


def _fifo_match(orders: pd.DataFrame) -> list[dict]:
    """Match buys and sells FIFO to compute round-trip P&L."""
    round_trips = []
    # Group by ticker and side
    for ticker, group in orders.groupby("ticker"):
        longs_open = []  # (qty_remaining, entry_price, entry_date)
        shorts_open = []

        for _, row in group.iterrows():
            action = row["action"].lower()
            qty = abs(row["qty"])
            price = row["fill_price"] or 0.0
            date = row["submitted_at"]

            if action == "buy":
                longs_open.append([qty, price, date])
            elif action == "sell" and longs_open:
                qty_left = qty
                while qty_left > 0 and longs_open:
                    entry_qty, entry_price, entry_date = longs_open[0]
                    matched = min(qty_left, entry_qty)
                    pnl = (price - entry_price) * matched
                    hold_days = max(1, (date - entry_date).days)
                    round_trips.append({
                        "ticker": ticker, "side": "long",
                        "entry_price": entry_price, "exit_price": price,
                        "qty": matched, "pnl": pnl,
                        "hold_days": hold_days,
                        "win": pnl > 0,
                    })
                    if matched >= entry_qty:
                        longs_open.pop(0)
                    else:
                        longs_open[0][0] -= matched
                    qty_left -= matched

            elif action == "short":
                shorts_open.append([qty, price, date])
            elif action == "cover" and shorts_open:
                qty_left = qty
                while qty_left > 0 and shorts_open:
                    entry_qty, entry_price, entry_date = shorts_open[0]
                    matched = min(qty_left, entry_qty)
                    pnl = (entry_price - price) * matched  # profit when price falls
                    hold_days = max(1, (date - entry_date).days)
                    round_trips.append({
                        "ticker": ticker, "side": "short",
                        "entry_price": entry_price, "exit_price": price,
                        "qty": matched, "pnl": pnl,
                        "hold_days": hold_days,
                        "win": pnl > 0,
                    })
                    if matched >= entry_qty:
                        shorts_open.pop(0)
                    else:
                        shorts_open[0][0] -= matched
                    qty_left -= matched

    return round_trips


def _holding_period_bucket(days: int) -> str:
    if days <= 5:
        return "1-5d"
    elif days <= 20:
        return "5-20d"
    elif days <= 60:
        return "20-60d"
    return "60d+"


def analyze_win_loss(db_path: str = DB_PATH) -> dict:
    """Return win/loss metrics sliced by side, holding period, and sector."""
    orders = _load_filled_orders(db_path)
    if orders.empty:
        return {
            "win_rate": 0.0, "pl_ratio": 0.0, "total_trades": 0,
            "by_side": {}, "by_holding_period": {}, "streaks": {}
        }

    trades = _fifo_match(orders)
    if not trades:
        return {"win_rate": 0.0, "pl_ratio": 0.0, "total_trades": 0,
                "by_side": {}, "by_holding_period": {}, "streaks": {}}

    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]

    win_rate = len(wins) / len(trades) * 100
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 1
    pl_ratio = avg_win / avg_loss if avg_loss else 0.0

    by_side: dict[str, dict] = {}
    for side in ("long", "short"):
        side_trades = [t for t in trades if t["side"] == side]
        if side_trades:
            side_wins = [t for t in side_trades if t["win"]]
            by_side[side] = {
                "win_rate": round(len(side_wins) / len(side_trades) * 100, 1),
                "count": len(side_trades),
                "avg_pnl": round(sum(t["pnl"] for t in side_trades) / len(side_trades), 2),
            }

    by_period: dict[str, dict] = {}
    for t in trades:
        bucket = _holding_period_bucket(t["hold_days"])
        by_period.setdefault(bucket, []).append(t)
    by_holding_period = {
        bucket: {
            "win_rate": round(sum(1 for t in ts if t["win"]) / len(ts) * 100, 1),
            "count": len(ts),
        }
        for bucket, ts in by_period.items()
    }

    # Win/loss streaks
    win_flags = [t["win"] for t in trades]
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for w in win_flags:
        if w:
            cur_win += 1; cur_loss = 0
        else:
            cur_loss += 1; cur_win = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    return {
        "win_rate": round(win_rate, 1),
        "pl_ratio": round(pl_ratio, 2),
        "total_trades": len(trades),
        "total_pnl": round(sum(t["pnl"] for t in trades), 2),
        "by_side": by_side,
        "by_holding_period": by_holding_period,
        "streaks": {"max_win": max_win_streak, "max_loss": max_loss_streak},
    }
