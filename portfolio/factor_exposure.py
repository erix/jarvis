"""Portfolio factor exposure calculator."""
import numpy as np
import pandas as pd

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


def calculate_exposure(
    target_weights: dict[str, float],
    scores_df: pd.DataFrame,
) -> dict:
    """Compute weighted average factor scores for a target portfolio.

    Returns a dict with:
      - per-factor exposure (weighted avg score)
      - overconcentration flags (exposure > 1 std dev from zero)
    """
    if not target_weights or scores_df.empty:
        return {}

    df = scores_df.set_index("ticker") if "ticker" in scores_df.columns else scores_df.copy()

    exposures = {}
    for factor in FACTOR_COLS:
        if factor not in df.columns:
            continue
        weighted_sum = 0.0
        total_weight = 0.0
        for ticker, weight in target_weights.items():
            if ticker in df.index and not pd.isna(df.loc[ticker, factor]):
                weighted_sum += weight * df.loc[ticker, factor]
                total_weight += abs(weight)
        exposures[factor] = weighted_sum / total_weight if total_weight > 0 else 0.0

    # Flag overconcentration: exposure > 1 std dev of all scores in that factor
    flags = {}
    for factor, exp in exposures.items():
        if factor in df.columns:
            std = df[factor].std()
            flags[factor] = abs(exp) > std if std and std > 0 else False

    # Summary stats
    long_tickers = [t for t, w in target_weights.items() if w > 0]
    short_tickers = [t for t, w in target_weights.items() if w < 0]

    return {
        "factor_exposures": exposures,
        "overconcentration_flags": flags,
        "num_long": len(long_tickers),
        "num_short": len(short_tickers),
        "gross_exposure": sum(abs(w) for w in target_weights.values()),
        "net_exposure": sum(target_weights.values()),
    }


def format_exposure_report(exposure: dict) -> str:
    """Return a human-readable factor exposure table."""
    lines = ["Factor Exposures (weighted avg score):"]
    factor_exp = exposure.get("factor_exposures", {})
    flags = exposure.get("overconcentration_flags", {})
    for factor, val in factor_exp.items():
        label = factor.replace("_score", "").replace("_", " ").title()
        flag = " [!]" if flags.get(factor) else ""
        lines.append(f"  {label:<22} {val:>6.1f}{flag}")
    return "\n".join(lines)
