#!/usr/bin/env python3
"""
Layer 2 Entry Point — Scoring Engine
Run: python run_scoring.py              # Score all tickers
     python run_scoring.py --ticker AAPL  # Single stock
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import sqlite3

sys.path.insert(0, os.path.dirname(__file__))

DB_PATH = os.path.join(os.path.dirname(__file__), "cache", "jarvis.db")


def _setup_logging() -> None:
    os.makedirs("output/logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("output/logs/scoring.log", mode="a", encoding="utf-8"),
        ],
        force=True,
    )


def _ensure_scores_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            momentum_score REAL,
            value_score REAL,
            quality_score REAL,
            growth_score REAL,
            revisions_score REAL,
            short_interest_score REAL,
            insider_score REAL,
            institutional_score REAL,
            composite_raw REAL,
            composite_score REAL,
            sector TEXT,
            regime TEXT,
            vix REAL,
            is_long_candidate INTEGER DEFAULT 0,
            is_short_candidate INTEGER DEFAULT 0,
            PRIMARY KEY (ticker, date)
        )
    """)
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(scores)").fetchall()
    }
    if "composite_raw" not in existing:
        conn.execute("ALTER TABLE scores ADD COLUMN composite_raw REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_date ON scores(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_ticker ON scores(ticker)")
    conn.commit()


def _load_universe(tickers_filter: list | None = None) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT symbol as ticker, sector FROM tickers WHERE is_benchmark=0"
    ).fetchall()
    conn.close()
    df = pd.DataFrame([dict(r) for r in rows])
    if tickers_filter:
        df = df[df["ticker"].isin(tickers_filter)]
    return df


