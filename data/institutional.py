"""Tracked-fund 13F institutional holdings via SEC EDGAR."""
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

import requests
import yaml
from tqdm import tqdm

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
_RATE_LIMITER_LAST = 0.0

TRACKED_FUNDS = [
    {"fund_name": "Citadel Advisors", "cik": "0001423053"},
    {"fund_name": "Point72 Asset Management", "cik": "0001603466"},
    {"fund_name": "Bridgewater Associates", "cik": "0001350694"},
    {"fund_name": "Tiger Global Management", "cik": "0001167483"},
    {"fund_name": "Third Point", "cik": "0001040273"},
    {"fund_name": "Berkshire Hathaway", "cik": "0001067983"},
    {"fund_name": "Appaloosa", "cik": "0001656456"},
    {"fund_name": "Baupost Group", "cik": "0001061768"},
    {"fund_name": "Pershing Square", "cik": "0001336528"},
]


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
        CREATE TABLE IF NOT EXISTS institutional_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            fund_name TEXT,
            cik TEXT,
            quarter TEXT,
            shares REAL,
            value REAL,
            filing_date TEXT,
            UNIQUE(ticker, cik, quarter)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inst_ticker ON institutional_holdings(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inst_quarter ON institutional_holdings(quarter)")
    conn.commit()


def _rate_limit(per_sec: int) -> None:
    global _RATE_LIMITER_LAST
    min_interval = 1.0 / max(per_sec, 1)
    elapsed = time.time() - _RATE_LIMITER_LAST
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _RATE_LIMITER_LAST = time.time()


def _edgar_get(url: str, user_agent: str, rate_per_sec: int) -> Optional[requests.Response]:
    headers = {"User-Agent": user_agent, "Accept": "application/json, text/html, */*"}
    _rate_limit(rate_per_sec)
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp
        logger.debug("EDGAR returned %s for %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("EDGAR request failed for %s: %s", url, exc)
    return None


def _latest_13f(cik: str, config: dict) -> Optional[dict]:
    cik = cik.zfill(10)
    resp = _edgar_get(
        f"{config['sec']['base_url']}/submissions/CIK{cik}.json",
        config["sec"]["user_agent"],
        config["sec"]["rate_limit_per_sec"],
    )
    if not resp:
        return None
    data = resp.json()
    recent = data.get("filings", {}).get("recent", {})
    for form, date, acc in zip(
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("accessionNumber", []),
    ):
        if form.startswith("13F-HR"):
            return {"filing_date": date, "accession": acc}
    return None


def _find_information_table_url(cik: str, accession: str, config: dict) -> Optional[str]:
    acc_clean = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/"
    resp = _edgar_get(base, config["sec"]["user_agent"], config["sec"]["rate_limit_per_sec"])
    if not resp:
        return None
    for href in re.findall(r'href="([^"]+\.xml)"', resp.text, re.IGNORECASE):
        lowered = href.lower()
        if "infotable" in lowered or "informationtable" in lowered or "form13f" in lowered:
            return href if href.startswith("http") else f"{base}{href.split('/')[-1]}"
    return None


def _text(node, tag: str) -> Optional[str]:
    found = node.find(f".//{{*}}{tag}")
    return (found.text or "").strip() if found is not None and found.text else None


def _parse_info_table(xml_text: str) -> List[dict]:
    root = ET.fromstring(xml_text)
    rows = []
    for info in root.findall(".//{*}infoTable"):
        ticker = _text(info, "nameOfIssuer")
        symbol = _text(info, "ticker") or _text(info, "symbol")
        shares = _text(info, "sshPrnamt")
        value = _text(info, "value")
        if not symbol:
            # Some 13F XMLs do not provide tickers. Keeping issuer names would
            # pollute downstream factor joins, so skip those rows.
            continue
        rows.append({
            "ticker": symbol.upper().replace(".", "-"),
            "issuer": ticker,
            "shares": float(shares) if shares else None,
            "value": float(value) * 1000 if value else None,
        })
    return rows


def update_institutional_holdings(funds: Optional[List[dict]] = None) -> int:
    """Fetch latest 13F information tables for tracked hedge funds."""
    config = _load_config()
    conn = _get_db(config)
    _ensure_schema(conn)
    funds = funds or TRACKED_FUNDS

    total = 0
    for fund in tqdm(funds, desc="Fetching 13F holdings", unit="fund"):
        cik = fund["cik"].zfill(10)
        latest = _latest_13f(cik, config)
        if not latest:
            continue
        quarter = latest["filing_date"][:7]
        xml_url = _find_information_table_url(cik, latest["accession"], config)
        if not xml_url:
            logger.debug("No information table XML for %s", fund["fund_name"])
            continue
        resp = _edgar_get(xml_url, config["sec"]["user_agent"], config["sec"]["rate_limit_per_sec"])
        if not resp:
            continue
        try:
            holdings = _parse_info_table(resp.text)
        except Exception as exc:
            logger.warning("13F parse failed for %s: %s", fund["fund_name"], exc)
            continue
        for row in holdings:
            conn.execute("""
                INSERT OR REPLACE INTO institutional_holdings
                (ticker, fund_name, cik, quarter, shares, value, filing_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                row["ticker"], fund["fund_name"], cik, quarter,
                row["shares"], row["value"], latest["filing_date"],
            ))
            total += 1
        conn.commit()

    conn.close()
    logger.info("Institutional holdings update: %d rows stored", total)
    return total


def get_institutional_count(config: dict) -> int:
    conn = _get_db(config)
    _ensure_schema(conn)
    row = conn.execute("SELECT COUNT(*) FROM institutional_holdings").fetchone()
    conn.close()
    return row[0] if row else 0
