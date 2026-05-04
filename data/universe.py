"""S&P 500 universe + benchmark tickers."""
import sqlite3
import logging
import os
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests
import yaml
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _get_db(config: dict) -> sqlite3.Connection:
    db_path = os.path.join(os.path.dirname(__file__), "..", config["data"]["db_path"])
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return sqlite3.connect(db_path)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            name TEXT,
            sector TEXT,
            sub_industry TEXT,
            is_benchmark INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _scrape_sp500(url: str) -> List[Dict]:
    """Scrape current S&P 500 list from Wikipedia."""
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    table = soup.find("table", {"id": "constituents"})
    if table is None:
        raise RuntimeError("Could not find S&P 500 constituents table on Wikipedia")

    rows = []
    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue
        symbol = cells[0].get_text(strip=True).replace(".", "-")  # BRK.B → BRK-B
        name = cells[1].get_text(strip=True)
        sector = cells[2].get_text(strip=True)
        sub_industry = cells[3].get_text(strip=True)
        rows.append({"symbol": symbol, "name": name, "sector": sector, "sub_industry": sub_industry})

    logger.info("Scraped %d tickers from Wikipedia", len(rows))
    return rows


def _cache_is_fresh(conn: sqlite3.Connection, ttl_days: int) -> bool:
    """Return True if the ticker list was updated within ttl_days."""
    row = conn.execute(
        "SELECT updated_at FROM tickers WHERE is_benchmark=0 ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return False
    last_update = datetime.fromisoformat(row[0])
    return (datetime.utcnow() - last_update) < timedelta(days=ttl_days)


def _upsert_tickers(conn: sqlite3.Connection, tickers: List[Dict], is_benchmark: bool = False) -> None:
    now = datetime.utcnow().isoformat()
    for t in tickers:
        conn.execute("""
            INSERT INTO tickers (symbol, name, sector, sub_industry, is_benchmark, updated_at)
            VALUES (:symbol, :name, :sector, :sub_industry, :is_benchmark, :updated_at)
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name,
                sector=excluded.sector,
                sub_industry=excluded.sub_industry,
                is_benchmark=excluded.is_benchmark,
                updated_at=excluded.updated_at
        """, {**t, "is_benchmark": 1 if is_benchmark else 0, "updated_at": now})
    conn.commit()


def get_universe(force_refresh: bool = False) -> List[Dict]:
    """Return list of dicts for all tickers (S&P 500 + benchmarks).

    Refreshes from Wikipedia if cache is older than ttl_days or force_refresh=True.
    """
    config = _load_config()
    conn = _get_db(config)
    _ensure_schema(conn)

    ttl_days = config["universe"].get("cache_ttl_days", 7)

    if force_refresh or not _cache_is_fresh(conn, ttl_days):
        logger.info("Refreshing S&P 500 universe from Wikipedia...")
        sp500 = _scrape_sp500(config["universe"]["sp500_wikipedia_url"])
        _upsert_tickers(conn, sp500, is_benchmark=False)

        benchmarks = config["universe"]["benchmarks"]
        bench_rows = [{"symbol": s, "name": s, "sector": "Benchmark", "sub_industry": "Benchmark"}
                      for s in benchmarks]
        _upsert_tickers(conn, bench_rows, is_benchmark=True)
        logger.info("Universe refreshed: %d SP500 + %d benchmarks", len(sp500), len(benchmarks))
    else:
        logger.info("Universe cache is fresh (<%d days old), skipping scrape", ttl_days)

    rows = conn.execute(
        "SELECT symbol, name, sector, sub_industry, is_benchmark FROM tickers ORDER BY symbol"
    ).fetchall()
    conn.close()

    return [
        {"symbol": r[0], "name": r[1], "sector": r[2], "sub_industry": r[3], "is_benchmark": bool(r[4])}
        for r in rows
    ]


def get_sp500_symbols(force_refresh: bool = False) -> List[str]:
    universe = get_universe(force_refresh=force_refresh)
    return [t["symbol"] for t in universe if not t["is_benchmark"]]


def get_benchmark_symbols() -> List[str]:
    config = _load_config()
    return config["universe"]["benchmarks"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tickers = get_universe(force_refresh=True)
    sp500 = [t for t in tickers if not t["is_benchmark"]]
    benchmarks = [t for t in tickers if t["is_benchmark"]]
    print(f"S&P 500: {len(sp500)} tickers")
    print(f"Benchmarks: {len(benchmarks)} tickers")
    print("Sample:", [t["symbol"] for t in sp500[:5]])
