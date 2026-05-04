"""SEC EDGAR filings: 10-K, 10-Q, 8-K, Form 4 insider transactions, 13F holdings."""
import sqlite3
import logging
import os
import re
import time
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from xml.etree import ElementTree as ET

import requests
import yaml
from tqdm import tqdm

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
_RATE_LIMITER_LAST: float = 0.0


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
        CREATE TABLE IF NOT EXISTS filings (
            ticker TEXT NOT NULL,
            form_type TEXT NOT NULL,
            filing_date TEXT,
            accession_number TEXT NOT NULL,
            cached_path TEXT,
            PRIMARY KEY (ticker, accession_number)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS insider_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            owner_name TEXT,
            owner_title TEXT,
            transaction_date TEXT,
            transaction_code TEXT,
            shares REAL,
            price_per_share REAL,
            shares_after REAL,
            filing_date TEXT,
            accession_number TEXT,
            UNIQUE(ticker, accession_number, transaction_date, transaction_code, shares)
        )
    """)
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_filings_ticker ON filings(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_insider_ticker ON insider_transactions(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inst_ticker ON institutional_holdings(ticker)")
    conn.commit()


def _rate_limit(per_sec: int = 10) -> None:
    global _RATE_LIMITER_LAST
    min_interval = 1.0 / per_sec
    elapsed = time.time() - _RATE_LIMITER_LAST
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _RATE_LIMITER_LAST = time.time()


def _edgar_get(url: str, user_agent: str, rate_per_sec: int = 10, max_retries: int = 3) -> Optional[requests.Response]:
    headers = {"User-Agent": user_agent, "Accept": "application/json, text/html, */*"}
    for attempt in range(max_retries):
        _rate_limit(rate_per_sec)
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp
            elif resp.status_code == 429:
                wait = 60
                logger.warning("SEC rate limit hit, sleeping %ds", wait)
                time.sleep(wait)
            else:
                logger.warning("EDGAR returned %d for %s", resp.status_code, url)
                return None
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                logger.error("EDGAR request failed for %s: %s", url, e)
    return None


def _ticker_to_cik(ticker: str, user_agent: str) -> Optional[str]:
    """Resolve ticker to SEC CIK using EDGAR company search."""
    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2020-01-01&forms=10-K&hits.hits._source=period_of_report,entity_name,file_num,period_of_report,biz_location,inc_states"
    # Use the tickers.json endpoint instead
    url = "https://www.sec.gov/files/company_tickers.json"
    resp = _edgar_get(url, user_agent, rate_per_sec=2)
    if resp is None:
        return None
    try:
        data = resp.json()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker.upper():
                return str(entry["cik_str"]).zfill(10)
    except Exception as e:
        logger.warning("CIK lookup failed for %s: %s", ticker, e)
    return None


def _get_submissions(cik: str, user_agent: str, base_url: str) -> Optional[Dict]:
    url = f"{base_url}/submissions/CIK{cik}.json"
    resp = _edgar_get(url, user_agent)
    if resp is None:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def _cache_filing(cache_dir: str, accession: str, text: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, accession.replace("-", "") + ".txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text[:500_000])  # truncate very large filings
    return path


def _parse_form4_xml(xml_text: str) -> List[Dict]:
    """Parse Form 4 XML and extract transactions with codes P and S."""
    results = []
    try:
        root = ET.fromstring(xml_text)

        def find(node, *tags):
            for tag in tags:
                found = node.find(f".//{tag}")
                if found is not None:
                    return (found.text or "").strip()
            return None

        owner_name = find(root, "rptOwnerName")
        owner_title = find(root, "officerTitle")

        for txn in root.findall(".//nonDerivativeTransaction"):
            code = find(txn, "transactionCode")
            if code not in ("P", "S"):
                continue
            date = find(txn, "transactionDate", "value")
            shares_el = txn.find(".//transactionShares/value")
            price_el = txn.find(".//transactionPricePerShare/value")
            after_el = txn.find(".//sharesOwnedFollowingTransaction/value")

            shares = float(shares_el.text) if shares_el is not None and shares_el.text else None
            if code == "S" and shares and shares > 0:
                shares = -shares  # sales are negative

            results.append({
                "owner_name": owner_name,
                "owner_title": owner_title,
                "transaction_code": code,
                "transaction_date": date,
                "shares": shares,
                "price_per_share": float(price_el.text) if price_el is not None and price_el.text else None,
                "shares_after": float(after_el.text) if after_el is not None and after_el.text else None,
            })
    except Exception as e:
        logger.warning("Form 4 XML parse error: %s", e)
    return results


def _update_filings_for_ticker(
    ticker: str, cik: str, conn: sqlite3.Connection,
    config: dict, form_types: List[str], cache_dir: str
) -> Dict[str, int]:
    user_agent = config["sec"]["user_agent"]
    base_url = config["sec"]["base_url"]
    rate_per_sec = config["sec"]["rate_limit_per_sec"]

    subs = _get_submissions(cik, user_agent, base_url)
    if not subs:
        return {"filings": 0, "insider": 0}

    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])

    filing_count = 0
    insider_count = 0
    cutoff = (datetime.utcnow() - timedelta(days=3 * 365)).strftime("%Y-%m-%d")

    for i, (form, filing_date, acc) in enumerate(zip(forms, dates, accessions)):
        if form not in form_types:
            continue
        if filing_date < cutoff:
            continue

        acc_clean = acc.replace("-", "")
        cached_path = None

        # Check if already cached
        existing = conn.execute(
            "SELECT cached_path FROM filings WHERE ticker=? AND accession_number=?",
            (ticker, acc)
        ).fetchone()

        if existing:
            continue

        if form == "4":
            # Fetch Form 4 XML
            url = f"https://www.sec.gov/Archives/edgar/full-index/{filing_date[:4]}/{filing_date[5:7]}/{acc_clean}/{acc_clean}.txt"
            # Use primary doc URL pattern
            idx_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{acc}-index.htm"
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{acc_clean}-index.htm"

            # Try to get the filing index to find the XML file
            submissions_url = f"{base_url}/submissions/CIK{cik}.json"
            # Direct approach: build URL from pattern
            xml_url = None
            doc_list_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4&dateb=&owner=include&count=40&search_text="
            # Simpler: use EDGAR full submission index
            idx_url2 = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/"

            resp = _edgar_get(idx_url2, user_agent, rate_per_sec)
            if resp:
                # Find XML link
                xml_match = re.search(r'href="([^"]*\.xml)"', resp.text, re.IGNORECASE)
                if xml_match:
                    xml_file = xml_match.group(1)
                    if not xml_file.startswith("http"):
                        xml_url = f"https://www.sec.gov{xml_file}" if xml_file.startswith("/") else f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{xml_file}"
                    else:
                        xml_url = xml_file

            if xml_url:
                xml_resp = _edgar_get(xml_url, user_agent, rate_per_sec)
                if xml_resp:
                    txns = _parse_form4_xml(xml_resp.text)
                    for txn in txns:
                        try:
                            conn.execute("""
                                INSERT OR IGNORE INTO insider_transactions
                                (ticker, owner_name, owner_title, transaction_date,
                                 transaction_code, shares, price_per_share, shares_after,
                                 filing_date, accession_number)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                ticker, txn["owner_name"], txn["owner_title"],
                                txn["transaction_date"], txn["transaction_code"],
                                txn["shares"], txn["price_per_share"],
                                txn["shares_after"], filing_date, acc
                            ))
                            insider_count += 1
                        except Exception as ex:
                            logger.debug("Insider insert error: %s", ex)

        conn.execute("""
            INSERT OR IGNORE INTO filings (ticker, form_type, filing_date, accession_number, cached_path)
            VALUES (?, ?, ?, ?, ?)
        """, (ticker, form, filing_date, acc, cached_path))
        filing_count += 1

    conn.commit()
    return {"filings": filing_count, "insider": insider_count}


