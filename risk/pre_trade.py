"""Pre-trade veto: 8 absolute checks. ANY failure = REJECT.

Closing/covering trades are ALWAYS approved — unwinding is never blocked.
"""
import logging
import os
import sqlite3

import numpy as np
import pandas as pd

from risk.circuit_breakers import halt_lock_exists
from risk.state import log_rejection

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "max_position_pct": 0.05,    # 5% of AUM
    "max_sector_pct": 0.25,      # 25% of AUM
    "max_gross_exposure": 1.65,  # 165%
    "net_exposure_min": -0.10,   # -10%
    "net_exposure_max": 0.15,    # +15%
    "max_beta": 0.20,
    "max_pairwise_corr": 0.80,
    "adv_pct_limit": 0.05,       # 5% of ADV
    "earnings_blackout_days": 5,
    "earnings_size_limit": 0.50, # 50% size during blackout
}


def _is_closing_trade(ticker: str, shares: float, current_positions: list[dict]) -> bool:
    """Return True if this trade reduces or closes an existing position."""
    existing = next((p for p in current_positions if p.get("ticker") == ticker), None)
    if existing is None:
        return False
    existing_shares = existing.get("shares", 0)
    # Closing: new order moves shares toward zero (opposite sign or reduces magnitude)
    if existing_shares > 0 and shares < 0:
        return True
    if existing_shares < 0 and shares > 0:
        return True
    # Partial close
    if existing_shares > 0 and shares < 0 and abs(shares) <= abs(existing_shares):
        return True
    if existing_shares < 0 and shares > 0 and abs(shares) <= abs(existing_shares):
        return True
    return False


def _get_adv(ticker: str, days: int = 20) -> float:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT volume FROM daily_prices WHERE ticker=? ORDER BY date DESC LIMIT ?",
        (ticker, days),
    ).fetchall()
    conn.close()
    if not rows:
        return 0.0
    return float(np.mean([r[0] for r in rows if r[0]]))


def _get_current_price(ticker: str) -> float:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT adj_close FROM daily_prices WHERE ticker=? ORDER BY date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    conn.close()
    return float(row[0]) if row and row[0] else 0.0


