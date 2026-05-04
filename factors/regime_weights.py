"""VIX-based regime detection and composite weight adjustment."""
import pandas as pd
import numpy as np
from ._base import get_db

BASE_WEIGHTS = {
    "momentum": 0.20,
    "quality": 0.15,
    "value": 0.15,
    "revisions": 0.15,
    "insider": 0.10,
    "growth": 0.10,
    "short_interest": 0.10,
    "institutional": 0.05,
}

LOW_VIX_ADJ = {"momentum": +0.05, "growth": +0.05, "value": -0.05, "short_interest": -0.05}
HIGH_VIX_ADJ = {"quality": +0.05, "value": +0.05, "momentum": -0.05, "growth": -0.05}


def get_current_vix() -> float:
    conn = get_db()
    row = conn.execute(
        "SELECT adj_close FROM daily_prices WHERE ticker='^VIX' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return float(row["adj_close"]) if row else 20.0  # default normal regime


def get_regime(vix: float, low_threshold: float = 15.0, high_threshold: float = 25.0) -> str:
    if vix < low_threshold:
        return "low"
    elif vix > high_threshold:
        return "high"
    return "normal"


def get_weights(vix: float | None = None,
                low_threshold: float = 15.0,
                high_threshold: float = 25.0) -> dict:
    if vix is None:
        vix = get_current_vix()

    regime = get_regime(vix, low_threshold, high_threshold)
    weights = dict(BASE_WEIGHTS)

    if regime == "low":
        for k, adj in LOW_VIX_ADJ.items():
            weights[k] = weights.get(k, 0) + adj
    elif regime == "high":
        for k, adj in HIGH_VIX_ADJ.items():
            weights[k] = weights.get(k, 0) + adj

    # Clamp to [0, 1] and renormalize to sum=1
    total = sum(max(0, v) for v in weights.values())
    weights = {k: max(0, v) / total for k, v in weights.items()}
    return weights, regime, vix
