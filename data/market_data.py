"""Daily OHLCV data via yfinance with incremental SQLite updates."""
import sqlite3
import logging
import os
import time
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict, Tuple

import pandas as pd
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
        CREATE TABLE IF NOT EXISTS daily_prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            adj_close REAL,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_ticker ON daily_prices(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_date ON daily_prices(date)")
    conn.commit()


def _latest_date(conn: sqlite3.Connection, ticker: str) -> Optional[str]:
    row = conn.execute(
        "SELECT MAX(date) FROM daily_prices WHERE ticker=?", (ticker,)
    ).fetchone()
    return row[0] if row else None


def _fetch_with_retry(ticker: str, start: str, end: str, max_retries: int = 3, backoff: float = 2.0) -> Optional[pd.DataFrame]:
    for attempt in range(max_retries):
        try:
            t = yf.Ticker(ticker)
            df = t.history(start=start, end=end, auto_adjust=False)
            if df is not None and not df.empty:
                return df
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                wait = backoff ** attempt
                logger.warning("Retry %d for %s after error: %s (sleeping %.1fs)", attempt + 1, ticker, e, wait)
                time.sleep(wait)
            else:
                logger.error("Failed to fetch %s after %d retries: %s", ticker, max_retries, e)
    return None


def _insert_prices(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> int:
    """Insert price rows, return count of new rows inserted."""
    rows = []
    for idx, row in df.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        close = float(row.get("Close", None) or 0)
        adj = float(row.get("Adj Close", close) or close)
        rows.append((
            ticker,
            date_str,
            float(row.get("Open", None) or 0) or None,
            float(row.get("High", None) or 0) or None,
            float(row.get("Low", None) or 0) or None,
            close or None,
            float(row.get("Volume", None) or 0) or None,
            adj or None,
        ))

    if not rows:
        return 0

    conn.executemany("""
        INSERT OR IGNORE INTO daily_prices (ticker, date, open, high, low, close, volume, adj_close)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    return len(rows)


def update_prices(
    lookback_years: int = 3,
    tickers: Optional[List[str]] = None,
    all_tickers: Optional[List[Dict]] = None,
) -> Dict[str, int]:
    """Fetch and store daily OHLCV for all tickers. Returns {ticker: rows_added}."""
    config = _load_config()
    if lookback_years is None:
        lookback_years = config["data"]["lookback_years"]

    conn = _get_db(config)
    _ensure_schema(conn)

    if tickers is None:
        if all_tickers is not None:
            tickers = [t["symbol"] for t in all_tickers]
        else:
            rows = conn.execute("SELECT symbol FROM tickers").fetchall()
            tickers = [r[0] for r in rows]

    start_global = (datetime.utcnow() - timedelta(days=lookback_years * 365)).strftime("%Y-%m-%d")
    end_global = datetime.utcnow().strftime("%Y-%m-%d")

    results = {}
    errors = 0

    for ticker in tqdm(tickers, desc="Fetching prices", unit="ticker"):
        latest = _latest_date(conn, ticker)
        if latest:
            # Start from the day after latest stored date
            start = (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            if start >= end_global:
                results[ticker] = 0
                continue
        else:
            start = start_global

        df = _fetch_with_retry(ticker, start, end_global)
        if df is None or df.empty:
            if latest is None:
                logger.warning("No price data for %s", ticker)
                errors += 1
            results[ticker] = 0
            continue

        added = _insert_prices(conn, ticker, df)
        results[ticker] = added
        if added > 0:
            logger.debug("Added %d price bars for %s", added, ticker)

    conn.close()
    total = sum(results.values())
    logger.info("Price update complete: %d bars added across %d tickers (%d errors)", total, len(tickers), errors)
    return results


def get_price_count(config: dict) -> int:
    conn = _get_db(config)
    row = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()
    conn.close()
    return row[0] if row else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = update_prices(tickers=["AAPL", "MSFT", "SPY"])
    print("Bars added:", results)
