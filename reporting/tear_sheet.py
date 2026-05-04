"""Institutional-format markdown tear sheet generator."""
import logging
import os
import sqlite3
from datetime import date, datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")


def _load_portfolio_history(db_path: str = DB_PATH) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT date, total_value FROM portfolio_history ORDER BY date",
        conn,
    )
    conn.close()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def _load_spy_returns(db_path: str = DB_PATH) -> pd.Series:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT date, adj_close FROM daily_prices WHERE ticker='SPY' ORDER BY date",
        conn,
    )
    conn.close()
    if df.empty:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["adj_close"].pct_change().dropna()


def _sharpe(returns: pd.Series, rf: float = 0.05) -> float:
    if returns.empty or returns.std() == 0:
        return 0.0
    ann = returns.mean() * 252 - rf
    vol = returns.std() * np.sqrt(252)
    return round(ann / vol, 2) if vol else 0.0


def _max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return round(float(dd.min()) * 100, 2)


def _monthly_returns_grid(returns: pd.Series) -> str:
    if returns.empty:
        return "_No data_"
    monthly = returns.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    monthly.index = monthly.index.to_period("M")
    pivot = monthly.groupby([monthly.index.year, monthly.index.month]).first().unstack()
    pivot.columns = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][:pivot.shape[1]]

    lines = ["| Year | " + " | ".join(pivot.columns) + " |"]
    lines.append("|------|" + "|".join([":---:"] * len(pivot.columns)) + "|")
    for year, row in pivot.iterrows():
        cells = []
        for v in row:
            if pd.isna(v):
                cells.append("   ")
            else:
                sign = "+" if v >= 0 else ""
                cells.append(f"{sign}{v*100:.1f}%")
        lines.append(f"| {year} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _load_factor_exposures(db_path: str = DB_PATH) -> dict:
    """Load latest factor exposures from risk_state or return empty."""
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT state_json FROM risk_state ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            import json
            state = json.loads(row[0])
            return state.get("factor_exposures", {})
    except Exception:
        pass
    return {}


def generate_tear_sheet(
    start_date: str = None,
    end_date: str = None,
    aum: float = 10_000_000.0,
    fund_name: str = "Meridian Capital Partners",
    db_path: str = DB_PATH,
) -> str:
    """Generate institutional markdown tear sheet. Returns path to saved file."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    hist = _load_portfolio_history(db_path)
    spy_returns = _load_spy_returns(db_path)

    today = end_date or date.today().isoformat()
    start = start_date or "2026-01-01"

    fund_returns = pd.Series(dtype=float)
    if not hist.empty:
        fund_returns = hist["total_value"].pct_change().dropna()
        if start_date:
            fund_returns = fund_returns[fund_returns.index >= start_date]

    spy_aligned = spy_returns.reindex(fund_returns.index).fillna(0) if not fund_returns.empty else pd.Series(dtype=float)

    fund_total = (1 + fund_returns).prod() - 1 if not fund_returns.empty else 0.0
    spy_total = (1 + spy_aligned).prod() - 1 if not spy_aligned.empty else 0.0
    alpha = fund_total - spy_total
    sharpe = _sharpe(fund_returns)
    max_dd = _max_drawdown(fund_returns)
    vol_ann = round(fund_returns.std() * np.sqrt(252) * 100, 2) if not fund_returns.empty else 0.0

    # Rolling 12-month Sharpe
    rolling_sharpe = 0.0
    if len(fund_returns) >= 252:
        rolling_sharpe = _sharpe(fund_returns.tail(252))

    monthly_grid = _monthly_returns_grid(fund_returns)
    factor_exposures = _load_factor_exposures(db_path)

    # Load positions for sector allocation
    conn = sqlite3.connect(db_path)
    positions = pd.read_sql_query(
        "SELECT p.ticker, p.shares, t.sector, p.entry_price "
        "FROM positions p LEFT JOIN tickers t ON p.ticker=t.symbol "
        "WHERE p.is_active=1", conn,
    )
    conn.close()

    sector_alloc = ""
    if not positions.empty:
        positions["value"] = positions["shares"].abs() * positions["entry_price"].fillna(0)
        total_val = positions["value"].sum()
        if total_val > 0:
            sector_totals = positions.groupby("sector")["value"].sum().sort_values(ascending=False)
            sector_lines = [f"- {s or 'Unknown'}: {v/total_val*100:.1f}%" for s, v in sector_totals.items()]
            sector_alloc = "\n".join(sector_lines)

    factor_lines = "\n".join(
        f"- {k.capitalize()}: {v:+.2f}"
        for k, v in sorted(factor_exposures.items(), key=lambda x: -abs(x[1]))
    ) or "- No factor data available"

    from reporting.turnover import compute_turnover
    turn = compute_turnover(aum=aum, db_path=db_path)

    content = f"""# {fund_name} — Performance Report
Period: {start} to {today}

## Returns
- Fund: {fund_total*100:+.2f}%
- SPY: {spy_total*100:+.2f}%
- Alpha: {alpha*100:+.2f}%
- Rolling 12mo Sharpe: {rolling_sharpe:.2f}

## Monthly Returns Grid
{monthly_grid}

## Risk Metrics
- Max Drawdown: {max_dd:.1f}%
- Volatility (ann): {vol_ann:.1f}%
- Sharpe Ratio: {sharpe:.2f}

## Factor Exposures
{factor_lines}

## Sector Allocation
{sector_alloc or "- No position data"}

## Turnover
30d: {turn['turnover_30d_pct']:.1f}% | Annualized: {turn['annualized_pct']:.0f}%

---
*Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} by JARVIS*
"""

    out_path = os.path.join(OUTPUT_DIR, f"tear_sheet_{today}.md")
    with open(out_path, "w") as f:
        f.write(content)
    logger.info("Tear sheet saved to %s", out_path)
    return out_path
