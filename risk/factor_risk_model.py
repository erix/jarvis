"""Barra-style factor risk model with MCTR decomposition.

Each day: r_i = alpha + sum_k(beta_k * F_k_i) + epsilon_i
Factor exposures are the 8 standardized factor scores from the scores table.
"""
import os
import sqlite3

import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")

FACTOR_COLS = [
    "momentum_score",
    "value_score",
    "quality_score",
    "growth_score",
    "revisions_score",
    "short_interest_score",
    "insider_score",
    "institutional_score",
]


def _load_returns(tickers: list[str], days: int = 65) -> pd.DataFrame:
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
    pivot = pivot.tail(days + 5)
    return pivot.pct_change().dropna(how="all")


def _load_scores(tickers: list[str]) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(tickers))
    df = pd.read_sql_query(
        f"""SELECT ticker, {', '.join(FACTOR_COLS)}
            FROM scores
            WHERE ticker IN ({placeholders})
            ORDER BY date DESC""",
        conn, params=tickers,
    )
    conn.close()
    if df.empty:
        return pd.DataFrame()
    # Keep most recent scores per ticker
    return df.drop_duplicates("ticker").set_index("ticker")


def decompose_portfolio(
    weights_dict: dict[str, float],
    scores_df: pd.DataFrame | None = None,
    returns_df: pd.DataFrame | None = None,
) -> dict:
    """Compute Barra-style factor vs specific risk decomposition and MCTR.

    weights_dict: {ticker: weight}  (weights should sum to ~1 for longs, can be negative)
    Returns risk_decomposition dict with factor_pct, specific_pct, mctr, alerts.
    """
    if not weights_dict:
        return _empty_decomposition()

    tickers = list(weights_dict.keys())

    if scores_df is None or scores_df.empty:
        scores_df = _load_scores(tickers)
    elif "ticker" in scores_df.columns:
        scores_df = scores_df.drop_duplicates("ticker").set_index("ticker")

    if returns_df is None or returns_df.empty:
        returns_df = _load_returns(tickers, days=65)

    # Filter to tickers that have both scores and returns
    available = [t for t in tickers if t in scores_df.index]
    if not available:
        return _empty_decomposition()

    weights = np.array([weights_dict[t] for t in available])

    # Build factor exposure matrix X (n_stocks x n_factors)
    X_raw = scores_df.loc[available, [c for c in FACTOR_COLS if c in scores_df.columns]].values.astype(float)
    # Z-score across stocks (standardize exposures cross-sectionally)
    col_means = np.nanmean(X_raw, axis=0)
    col_stds = np.nanstd(X_raw, axis=0)
    col_stds[col_stds == 0] = 1.0
    X = (X_raw - col_means) / col_stds
    X = np.nan_to_num(X, nan=0.0)

    # Factor covariance matrix from 60-day return-based regression residuals
    # Use OLS cross-sectional regression each day to get factor return time series
    factor_cov, specific_vars = _estimate_factor_cov(returns_df, available, X, days=60)

    # Factor variance contribution
    w = weights
    factor_var = float(w @ X @ factor_cov @ X.T @ w)
    specific_var = float(np.sum((w * specific_vars) ** 2))
    total_var = factor_var + specific_var

    if total_var <= 0:
        return _empty_decomposition()

    factor_pct = factor_var / total_var * 100
    specific_pct = specific_var / total_var * 100
    portfolio_vol = np.sqrt(total_var)

    # MCTR: marginal contribution to risk = w_i * cov(r_i, r_p) / vol_p
    # cov(r_i, r_p) via factor model: X_i @ F_cov @ X^T @ w + specific_var_i * w_i
    stock_cov_with_portfolio = X @ factor_cov @ X.T @ w + specific_vars**2 * w
    mctr = w * stock_cov_with_portfolio / portfolio_vol if portfolio_vol > 0 else np.zeros_like(w)
    mctr_pct = mctr / portfolio_vol * 100 if portfolio_vol > 0 else np.zeros_like(w)

    mctr_dict = {t: float(mctr[i]) for i, t in enumerate(available)}
    mctr_pct_dict = {t: float(mctr_pct[i]) for i, t in enumerate(available)}
    weight_pct_dict = {t: float(abs(w[i]) / (np.sum(np.abs(w)) + 1e-9) * 100) for i, t in enumerate(available)}

    # Flag disproportionate risk contributors: MCTR_pct > 1.5 * weight_pct
    alerts = []
    for t in available:
        if weight_pct_dict[t] > 0 and mctr_pct_dict[t] > 1.5 * weight_pct_dict[t]:
            alerts.append({
                "ticker": t,
                "mctr_pct": mctr_pct_dict[t],
                "weight_pct": weight_pct_dict[t],
                "ratio": mctr_pct_dict[t] / weight_pct_dict[t],
            })

    max_mctr_ticker = max(mctr_dict, key=lambda t: abs(mctr_dict[t])) if mctr_dict else None

    return {
        "factor_pct": round(factor_pct, 2),
        "specific_pct": round(specific_pct, 2),
        "portfolio_vol_annualized": round(portfolio_vol * np.sqrt(252) * 100, 2),
        "mctr": mctr_dict,
        "mctr_pct": mctr_pct_dict,
        "weight_pct": weight_pct_dict,
        "max_mctr_ticker": max_mctr_ticker,
        "alerts": alerts,
        "num_factors": X.shape[1],
        "num_stocks": len(available),
    }


