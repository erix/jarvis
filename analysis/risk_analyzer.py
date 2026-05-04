"""10-K risk factor analysis via Claude."""
import logging
import os
import re
import sqlite3
from typing import Optional

from .api_client import APIClient
from . import cache as analysis_cache

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
_FILINGS_DIR = os.path.join(os.path.dirname(__file__), "..", "cache", "filings")

_SYSTEM_PROMPT = """You are a risk analyst specializing in SEC filing analysis.
You distinguish material risks from boilerplate legal language, assess risk trends,
and identify macro sensitivities. Focus on changes vs prior filings and severity assessment."""

_USER_TEMPLATE = """Analyze the Risk Factors section of {ticker}'s latest 10-K filing.

Risk Factors text:
{risk_text}

Tasks:
1. Identify new risks (not present in typical prior filings for this sector)
2. Separate material/specific risks from generic boilerplate language
3. Estimate what percentage is boilerplate (0.0 = all specific, 1.0 = all boilerplate)
4. Assess overall severity: LOW/MEDIUM/HIGH
5. Identify macro sensitivities (interest rates, FX, commodity prices, regulation, etc.)
6. Assess risk trend vs typical prior year: improving/stable/worsening

Return ONLY a JSON object:
{{
  "new_risks": [<list of strings, max 5>],
  "material_risks": [<list of strings, max 5>],
  "boilerplate_percentage": <float 0.0-1.0>,
  "risk_severity": "<LOW|MEDIUM|HIGH>",
  "macro_sensitivity": [<list of factors>],
  "risk_trend": "<improving|stable|worsening>",
  "one_line_summary": "<string>"
}}"""


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _get_risk_text(ticker: str) -> Optional[tuple]:
    """Return (risk_text, filing_date, accession_number) from cached 10-K or None."""
    try:
        conn = sqlite3.connect(_DB_PATH)
        # Get most recent 10-K with a cached file
        row = conn.execute(
            """SELECT filing_date, accession_number, cached_path FROM filings
               WHERE ticker=? AND form_type='10-K'
               ORDER BY filing_date DESC LIMIT 1""",
            (ticker,),
        ).fetchone()
        conn.close()

        if not row:
            return None

        filing_date, accession_number, cached_path = row

        # Try cached_path first
        if cached_path and os.path.exists(cached_path):
            with open(cached_path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            return text[:80_000], filing_date, accession_number

        # Try common cache location
        acc_clean = accession_number.replace("-", "") if accession_number else ""
        candidate = os.path.join(_FILINGS_DIR, acc_clean + ".txt")
        if os.path.exists(candidate):
            with open(candidate, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            return text[:80_000], filing_date, accession_number

        return None
    except Exception as e:
        logger.debug("Risk text lookup failed for %s: %s", ticker, e)
        return None


def analyze(ticker: str, client: APIClient, force: bool = False) -> Optional[dict]:
    """Run 10-K risk factor analysis for ticker."""
    filing_data = _get_risk_text(ticker)
    if not filing_data:
        logger.debug("[%s] No 10-K filing text available", ticker)
        return None

    text, filing_date, accession = filing_data
    if len(text.strip()) < 200:
        return None

    artifact_key = filing_date or accession or "unknown"

    if not force:
        cached = analysis_cache.get_cached(ticker, "risk", artifact_key)
        if cached:
            logger.debug("[%s] Risk cache hit", ticker)
            return cached

    # Strip HTML and cap at 80K chars
    clean_text = _strip_html(text)[:80_000]
    user_prompt = _USER_TEMPLATE.format(ticker=ticker, risk_text=clean_text)

    result = client.analyze(
        ticker=ticker,
        analyzer_type="risk",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=768,
    )

    if result:
        analysis_cache.set_cache(ticker, "risk", result, artifact_key)
        logger.info("[%s] Risk analysis complete: %s", ticker, result.get("one_line_summary", ""))
    else:
        logger.warning("[%s] Risk analysis returned no result", ticker)

    return result


def score_from_result(result: Optional[dict]) -> Optional[float]:
    """Convert risk analysis to 0-100 score (higher = lower risk = better for longs)."""
    if not result:
        return None
    try:
        severity_map = {"LOW": 80, "MEDIUM": 55, "HIGH": 25}
        severity_score = severity_map.get(result.get("risk_severity", "MEDIUM"), 55)

        trend_map = {"improving": 75, "stable": 55, "worsening": 25}
        trend_score = trend_map.get(result.get("risk_trend", "stable"), 55)

        boilerplate = float(result.get("boilerplate_percentage", 0.5))
        new_risk_penalty = len(result.get("new_risks", [])) * 3

        raw = 0.5 * severity_score + 0.3 * trend_score + 0.2 * (boilerplate * 100)
        return round(max(0, min(100, raw - new_risk_penalty)), 1)
    except Exception:
        return None
