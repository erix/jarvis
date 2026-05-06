"""Tail risk monitor: VIX/credit-based exposure suggestions.

IMPORTANT: This module SUGGESTS exposure reduction but does NOT auto-execute any trades.
All recommendations must be manually reviewed and acted upon by the operator.
"""
from __future__ import annotations

import logging
import os
import sqlite3

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
logger = logging.getLogger(__name__)

# VIX thresholds → suggested gross exposure reduction (percentage points)
VIX_SCHEDULE = [
    (30, 0.30),   # VIX > 30: suggest -30% gross
    (25, 0.20),   # VIX 25-30: suggest -20% gross
    (20, 0.10),   # VIX 20-25: suggest -10% gross
    (15, 0.05),   # VIX 15-20: suggest -5% gross
    (0,  0.00),   # VIX < 15: no action
]

# Credit spread thresholds use the same reduction as VIX 20-25
CREDIT_Z_THRESHOLD = 2.0
CREDIT_REDUCTION = 0.10


def _get_latest_vix() -> float | None:
    """Try to pull VIX from the scores table (stored during scoring)."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT vix FROM scores WHERE vix IS NOT NULL ORDER BY date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return float(row[0]) if row and row[0] else None


def _compute_credit_z(credit_spread: float | None) -> float | None:
    """Compute Z-score of current credit spread vs historical.

    If credit_spread is None (FRED API not available), returns None.
    """
    if credit_spread is None:
        return None
    # Without FRED data, we can't compute a historical Z-score; return None to skip
    return None


def check_tail_risk(
    vix_value: float | None = None,
    credit_spread: float | None = None,
) -> dict:
    """Assess tail risk and suggest (not execute) gross exposure adjustments.

    Returns exposure_adjustment dict with:
      - suggested_reduction: float (e.g. 0.20 = reduce gross by 20%)
      - reason: str
      - vix: float
      - recommendation: str (human-readable suggestion for operator)
    """
    # Try to load VIX from DB if not provided
    if vix_value is None:
        vix_value = _get_latest_vix()

    if vix_value is None:
        return {
            "suggested_reduction": 0.0,
            "reason": "VIX data unavailable",
            "vix": None,
            "recommendation": "Cannot assess tail risk — VIX data missing",
            "action": "NONE",
        }

    # Determine VIX-based reduction
    vix_reduction = 0.0
    vix_regime = "normal"
    for threshold, reduction in VIX_SCHEDULE:
        if vix_value > threshold:
            vix_reduction = reduction
            if threshold >= 30:
                vix_regime = "extreme_stress"
            elif threshold >= 25:
                vix_regime = "high_stress"
            elif threshold >= 20:
                vix_regime = "elevated"
            elif threshold >= 15:
                vix_regime = "moderate"
            break

    # Credit spread contribution
    credit_reduction = 0.0
    credit_note = ""
    credit_z = _compute_credit_z(credit_spread)
    if credit_z is not None and credit_z > CREDIT_Z_THRESHOLD:
        credit_reduction = CREDIT_REDUCTION
        credit_note = f"; HY credit spread Z={credit_z:.1f} (>{CREDIT_Z_THRESHOLD}) — additional {credit_reduction*100:.0f}% reduction suggested"

    # Take the maximum of VIX and credit reductions
    total_reduction = max(vix_reduction, credit_reduction)

    if total_reduction == 0:
        action = "NONE"
        recommendation = f"VIX={vix_value:.1f} — tail risk normal, no exposure adjustment needed"
    else:
        action = "SUGGEST_REDUCE"
        recommendation = (
            f"VIX={vix_value:.1f} ({vix_regime.replace('_', ' ')}): "
            f"SUGGEST reducing gross exposure by {total_reduction*100:.0f}%{credit_note}. "
            f"This is a recommendation only — operator must manually execute."
        )
        logger.warning("Tail risk alert: %s", recommendation)

    return {
        "suggested_reduction": total_reduction,
        "vix_reduction": vix_reduction,
        "credit_reduction": credit_reduction,
        "reason": f"VIX={vix_value:.1f}, regime={vix_regime}",
        "vix": vix_value,
        "vix_regime": vix_regime,
        "recommendation": recommendation,
        "action": action,
    }
