"""Earnings call transcripts via Financial Modeling Prep API."""
import sqlite3
import logging
import os
import time
from datetime import datetime
from typing import List, Optional, Dict

import requests
import yaml
from tqdm import tqdm

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _get_db(config: dict) -> sqlite3.Connection:
    db_path = os.path.join(os.path.dirname(__file__), "..", config["data"]["db_path"])
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_transcripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT,
            quarter TEXT,
            transcript_text TEXT,
            UNIQUE(ticker, date, quarter)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transcripts_ticker ON earnings_transcripts(ticker)")
    conn.commit()


def _fetch_transcripts_fmp(ticker: str, api_key: str) -> List[Dict]:
    """Fetch earnings transcripts from FMP API."""
    url = f"https://financialmodelingprep.com/api/v3/earning_call_transcript/{ticker}"
    params = {"apikey": api_key, "limit": 8}
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
    except Exception as e:
        logger.warning("FMP transcript fetch failed for %s: %s", ticker, e)
    return []


def update_transcripts(tickers: Optional[List[str]] = None) -> int:
    """Fetch and store earnings transcripts. Skips silently if no FMP key."""
    config = _load_config()
    api_key = (os.environ.get("FMP_API_KEY") or "").strip()
    if not api_key or api_key.startswith("#") or len(api_key) < 8:
        logger.warning("FMP_API_KEY not set — skipping transcript fetch")
        return 0

    conn = _get_db(config)
    _ensure_schema(conn)

    if tickers is None:
        rows = conn.execute("SELECT symbol FROM tickers WHERE is_benchmark=0").fetchall()
        tickers = [r[0] for r in rows]

    total = 0
    for ticker in tqdm(tickers, desc="Fetching transcripts", unit="ticker"):
        transcripts = _fetch_transcripts_fmp(ticker, api_key)
        for t in transcripts:
            try:
                date = t.get("date", "")[:10]
                quarter = f"Q{t.get('quarter', '')}-{t.get('year', '')}"
                text = t.get("content", "")
                if len(text) > 200_000:
                    text = text[:200_000]
                conn.execute("""
                    INSERT OR IGNORE INTO earnings_transcripts (ticker, date, quarter, transcript_text)
                    VALUES (?, ?, ?, ?)
                """, (ticker, date, quarter, text))
                total += 1
            except Exception as e:
                logger.debug("Transcript insert error for %s: %s", ticker, e)
        conn.commit()
        time.sleep(0.2)  # FMP rate limiting

    conn.close()
    logger.info("Transcripts update: %d records stored", total)
    return total


def get_transcript_count(config: dict) -> int:
    conn = _get_db(config)
    row = conn.execute("SELECT COUNT(*) FROM earnings_transcripts").fetchone()
    conn.close()
    return row[0] if row else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    count = update_transcripts(tickers=["AAPL"])
    print(f"Transcripts: {count}")
