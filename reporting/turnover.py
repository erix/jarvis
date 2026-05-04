"""Turnover analytics and tax liability estimate."""
import logging
import os
import sqlite3
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")

SHORT_TERM_TAX_RATE = 0.37
LONG_TERM_TAX_RATE = 0.20
TRADING_DAYS_PER_YEAR = 252


def _load_orders(days: int, db_path: str = DB_PATH) -> pd.DataFrame:
    if not os.path.exists(db_path):
        return pd.DataFrame()
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(
            "SELECT ticker, action, qty, fill_price, submitted_at "
            "FROM orders WHERE status='filled' AND submitted_at >= ? ORDER BY submitted_at",
            conn, params=(cutoff,),
        )
        conn.close()
        df["submitted_at"] = pd.to_datetime(df["submitted_at"])
        df["notional"] = df["qty"].abs() * df["fill_price"].fillna(0)
        return df
    except Exception as exc:
        logger.warning("Could not load orders: %s", exc)
        return pd.DataFrame()


def compute_turnover(aum: float = 10_000_000.0, db_path: str = DB_PATH) -> dict:
    """Compute trailing 30d and 90d turnover and annualized rate."""
    orders_30 = _load_orders(days=30, db_path=db_path)
    orders_90 = _load_orders(days=90, db_path=db_path)

    notional_30 = orders_30["notional"].sum() if not orders_30.empty else 0.0
    notional_90 = orders_90["notional"].sum() if not orders_90.empty else 0.0

    turnover_30d_pct = (notional_30 / aum * 100) if aum else 0.0
    turnover_90d_pct = (notional_90 / aum * 100) if aum else 0.0

    # Annualize: 30d × (252/30) — uses trading days
    annualized_pct = turnover_30d_pct * (TRADING_DAYS_PER_YEAR / 30)

    return {
        "turnover_30d_pct": round(turnover_30d_pct, 1),
        "turnover_90d_pct": round(turnover_90d_pct, 1),
        "annualized_pct": round(annualized_pct, 1),
        "notional_30d": round(notional_30, 2),
        "notional_90d": round(notional_90, 2),
    }


def estimate_tax_liability(db_path: str = DB_PATH) -> dict:
    """FIFO-based realized P&L split into short-term and long-term gains."""
    orders = _load_orders(days=365 * 3, db_path=db_path)
    if orders.empty:
        return {
            "short_term_gains": 0.0, "long_term_gains": 0.0,
            "short_term_tax": 0.0, "long_term_tax": 0.0, "total_tax": 0.0,
        }

    st_gains = 0.0
    lt_gains = 0.0

    for ticker, group in orders.groupby("ticker"):
        open_lots: list[list] = []  # [qty, price, date]
        for _, row in group.iterrows():
            action = row["action"].lower()
            qty = abs(row["qty"])
            price = row["fill_price"] or 0.0
            date = row["submitted_at"]

            if action in ("buy", "cover"):
                open_lots.append([qty, price, date])
            elif action in ("sell", "short") and open_lots:
                qty_left = qty
                while qty_left > 0 and open_lots:
                    lot_qty, lot_price, lot_date = open_lots[0]
                    matched = min(qty_left, lot_qty)
                    pnl = (price - lot_price) * matched
                    hold_days = (date - lot_date).days
                    if hold_days >= 365:
                        lt_gains += pnl
                    else:
                        st_gains += pnl
                    if matched >= lot_qty:
                        open_lots.pop(0)
                    else:
                        open_lots[0][0] -= matched
                    qty_left -= matched

    st_tax = max(0.0, st_gains * SHORT_TERM_TAX_RATE)
    lt_tax = max(0.0, lt_gains * LONG_TERM_TAX_RATE)

    return {
        "short_term_gains": round(st_gains, 2),
        "long_term_gains": round(lt_gains, 2),
        "short_term_tax": round(st_tax, 2),
        "long_term_tax": round(lt_tax, 2),
        "total_tax": round(st_tax + lt_tax, 2),
    }