def _save_scores(scores_df: pd.DataFrame, date_str: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    _ensure_scores_table(conn)

    cols = [
        "ticker", "momentum_score", "value_score", "quality_score", "growth_score",
        "revisions_score", "short_interest_score", "insider_score", "institutional_score",
        "composite_raw", "composite_score", "sector", "regime", "vix",
        "is_long_candidate", "is_short_candidate",
    ]

    rows = []
    for _, r in scores_df.iterrows():
        rows.append((
            r["ticker"], date_str,
            r.get("momentum_score"), r.get("value_score"), r.get("quality_score"),
            r.get("growth_score"), r.get("revisions_score"), r.get("short_interest_score"),
            r.get("insider_score"), r.get("institutional_score"), r.get("composite_raw"),
            r.get("composite_score"),
            r.get("sector"), r.get("regime"), r.get("vix"),
            int(r.get("is_long_candidate", 0)), int(r.get("is_short_candidate", 0)),
        ))

    conn.executemany("""
        INSERT OR REPLACE INTO scores
        (ticker, date, momentum_score, value_score, quality_score, growth_score,
         revisions_score, short_interest_score, insider_score, institutional_score,
         composite_raw, composite_score, sector, regime, vix, is_long_candidate, is_short_candidate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS Layer 2 — Scoring Engine")
    parser.add_argument("--ticker", help="Score a single ticker only")
    parser.add_argument("--tickers", nargs="+", help="Score specific tickers")
    args = parser.parse_args()

    _setup_logging()
    logger = logging.getLogger("run_scoring")
    start_time = time.time()

    logger.info("=" * 60)
    logger.info("JARVIS Layer 2 — Scoring Engine starting")
    logger.info("=" * 60)

    tickers_filter = None
    if args.ticker:
        tickers_filter = [args.ticker]
    elif args.tickers:
        tickers_filter = args.tickers

    # Load universe
    logger.info("[1/7] Loading universe...")
    universe = _load_universe(tickers_filter)
    logger.info("Universe: %d tickers", len(universe))

    if universe.empty:
        logger.error("No tickers found — aborting")
        sys.exit(1)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Factor 1: Momentum
    logger.info("[2/7] Computing momentum scores...")
    from factors.momentum import calculate_all as calc_momentum
    mom = calc_momentum(universe)
    logger.info("Momentum complete: %d tickers", len(mom))

    # Factor 2: Value
    logger.info("[3/7] Computing value scores...")
    from factors.value import calculate_all as calc_value
    val = calc_value(universe)
    logger.info("Value complete: %d tickers", len(val))

    # Factor 3: Quality
    logger.info("[4/7] Computing quality scores...")
    from factors.quality import calculate_all as calc_quality
    qual = calc_quality(universe)
    logger.info("Quality complete: %d tickers", len(qual))

    # Factor 4: Growth
    logger.info("[4/7] Computing growth scores...")
    from factors.growth import calculate_all as calc_growth
    grow = calc_growth(universe)
    logger.info("Growth complete: %d tickers", len(grow))

    # Factor 5: Revisions
    logger.info("[5/7] Computing revision scores...")
    from factors.revisions import calculate_all as calc_revisions
    rev = calc_revisions(universe)
    logger.info("Revisions complete: %d tickers", len(rev))

    # Factor 6: Short Interest
    logger.info("[5/7] Computing short interest scores...")
    from factors.short_interest import calculate_all as calc_short
    si = calc_short(universe)
    logger.info("Short interest complete: %d tickers", len(si))

    # Factor 7: Insider
    logger.info("[6/7] Computing insider scores...")
    from factors.insider import calculate_all as calc_insider
    ins = calc_insider(universe)
    logger.info("Insider complete: %d tickers", len(ins))

    # Factor 8: Institutional
    logger.info("[6/7] Computing institutional scores...")
    from factors.institutional import calculate_all as calc_inst
    inst = calc_inst(universe)
    logger.info("Institutional complete: %d tickers", len(inst))

    # Merge all factor scores
    logger.info("[7/7] Computing composite scores...")
    scores = universe[["ticker", "sector"]].copy()
    for factor_df, col in [
        (mom, "momentum_score"), (val, "value_score"), (qual, "quality_score"),
        (grow, "growth_score"), (rev, "revisions_score"), (si, "short_interest_score"),
        (ins, "insider_score"), (inst, "institutional_score"),
    ]:
        scores = scores.merge(factor_df[["ticker", col]], on="ticker", how="left")

    from factors.composite import calculate_composite
    scores = calculate_composite(scores)

    # Save to DB
    _save_scores(scores, date_str)
    logger.info("Scores saved to DB: %d rows", len(scores))

    # Export CSV
    os.makedirs("output", exist_ok=True)
    csv_path = "output/scored_universe_latest.csv"
    scores.to_csv(csv_path, index=False)
    logger.info("CSV exported: %s", csv_path)

    # Crowding detection
    from factors.crowding import detect_crowding
    crowding_alerts = detect_crowding(scores)
    logger.info("Crowding alerts: %d", len(crowding_alerts))

    # Summary
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    longs = (
        scores[scores["is_long_candidate"] == 1]
        .sort_values("composite_raw", ascending=False)["ticker"]
        .tolist()
    )
    shorts = (
        scores[scores["is_short_candidate"] == 1]
        .sort_values("composite_raw", ascending=True)["ticker"]
        .tolist()
    )
    regime = scores["regime"].iloc[0] if not scores.empty else "unknown"
    vix_val = scores["vix"].iloc[0] if not scores.empty else "N/A"

    print()
    print("=" * 60)
    print(f"Layer 2 complete: {len(scores)} tickers scored")
    print(f"Top long candidates:  {', '.join(longs[:10])}{'...' if len(longs) > 10 else ''}")
    print(f"Top short candidates: {', '.join(shorts[:10])}{'...' if len(shorts) > 10 else ''}")
    print(f"VIX regime: {regime} (VIX={vix_val:.2f})" if isinstance(vix_val, float) else f"VIX regime: {regime}")
    print(f"Crowding alerts: {len(crowding_alerts)}")
    print(f"Runtime: {minutes}m {seconds}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
