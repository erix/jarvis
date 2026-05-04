"""Factor spread monitor: Z-score portfolio factor exposures vs history."""
import logging
import os
import sqlite3

import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
logger = logging.getLogger(__name__)

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

Z_SCORE_THRESHOLD = 1.5


def _load_historical_factor_exposures(days: int = 90) -> pd.DataFrame:
    """Load historical weighted portfolio factor exposures from scores table."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        f"SELECT date, {', '.join(FACTOR_COLS)} FROM scores ORDER BY date DESC LIMIT {days * 600}",
        conn,
    )
    conn.close()
    return df


def _compute_portfolio_factor_exposure(
    weights_dict: dict[str, float],
    scores_df: pd.DataFrame,
) -> dict[str, float]:
    """Compute weighted average factor exposure for current portfolio."""
    if not weights_dict or scores_df.empty:
        return {}

    if "ticker" in scores_df.columns:
        scores_df = scores_df.set_index("ticker")

    total_weight = sum(abs(w) for w in weights_dict.values())
    if total_weight == 0:
        return {}

    exposures = {}
    for factor in FACTOR_COLS:
        if factor not in scores_df.columns:
            continue
        weighted = sum(
            weights_dict[t] * scores_df.loc[t, factor]
            for t in weights_dict
            if t in scores_df.index and not pd.isna(scores_df.loc[t, factor])
        )
        exposures[factor] = weighted / total_weight

    return exposures


def check_factor_spread(
    portfolio_factor_exposures: dict[str, float] | None = None,
    historical_exposures_df: pd.DataFrame | None = None,
    weights_dict: dict[str, float] | None = None,
    scores_df: pd.DataFrame | None = None,
) -> list[dict]:
    """Check for overconcentrated factor exposures (|Z| > 1.5).

    Can be called with pre-computed exposures or with weights+scores to compute them.
    Cross-references crowding by checking vs historical distribution.
    Returns list of alert dicts.
    """
    # Resolve factor exposures
    if portfolio_factor_exposures is None:
        if weights_dict and scores_df is not None:
            portfolio_factor_exposures = _compute_portfolio_factor_exposure(weights_dict, scores_df)
        else:
            return [{"level": "WARNING", "message": "No factor exposures provided"}]

    if not portfolio_factor_exposures:
        return []

    # Load historical exposures if not provided
    if historical_exposures_df is None or historical_exposures_df.empty:
        historical_exposures_df = _load_historical_factor_exposures()

    alerts = []

    for factor, current_exposure in portfolio_factor_exposures.items():
        if factor not in historical_exposures_df.columns:
            continue

        hist_vals = historical_exposures_df[factor].dropna()
        if len(hist_vals) < 10:
            continue

        hist_mean = float(hist_vals.mean())
        hist_std = float(hist_vals.std())
        if hist_std == 0:
            continue

        z = (current_exposure - hist_mean) / hist_std

        label = factor.replace("_score", "").replace("_", " ").title()
        if abs(z) > Z_SCORE_THRESHOLD:
            direction = "overweight" if z > 0 else "underweight"
            alerts.append({
                "factor": factor,
                "label": label,
                "z_score": round(z, 2),
                "current_exposure": round(current_exposure, 2),
                "hist_mean": round(hist_mean, 2),
                "hist_std": round(hist_std, 2),
                "direction": direction,
                "level": "WARNING" if abs(z) < 2.5 else "CRITICAL",
                "message": f"{label} {direction}: Z={z:.2f} (threshold ±{Z_SCORE_THRESHOLD})",
            })
            logger.warning("Factor overconcentration: %s Z=%.2f", label, z)

    return alerts


def get_portfolio_factor_exposures(
    weights_dict: dict[str, float],
    scores_df: pd.DataFrame | None = None,
) -> dict[str, float]:
    """Public helper to compute current portfolio factor exposures."""
    if scores_df is None:
        conn = sqlite3.connect(DB_PATH)
        tickers = list(weights_dict.keys())
        placeholders = ",".join("?" * len(tickers))
        scores_df = pd.read_sql_query(
            f"SELECT ticker, {', '.join(FACTOR_COLS)} FROM scores "
            f"WHERE ticker IN ({placeholders}) ORDER BY date DESC",
            conn, params=tickers,
        )
        conn.close()
        if not scores_df.empty:
            scores_df = scores_df.drop_duplicates("ticker")

    return _compute_portfolio_factor_exposure(weights_dict, scores_df)
