"""Macro/rates/credit data via the free FRED API."""
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Dict, Optional

import requests
import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
FRED_BASE_URL = "https://api.stlouisfed.org/fred"

DEFAULT_SERIES = {
    "FEDFUNDS": "Effective Federal Funds Rate",
    "DGS2": "2-Year Treasury Yield",
    "DGS10": "10-Year Treasury Yield",
    "T10Y2Y": "10Y-2Y Treasury Spread",
    "T10Y3M": "10Y-3M Treasury Spread",
    "BAMLH0A0HYM2": "High Yield OAS",
    "BAMLC0A0CM": "Investment Grade Corporate OAS",
    "NFCI": "Chicago Fed National Financial Conditions Index",
    "STLFSI4": "St. Louis Fed Financial Stress Index",
    "CPIAUCSL": "CPI",
    "PCEPI": "PCE Price Index",
    "UNRATE": "Unemployment Rate",
    "PAYEMS": "Nonfarm Payrolls",
    "ICSA": "Initial Jobless Claims",
    "GDP": "Gross Domestic Product",
    "INDPRO": "Industrial Production",
    "RSAFS": "Retail Sales",
    "MORTGAGE30US": "30-Year Mortgage Rate",
}


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
        CREATE TABLE IF NOT EXISTS macro_series (
            series_id TEXT PRIMARY KEY,
            title TEXT,
            source TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_observations (
            series_id TEXT NOT NULL,
            date TEXT NOT NULL,
            value REAL,
            realtime_start TEXT,
            realtime_end TEXT,
            source TEXT,
            PRIMARY KEY (series_id, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_macro_obs_series ON macro_observations(series_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_macro_obs_date ON macro_observations(date)")
    conn.commit()


def _safe_float(value: str) -> Optional[float]:
    if value in (None, "", "."):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _series_from_config(config: dict) -> Dict[str, str]:
    configured = config.get("macro", {}).get("fred_series")
    if isinstance(configured, dict) and configured:
        return configured
    if isinstance(configured, list) and configured:
        return {series_id: DEFAULT_SERIES.get(series_id, series_id) for series_id in configured}
    return DEFAULT_SERIES


def _fetch_observations(api_key: str, series_id: str, limit: int = 1200) -> list[dict]:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    for attempt in range(3):
        try:
            resp = requests.get(f"{FRED_BASE_URL}/series/observations", params=params, timeout=30)
            if resp.ok:
                data = resp.json()
                return data.get("observations", []) or []
            if resp.status_code >= 500 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            logger.warning("FRED fetch failed for %s: HTTP %s", series_id, resp.status_code)
            return []
        except Exception as exc:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            logger.warning("FRED fetch failed for %s: %s", series_id, exc.__class__.__name__)
    return []


def update_macro(series: Optional[Dict[str, str]] = None) -> int:
    """Fetch configured FRED macro series into SQLite."""
    config = _load_config()
    api_key = (os.environ.get("FRED_API_KEY") or "").strip()
    if not api_key:
        logger.info("FRED_API_KEY not set - skipping macro fetch")
        return 0

    conn = _get_db(config)
    _ensure_schema(conn)
    series = series or _series_from_config(config)
    updated_at = datetime.utcnow().isoformat()
    total = 0

    for series_id, title in series.items():
        conn.execute("""
            INSERT OR REPLACE INTO macro_series (series_id, title, source, updated_at)
            VALUES (?, ?, ?, ?)
        """, (series_id, title, "fred", updated_at))
        for obs in _fetch_observations(api_key, series_id):
            conn.execute("""
                INSERT OR REPLACE INTO macro_observations
                (series_id, date, value, realtime_start, realtime_end, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                series_id,
                obs.get("date"),
                _safe_float(obs.get("value")),
                obs.get("realtime_start"),
                obs.get("realtime_end"),
                "fred",
            ))
            total += 1
        conn.commit()

    conn.close()
    logger.info("FRED macro update: %d observations stored across %d series", total, len(series))
    return total


def get_macro_count(config: dict) -> int:
    conn = _get_db(config)
    _ensure_schema(conn)
    row = conn.execute("SELECT COUNT(*) FROM macro_observations").fetchone()
    conn.close()
    return row[0] if row else 0
