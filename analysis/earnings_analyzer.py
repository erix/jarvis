"""Earnings call transcript analysis via Claude."""
import logging
import sqlite3
import os
from typing import Optional

from .api_client import APIClient
from . import cache as analysis_cache

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")

_SYSTEM_PROMPT = """You are a senior equity analyst specializing in earnings call analysis.
Your job is to extract structured insights from earnings call transcripts with precision and objectivity.
Focus on management tone, forward guidance, and operational signals. Be concise and data-driven."""

_USER_TEMPLATE = """Analyze this earnings call transcript for {ticker}. Focus on:
1. Management confidence level (1-10, where 10 = extremely confident)
2. Revenue guidance: beat/miss/maintain vs prior expectations
3. Margin outlook: expanding/contracting/stable
4. Capital allocation priorities
5. Key risks or concerns mentioned by management
6. Any guidance changes vs prior quarter
7. Overall tone: bullish/neutral/bearish

Transcript:
{transcript}

Return ONLY a JSON object with these exact fields:
{{
  "management_confidence": <float 1-10>,
  "revenue_guidance": "<beat|miss|maintain>",
  "margin_outlook": "<expanding|contracting|stable>",
  "capital_allocation": [<list of strings>],
  "key_risks": [<list of strings>],
  "guidance_change": "<up|down|unchanged>",
  "overall_tone": "<bullish|neutral|bearish>",
  "one_line_summary": "<string>"
}}"""


def _get_latest_transcript(ticker: str) -> Optional[tuple]:
    """Return (transcript_text, date, quarter) or None."""
    try:
        conn = sqlite3.connect(_DB_PATH)
        row = conn.execute(
            """SELECT transcript_text, date, quarter FROM earnings_transcripts
               WHERE ticker=? AND transcript_text IS NOT NULL
               ORDER BY date DESC LIMIT 1""",
            (ticker,),
        ).fetchone()
        conn.close()
        return row
    except Exception as e:
        logger.debug("No earnings_transcripts table or error for %s: %s", ticker, e)
        return None


def analyze(ticker: str, client: APIClient, force: bool = False) -> Optional[dict]:
    """Run earnings call analysis for ticker. Returns structured dict or None."""
    row = _get_latest_transcript(ticker)
    if not row:
        logger.debug("[%s] No earnings transcript available", ticker)
        return None

    transcript_text, date, quarter = row
    if not transcript_text or len(transcript_text.strip()) < 100:
        return None

    artifact_key = f"{date}_{quarter}"

    if not force:
        cached = analysis_cache.get_cached(ticker, "earnings", artifact_key)
        if cached:
            logger.debug("[%s] Earnings cache hit", ticker)
            return cached

    # Truncate transcript to ~60K chars to stay within context
    transcript = transcript_text[:60_000]

    user_prompt = _USER_TEMPLATE.format(ticker=ticker, transcript=transcript)
    result = client.analyze(
        ticker=ticker,
        analyzer_type="earnings",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=512,
    )

    if result:
        analysis_cache.set_cache(ticker, "earnings", result, artifact_key)
        logger.info("[%s] Earnings analysis complete: %s", ticker, result.get("one_line_summary", ""))
    else:
        logger.warning("[%s] Earnings analysis returned no result", ticker)

    return result


def score_from_result(result: Optional[dict]) -> Optional[float]:
    """Convert earnings analysis to 0-100 score."""
    if not result:
        return None
    try:
        confidence = float(result.get("management_confidence", 5)) / 10 * 100

        tone_map = {"bullish": 80, "neutral": 50, "bearish": 20}
        tone_score = tone_map.get(result.get("overall_tone", "neutral"), 50)

        guidance_map = {"up": 80, "unchanged": 55, "down": 25}
        guidance_score = guidance_map.get(result.get("guidance_change", "unchanged"), 55)

        margin_map = {"expanding": 80, "stable": 55, "contracting": 25}
        margin_score = margin_map.get(result.get("margin_outlook", "stable"), 55)

        return round(
            0.30 * confidence + 0.30 * tone_score + 0.20 * guidance_score + 0.20 * margin_score, 1
        )
    except Exception:
        return None
