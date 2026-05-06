"""Factor 3: Quality — 8 sub-factors, sector-relative percentile rank."""
import pandas as pd
import numpy as np
from ._base import get_db, apply_sector_ranks, table_exists


def calculate_all(universe: pd.DataFrame) -> pd.DataFrame:
    conn = get_db()

    # All quarterly fundamentals for time-series calculations
    if table_exists(conn, "fundamentals"):
        fund_all = pd.read_sql_query(
            """
            SELECT ticker, report_date, roe, gross_margin, debt_equity,
                   cfo_ni, accruals_ratio, piotroski_f_score, altman_z_score,
                   net_income, operating_cash_flow, total_assets
            FROM fundamentals
            ORDER BY ticker, report_date
            """,
            conn,
        )
    else:
        fund_all = pd.DataFrame(columns=[
            "ticker", "report_date", "roe", "gross_margin", "debt_equity",
            "cfo_ni", "accruals_ratio", "piotroski_f_score", "altman_z_score",
            "net_income", "operating_cash_flow", "total_assets",
        ])

    fund_all = fund_all.drop_duplicates(subset=["ticker", "report_date"])
    latest = fund_all.groupby("ticker").last().reset_index()

    conn.close()

    df = universe.copy()
    rank_cols = [
        "rank_roe_stability", "rank_gm_level", "rank_gm_trend", "rank_debt_eq_inv",
        "rank_cfo_ni_score", "rank_accruals_inv", "rank_piotroski_norm",
        "rank_altman_score",
    ]
    if fund_all.empty:
        for col in rank_cols:
            df[col] = 50.0
        df["quality_score"] = 50.0
        return df[["ticker", "sector", "quality_score"] + rank_cols]

    latest_renamed = latest[["ticker", "gross_margin", "debt_equity", "cfo_ni",
                             "accruals_ratio", "piotroski_f_score", "altman_z_score"]].copy()
    latest_renamed = latest_renamed.rename(columns={"gross_margin": "gm_latest"})
    df = df.merge(latest_renamed, on="ticker", how="left")

    # Sub-factor 1: ROE stability (std dev inverted — lower vol = better)
    roe_std = (
        fund_all.groupby("ticker")["roe"]
        .std()
        .rename("roe_std")
        .reset_index()
    )
    df = df.merge(roe_std, on="ticker", how="left")
    df["roe_stability"] = np.where(df["roe_std"].notna() & (df["roe_std"] >= 0),
                                   -df["roe_std"], np.nan)

    # Sub-factor 2: Gross margin level
    df["gm_level"] = df["gm_latest"]

    # Sub-factor 3: Gross margin trend (latest minus 4Q ago)
    gm_trend = []
    for tkr, grp in fund_all.groupby("ticker"):
        grp = grp.sort_values("report_date")
        if len(grp) >= 5:
            trend = grp["gross_margin"].iloc[-1] - grp["gross_margin"].iloc[-5]
        elif len(grp) >= 2:
            trend = grp["gross_margin"].iloc[-1] - grp["gross_margin"].iloc[0]
        else:
            trend = np.nan
        gm_trend.append({"ticker": tkr, "gm_trend": trend})
    df = df.merge(pd.DataFrame(gm_trend), on="ticker", how="left")

    # Sub-factor 4: Debt/equity inverted
    df["debt_eq_inv"] = np.where(
        df["debt_equity"].notna() & (df["debt_equity"] >= 0),
        -df["debt_equity"],
        np.nan
    )

    # Sub-factor 5: CFO/NI (higher = better earnings quality)
    df["cfo_ni_score"] = df["cfo_ni"]

    # Sub-factor 6: Accruals ratio inverted (high accruals = bad)
    df["accruals_inv"] = np.where(
        df["accruals_ratio"].notna(),
        -df["accruals_ratio"],
        np.nan
    )

    # Sub-factor 7: Piotroski F-score normalized 0-9 → 0-100
    df["piotroski_norm"] = np.where(
        df["piotroski_f_score"].notna(),
        df["piotroski_f_score"] / 9.0 * 100,
        np.nan
    )

    # Sub-factor 8: Altman Z-score → score proxy (higher Z = healthier)
    df["altman_score"] = df["altman_z_score"]

    for col in ["roe_stability", "gm_level", "gm_trend", "debt_eq_inv",
                "cfo_ni_score", "accruals_inv", "piotroski_norm", "altman_score"]:
        df[f"rank_{col}"] = apply_sector_ranks(df, col)

    df["quality_score"] = df[rank_cols].mean(axis=1)

    return df[["ticker", "sector", "quality_score"] + rank_cols]
