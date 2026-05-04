"""Crowding detection: pairwise factor return correlations, flags crowded pairs."""
import json
import os
import pandas as pd
import numpy as np
from ._base import get_db

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "crowding_alerts.json")

FACTOR_COLS = [
    "momentum_score", "value_score", "quality_score", "growth_score",
    "revisions_score", "short_interest_score", "insider_score", "institutional_score",
]

CORR_THRESHOLD = 0.7


def detect_crowding(scores_df: pd.DataFrame, lookback_days: int = 90) -> list:
    """
    Detect crowded factor pairs by computing pairwise correlations between
    factor scores (proxy for factor returns) across all tickers.
    Flags pairs where correlation > 0.7.
    Returns list of alert dicts; also writes to output/crowding_alerts.json.
    """
    available = [c for c in FACTOR_COLS if c in scores_df.columns]
    if len(available) < 2:
        return []

    factor_data = scores_df[available].dropna(how="all")
    if len(factor_data) < 5:
        return []

    corr_matrix = factor_data[available].corr()

    alerts = []
    seen = set()
    for i, f1 in enumerate(available):
        for j, f2 in enumerate(available):
            if j <= i:
                continue
            pair = tuple(sorted([f1, f2]))
            if pair in seen:
                continue
            seen.add(pair)
            corr_val = corr_matrix.loc[f1, f2]
            if pd.notna(corr_val) and abs(corr_val) > CORR_THRESHOLD:
                alerts.append({
                    "factor_1": f1,
                    "factor_2": f2,
                    "correlation": round(float(corr_val), 4),
                    "threshold": CORR_THRESHOLD,
                    "crowded": True,
                })

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump({"alerts": alerts, "total": len(alerts)}, f, indent=2)

    return alerts
