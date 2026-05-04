"""Order submission with pre-trade veto and fill tracking."""
import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

from ib_insync import IB, LimitOrder, Stock

from execution.costs import record_order, track_slippage
from execution.order_manager import OrderManager
from execution.short_check import is_shortable
from risk.pre_trade import pre_trade_veto

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")

_FILL_POLL_INTERVAL = 5   # seconds
_ORDER_TIMEOUT = 300       # 5 minutes


def _get_current_positions(db_path: str = DB_PATH) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM positions WHERE is_active=1").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def execute_trade(
    ticker: str,
    action: str,
    shares: float,
    signal_price: float,
    ib: Optional[IB] = None,
    order_manager: Optional[OrderManager] = None,
    dry_run: bool = True,
    aum: float = 10_000_000.0,
    db_path: str = DB_PATH,
) -> dict:
    """Place a single trade via IBKR after passing pre-trade veto.

    action: 'buy' | 'sell' | 'short' | 'cover'
    dry_run=True: log what would happen, don't place order (DEFAULT).
    """
    current_positions = _get_current_positions(db_path)

    # Pre-trade veto is ALWAYS run, even in dry-run
    veto_shares = shares if action in ("buy", "cover") else -abs(shares)
    veto_result = pre_trade_veto(
        ticker=ticker,
        shares=veto_shares,
        current_positions=current_positions,
        aum=aum,
    )

    if not veto_result["approved"]:
        logger.warning("[%s] PRE-TRADE REJECTED: %s", ticker, veto_result["reason"])
        return {
            "ticker": ticker,
            "action": action,
            "shares": shares,
            "status": "rejected",
            "reason": veto_result["reason"],
            "dry_run": dry_run,
        }

    # Check short availability
    if action == "short" and ib is not None:
        if not is_shortable(ticker, ib):
            logger.warning("[%s] SHORT NOT AVAILABLE — skipping", ticker)
            return {
                "ticker": ticker, "action": action, "shares": shares,
                "status": "rejected", "reason": "Short sale slot not available",
                "dry_run": dry_run,
            }

    if dry_run:
        logger.info("[DRY-RUN] Would %s %d shares of %s at ~%.2f", action, abs(shares), ticker, signal_price)
        return {
            "ticker": ticker, "action": action, "shares": shares,
            "signal_price": signal_price, "status": "dry_run", "dry_run": True,
        }

    if ib is None:
        raise ValueError("ib (IB instance) required for live execution")

    # Build contract
    stock = Stock(ticker, "SMART", "USD")
    ib.qualifyContracts(stock)

    # Get market data for limit price calculation
    ticker_obj = ib.reqMktData(stock, "", False, False)
    ib.sleep(1)

    bid = ticker_obj.bid if ticker_obj.bid and ticker_obj.bid > 0 else signal_price
    ask = ticker_obj.ask if ticker_obj.ask and ticker_obj.ask > 0 else signal_price
    mid = (bid + ask) / 2

    if action in ("buy", "cover"):
        limit_price = round(mid + 0.01, 2)
        ib_action = "BUY"
    else:  # sell / short — in IBKR shorts are "SELL"
        limit_price = round(mid - 0.01, 2)
        ib_action = "SELL"

    order = LimitOrder(ib_action, abs(shares), limit_price)
    trade = ib.placeOrder(stock, order)
    order_id = str(trade.order.orderId)
    perm_id = str(trade.order.permId)

    submitted_at = datetime.utcnow().isoformat()
    logger.info("[%s] Placed %s %d @ %.2f (order_id=%s)", ticker, ib_action, abs(shares), limit_price, order_id)

    if order_manager:
        from execution.costs import ensure_orders_table, record_order
        record_order({
            "submitted_at": submitted_at, "ticker": ticker, "action": action,
            "qty": shares, "signal_price": signal_price, "limit_price": limit_price,
            "fill_price": None, "slippage_bps": None, "commission": None,
            "status": "pending", "order_id": order_id, "perm_id": perm_id,
        }, db_path)

    # Poll for fill
    elapsed = 0
    fill_price = None
    commission = None
    status = "pending"

    while elapsed < _ORDER_TIMEOUT:
        ib.sleep(_FILL_POLL_INTERVAL)
        elapsed += _FILL_POLL_INTERVAL

        os_status = trade.orderStatus.status
        if os_status in ("Filled",):
            fill_price = trade.orderStatus.avgFillPrice
            status = "filled"
            # Commission from commission reports
            for cr in ib.commissionReport():
                if hasattr(cr, "execId") and str(trade.order.orderId) in str(cr.execId):
                    commission = cr.commission
                    break
            break
        elif os_status in ("Cancelled", "ApiCancelled"):
            status = "cancelled"
            break
        elif os_status in ("PartiallyFilled",):
            status = "partial"
            fill_price = trade.orderStatus.avgFillPrice
        logger.debug("[%s] Order status: %s (elapsed %ds)", ticker, os_status, elapsed)

    if status == "pending" or (status == "partial" and elapsed >= _ORDER_TIMEOUT):
        # Timeout — cancel
        ib.cancelOrder(trade.order)
        status = "cancelled"
        logger.warning("[%s] Order timed out after %ds — cancelled", ticker, _ORDER_TIMEOUT)

    slippage_bps = None
    if fill_price:
        slippage_bps = track_slippage({
            "fill_price": fill_price, "signal_price": signal_price, "action": action
        })
        logger.info("[%s] Filled @ %.2f, slippage %.1f bps, commission $%.2f",
                    ticker, fill_price, slippage_bps, commission or 0)

    if order_manager:
        order_manager.update_order_status(
            order_id, status,
            fill_price=fill_price,
            slippage_bps=slippage_bps,
            commission=commission,
        )
        order_manager.sync_with_portfolio()

    return {
        "ticker": ticker, "action": action, "shares": shares,
        "signal_price": signal_price, "limit_price": limit_price,
        "fill_price": fill_price, "slippage_bps": slippage_bps,
        "commission": commission, "status": status, "order_id": order_id,
        "dry_run": False,
    }
