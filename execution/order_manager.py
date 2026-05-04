"""Order lifecycle management: pending → partial → filled/cancelled."""
import logging
import os
import signal
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Optional

from execution.costs import ensure_orders_table

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")


class OrderManager:
    def __init__(self, db_path: str = DB_PATH, broker=None):
        self.db_path = db_path
        self.broker = broker
        ensure_orders_table(db_path)
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_orders(self, status: Optional[str] = None) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if status:
            rows = conn.execute("SELECT * FROM orders WHERE status=?", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM orders ORDER BY submitted_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_open_orders(self) -> list[dict]:
        return self.get_orders("pending") + self.get_orders("partial")

    def get_filled_orders(self, days: int = 30) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM orders WHERE status='filled' AND submitted_at >= ? ORDER BY submitted_at DESC",
            (cutoff,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Mutation methods
    # ------------------------------------------------------------------

    def update_order_status(self, order_id: str, status: str, fill_price: float = None,
                            slippage_bps: float = None, commission: float = None) -> None:
        conn = sqlite3.connect(self.db_path)
        if fill_price is not None:
            conn.execute(
                "UPDATE orders SET status=?, fill_price=?, slippage_bps=?, commission=? WHERE order_id=?",
                (status, fill_price, slippage_bps, commission, order_id),
            )
        else:
            conn.execute("UPDATE orders SET status=? WHERE order_id=?", (status, order_id))
        conn.commit()
        conn.close()

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order. If broker is attached, sends cancel to IBKR."""
        if self.broker and self.broker.is_connected():
            open_trades = self.broker.ib.openTrades()
            for trade in open_trades:
                if str(trade.order.orderId) == str(order_id):
                    self.broker.ib.cancelOrder(trade.order)
                    logger.info("Sent cancel for order %s to IBKR", order_id)
                    break
        self.update_order_status(order_id, "cancelled")
        logger.info("Order %s marked cancelled in DB", order_id)
        return True

    def sync_with_portfolio(self) -> None:
        """Reconcile filled orders with Layer 4 positions table."""
        filled = self.get_filled_orders(days=1)
        if not filled:
            return
        conn = sqlite3.connect(self.db_path)
        for order in filled:
            ticker = order["ticker"]
            qty = order["qty"]
            fill_price = order["fill_price"] or order["limit_price"] or 0.0
            action = order["action"]

            if action in ("buy", "cover"):
                shares_delta = abs(qty)
            else:  # sell / short
                shares_delta = -abs(qty)

            existing = conn.execute(
                "SELECT id, shares FROM positions WHERE ticker=? AND is_active=1", (ticker,)
            ).fetchone()
            if existing:
                new_shares = existing[1] + shares_delta
                if abs(new_shares) < 0.01:
                    conn.execute("UPDATE positions SET is_active=0 WHERE id=?", (existing[0],))
                else:
                    conn.execute("UPDATE positions SET shares=? WHERE id=?", (new_shares, existing[0]))
            elif shares_delta != 0:
                conn.execute(
                    "INSERT INTO positions (ticker, shares, entry_price, is_active) VALUES (?, ?, ?, 1)",
                    (ticker, shares_delta, fill_price),
                )
        conn.commit()
        conn.close()
        logger.info("Portfolio synced with %d filled orders", len(filled))

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _shutdown_handler(self, signum, frame):
        logger.warning("Interrupt received — cancelling open orders")
        for order in self.get_open_orders():
            self.cancel_order(order["order_id"])
        if self.broker:
            self.broker.disconnect()
        sys.exit(0)
