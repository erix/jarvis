"""10-K financial forensics via Claude."""
import logging
import sqlite3
import os
from typing import Optional

from .api_client import APIClient
from . import cache as analysis_cache

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")

_SYSTEM_PROMPT = """You are a forensic accounting expert and CFA with expertise in financial statement analysis.
You identify earnings quality issues, accrual manipulation, and red flags in financial data.
Be objective, cite specific metrics, and provide actionable insights."""

_USER_TEMPLATE = """Perform a forensic accounting review of {ticker} based on these fundamentals:

{fundamentals_text}

Assess:
- Financial Health (1-10): overall balance sheet and cash flow quality
- Earnings Quality (1-10): is net income supported by operating cash flow? (CFO/NI ratio)
- Revenue Quality (1-10): are earnings recurring vs one-time?
- Balance Sheet Health (1-10): debt levels, liquidity, working capital

Red Flags to check:
- CFO < NI (earnings not backed by cash)
- AR growing faster than revenue
- Inventory piling up
- Goodwill > 50% of total assets
- Debt/equity deteriorating

Green Flags to check:
- FCF > NI for multiple periods
- Buybacks exceeding dilution
- Improving Piotroski F-score
- Improving Altman Z-score

Return ONLY a JSON object:
{{
  "financial_health": <float 1-10>,
  "earnings_quality": <float 1-10>,
  "revenue_quality": <float 1-10>,
  "balance_sheet_health": <float 1-10>,
  "red_flags": [<list of strings>],
  "green_flags": [<list of strings>],
  "accruals_commentary": "<string>",
  "one_line_summary": "<string>"
}}"""


def _get_fundamentals(ticker: str) -> Optional[tuple]:
    """Return (fundamentals_dict, report_date) for most recent quarter."""
    try:
        conn = sqlite3.connect(_DB_PATH)
        row = conn.execute(
            """SELECT report_date, revenue, net_income, operating_cash_flow, free_cash_flow,
                      total_assets, total_liabilities, total_equity, total_debt, cash,
                      accounts_receivable, inventory, goodwill, current_ratio,
                      debt_equity, gross_margin, operating_margin, net_margin,
                      revenue_growth_yoy, earnings_growth_yoy, cfo_ni, accruals_ratio,
                      piotroski_f_score, altman_z_score, fcf_yield, pe_ratio, market_cap
               FROM fundamentals WHERE ticker=?
               ORDER BY report_date DESC LIMIT 4""",
            (ticker,),
        ).fetchall()
        conn.close()
        return row or None
    except Exception as e:
        logger.debug("No fundamentals for %s: %s", ticker, e)
        return None


def _format_fundamentals(ticker: str, rows: list) -> str:
    if not rows:
        return "No fundamental data available."
    lines = [f"Fundamentals for {ticker} (most recent 4 quarters):"]
    cols = [
        "report_date", "revenue", "net_income", "operating_cash_flow", "free_cash_flow",
        "total_assets", "total_liabilities", "total_equity", "total_debt", "cash",
        "accounts_receivable", "inventory", "goodwill", "current_ratio",
        "debt_equity", "gross_margin", "operating_margin", "net_margin",
        "revenue_growth_yoy", "earnings_growth_yoy", "cfo_ni", "accruals_ratio",
        "piotroski_f_score", "altman_z_score", "fcf_yield", "pe_ratio", "market_cap",
    ]
    for row in rows:
        d = dict(zip(cols, row))
        date = d.pop("report_date", "?")
        lines.append(f"\n  Period: {date}")
        for k, v in d.items():
            if v is not None:
                lines.append(f"    {k}: {v:.2f}" if isinstance(v, float) else f"    {k}: {v}")
    return "\n".join(lines)


def analyze(ticker: str, client: APIClient, force: bool = False) -> Optional[dict]:
    """Run filing/fundamentals analysis for ticker."""
    rows = _get_fundamentals(ticker)
    if not rows:
        logger.debug("[%s] No fundamentals available", ticker)
        return None

    artifact_key = rows[0][0] if rows else "unknown"  # most recent report_date

    if not force:
        cached = analysis_cache.get_cached(ticker, "filing", artifact_key)
        if cached:
            logger.debug("[%s] Filing cache hit", ticker)
            return cached

    fundamentals_text = _format_fundamentals(ticker, rows)
    user_prompt = _USER_TEMPLATE.format(ticker=ticker, fundamentals_text=fundamentals_text)

    result = client.analyze(
        ticker=ticker,
        analyzer_type="filing",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=1024,
    )

    if result:
        analysis_cache.set_cache(ticker, "filing", result, artifact_key)
        logger.info("[%s] Filing analysis complete: %s", ticker, result.get("one_line_summary", ""))
    else:
        logger.warning("[%s] Filing analysis returned no result", ticker)

    return result


def score_from_result(result: Optional[dict]) -> Optional[float]:
    """Convert filing analysis to 0-100 score."""
    if not result:
        return None
    try:
        scores = [
            float(result.get("financial_health", 5)),
            float(result.get("earnings_quality", 5)),
            float(result.get("revenue_quality", 5)),
            float(result.get("balance_sheet_health", 5)),
        ]
        avg = sum(scores) / len(scores)
        raw = (avg / 10) * 100

        # Penalty for red flags, bonus for green flags
        red_count = len(result.get("red_flags", []))
        green_count = len(result.get("green_flags", []))
        adjustment = (green_count - red_count) * 2
        return round(max(0, min(100, raw + adjustment)), 1)
    except Exception:
        return None
