"""Factor 8: Institutional Flow — 3 sub-factors from 13F holdings."""
import pandas as pd
import numpy as np
from ._base import get_db, apply_sector_ranks


def calculate_all(universe: pd.DataFrame) -> pd.DataFrame:
    conn = get_db()
    holdings = pd.read_sql_query(
        "SELECT ticker, fund_name, cik, quarter, shares, value FROM institutional_holdings",
        conn,
    )
    conn.close()

    df = universe.copy()

    if holdings.empty:
        df["inst_fund_count"] = np.nan
        df["inst_net_change"] = np.nan
        df["inst_new_openers"] = np.nan
    else:
        quarters = sorted(holdings["quarter"].unique())
        latest_q = quarters[-1] if quarters else None
        prev_q = quarters[-2] if len(quarters) >= 2 else None

        latest = holdings[holdings["quarter"] == latest_q] if latest_q else holdings.iloc[:0]
        prev = holdings[holdings["quarter"] == prev_q] if prev_q else holdings.iloc[:0]

        # Sub-factor 1: Number of funds holding the stock
        fund_count = (
            latest.groupby("ticker")["fund_name"]
            .nunique()
            .rename("inst_fund_count")
            .reset_index()
        )
        df = df.merge(fund_count, on="ticker", how="left")

        # Sub-factor 2: Net change in aggregate holdings (latest vs prior quarter)
        if not prev.empty:
            latest_agg = latest.groupby("ticker")["shares"].sum().rename("shares_latest")
            prev_agg = prev.groupby("ticker")["shares"].sum().rename("shares_prev")
            change = (latest_agg - prev_agg).rename("inst_net_change").reset_index()
            df = df.merge(change, on="ticker", how="left")
        else:
            df["inst_net_change"] = np.nan

        # Sub-factor 3: Multi-fund simultaneous opening (3+ new positions same ticker same quarter)
        if not prev.empty:
            prev_tickers = set(prev.groupby(["ticker", "cik"]).groups.keys())
            latest_new = latest[~latest.apply(lambda r: (r["ticker"], r["cik"]) in prev_tickers, axis=1)]
            openers = (
                latest_new.groupby("ticker")["cik"]
                .nunique()
                .rename("new_openers")
                .reset_index()
            )
            openers["inst_new_openers"] = (openers["new_openers"] >= 3).astype(float)
            df = df.merge(openers[["ticker", "inst_new_openers"]], on="ticker", how="left")
        else:
            df["inst_new_openers"] = np.nan

    for col in ["inst_fund_count", "inst_net_change", "inst_new_openers"]:
        if col not in df.columns:
            df[col] = np.nan
        df[f"rank_{col}"] = apply_sector_ranks(df, col)

    rank_cols = [c for c in df.columns if c.startswith("rank_")]
    df["institutional_score"] = df[rank_cols].mean(axis=1).fillna(50.0)

    return df[["ticker", "sector", "institutional_score"] + rank_cols]