def _estimate_factor_cov(
    returns_df: pd.DataFrame,
    tickers: list[str],
    X: np.ndarray,
    days: int = 60,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate factor covariance matrix and specific variances via OLS."""
    n_factors = X.shape[1]

    available_in_returns = [t for t in tickers if t in returns_df.columns]
    if len(available_in_returns) < n_factors + 1:
        # Fall back to diagonal identity if insufficient data
        return np.eye(n_factors) * 1e-4, np.ones(len(tickers)) * 0.02

    ret_tail = returns_df[available_in_returns].tail(days).dropna(how="all")
    if len(ret_tail) < 20:
        return np.eye(n_factors) * 1e-4, np.ones(len(tickers)) * 0.02

    # Map X rows to available_in_returns order
    ticker_to_idx = {t: i for i, t in enumerate(tickers)}
    ret_idx = [ticker_to_idx[t] for t in available_in_returns if t in ticker_to_idx]
    X_sub = X[ret_idx]

    # Cross-sectional OLS each day: r = X @ f + e
    factor_returns = []
    residuals_by_ticker = {t: [] for t in available_in_returns}

    for _, row in ret_tail.iterrows():
        r = row[available_in_returns].values.astype(float)
        mask = ~np.isnan(r)
        if mask.sum() < n_factors + 1:
            continue
        X_day = X_sub[mask]
        r_day = r[mask]
        try:
            f, _, _, _ = np.linalg.lstsq(X_day, r_day, rcond=None)
        except np.linalg.LinAlgError:
            continue
        e = r_day - X_day @ f
        factor_returns.append(f)
        for j, t in enumerate([available_in_returns[k] for k in range(len(available_in_returns)) if mask[k]]):
            residuals_by_ticker[t].append(e[list(np.where(mask)[0]).index(
                [k for k in range(len(available_in_returns)) if available_in_returns[k] == t][0]
            )] if t in [available_in_returns[k] for k in range(len(available_in_returns)) if mask[k]] else 0.0)

    if len(factor_returns) < 10:
        return np.eye(n_factors) * 1e-4, np.ones(len(tickers)) * 0.02

    F = np.array(factor_returns)
    factor_cov = np.cov(F.T) if F.shape[1] > 1 else np.array([[np.var(F[:, 0])]])

    # Specific variances per ticker
    specific_vars = np.zeros(len(tickers))
    for i, t in enumerate(tickers):
        if t in available_in_returns and residuals_by_ticker[t]:
            specific_vars[i] = np.std(residuals_by_ticker[t])
        else:
            specific_vars[i] = 0.02

    return factor_cov, specific_vars


def _empty_decomposition() -> dict:
    return {
        "factor_pct": 0.0,
        "specific_pct": 100.0,
        "portfolio_vol_annualized": 0.0,
        "mctr": {},
        "mctr_pct": {},
        "weight_pct": {},
        "max_mctr_ticker": None,
        "alerts": [],
        "num_factors": 0,
        "num_stocks": 0,
    }
