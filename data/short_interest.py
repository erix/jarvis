"""Short interest data from FMP or yfinance fallback."""
import sqlite3
import logging
import os
import time
from datetime import datetime
from typing import List, Optional, Dict, Any

import requests
import yaml
import yfinance as yf
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
        CREATE TABLE IF NOT EXISTS short_interest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            short_pct_float REAL,
            days_to_cover REAL,
            short_ratio REAL,
            source TEXT,
            UNIQUE(ticker, date, source)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_short_ticker ON short_interest(ticker)")
    conn.commit()


def _fetch_fmp(ticker: str, api_key: str) -> Optional[Dict]:
    url = f"https://financialmodelingprep.com/api/v4/short-interest"
    params = {"symbol": ticker, "apikey": api_key}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            latest = data[0]
            return {
                "date": latest.get("settleDate", datetime.utcnow().strftime("%Y-%m-%d"))[:10],
                "short_pct_float": latest.get("shortPercentOfFloat"),
                "days_to_cover": latest.get("daysToCover"),
                "short_ratio": latest.get("shortRatio"),
                "source": "fmp",
            }
    except Exception as e:
        logger.debug("FMP short interest failed for %s: %s", ticker, e)
    return None


def _fetch_yfinance(ticker: str) -> Optional[Dict]:
    try:
        t = yf.Ticker(ticker)
        info = t.info
        short_pct = info.get("shortPercentOfFloat")
        short_ratio = info.get("shortRatio")
        shares_short = info.get("sharesShort")
        avg_vol = info.get("averageVolume10days") or info.get("averageDailyVolume10Day")

        days_to_cover = None
        if shares_short and avg_vol and avg_vol > 0:
            days_to_cover = shares_short / avg_vol

        if short_pct is None and short_ratio is None:
            return None

        return {
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "short_pct_float": float(short_pct) if short_pct else None,
            "days_to_cover": days_to_cover,
            "short_ratio": float(short_ratio) if short_ratio else None,
            "source": "yfinance",
        }
    except Exception as e:
        logger.debug("yfinance short interest failed for %s: %s", ticker, e)
    return None


def update_short_interest(tickers: Optional[List[str]] = None) -> int:
    """Fetch short interest from FMP (preferred) or yfinance fallback."""
    config = _load_config()
    conn = _get_db(config)
    _ensure_schema(conn)

    fmp_key = os.environ.get("FMP_API_KEY", "")

    if tickers is None:
        rows = conn.execute("SELECT symbol FROM tickers WHERE is_benchmark=0").fetchall()
        tickers = [r[0] for r in rows]

    total = 0
    for ticker in tqdm(tickers, desc="Fetching short interest", unit="ticker"):
        data = None

        if fmp_key:
            data = _fetch_fmp(ticker, fmp_key)

        if data is None:
            data = _fetch_yfinance(ticker)

        if data is None:
            logger.debug("No short interest data for %s", ticker)
            continue

        try:
            conn.execute("""
                INSERT OR REPLACE INTO short_interest
                (ticker, date, short_pct_float, days_to_cover, short_ratio, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ticker, data["date"], data["short_pct_float"],
                  data["days_to_cover"], data["short_ratio"], data["source"]))
            total += 1
        except Exception as e:
            logger.warning("Short interest insert error for %s: %s", ticker, e)

        conn.commit()

    conn.close()
    logger.info("Short interest update: %d records stored", total)
    return total


def get_short_interest_count(config: dict) -> int:
    conn = _get_db(config)
    row = conn.execute("SELECT COUNT(*) FROM short_interest").fetchone()
    conn.close()
    return row[0] if row else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    count = update_short_interest(tickers=["AAPL", "TSLA"])
    print(f"Short interest records: {count}")
