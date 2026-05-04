"""Aggregate sector-level analysis from individual ticker results."""
import logging
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


def build_sector_summary(
    ticker_results: dict[str, dict],
    scores_df=None,
) -> dict[str, dict]:
    """
    Build per-sector summaries from claude analysis results.

    Args:
        ticker_results: {ticker: {"claude_score": float, "sector": str, "analyses": {...}}}
        scores_df: optional DataFrame with ticker/sector/composite_score columns

    Returns:
        {sector: {"top_long": str, "top_short": str, "outlook": str, "tickers": [...]}}
    """
    sector_map: dict[str, list] = defaultdict(list)

    for ticker, data in ticker_results.items():
        sector = data.get("sector", "Unknown")
        claude_score = data.get("claude_score")
        quant_score = data.get("composite_score")
        combined = data.get("combined_score")
        sector_map[sector].append({
            "ticker": ticker,
            "claude_score": claude_score,
            "composite_score": quant_score,
            "combined_score": combined,
        })

    sector_summaries = {}
    for sector, entries in sector_map.items():
        scored = [e for e in entries if e.get("combined_score") is not None]
        scored.sort(key=lambda x: x["combined_score"], reverse=True)

        top_long = scored[0]["ticker"] if scored else None
        top_short = scored[-1]["ticker"] if len(scored) > 1 else None

        avg_score = (
            sum(e["combined_score"] for e in scored) / len(scored) if scored else None
        )

        if avg_score is None:
            outlook = "insufficient data"
        elif avg_score >= 65:
            outlook = "broadly positive — sector positioning favorable"
        elif avg_score >= 45:
            outlook = "mixed — selective stock picking required"
        else:
            outlook = "broadly negative — defensive positioning recommended"

        sector_summaries[sector] = {
            "top_long": top_long,
            "top_short": top_short,
            "avg_combined_score": round(avg_score, 1) if avg_score else None,
            "outlook": outlook,
            "ticker_count": len(entries),
            "analyzed_count": len(scored),
            "tickers": [e["ticker"] for e in scored],
        }

    return sector_summaries


def format_sector_report(sector_summaries: dict) -> str:
    """Format sector summaries as a readable string."""
    lines = ["### Sector Analysis Summary", ""]
    for sector, data in sorted(sector_summaries.items()):
        lines.append(f"**{sector}**")
        if data.get("top_long"):
            lines.append(f"  Top Long:  {data['top_long']}")
        if data.get("top_short"):
            lines.append(f"  Top Short: {data['top_short']}")
        if data.get("avg_combined_score") is not None:
            lines.append(f"  Avg Score: {data['avg_combined_score']:.1f}")
        lines.append(f"  Outlook: {data['outlook']}")
        lines.append("")
    return "\n".join(lines)
