"""Factor 7: Insider Activity — 3 sub-factors from Form 4 data."""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from ._base import get_db, apply_sector_ranks


_CEO_CFO_TITLES = ["ceo", "chief executive", "cfo", "chief financial"]


def _is_exec(title: str) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(k in t for k in _CEO_CFO_TITLES)


def calculate_all(universe: pd.DataFrame) -> pd.DataFrame:
    conn = get_db()
    txns = pd.read_sql_query(
        """
        SELECT ticker, owner_title, transaction_date, transaction_code,
               shares, price_per_share
        FROM insider_transactions
        WHERE transaction_code IN ('P', 'S')
        """,
        conn,
    )
    conn.close()

    df = universe.copy()
    cutoff_90 = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
    cutoff_30 = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

    if txns.empty:
        df["ins_net_flow"] = np.nan
        df["ins_exec_flow"] = np.nan
        df["ins_cluster"] = np.nan
    else:
        txns["dollar_flow"] = txns.apply(
            lambda r: (r["shares"] or 0) * (r["price_per_share"] or 0) *
                      (1 if r["transaction_code"] == "P" else -1),
            axis=1,
        )
        txns["is_exec"] = txns["owner_title"].apply(_is_exec)
        recent90 = txns[txns["transaction_date"] >= cutoff_90]
        recent30 = txns[txns["transaction_date"] >= cutoff_30]

        # Sub-factor 1: Net dollar flow over 90 days
        net_flow = (
            recent90.groupby("ticker")["dollar_flow"]
            .sum()
            .rename("ins_net_flow")
            .reset_index()
        )
        df = df.merge(net_flow, on="ticker", how="left")

        # Sub-factor 2: CEO/CFO purchases (weighted 3x) vs all insiders
        exec_flow = (
            recent90[recent90["is_exec"] & (recent90["transaction_code"] == "P")]
            .groupby("ticker")["dollar_flow"]
            .sum()
            .rename("ins_exec_flow")
            .reset_index()
        )
        df = df.merge(exec_flow, on="ticker", how="left")

        # Sub-factor 3: Cluster-buy flag (3+ insiders buying within 30 days)
        cluster = (
            recent30[recent30["transaction_code"] == "P"]
            .groupby("ticker")["owner_name"].nunique()
            .rename("cluster_buyers")
            .reset_index()
        ) if "owner_name" in recent30.columns else (
            recent30[recent30["transaction_code"] == "P"]
            .groupby("ticker")["owner_title"].count()
            .rename("cluster_buyers")
            .reset_index()
        )
        cluster["ins_cluster"] = (cluster["cluster_buyers"] >= 3).astype(float)
        df = df.merge(cluster[["ticker", "ins_cluster"]], on="ticker", how="left")

    for col in ["ins_net_flow", "ins_exec_flow", "ins_cluster"]:
        if col not in df.columns:
            df[col] = np.nan
        df[f"rank_{col}"] = apply_sector_ranks(df, col)

    rank_cols = [c for c in df.columns if c.startswith("rank_")]
    df["insider_score"] = df[rank_cols].mean(axis=1).fillna(50.0)

    return df[["ticker", "sector", "insider_score"] + rank_cols]
