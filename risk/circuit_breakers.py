"""Circuit breakers: alert-only thresholds + kill-switch lock file.

CRITICAL: Circuit breakers do NOT automatically close positions.
They ONLY block new trades and log alerts. Existing positions are never auto-closed.
"""
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timezone

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
HALT_LOCK_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "halt.lock")

logger = logging.getLogger(__name__)


def halt_lock_exists() -> bool:
    return os.path.isfile(HALT_LOCK_PATH)


def create_halt_lock(reason: str) -> None:
    payload = {
        "halted": True,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(HALT_LOCK_PATH), exist_ok=True)
    with open(HALT_LOCK_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    logger.critical("KILL SWITCH ACTIVATED: %s — lock file written to %s", reason, HALT_LOCK_PATH)
    print(f"\n{'='*60}")
    print("KILL SWITCH ACTIVATED")
    print(f"Reason: {reason}")
    print(f"Lock file: {HALT_LOCK_PATH}")
    print("All new trades are BLOCKED until --clear-halt is run.")
    print(f"{'='*60}\n")


def clear_halt_lock() -> bool:
    if os.path.isfile(HALT_LOCK_PATH):
        os.remove(HALT_LOCK_PATH)
        logger.info("Halt lock cleared by operator.")
        return True
    return False


def get_halt_info() -> dict | None:
    if not halt_lock_exists():
        return None
    with open(HALT_LOCK_PATH) as f:
        return json.load(f)


def _load_portfolio_values(days: int = 10) -> pd.DataFrame:
    """Compute daily portfolio value from portfolio_history + current positions."""
    conn = sqlite3.connect(DB_PATH)

    # Get mark-to-market values from daily_prices × positions
    try:
        positions_df = pd.read_sql_query(
            "SELECT ticker, shares, entry_price, current_price FROM positions WHERE is_active=1",
            conn,
        )
        if positions_df.empty:
            conn.close()
            return pd.DataFrame()

        # Get recent prices for all active tickers
        tickers = positions_df["ticker"].tolist()
        placeholders = ",".join("?" * len(tickers))
        prices_df = pd.read_sql_query(
            f"""SELECT ticker, date, adj_close FROM daily_prices
                WHERE ticker IN ({placeholders})
                ORDER BY date DESC LIMIT {len(tickers) * (days + 5)}""",
            conn, params=tickers,
        )
        conn.close()
    except Exception:
        conn.close()
        return pd.DataFrame()

    if prices_df.empty:
        return pd.DataFrame()

    prices_df["date"] = pd.to_datetime(prices_df["date"])
    pivot = prices_df.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    pivot = pivot.tail(days + 2)

    shares = positions_df.set_index("ticker")["shares"]
    # Portfolio value = sum of price * shares for each day
    common = [t for t in shares.index if t in pivot.columns]
    if not common:
        return pd.DataFrame()

    daily_values = pivot[common].mul(shares[common]).sum(axis=1)
    return daily_values.reset_index().rename(columns={0: "portfolio_value", "index": "date"})


def check_circuit_breakers(
    aum: float,
    portfolio_value: float | None = None,
    positions_df: pd.DataFrame | None = None,
) -> dict:
    """Check all circuit breaker thresholds.

    Does NOT auto-close positions — only logs alerts and may activate kill switch.
    Returns {"action": str, "message": str, "alerts": list[str]}
    """
    alerts = []
    action = "OK"
    message = "All circuit breakers clear"

    if aum <= 0:
        return {"action": "OK", "message": "No AUM — skipping circuit breakers", "alerts": []}

    # --- Load portfolio history for P&L calc ---
    hist = _load_portfolio_values(days=10)

    if hist.empty or len(hist) < 2:
        # No history yet — can't compute P&L
        return {
            "action": "OK",
            "message": "Insufficient history for P&L calculation",
            "alerts": ["WARNING: Cannot compute P&L — no price history"],
        }

    hist = hist.sort_values("date")
    current_val = float(hist.iloc[-1]["portfolio_value"])
    prev_val = float(hist.iloc[-2]["portfolio_value"])

    daily_pnl_pct = (current_val - prev_val) / aum if aum else 0.0

    # Weekly P&L (5-day lookback)
    week_start_val = float(hist.iloc[max(0, len(hist) - 6)]["portfolio_value"])
    weekly_pnl_pct = (current_val - week_start_val) / aum if aum else 0.0

    # Drawdown from all-time peak in risk_state
    from risk.state import get_recent_risk_states
    recent_states = get_recent_risk_states(days=252)
    if recent_states:
        # Use gross_exposure as nav proxy (actual NAV tracking would need more context)
        peak_val = max((s.get("gross_exposure") or 0) for s in recent_states)
        if peak_val > 0:
            drawdown_pct = (current_val - peak_val) / peak_val
        else:
            drawdown_pct = 0.0
    else:
        drawdown_pct = 0.0

    # --- Check thresholds ---

    # Daily loss > 1.5%
    if daily_pnl_pct < -0.015:
        msg = f"Daily loss {daily_pnl_pct*100:.2f}% exceeds 1.5% threshold — flagged for manual review"
        alerts.append(f"WARNING: {msg}")
        logger.warning(msg)
        action = "ALERT"
        message = msg

    # Daily loss > 2.5% — critical warning
    if daily_pnl_pct < -0.025:
        msg = f"Daily loss {daily_pnl_pct*100:.2f}% exceeds 2.5% threshold — recommend manual review of positions"
        alerts.append(f"CRITICAL: {msg}")
        logger.critical(msg)
        action = "CRITICAL_ALERT"
        message = msg

    # Weekly loss > 4%
    if weekly_pnl_pct < -0.04:
        msg = f"Weekly loss {weekly_pnl_pct*100:.2f}% exceeds 4% threshold — flagged for manual review"
        alerts.append(f"WARNING: {msg}")
        logger.warning(msg)
        if action == "OK":
            action = "ALERT"
            message = msg

    # Drawdown > 8% — KILL SWITCH
    if drawdown_pct < -0.08:
        kill_reason = f"Drawdown {drawdown_pct*100:.2f}% exceeds 8% threshold"
        alerts.append(f"KILL_SWITCH: {kill_reason}")
        if not halt_lock_exists():
            create_halt_lock(kill_reason)
        action = "KILL_SWITCH"
        message = kill_reason

    # Single position > 3% of NAV
    if positions_df is not None and not positions_df.empty and portfolio_value and portfolio_value > 0:
        for _, pos in positions_df.iterrows():
            ticker = pos.get("ticker", "?")
            shares = pos.get("shares", 0)
            price = pos.get("current_price") or pos.get("entry_price") or 0
            pos_val = abs(shares * price)
            nav_pct = pos_val / portfolio_value * 100
            if nav_pct > 3.0:
                msg = f"Position {ticker} = {nav_pct:.1f}% of NAV (>3%) — flagged for manual review"
                alerts.append(f"WARNING: {msg}")
                logger.warning(msg)

    return {
        "action": action,
        "message": message,
        "alerts": alerts,
        "daily_pnl_pct": round(daily_pnl_pct * 100, 3),
        "weekly_pnl_pct": round(weekly_pnl_pct * 100, 3),
        "drawdown_pct": round(drawdown_pct * 100, 3),
        "current_portfolio_value": round(current_val, 2),
    }
