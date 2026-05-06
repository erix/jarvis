"""Factor 2: Value — 6 sub-factors, sector-relative percentile rank."""
import pandas as pd
import numpy as np
from ._base import get_db, apply_sector_ranks, table_exists


def calculate_all(universe: pd.DataFrame) -> pd.DataFrame:
    """
    Returns DataFrame with ticker + value_score (0-100).
    universe: DataFrame with columns [ticker, sector]
    """
    conn = get_db()

    if table_exists(conn, "fundamentals"):
        fund_raw = pd.read_sql_query(
            """
            SELECT f.ticker, f.pe_ratio, f.fcf_yield, f.ev_ebitda,
                   f.shareholder_yield, f.market_cap, f.total_equity,
                   f.shares_outstanding, f.revenue, f.total_debt, f.cash,
                   f.free_cash_flow, f.buyback_yield, f.dividend_yield
            FROM fundamentals f
            INNER JOIN (
                SELECT ticker, MAX(report_date) AS max_date
                FROM fundamentals GROUP BY ticker
            ) latest ON f.ticker = latest.ticker AND f.report_date = latest.max_date
            """,
            conn,
        )
    else:
        fund_raw = pd.DataFrame(columns=[
            "ticker", "pe_ratio", "fcf_yield", "ev_ebitda", "shareholder_yield",
            "market_cap", "total_equity", "shares_outstanding", "revenue",
            "total_debt", "cash", "free_cash_flow", "buyback_yield",
            "dividend_yield",
        ])
    fund = fund_raw.drop_duplicates("ticker")

    latest_prices = pd.read_sql_query(
        """
        SELECT ticker, adj_close
        FROM daily_prices
        WHERE (ticker, date) IN (
            SELECT ticker, MAX(date) FROM daily_prices GROUP BY ticker
        )
        """,
        conn,
    )
    conn.close()

    df = universe.copy()
    rank_cols = [
        "rank_earnings_yield", "rank_book_to_price", "rank_fcf_yield_v",
        "rank_ev_ebitda_inv", "rank_sh_yield", "rank_sales_to_ev",
    ]
    if fund.empty:
        for col in rank_cols:
            df[col] = 50.0
        df["value_score"] = 50.0
        return df[["ticker", "sector", "value_score"] + rank_cols]

    df = df.merge(fund, on="ticker", how="left")
    df = df.merge(latest_prices, on="ticker", how="left")

    # Sub-factor 1: earnings yield = 1/PE
    df["earnings_yield"] = np.where(
        df["pe_ratio"].notna() & (df["pe_ratio"] > 0),
        1.0 / df["pe_ratio"],
        np.nan
    )

    # Sub-factor 2: Book-to-price
    df["book_to_price"] = np.where(
        df["adj_close"].notna() & (df["adj_close"] > 0) &
        df["total_equity"].notna() & df["shares_outstanding"].notna() & (df["shares_outstanding"] > 0),
        df["total_equity"] / df["shares_outstanding"] / df["adj_close"],
        np.nan
    )

    # Sub-factor 3: FCF yield
    df["fcf_yield_v"] = df["fcf_yield"]

    # Sub-factor 4: EV/EBITDA inverted
    df["ev_ebitda_inv"] = np.where(
        df["ev_ebitda"].notna() & (df["ev_ebitda"] > 0),
        1.0 / df["ev_ebitda"],
        np.nan
    )

    # Sub-factor 5: Shareholder yield
    df["sh_yield"] = df["shareholder_yield"].fillna(
        df["buyback_yield"].fillna(0) + df["dividend_yield"].fillna(0)
    )

    # Sub-factor 6: Sales-to-EV
    df["ev"] = df["market_cap"].fillna(0) + df["total_debt"].fillna(0) - df["cash"].fillna(0)
    df["sales_to_ev"] = np.where(
        (df["ev"] > 0) & df["revenue"].notna(),
        df["revenue"] / df["ev"],
        np.nan
    )

    for col in ["earnings_yield", "book_to_price", "fcf_yield_v", "ev_ebitda_inv", "sh_yield", "sales_to_ev"]:
        df[f"rank_{col}"] = apply_sector_ranks(df, col)

    df["value_score"] = df[rank_cols].mean(axis=1)

    return df[["ticker", "sector", "value_score"] + rank_cols]
