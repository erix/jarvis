"""Analyst estimate daily snapshots via yfinance."""
import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

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
        CREATE TABLE IF NOT EXISTS analyst_estimates (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            forward_eps REAL,
            trailing_eps REAL,
            target_mean_price REAL,
            target_high_price REAL,
            target_low_price REAL,
            recommendation_mean REAL,
            analyst_count INTEGER,
            source TEXT,
            PRIMARY KEY (ticker, date, source)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_estimates_ticker ON analyst_estimates(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_estimates_date ON analyst_estimates(date)")
    conn.commit()


def _safe_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _fetch_yfinance(ticker: str) -> Optional[Dict]:
    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:
        logger.debug("Estimate fetch failed for %s: %s", ticker, exc)
        return None

    if not info:
        return None

    row = {
        "forward_eps": _safe_float(info.get("forwardEps")),
        "trailing_eps": _safe_float(info.get("trailingEps")),
        "target_mean_price": _safe_float(info.get("targetMeanPrice")),
        "target_high_price": _safe_float(info.get("targetHighPrice")),
        "target_low_price": _safe_float(info.get("targetLowPrice")),
        "recommendation_mean": _safe_float(info.get("recommendationMean")),
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "source": "yfinance",
    }
    if all(row.get(k) is None for k in ["forward_eps", "target_mean_price", "recommendation_mean"]):
        return None
    return row


def update_estimates(tickers: Optional[List[str]] = None) -> int:
    """Store one analyst-estimate snapshot per ticker for today."""
    config = _load_config()
    conn = _get_db(config)
    _ensure_schema(conn)

    if tickers is None:
        rows = conn.execute("SELECT symbol FROM tickers WHERE is_benchmark=0").fetchall()
        tickers = [r[0] for r in rows]

    today = datetime.utcnow().strftime("%Y-%m-%d")
    total = 0
    for ticker in tqdm(tickers, desc="Fetching estimates", unit="ticker"):
        data = _fetch_yfinance(ticker)
        if not data:
            continue
        conn.execute("""
            INSERT OR REPLACE INTO analyst_estimates (
                ticker, date, forward_eps, trailing_eps, target_mean_price,
                target_high_price, target_low_price, recommendation_mean,
                analyst_count, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker, today, data["forward_eps"], data["trailing_eps"],
            data["target_mean_price"], data["target_high_price"], data["target_low_price"],
            data["recommendation_mean"], data["analyst_count"], data["source"],
        ))
        total += 1
        conn.commit()

    conn.close()
    logger.info("Analyst estimates update: %d snapshots stored", total)
    return total


def get_estimate_count(config: dict) -> int:
    conn = _get_db(config)
    _ensure_schema(conn)
    row = conn.execute("SELECT COUNT(*) FROM analyst_estimates").fetchone()
    conn.close()
    return row[0] if row else 0
