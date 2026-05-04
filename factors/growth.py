"""Factor 4: Growth — 5 sub-factors, sector-relative percentile rank."""
import pandas as pd
import numpy as np
from ._base import get_db, apply_sector_ranks


def calculate_all(universe: pd.DataFrame) -> pd.DataFrame:
    conn = get_db()

    fund_all = pd.read_sql_query(
        """
        SELECT ticker, report_date, revenue_growth_yoy, earnings_growth_yoy,
               free_cash_flow, revenue, rd_expense
        FROM fundamentals
        ORDER BY ticker, report_date
        """,
        conn,
    )
    conn.close()

    fund_all = fund_all.drop_duplicates(subset=["ticker", "report_date"])
    latest = fund_all.groupby("ticker").last().reset_index()

    df = universe.copy()
    df = df.merge(latest[["ticker", "revenue_growth_yoy", "earnings_growth_yoy",
                           "free_cash_flow", "revenue", "rd_expense"]],
                  on="ticker", how="left")

    # Sub-factor 1: Revenue growth YoY
    df["rev_growth"] = df["revenue_growth_yoy"]

    # Sub-factor 2: Earnings growth YoY
    df["earn_growth"] = df["earnings_growth_yoy"]

    # Sub-factor 3: Revenue growth acceleration (latest YoY minus 4Q-ago YoY)
    accel_rows = []
    for tkr, grp in fund_all.groupby("ticker"):
        grp = grp.sort_values("report_date")
        ry = grp["revenue_growth_yoy"].dropna()
        if len(ry) >= 5:
            accel = float(ry.iloc[-1]) - float(ry.iloc[-5])
        elif len(ry) >= 2:
            accel = float(ry.iloc[-1]) - float(ry.iloc[0])
        else:
            accel = np.nan
        accel_rows.append({"ticker": tkr, "rev_accel": accel})
    df = df.merge(pd.DataFrame(accel_rows), on="ticker", how="left")

    # Sub-factor 4: R&D intensity (R&D / Revenue)
    df["rd_intensity"] = np.where(
        df["revenue"].notna() & (df["revenue"] > 0) & df["rd_expense"].notna(),
        df["rd_expense"] / df["revenue"],
        np.nan
    )

    # Sub-factor 5: FCF growth YoY
    fcf_growth_rows = []
    for tkr, grp in fund_all.groupby("ticker"):
        grp = grp.sort_values("report_date")
        recent_fcf = grp["free_cash_flow"].dropna()
        if len(recent_fcf) >= 5:
            old = recent_fcf.iloc[-5]
            new = recent_fcf.iloc[-1]
            fcf_g = (new / abs(old) - 1) if old != 0 else np.nan
        else:
            fcf_g = np.nan
        fcf_growth_rows.append({"ticker": tkr, "fcf_growth": fcf_g})
    df = df.merge(pd.DataFrame(fcf_growth_rows), on="ticker", how="left")

    for col in ["rev_growth", "earn_growth", "rev_accel", "rd_intensity", "fcf_growth"]:
        df[f"rank_{col}"] = apply_sector_ranks(df, col)

    rank_cols = [c for c in df.columns if c.startswith("rank_")]
    df["growth_score"] = df[rank_cols].mean(axis=1)

    return df[["ticker", "sector", "growth_score"] + rank_cols]
