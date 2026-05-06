"""Correlation monitor: pairwise correlations, effective bets, diversification alerts."""
from __future__ import annotations

import logging
import os
import sqlite3

import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
logger = logging.getLogger(__name__)

AVG_CORR_THRESHOLD = 0.40
PAIR_CORR_THRESHOLD = 0.85
MIN_HISTORY_DAYS = 60


def _load_returns(tickers: list[str], days: int = 65) -> pd.DataFrame:
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


def _effective_bets(weights: dict[str, float]) -> float:
    """Compute effective number of bets: 1 / sum(w_i^2 / sum(w_l^2)).

    Higher = more diversified.
    """
    total_sq = sum(w**2 for w in weights.values())
    if total_sq == 0:
        return 0.0
    return 1.0 / total_sq * (sum(abs(w) for w in weights.values()) ** 2)


def check_correlations(
    returns_df: pd.DataFrame | None = None,
    weights_dict: dict[str, float] | None = None,
) -> dict:
    """Compute within-book correlations and effective bets for long and short books.

    Returns {
        "long_book": {"alerts": [...], "avg_corr": float, "effective_bets": float, "n_positions": int},
        "short_book": {...},
        "high_corr_pairs": [{"ticker_a", "ticker_b", "correlation"}],
    }
    """
    if not weights_dict:
        return _empty_result()

    long_weights = {t: w for t, w in weights_dict.items() if w > 0}
    short_weights = {t: abs(w) for t, w in weights_dict.items() if w < 0}

    all_tickers = list(weights_dict.keys())
    if returns_df is None or returns_df.empty:
        returns_df = _load_returns(all_tickers, days=MIN_HISTORY_DAYS + 5)

    high_corr_pairs = []

    long_result = _analyze_book("long", long_weights, returns_df, high_corr_pairs)
    short_result = _analyze_book("short", short_weights, returns_df, high_corr_pairs)

    return {
        "long_book": long_result,
        "short_book": short_result,
        "high_corr_pairs": high_corr_pairs,
    }


def _analyze_book(
    book_name: str,
    weights: dict[str, float],
    returns_df: pd.DataFrame,
    high_corr_pairs: list,
) -> dict:
    n = len(weights)
    if n < 2:
        return {
            "book": book_name,
            "avg_corr": 0.0,
            "effective_bets": float(n),
            "n_positions": n,
            "alerts": [],
        }

    tickers = list(weights.keys())
    available = [t for t in tickers if t in returns_df.columns]

    eff_bets = _effective_bets({t: weights[t] for t in available}) if available else float(n)

    if len(available) < 2:
        return {
            "book": book_name,
            "avg_corr": 0.0,
            "effective_bets": eff_bets,
            "n_positions": n,
            "alerts": [f"Insufficient price history for {book_name} correlation analysis"],
        }

    ret_sub = returns_df[available].tail(MIN_HISTORY_DAYS).dropna(how="all")
    # Drop tickers with fewer than 20 observations
    valid = [t for t in available if ret_sub[t].notna().sum() >= 20]
    if len(valid) < 2:
        return {
            "book": book_name,
            "avg_corr": 0.0,
            "effective_bets": eff_bets,
            "n_positions": n,
            "alerts": [f"Insufficient return history (<20 days) for {book_name} correlation"],
        }

    corr_matrix = ret_sub[valid].corr()
    alerts = []
    corr_vals = []

    for i, t_a in enumerate(valid):
        for j, t_b in enumerate(valid):
            if j <= i:
                continue
            corr = corr_matrix.loc[t_a, t_b]
            if pd.isna(corr):
                continue
            corr_vals.append(corr)
            if abs(corr) > PAIR_CORR_THRESHOLD:
                pair = {"ticker_a": t_a, "ticker_b": t_b, "correlation": round(float(corr), 3), "book": book_name}
                high_corr_pairs.append(pair)
                msg = f"{book_name.title()} book: {t_a}/{t_b} correlation = {corr:.2f} > {PAIR_CORR_THRESHOLD}"
                alerts.append({"level": "WARNING", "message": msg})
                logger.warning(msg)

    avg_corr = float(np.mean(corr_vals)) if corr_vals else 0.0
    if avg_corr > AVG_CORR_THRESHOLD:
        msg = f"{book_name.title()} book avg correlation {avg_corr:.2f} > {AVG_CORR_THRESHOLD} — reduced diversification"
        alerts.append({"level": "WARNING", "message": msg})
        logger.warning(msg)

    n_pos = len(valid)
    print(f"  {book_name.title()} book diversification: {eff_bets:.1f} effective bets / {n_pos} positions")

    return {
        "book": book_name,
        "avg_corr": round(avg_corr, 3),
        "effective_bets": round(eff_bets, 1),
        "n_positions": n_pos,
        "alerts": alerts,
    }


def _empty_result() -> dict:
    return {
        "long_book": {"book": "long", "avg_corr": 0.0, "effective_bets": 0.0, "n_positions": 0, "alerts": []},
        "short_book": {"book": "short", "avg_corr": 0.0, "effective_bets": 0.0, "n_positions": 0, "alerts": []},
        "high_corr_pairs": [],
    }
