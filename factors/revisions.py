"""Factor 5: Estimate Revisions — 3 sub-factors (degenerate: sector median 50 when no data)."""
import pandas as pd
import numpy as np
from ._base import get_db, apply_sector_ranks


def calculate_all(universe: pd.DataFrame) -> pd.DataFrame:
    """
    Revisions require analyst estimate snapshots we don't yet have.
    Use price momentum over 30/60/90 days as proxy when estimates unavailable.
    """
    conn = get_db()
    prices = pd.read_sql_query(
        "SELECT ticker, date, adj_close FROM daily_prices ORDER BY ticker, date",
        conn,
    )
    conn.close()

    D30, D60, D90 = 30, 60, 90

    records = []
    for _, row in universe.iterrows():
        tkr = row["ticker"]
        sector = row["sector"]
        s = prices[prices["ticker"] == tkr].sort_values("date")
        close = s["adj_close"].values
        n = len(close)

        def proxy_return(lookback):
            if n <= lookback:
                return np.nan
            p0 = close[max(0, n - lookback - 1)]
            p1 = close[-1]
            if p0 <= 0 or np.isnan(p0) or np.isnan(p1):
                return np.nan
            return (p1 / p0) - 1

        records.append({
            "ticker": tkr,
            "sector": sector,
            "rev_30d": proxy_return(D30),
            "rev_60d": proxy_return(D60),
            "rev_90d": proxy_return(D90),
        })

    df = pd.DataFrame(records)

    for col in ["rev_30d", "rev_60d", "rev_90d"]:
        df[f"rank_{col}"] = apply_sector_ranks(df, col)

    rank_cols = [c for c in df.columns if c.startswith("rank_")]
    df["revisions_score"] = df[rank_cols].mean(axis=1).fillna(50.0)

    return df[["ticker", "sector", "revisions_score"] + rank_cols]
