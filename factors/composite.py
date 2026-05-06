"""Composite score: weighted blend of all 8 factors, re-ranked within sector."""
from __future__ import annotations

import pandas as pd
import numpy as np
from .regime_weights import get_weights
from ._base import apply_sector_ranks


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

    # Long/short candidates: top/bottom 30 within each sector, capped to avoid overlap
    # in sectors with fewer than 60 names.
    df["rank_in_sector"] = df.groupby("sector")["composite_score"].rank(ascending=False, method="first")
    df["sector_size"] = df.groupby("sector")["sector"].transform("count")
    df["candidate_bucket_size"] = np.minimum(30, np.floor(df["sector_size"] / 2)).astype(int)

    df["is_long_candidate"] = (df["rank_in_sector"] <= df["candidate_bucket_size"]).astype(int)
    df["is_short_candidate"] = (
        df["rank_in_sector"] > df["sector_size"] - df["candidate_bucket_size"]
    ).astype(int)

    df["regime"] = regime
    df["vix"] = vix_val

    return df
