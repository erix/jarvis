"""Factor 5: Estimate Revisions — 3 sub-factors from estimate snapshots."""
import pandas as pd
import numpy as np
from ._base import get_db, apply_sector_ranks, table_exists


def calculate_all(universe: pd.DataFrame) -> pd.DataFrame:
    """
    Prefer analyst estimate snapshots. If the table is unavailable or has fewer
    than two snapshots per ticker, fall back to price momentum as a weak proxy.
    """
    conn = get_db()
    if table_exists(conn, "analyst_estimates"):
        estimates = pd.read_sql_query(
            """
            SELECT ticker, date, forward_eps, target_mean_price, recommendation_mean
            FROM analyst_estimates ORDER BY ticker, date
            """,
            conn,
        )
    else:
        estimates = pd.DataFrame()

    if not estimates.empty:
        records = []
        for _, row in universe.iterrows():
            tkr = row["ticker"]
            s = estimates[estimates["ticker"] == tkr].sort_values("date")
            latest = s.iloc[-1] if not s.empty else None
            prev_30 = s.iloc[max(0, len(s) - 31)] if len(s) >= 2 else None
            prev_60 = s.iloc[max(0, len(s) - 61)] if len(s) >= 2 else None

            def pct_change(field, prev):
                if latest is None or prev is None:
                    return np.nan
                cur = latest.get(field)
                old = prev.get(field)
                if pd.isna(cur) or pd.isna(old) or old == 0:
                    return np.nan
                return (cur / abs(old)) - 1

            rec_change = np.nan
            if latest is not None:
                # Lower recommendationMean is better in yfinance's scale.
                rec = latest.get("recommendation_mean")
                rec_change = -rec if pd.notna(rec) else np.nan

            records.append({
                "ticker": tkr,
                "sector": row["sector"],
                "eps_rev_30d": pct_change("forward_eps", prev_30),
                "eps_rev_60d": pct_change("forward_eps", prev_60),
                "target_rev_30d": pct_change("target_mean_price", prev_30),
                "recommendation_quality": rec_change,
            })

        df = pd.DataFrame(records)
        signal_cols = ["eps_rev_30d", "eps_rev_60d", "target_rev_30d", "recommendation_quality"]
        non_null_signals = int(df[signal_cols].notna().sum().sum())
        if non_null_signals > 0:
            for col in signal_cols:
                df[f"rank_{col}"] = apply_sector_ranks(df, col)
            rank_cols = [c for c in df.columns if c.startswith("rank_")]
            df["revisions_score"] = df[rank_cols].mean(axis=1).fillna(50.0)
            conn.close()
            return df[["ticker", "sector", "revisions_score"] + rank_cols]

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
