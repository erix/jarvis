"""Composite score: weighted blend of all 8 factors, re-ranked within sector."""
from __future__ import annotations

import pandas as pd
import numpy as np
from .regime_weights import get_weights
from ._base import apply_sector_ranks

CANDIDATE_LONG_RAW_THRESHOLD = 62.0
CANDIDATE_SHORT_RAW_THRESHOLD = 38.0
CANDIDATE_SECTOR_FRACTION = 0.20
CANDIDATE_MAX_PER_SECTOR_SIDE = 20


def calculate_composite(scores: pd.DataFrame, vix: float | None = None) -> pd.DataFrame:
    """
    scores: DataFrame with columns [ticker, sector, momentum_score, value_score,
            quality_score, growth_score, revisions_score, short_interest_score,
            insider_score, institutional_score]
    Returns scores DataFrame with composite_score and candidate flags added.
    """
    weights, regime, vix_val = get_weights(vix)

    factor_cols = {
        "momentum": "momentum_score",
        "value": "value_score",
        "quality": "quality_score",
        "growth": "growth_score",
        "revisions": "revisions_score",
        "short_interest": "short_interest_score",
        "insider": "insider_score",
        "institutional": "institutional_score",
    }

    df = scores.copy()

    # Fill missing factor scores with sector median (50)
    for col in factor_cols.values():
        if col in df.columns:
            df[col] = df[col].fillna(50.0)
        else:
            df[col] = 50.0

    # Weighted raw composite
    df["composite_raw"] = sum(
        df[factor_cols[f]] * w for f, w in weights.items()
    )

    # Re-rank within sector
    df["composite_score"] = apply_sector_ranks(df, "composite_raw")

    # Long/short candidates use absolute raw-score conviction, then a sector cap.
    # Using the sector percentile score here would force symmetric top/bottom
    # buckets in every sector, even when one tail has much weaker signals.
    df["rank_in_sector"] = df.groupby("sector")["composite_raw"].rank(ascending=False, method="first")
    df["rank_in_sector_asc"] = df.groupby("sector")["composite_raw"].rank(ascending=True, method="first")
    df["sector_size"] = df.groupby("sector")["sector"].transform("count")
    df["candidate_bucket_size"] = np.minimum(
        CANDIDATE_MAX_PER_SECTOR_SIDE,
        np.ceil(df["sector_size"] * CANDIDATE_SECTOR_FRACTION),
    ).astype(int)

    df["is_long_candidate"] = (
        (df["composite_raw"] >= CANDIDATE_LONG_RAW_THRESHOLD) &
        (df["rank_in_sector"] <= df["candidate_bucket_size"])
    ).astype(int)
    df["is_short_candidate"] = (
        (df["composite_raw"] <= CANDIDATE_SHORT_RAW_THRESHOLD) &
        (df["rank_in_sector_asc"] <= df["candidate_bucket_size"])
    ).astype(int)

    df["regime"] = regime
    df["vix"] = vix_val

    return df
