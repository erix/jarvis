#!/usr/bin/env python3
"""
Layer 1 Entry Point — Data Infrastructure
Run: python run_data.py [--no-filings] [--no-13f]
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime

import yaml
from dotenv import load_dotenv

# Load .env first
_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(_ENV_PATH)

# Ensure module directory is on path
sys.path.insert(0, os.path.dirname(__file__))

from data.universe import get_universe
from data.market_data import update_prices, get_price_count
from data.fundamentals import update_fundamentals, get_fundamental_count
from data.short_interest import update_short_interest, get_short_interest_count
from data.transcripts import update_transcripts, get_transcript_count


def _setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO"))
    log_file = os.path.join(os.path.dirname(__file__), log_cfg.get("file", "output/logs/run.log"))
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    fmt = log_cfg.get("format", "%(asctime)s | %(levelname)s | %(message)s")
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, mode="a", encoding="utf-8"),
    ]
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)


def _load_config() -> dict:
    path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS Layer 1 — Data Ingestion")
    parser.add_argument("--no-filings", action="store_true", help="Skip SEC filings")
    parser.add_argument("--no-13f", action="store_true", help="Skip 13F institutional holdings")
    parser.add_argument("--tickers", nargs="+", help="Limit to specific tickers (for testing)")
    parser.add_argument("--prices-only", action="store_true", help="Only update prices")
    args = parser.parse_args()

    config = _load_config()
    _setup_logging(config)
    logger = logging.getLogger("run_data")

    start_time = time.time()
    logger.info("=" * 60)
    logger.info("JARVIS Layer 1 — Data Infrastructure starting")
    logger.info("=" * 60)

    errors = 0
    stats: dict = {
        "tickers": 0,
        "price_bars": 0,
        "fundamental_records": 0,
        "filings": 0,
        "insider_transactions": 0,
        "short_interest": 0,
        "transcripts": 0,
    }

    # ── Step 1: Universe ────────────────────────────────────────────
    logger.info("[1/6] Updating universe (S&P 500 + benchmarks)...")
    try:
        universe = get_universe()
        stats["tickers"] = len(universe)
        logger.info("Universe: %d tickers total", len(universe))
    except Exception as e:
        logger.error("Universe update failed: %s", e)
        errors += 1
        universe = []

    # Determine which tickers to process
    if args.tickers:
        tickers = args.tickers
        logger.info("Processing subset: %s", tickers)
    else:
        tickers = [t["symbol"] for t in universe]

    if not tickers:
        logger.error("No tickers to process — aborting")
        sys.exit(1)

    # ── Step 2: Prices ──────────────────────────────────────────────
    logger.info("[2/6] Updating daily prices (lookback=%d years)...", config["data"]["lookback_years"])
    try:
        price_results = update_prices(
            lookback_years=config["data"]["lookback_years"],
            tickers=tickers,
        )
        stats["price_bars"] = sum(price_results.values())
        logger.info("Prices: %d new bars added", stats["price_bars"])
    except Exception as e:
        logger.error("Price update failed: %s", e)
        errors += 1

    if args.prices_only:
        logger.info("--prices-only flag set, stopping after prices")
        _print_summary(stats, errors, start_time)
        return

    # ── Step 3: Fundamentals ────────────────────────────────────────
    logger.info("[3/6] Updating fundamentals (financial statements + ratios)...")
    try:
        sp500_tickers = [t["symbol"] for t in universe if not t["is_benchmark"]]
        if args.tickers:
            sp500_tickers = [s for s in args.tickers if s in sp500_tickers or args.tickers]
        stats["fundamental_records"] = update_fundamentals(tickers=sp500_tickers)
        logger.info("Fundamentals: %d records stored", stats["fundamental_records"])
    except Exception as e:
        logger.error("Fundamentals update failed: %s", e)
        errors += 1

    # ── Step 4: SEC Filings ─────────────────────────────────────────
    if args.no_filings:
        logger.info("[4/6] Skipping SEC filings (--no-filings)")
    else:
        logger.info("[4/6] Updating SEC filings (10-K, 10-Q, 8-K, Form 4%s)...",
                    ", 13F" if not args.no_13f else "")
        try:
            from data.filings import update_filings, get_filing_counts
            sp500_only = [t["symbol"] for t in universe if not t["is_benchmark"]]
            if args.tickers:
                sp500_only = args.tickers
            counts = update_filings(tickers=sp500_only, no_13f=args.no_13f)
            stats["filings"] = counts["filings"]
            stats["insider_transactions"] = counts["insider"]
            logger.info("Filings: %d metadata records, %d insider transactions",
                        stats["filings"], stats["insider_transactions"])
        except Exception as e:
            logger.error("Filings update failed: %s", e)
            errors += 1

    # ── Step 5: Short Interest ──────────────────────────────────────
    logger.info("[5/6] Updating short interest...")
    try:
        sp500_only = [t["symbol"] for t in universe if not t["is_benchmark"]]
        if args.tickers:
            sp500_only = args.tickers
        stats["short_interest"] = update_short_interest(tickers=sp500_only)
        logger.info("Short interest: %d records stored", stats["short_interest"])
    except Exception as e:
        logger.error("Short interest update failed: %s", e)
        errors += 1

    # ── Step 6: Transcripts ─────────────────────────────────────────
    logger.info("[6/6] Updating earnings transcripts (requires FMP_API_KEY)...")
    try:
        sp500_only = [t["symbol"] for t in universe if not t["is_benchmark"]]
        if args.tickers:
            sp500_only = args.tickers
        stats["transcripts"] = update_transcripts(tickers=sp500_only)
        logger.info("Transcripts: %d records stored", stats["transcripts"])
    except Exception as e:
        logger.error("Transcripts update failed: %s", e)
        errors += 1

    _print_summary(stats, errors, start_time)


def _print_summary(stats: dict, errors: int, start_time: float) -> None:
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    print()
    print("=" * 40)
    print("=== Layer 1 Complete ===")
    print(f"Tickers in universe:       {stats['tickers']}")
    print(f"Price bars added:          {stats['price_bars']}")
    print(f"Fundamental records:       {stats['fundamental_records']}")
    print(f"Filings cached:            {stats['filings']}")
    print(f"Insider transactions:      {stats['insider_transactions']}")
    print(f"Short interest records:    {stats['short_interest']}")
    print(f"Transcripts fetched:       {stats['transcripts']}")
    print(f"Errors:                    {errors}")
    print(f"Runtime:                   {minutes}m {seconds}s")
    print("=" * 40)

    logging.getLogger("run_data").info(
        "Layer 1 complete: %d tickers, %d price bars, %d fundamentals, "
        "%d filings, %d insider txns, %d short interest, %d transcripts — "
        "%d errors in %dm %ds",
        stats["tickers"], stats["price_bars"], stats["fundamental_records"],
        stats["filings"], stats["insider_transactions"], stats["short_interest"],
        stats["transcripts"], errors, minutes, seconds
    )


if __name__ == "__main__":
    main()
