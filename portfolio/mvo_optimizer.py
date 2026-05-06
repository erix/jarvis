"""Markowitz Mean-Variance Optimizer with sector, beta, and position constraints."""
from __future__ import annotations

import os
import sqlite3
import warnings as _warnings

import numpy as np
import pandas as pd
from scipy.optimize import minimize, OptimizeResult

from portfolio.beta import get_betas

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")

# Shrinkage factor for covariance matrix (Ledoit-Wolf style constant)
SHRINKAGE = 0.1


def _load_returns(tickers: list[str], days: int = 65) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(tickers))
    df = pd.read_sql_query(
        f"""
        SELECT ticker, date, adj_close FROM daily_prices
        WHERE ticker IN ({placeholders})
        ORDER BY ticker, date
        """,
        conn,
        params=tickers,
    )
    conn.close()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    pivot = df.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    return pivot.pct_change().dropna(how="all").tail(days)


def _load_latest_prices(tickers: list[str]) -> dict[str, float]:
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT ticker, adj_close FROM daily_prices dp
        WHERE ticker IN ({placeholders})
          AND date = (SELECT MAX(date) FROM daily_prices WHERE ticker=dp.ticker)
        """,
        tickers,
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows if r[1] and r[1] > 0}


def _load_adv(tickers: list[str], days: int = 20) -> dict[str, float]:
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT ticker, AVG(volume) as adv
        FROM (
            SELECT ticker, volume,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) as rn
            FROM daily_prices WHERE ticker IN ({placeholders})
        ) WHERE rn <= {days}
        GROUP BY ticker
        """,
        tickers,
    ).fetchall()
    conn.close()
    return {r[0]: r[1] or 0.0 for r in rows}


def _build_covariance(returns: pd.DataFrame, tickers: list[str]) -> np.ndarray:
    """Return shrunk sample covariance matrix (annualised)."""
    available = [t for t in tickers if t in returns.columns]
    if not available:
        n = len(tickers)
        return np.eye(n) * 0.04  # fallback: 20% vol assumed

    sub = returns[available].dropna()
    cov = sub.cov().values * 252  # annualise

    # Constant-correlation shrinkage: blend toward diagonal
    diag = np.diag(np.diag(cov))
    cov_shrunk = (1 - SHRINKAGE) * cov + SHRINKAGE * diag

    # If tickers have no data, pad with mean variance
    n_full = len(tickers)
    if len(available) < n_full:
        mean_var = np.mean(np.diag(cov_shrunk))
        full = np.eye(n_full) * mean_var
        idx_map = {t: i for i, t in enumerate(available)}
        for i, ti in enumerate(tickers):
            for j, tj in enumerate(tickers):
                if ti in idx_map and tj in idx_map:
                    full[i, j] = cov_shrunk[idx_map[ti], idx_map[tj]]
        return full

    return cov_shrunk


def _score_to_expected_return(score: float) -> float:
    """Map composite score [0,100] → annual expected return [-15%, +15%]."""
    return (score - 50) / 50 * 0.15