def _get_sector(ticker: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT sector FROM tickers WHERE symbol=?", (ticker,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _get_returns(tickers: list[str], days: int = 65) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(tickers))
    df = pd.read_sql_query(
        f"SELECT ticker, date, adj_close FROM daily_prices "
        f"WHERE ticker IN ({placeholders}) ORDER BY ticker, date",
        conn, params=tickers,
    )
    conn.close()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    pivot = df.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    return pivot.tail(days + 5).pct_change().dropna(how="all")


def _get_beta(ticker: str) -> float:
    """Get beta from positions table, or compute from price data."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT beta FROM positions WHERE ticker=? AND is_active=1", (ticker,)
    ).fetchone()
    conn.close()
    if row and row[0] is not None:
        return float(row[0])
    # Fallback: compute rolling beta vs SPY
    returns = _get_returns([ticker, "SPY"], days=65)
    if returns.empty or ticker not in returns.columns or "SPY" not in returns.columns:
        return 1.0
    pair = returns[[ticker, "SPY"]].dropna().tail(60)
    if len(pair) < 20:
        return 1.0
    cov = np.cov(pair[ticker].values, pair["SPY"].values)
    var_spy = cov[1, 1]
    return float(cov[0, 1] / var_spy) if var_spy != 0 else 1.0


def _has_earnings_soon(ticker: str, days: int = 5) -> bool:
    """Return True if earnings expected within `days` calendar days."""
    conn = sqlite3.connect(DB_PATH)
    from datetime import date, timedelta
    today = date.today().isoformat()
    future = (date.today() + pd.Timedelta(days=days)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) FROM earnings_transcripts WHERE ticker=? AND date BETWEEN ? AND ?",
        (ticker, today, future),
    ).fetchone()
    conn.close()
    return bool(row and row[0] > 0)


def pre_trade_veto(
    ticker: str,
    shares: float,
    current_positions: list[dict],
    config: dict | None = None,
    aum: float = 1_000_000.0,
) -> dict:
    """Run 8 pre-trade veto checks. Returns approval result with check details.

    Closing/covering trades are ALWAYS approved.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    checks = []

    # Closing trades always approved
    if _is_closing_trade(ticker, shares, current_positions):
        return {
            "approved": True,
            "reason": "Closing/covering trade — always approved",
            "checks": [{"check": "closing_trade", "passed": True, "note": "Unwind approved"}],
        }

    def reject(check_num: int, reason: str) -> dict:
        log_rejection(ticker, reason, check_num, shares)
        logger.warning("PRE-TRADE REJECT [check %d] %s: %s", check_num, ticker, reason)
        return {
            "approved": False,
            "reason": reason,
            "check_number": check_num,
            "checks": checks,
        }

    # --- Check 1: Kill switch lock file ---
    if halt_lock_exists():
        checks.append({"check": 1, "name": "halt_lock", "passed": False})
        return reject(1, "Trading halted — kill switch is active (run --clear-halt to resume)")
    checks.append({"check": 1, "name": "halt_lock", "passed": True})

    # --- Check 2: Earnings blackout ---
    in_blackout = _has_earnings_soon(ticker, cfg["earnings_blackout_days"])
    if in_blackout:
        # Allow at 50% size only — if shares > 50% limit, reject
        max_allowed = abs(shares) * cfg["earnings_size_limit"]
        if abs(shares) > max_allowed:
            checks.append({"check": 2, "name": "earnings_blackout", "passed": False,
                          "note": f"Earnings within {cfg['earnings_blackout_days']}d, max 50% size"})
            return reject(2, f"Earnings blackout: reduce order to {max_allowed:.0f} shares (50% limit)")
    checks.append({"check": 2, "name": "earnings_blackout", "passed": True,
                  "note": "in blackout — size approved at 50%" if in_blackout else "clear"})

    # --- Check 3: Liquidity — shares ≤ 5% of 20-day ADV ---
    adv = _get_adv(ticker)
    if adv > 0 and abs(shares) > cfg["adv_pct_limit"] * adv:
        checks.append({"check": 3, "name": "liquidity", "passed": False,
                      "adv": adv, "requested": abs(shares), "limit": cfg["adv_pct_limit"] * adv})
        return reject(3, f"Liquidity: {abs(shares):.0f} shares > {cfg['adv_pct_limit']*100:.0f}% of ADV ({adv:.0f})")
    checks.append({"check": 3, "name": "liquidity", "passed": True, "adv": adv})

    # --- Check 4: Position limit ≤ 5% of AUM ---
    price = _get_current_price(ticker)
    pos_value = abs(shares) * price
    # Include existing position value
    existing = next((p for p in current_positions if p.get("ticker") == ticker), None)
    existing_value = abs((existing or {}).get("shares", 0)) * price
    total_pos_value = pos_value + existing_value
    if aum > 0 and total_pos_value / aum > cfg["max_position_pct"]:
        checks.append({"check": 4, "name": "position_limit", "passed": False,
                      "pos_pct": total_pos_value / aum * 100})
        return reject(4, f"Position limit: {total_pos_value/aum*100:.1f}% > {cfg['max_position_pct']*100:.0f}% of AUM")
    checks.append({"check": 4, "name": "position_limit", "passed": True,
                  "pos_pct": round(total_pos_value / aum * 100, 2) if aum else 0})

    # --- Check 5: Sector limit ≤ 25% of AUM ---
    sector = _get_sector(ticker) or (existing or {}).get("sector")
    if sector:
        sector_positions = [
            p for p in current_positions
            if p.get("sector") == sector and p.get("is_active", 1) and p.get("ticker") != ticker
        ]
        sector_price_total = sum(
            abs(p.get("shares", 0)) * (p.get("current_price") or p.get("entry_price") or 0)
            for p in sector_positions
        )
        total_sector = sector_price_total + total_pos_value
        if aum > 0 and total_sector / aum > cfg["max_sector_pct"]:
            checks.append({"check": 5, "name": "sector_limit", "passed": False,
                          "sector": sector, "sector_pct": total_sector / aum * 100})
            return reject(5, f"Sector limit: {sector} = {total_sector/aum*100:.1f}% > {cfg['max_sector_pct']*100:.0f}%")
    checks.append({"check": 5, "name": "sector_limit", "passed": True, "sector": sector})

    # --- Check 6: Gross/net exposure ---
    # Simulate adding this position
    target_positions = {p["ticker"]: p.get("shares", 0) for p in current_positions if p.get("is_active", 1)}
    target_positions[ticker] = target_positions.get(ticker, 0) + shares

    prices = {}
    for t in target_positions:
        p_row = _get_current_price(t)
        prices[t] = p_row if p_row else 0

    total_long_val = sum(s * prices.get(t, 0) for t, s in target_positions.items() if s > 0)
    total_short_val = sum(abs(s) * prices.get(t, 0) for t, s in target_positions.items() if s < 0)
    gross = (total_long_val + total_short_val) / aum if aum else 0
    net = (total_long_val - total_short_val) / aum if aum else 0

    gross_ok = gross <= cfg["max_gross_exposure"]
    net_ok = cfg["net_exposure_min"] <= net <= cfg["net_exposure_max"]
    if not gross_ok or not net_ok:
        checks.append({"check": 6, "name": "gross_net_exposure", "passed": False,
                      "gross": gross, "net": net})
        reason = []
        if not gross_ok:
            reason.append(f"Gross {gross*100:.1f}% > {cfg['max_gross_exposure']*100:.0f}%")
        if not net_ok:
            reason.append(f"Net {net*100:.1f}% out of [{cfg['net_exposure_min']*100:.0f}%, {cfg['net_exposure_max']*100:.0f}%]")
        return reject(6, "; ".join(reason))
    checks.append({"check": 6, "name": "gross_net_exposure", "passed": True,
                  "gross": round(gross * 100, 2), "net": round(net * 100, 2)})

    # --- Check 7: Beta limit — |net portfolio beta| ≤ 0.20 ---
    beta_ticker = _get_beta(ticker)
    existing_betas = {
        p["ticker"]: (p.get("beta") or 1.0) for p in current_positions if p.get("is_active", 1)
    }
    existing_betas[ticker] = beta_ticker

    total_weight = sum(abs(s) * prices.get(t, 0) for t, s in target_positions.items())
    if total_weight > 0:
        net_beta = sum(
            s * prices.get(t, 0) / total_weight * existing_betas.get(t, 1.0)
            for t, s in target_positions.items()
        )
    else:
        net_beta = 0.0

    if abs(net_beta) > cfg["max_beta"]:
        checks.append({"check": 7, "name": "beta_limit", "passed": False, "net_beta": net_beta})
        return reject(7, f"Beta limit: net portfolio beta {net_beta:.3f} > ±{cfg['max_beta']:.2f}")
    checks.append({"check": 7, "name": "beta_limit", "passed": True, "net_beta": round(net_beta, 3)})

    # --- Check 8: Correlation check — max pairwise ≤ 0.80 ---
    existing_tickers = [p["ticker"] for p in current_positions if p.get("is_active", 1) and p["ticker"] != ticker]
    if existing_tickers:
        returns = _get_returns([ticker] + existing_tickers, days=65)
        if not returns.empty and ticker in returns.columns:
            pair_returns = returns[[ticker] + [t for t in existing_tickers if t in returns.columns]].tail(60).dropna()
            if len(pair_returns) >= 20 and ticker in pair_returns.columns:
                corr_matrix = pair_returns.corr()
                for other in existing_tickers:
                    if other in corr_matrix.columns and ticker in corr_matrix.index:
                        corr = corr_matrix.loc[ticker, other]
                        if abs(corr) > cfg["max_pairwise_corr"]:
                            checks.append({"check": 8, "name": "correlation_check", "passed": False,
                                          "ticker": ticker, "other": other, "corr": corr})
                            return reject(8, f"Correlation: {ticker}/{other} = {corr:.2f} > {cfg['max_pairwise_corr']:.2f}")
    checks.append({"check": 8, "name": "correlation_check", "passed": True})

    return {
        "approved": True,
        "reason": "All 8 pre-trade checks passed",
        "checks": checks,
    }
