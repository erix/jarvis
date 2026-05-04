"""Form 4 insider transaction pattern analysis via Claude."""
import logging
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .api_client import APIClient
from . import cache as analysis_cache

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")

_SYSTEM_PROMPT = """You are an expert in insider trading pattern analysis.
You analyze Form 4 SEC filings to distinguish meaningful insider buying from routine sales.
You weight CEO/CFO activity 3x, look for cluster buys (3+ insiders within 30 days),
and distinguish planned selling (10b5-1 programs) from opportunistic buying."""

_USER_TEMPLATE = """Analyze insider trading activity for {ticker} over the last 90 days.

Transactions (P=Purchase, S=Sale):
{transactions_text}

Analysis tasks:
1. Weight CEO/CFO purchases 3x more heavily than other insiders
2. Identify cluster buys: 3+ insiders buying within a 30-day window
3. Distinguish routine selling from meaningful buying
4. Assess overall signal strength and confidence
5. Analyze timing (near earnings, after dip, etc.)

Return ONLY a JSON object:
{{
  "signal_strength": "<STRONG_BUY|BUY|NEUTRAL|SELL|STRONG_SELL>",
  "confidence": <float 0.0-1.0>,
  "key_transactions": [<list of dicts with owner, role, code, shares, value, date>],
  "pattern": "<accumulation|distribution|neutral>",
  "timing_analysis": "<string>",
  "cluster_summary": "<string>",
  "one_line_summary": "<string>"
}}"""


def _get_insider_transactions(ticker: str, days: int = 90) -> Optional[list]:
    """Return recent insider transactions or None."""
    try:
        conn = sqlite3.connect(_DB_PATH)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """SELECT owner_name, owner_title, transaction_code, transaction_date,
                      shares, price_per_share, shares_after, filing_date
               FROM insider_transactions
               WHERE ticker=? AND transaction_code IN ('P','S')
                 AND transaction_date >= ?
               ORDER BY transaction_date DESC LIMIT 50""",
            (ticker, cutoff),
        ).fetchall()
        conn.close()
        return rows or None
    except Exception as e:
        logger.debug("Insider transaction lookup failed for %s: %s", ticker, e)
        return None


def _format_transactions(rows: list) -> str:
    if not rows:
        return "No insider transactions found."
    lines = []
    for row in rows:
        owner_name, owner_title, code, date, shares, price, shares_after, filing_date = row
        value = (abs(shares or 0) * (price or 0))
        lines.append(
            f"  {date} | {code} | {owner_name or 'Unknown'} ({owner_title or 'Unknown'}) | "
            f"Shares: {shares:+,.0f} | Price: ${price:.2f} | Value: ${value:,.0f}" if price and shares else
            f"  {date} | {code} | {owner_name or 'Unknown'} ({owner_title or 'Unknown'}) | Shares: {shares}"
        )
    return "\n".join(lines)


def analyze(ticker: str, client: APIClient, force: bool = False) -> Optional[dict]:
    """Run insider transaction analysis for ticker."""
    rows = _get_insider_transactions(ticker)
    if not rows:
        logger.debug("[%s] No insider transactions available", ticker)
        return None

    # Use the most recent transaction date as artifact key
    artifact_key = rows[0][3] if rows else "unknown"

    if not force:
        cached = analysis_cache.get_cached(ticker, "insider", artifact_key)
        if cached:
            logger.debug("[%s] Insider cache hit", ticker)
            return cached

    transactions_text = _format_transactions(rows)
    user_prompt = _USER_TEMPLATE.format(ticker=ticker, transactions_text=transactions_text)

    result = client.analyze(
        ticker=ticker,
        analyzer_type="insider",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=512,
    )

    if result:
        analysis_cache.set_cache(ticker, "insider", result, artifact_key)
        logger.info("[%s] Insider analysis complete: %s", ticker, result.get("one_line_summary", ""))
    else:
        logger.warning("[%s] Insider analysis returned no result", ticker)

    return result


def score_from_result(result: Optional[dict]) -> Optional[float]:
    """Convert insider analysis to 0-100 score."""
    if not result:
        return None
    try:
        signal_map = {
            "STRONG_BUY": 90, "BUY": 70, "NEUTRAL": 50, "SELL": 30, "STRONG_SELL": 10
        }
        signal_score = signal_map.get(result.get("signal_strength", "NEUTRAL"), 50)
        confidence = float(result.get("confidence", 0.5))
        pattern_map = {"accumulation": 75, "neutral": 50, "distribution": 25}
        pattern_score = pattern_map.get(result.get("pattern", "neutral"), 50)

        raw = 0.5 * signal_score + 0.3 * pattern_score + 0.2 * (confidence * 100)
        return round(max(0, min(100, raw)), 1)
    except Exception:
        return None