def mvo_optimize(
    scores_df: pd.DataFrame,
    cfg: dict,
    current_positions: list[dict] | None = None,
) -> dict:
    """Run constrained Markowitz MVO.

    Returns the same shape dict as conviction_tilt().
    """
    num_longs = cfg.get("num_longs", 20)
    num_shorts = cfg.get("num_shorts", 20)
    max_pos_pct = cfg.get("max_position_pct", 5.0) / 100.0
    max_sector_pct = cfg.get("max_sector_pct", 25.0) / 100.0
    gross_exp = cfg.get("gross_exposure", 165.0) / 100.0
    net_min = cfg.get("net_exposure_min", -10.0) / 100.0
    net_max = cfg.get("net_exposure_max", 15.0) / 100.0
    max_beta = cfg.get("max_portfolio_beta", 0.20)
    lam = cfg.get("mvo_risk_aversion", 1.0)
    aum = cfg.get("aum", 10_000_000)

    warnings_out: list[str] = []

    # Select candidate pool
    long_pool = (
        scores_df[scores_df["is_long_candidate"] == 1]
        .sort_values("composite_score", ascending=False)
        .head(num_longs * 2)
    )
    short_pool = (
        scores_df[scores_df["is_short_candidate"] == 1]
        .sort_values("composite_score", ascending=True)
        .head(num_shorts * 2)
    )

    candidates = pd.concat([long_pool, short_pool]).drop_duplicates("ticker")
    tickers = candidates["ticker"].tolist()
    n = len(tickers)

    if n == 0:
        return _empty_result("No candidates found")

    prices = _load_latest_prices(tickers)
    adv = _load_adv(tickers)
    sectors_map = dict(zip(candidates["ticker"], candidates.get("sector", pd.Series(dtype=str))))

    # Drop tickers without price data
    valid = [t for t in tickers if prices.get(t, 0) > 0]
    if len(valid) < 4:
        return _empty_result("Insufficient price data for MVO")

    tickers = valid
    n = len(tickers)
    ticker_idx = {t: i for i, t in enumerate(tickers)}

    # Expected returns vector
    mu = np.array([
        _score_to_expected_return(
            candidates.loc[candidates["ticker"] == t, "composite_score"].iloc[0]
            if not candidates.loc[candidates["ticker"] == t].empty
            else 50.0
        )
        for t in tickers
    ])

    # Covariance matrix
    returns_data = _load_returns(tickers, days=65)
    Sigma = _build_covariance(returns_data, tickers)

    # Betas
    betas_dict = get_betas(tickers, sectors_map)
    beta_vec = np.array([betas_dict.get(t, 1.0) for t in tickers])

    # Sector membership (for constraints)
    sectors_unique = list({sectors_map.get(t, "Unknown") for t in tickers})
    sector_matrix = np.array([
        [1.0 if sectors_map.get(tickers[i], "Unknown") == s else 0.0 for i in range(n)]
        for s in sectors_unique
    ])

    # Objective: maximize mu^T w - lambda * w^T Sigma w
    def neg_utility(w: np.ndarray) -> float:
        return -(mu @ w - lam * w @ Sigma @ w)

    def grad(w: np.ndarray) -> np.ndarray:
        return -(mu - 2 * lam * Sigma @ w)

    # Initial guess: equal-weight long/short
    n_long = min(num_longs, n // 2)
    n_short = min(num_shorts, n - n_long)
    w0 = np.zeros(n)
    long_base = gross_exp * 0.55 / max(n_long, 1)
    short_base = gross_exp * 0.45 / max(n_short, 1)
    for i in range(n_long):
        w0[i] = long_base
    for i in range(n_short):
        w0[n_long + i] = -short_base

    # Bounds: [-max_pos, +max_pos] per position
    bounds = [(-max_pos_pct, max_pos_pct)] * n

    # Split bounds into long (>=0) and short (<=0) halves for gross constraint.
    # Use auxiliary variable approach: enforce gross via bounding sum of positive
    # and negative weights separately. SLSQP can't differentiate abs(w), so we
    # use two one-sided inequalities around the target gross exposure band.
    gross_tol = 0.05  # allow ±5% of target gross
    constraints = [
        # Gross exposure in [target-tol, target+tol]
        {
            "type": "ineq",
            "fun": lambda w: np.sum(np.abs(w)) - (gross_exp - gross_tol),
        },
        {
            "type": "ineq",
            "fun": lambda w: (gross_exp + gross_tol) - np.sum(np.abs(w)),
        },
        # Net exposure bounds
        {
            "type": "ineq",
            "fun": lambda w: np.sum(w) - net_min,
        },
        {
            "type": "ineq",
            "fun": lambda w: net_max - np.sum(w),
        },
        # Beta constraint (split into two to avoid abs())
        {
            "type": "ineq",
            "fun": lambda w: max_beta - (beta_vec @ w),
        },
        {
            "type": "ineq",
            "fun": lambda w: max_beta + (beta_vec @ w),
        },
    ]

    # Sector constraints: net sector exposure within [-max, +max]
    for row in sector_matrix:
        _row = row.copy()
        constraints.append({
            "type": "ineq",
            "fun": lambda w, r=_row: max_sector_pct - (r @ w),
        })
        constraints.append({
            "type": "ineq",
            "fun": lambda w, r=_row: max_sector_pct + (r @ w),
        })

    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        result: OptimizeResult = minimize(
            neg_utility,
            w0,
            jac=grad,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 2000, "ftol": 1e-8},
        )

    if not result.success:
        warnings_out.append(f"MVO solver: {result.message} — falling back to initial weights")
        w_opt = w0
    else:
        w_opt = result.x

    # Round near-zero weights
    w_opt[np.abs(w_opt) < 1e-4] = 0.0

    target_weights = {tickers[i]: float(w_opt[i]) for i in range(n) if w_opt[i] != 0}

    long_tickers = sorted([t for t, w in target_weights.items() if w > 0], key=lambda t: -target_weights[t])
    short_tickers = sorted([t for t, w in target_weights.items() if w < 0], key=lambda t: target_weights[t])

    expected_return = float(mu @ w_opt)
    expected_vol = float(np.sqrt(w_opt @ Sigma @ w_opt))
    port_beta = float(beta_vec @ w_opt)

    sector_exp: dict[str, float] = {}
    for t, w in target_weights.items():
        sec = sectors_map.get(t, "Unknown")
        sector_exp[sec] = sector_exp.get(sec, 0.0) + w
    sector_neutrality_score = float(np.std(list(sector_exp.values()))) if sector_exp else 0.0

    return {
        "target_weights": target_weights,
        "expected_return": expected_return,
        "expected_volatility": expected_vol,
        "sector_neutrality_score": sector_neutrality_score,
        "long_tickers": long_tickers,
        "short_tickers": short_tickers,
        "warnings": warnings_out,
        "betas": betas_dict,
        "portfolio_beta": port_beta,
        "prices": prices,
        "adv": adv,
    }


def _empty_result(reason: str) -> dict:
    return {
        "target_weights": {},
        "expected_return": 0.0,
        "expected_volatility": 0.0,
        "sector_neutrality_score": 0.0,
        "long_tickers": [],
        "short_tickers": [],
        "warnings": [reason],
        "betas": {},
        "portfolio_beta": 0.0,
        "prices": {},
        "adv": {},
    }
