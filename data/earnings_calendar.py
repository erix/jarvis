"""Upcoming earnings calendar snapshots via yfinance."""
import logging
import os
import sqlite3
from datetime import datetime
from typing import List, Optional

import pandas as pd
import yfinance as yf
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
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            ticker TEXT NOT NULL,
            earnings_date TEXT NOT NULL,
            eps_estimate REAL,
            reported_eps REAL,
            surprise_pct REAL,
            source TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (ticker, earnings_date, source)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calendar_ticker ON earnings_calendar(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calendar_date ON earnings_calendar(earnings_date)")
    conn.commit()


def _safe_float(value) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _fetch_yfinance(ticker: str) -> list[dict]:
    try:
        df = yf.Ticker(ticker).get_earnings_dates(limit=8)
    except Exception as exc:
        logger.debug("Earnings calendar fetch failed for %s: %s", ticker, exc)
        return []
    if df is None or df.empty:
        return []

    rows = []
    for idx, row in df.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        rows.append({
            "earnings_date": date_str,
            "eps_estimate": _safe_float(row.get("EPS Estimate")),
            "reported_eps": _safe_float(row.get("Reported EPS")),
            "surprise_pct": _safe_float(row.get("Surprise(%)")),
        })
    return rows


def update_earnings_calendar(tickers: Optional[List[str]] = None) -> int:
    """Fetch upcoming/recent earnings dates for each ticker."""
    config = _load_config()
    conn = _get_db(config)
    _ensure_schema(conn)

    if tickers is None:
        rows = conn.execute("SELECT symbol FROM tickers WHERE is_benchmark=0").fetchall()
        tickers = [r[0] for r in rows]

    updated_at = datetime.utcnow().isoformat()
    total = 0
    for ticker in tqdm(tickers, desc="Fetching earnings calendar", unit="ticker"):
        for row in _fetch_yfinance(ticker):
            conn.execute("""
                INSERT OR REPLACE INTO earnings_calendar (
                    ticker, earnings_date, eps_estimate, reported_eps,
                    surprise_pct, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker, row["earnings_date"], row["eps_estimate"],
                row["reported_eps"], row["surprise_pct"], "yfinance", updated_at,
            ))
            total += 1
        conn.commit()

    conn.close()
    logger.info("Earnings calendar update: %d rows stored", total)
    return total


def get_earnings_calendar_count(config: dict) -> int:
    conn = _get_db(config)
    _ensure_schema(conn)
    row = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()
    conn.close()
    return row[0] if row else 0