def update_filings(
    tickers: Optional[List[str]] = None,
    no_13f: bool = False,
    cik_map: Optional[Dict[str, str]] = None,
) -> Dict[str, int]:
    """Fetch and store SEC filings metadata and insider transactions."""
    config = _load_config()
    conn = _get_db(config)
    _ensure_schema(conn)

    user_agent = config["sec"]["user_agent"]
    cache_dir = os.path.join(
        os.path.dirname(__file__), "..", config["data"].get("filings_cache_dir", "cache/filings")
    )

    if tickers is None:
        rows = conn.execute("SELECT symbol FROM tickers WHERE is_benchmark=0").fetchall()
        tickers = [r[0] for r in rows]

    form_types = ["10-K", "10-Q", "8-K", "4"]
    if not no_13f:
        form_types.append("13F-HR")

    # Load CIK map once
    if cik_map is None:
        cik_map = {}
        logger.info("Loading SEC company ticker → CIK map...")
        resp = _edgar_get("https://www.sec.gov/files/company_tickers.json", user_agent, rate_per_sec=2)
        if resp:
            try:
                data = resp.json()
                for entry in data.values():
                    sym = entry.get("ticker", "").upper()
                    cik = str(entry["cik_str"]).zfill(10)
                    cik_map[sym] = cik
                logger.info("Loaded CIK map: %d entries", len(cik_map))
            except Exception as e:
                logger.error("Failed to load CIK map: %s", e)

    total_filings = 0
    total_insider = 0

    for ticker in tqdm(tickers, desc="Fetching filings", unit="ticker"):
        cik = cik_map.get(ticker.upper())
        if not cik:
            logger.debug("No CIK found for %s, skipping", ticker)
            continue

        try:
            counts = _update_filings_for_ticker(ticker, cik, conn, config, form_types, cache_dir)
            total_filings += counts["filings"]
            total_insider += counts["insider"]
        except Exception as e:
            logger.error("Error processing filings for %s: %s", ticker, e)

    conn.close()
    logger.info("Filings update: %d filings, %d insider transactions", total_filings, total_insider)
    return {"filings": total_filings, "insider": total_insider}


def get_filing_counts(config: dict) -> Dict[str, int]:
    conn = _get_db(config)
    filings = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
    insider = conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0]
    inst = conn.execute("SELECT COUNT(*) FROM institutional_holdings").fetchone()[0]
    conn.close()
    return {"filings": filings, "insider": insider, "institutional": inst}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    counts = update_filings(tickers=["AAPL"], no_13f=True)
    print("Filings:", counts)
