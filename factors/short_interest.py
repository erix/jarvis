"""Factor 6: Short Interest — 3 sub-factors. For LONGS: declining SI = better."""
import pandas as pd
import numpy as np
from ._base import get_db, apply_sector_ranks, table_exists


def calculate_all(universe: pd.DataFrame) -> pd.DataFrame:
    conn = get_db()
    if table_exists(conn, "short_interest"):
        si = pd.read_sql_query(
            "SELECT ticker, date, short_pct_float, days_to_cover FROM short_interest ORDER BY ticker, date",
            conn,
        )
    else:
        si = pd.DataFrame(columns=["ticker", "date", "short_pct_float", "days_to_cover"])
    conn.close()

    df = universe.copy()

    if si.empty:
        df["si_pct_float"] = np.nan
        df["si_days_cover"] = np.nan
        df["si_change"] = np.nan
    else:
        # Latest record per ticker
        latest_si = si.groupby("ticker").last().reset_index()
        df = df.merge(latest_si[["ticker", "short_pct_float", "days_to_cover"]],
                      on="ticker", how="left")
        df.rename(columns={"short_pct_float": "si_pct_float",
                            "days_to_cover": "si_days_cover"}, inplace=True)

        # Change in short interest vs prior period
        change_rows = []
        for tkr, grp in si.groupby("ticker"):
            grp = grp.sort_values("date")
            if len(grp) >= 2:
                change = grp["short_pct_float"].iloc[-1] - grp["short_pct_float"].iloc[-2]
            else:
                change = np.nan
            change_rows.append({"ticker": tkr, "si_change": change})
        df = df.merge(pd.DataFrame(change_rows), on="ticker", how="left")

    # For long-biased scoring: declining short interest = higher score
    # So invert both pct_float and days_to_cover (lower SI = more bullish)
    df["si_pct_inv"] = np.where(df["si_pct_float"].notna(), -df["si_pct_float"], np.nan)
    df["si_dtc_inv"] = np.where(df["si_days_cover"].notna(), -df["si_days_cover"], np.nan)
    # Declining change = declining SI = bullish
    df["si_change_inv"] = np.where(df["si_change"].notna(), -df["si_change"], np.nan)

    for col in ["si_pct_inv", "si_dtc_inv", "si_change_inv"]:
        df[f"rank_{col}"] = apply_sector_ranks(df, col)

    rank_cols = [c for c in df.columns if c.startswith("rank_")]
    df["short_interest_score"] = df[rank_cols].mean(axis=1).fillna(50.0)

    return df[["ticker", "sector", "short_interest_score"] + rank_cols]
