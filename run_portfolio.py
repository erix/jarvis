#!/usr/bin/env python3
"""
JARVIS Layer 4 — Portfolio Construction Entry Point

Usage:
  python run_portfolio.py --whatif                    # Dry run, show trades only
  python run_portfolio.py --rebalance                 # Generate target + save as pending
  python run_portfolio.py --current                   # Show current positions
  python run_portfolio.py --optimize-method mvo       # Use MVO (default)
  python run_portfolio.py --optimize-method conviction # Use conviction-tilt
"""
import argparse
import logging
import os
import sqlite3
import sys

import pandas as pd
import yaml

sys.path.insert(0, os.path.dirname(__file__))

DB_PATH = os.path.join(os.path.dirname(__file__), "cache", "jarvis.db")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def _setup_logging() -> None:
    os.makedirs("output/logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("output/logs/portfolio.log", mode="a", encoding="utf-8"),
        ],
        force=True,
    )


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("portfolio", {})


def _load_scores() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT s.ticker, s.composite_score, s.momentum_score, s.value_score,
               s.quality_score, s.growth_score, s.revisions_score,
               s.short_interest_score, s.insider_score, s.institutional_score,
               s.sector, s.is_long_candidate, s.is_short_candidate
        FROM scores s
        INNER JOIN (
            SELECT ticker, MAX(date) as max_date FROM scores GROUP BY ticker
        ) latest ON s.ticker=latest.ticker AND s.date=latest.max_date
        """,
        conn,
    )
    conn.close()
    return df


def _show_current(logger: logging.Logger) -> None:
    from portfolio.state import get_current_positions, get_portfolio_summary

    positions = get_current_positions()
    summary = get_portfolio_summary()

    if not positions:
        print("\nNo active positions.\n")
        return

    print()
    print("=" * 60)
    print("Current Portfolio")
    print("=" * 60)
    print(f"Positions: {summary['positions']} ({summary['longs']} long / {summary['shorts']} short)")
    print(f"Total P&L: ${summary['total_pnl']:,.2f}")
    print()
    print(f"{'Ticker':<8} {'Shares':>8} {'Entry':>8} {'Current':>8} {'P&L':>10} {'Status':<10}")
    print("-" * 60)
    for p in sorted(positions, key=lambda x: -x["shares"]):
        pnl = p.get("pnl") or 0
        entry = p.get("entry_price") or 0
        cur = p.get("current_price") or 0
        print(
            f"{p['ticker']:<8} {p['shares']:>8.1f} {entry:>8.2f} {cur:>8.2f} "
            f"${pnl:>9,.2f} {p.get('approval_status',''):<10}"
        )
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS Layer 4 — Portfolio Construction")
    parser.add_argument("--rebalance", action="store_true", help="Generate target and save as pending")
    parser.add_argument("--whatif", action="store_true", help="Dry run — show trades without saving")
    parser.add_argument("--current", action="store_true", help="Show current positions")
    parser.add_argument(
        "--optimize-method",
        choices=["mvo", "conviction"],
        default="mvo",
        help="Optimization method (default: mvo)",
    )
    args = parser.parse_args()

    _setup_logging()
    logger = logging.getLogger("run_portfolio")

    if args.current:
        _show_current(logger)
        return

    if not args.rebalance and not args.whatif:
        parser.print_help()
        return

    cfg = _load_config()
    logger.info("Config loaded: %s", cfg)

    # 1. Load scored universe
    logger.info("Loading scores...")
    scores_df = _load_scores()
    logger.info("Scores: %d tickers", len(scores_df))

    if scores_df.empty:
        logger.error("No scores found — run run_scoring.py first")
        sys.exit(1)

    # 2. Load current positions
    from portfolio.state import get_current_positions, ensure_tables
    ensure_tables()
    current_positions = get_current_positions()
    logger.info("Current positions: %d", len(current_positions))

    # 3. Rebalance schedule advisory
    from portfolio.rebalance_schedule import get_rebalance_advice
    target_tickers_hint = scores_df[scores_df["is_long_candidate"] == 1]["ticker"].tolist()[:40]
    advice = get_rebalance_advice(target_tickers=target_tickers_hint)
    for w in advice.get("warnings", []):
        logger.warning("Schedule advisory: %s", w)

    # 4. Optimize
    method = args.optimize_method
    logger.info("Running %s optimizer...", method.upper())

    if method == "mvo":
        from portfolio.mvo_optimizer import mvo_optimize
        opt_result = mvo_optimize(scores_df, cfg, current_positions)
    else:
        from portfolio.optimizer import conviction_tilt
        opt_result = conviction_tilt(scores_df, cfg, current_positions)

    target_weights = opt_result["target_weights"]
    prices = opt_result.get("prices", {})
    adv = opt_result.get("adv", {})

    if not target_weights:
        logger.error("Optimizer returned empty portfolio. Warnings: %s", opt_result.get("warnings"))
        sys.exit(1)

    for w in opt_result.get("warnings", []):
        logger.warning("Optimizer: %s", w)

    logger.info(
        "Target: %d long / %d short",
        len(opt_result.get("long_tickers", [])),
        len(opt_result.get("short_tickers", [])),
    )

    # 5. Factor exposures
    from portfolio.factor_exposure import calculate_exposure
    exposure = calculate_exposure(target_weights, scores_df)

    # 6. Generate trades
    from portfolio.rebalance import generate_trades, whatif_report
    aum = cfg.get("aum", 10_000_000)
    turnover_budget = cfg.get("turnover_budget_pct", 30.0)

    trade_result = generate_trades(
        current_positions=current_positions,
        target_weights=target_weights,
        prices=prices,
        adv=adv,
        aum=aum,
        turnover_budget_pct=turnover_budget,
    )

    # 7. What-if: print and exit
    if args.whatif:
        report = whatif_report(trade_result, opt_result, exposure, advice)
        print(report)
        return

    # 8. Rebalance: save positions as pending
    from portfolio.state import add_position
    sectors_map = dict(zip(scores_df["ticker"], scores_df["sector"]))
    betas = opt_result.get("betas", {})

    saved = 0
    for ticker, weight in target_weights.items():
        price = prices.get(ticker, 0.0)
        if price <= 0:
            continue
        shares = (weight * aum) / price
        sector = sectors_map.get(ticker)
        beta = betas.get(ticker)
        add_position(
            ticker=ticker,
            shares=round(shares, 4),
            price=price,
            sector=sector,
            beta=beta,
            approval_status="pending",
            reason=f"{method}_rebalance",
        )
        saved += 1

    logger.info("Saved %d pending positions", saved)

    # 9. Print summary
    longs = opt_result.get("long_tickers", [])
    shorts = opt_result.get("short_tickers", [])
    weights = opt_result.get("target_weights", {})
    gross = sum(abs(w) for w in weights.values()) * 100
    net = sum(weights.values()) * 100

    # Sector concentration
    sector_weights: dict[str, float] = {}
    for ticker, w in weights.items():
        sec = sectors_map.get(ticker, "Unknown")
        sector_weights[sec] = sector_weights.get(sec, 0.0) + abs(w)
    max_sector = max(sector_weights.values()) * 100 if sector_weights else 0.0

    print()
    print("=" * 60)
    print("Portfolio Rebalance")
    print("=" * 60)
    print(f"Positions: {len(longs)} long / {len(shorts)} short")
    print(f"Gross exposure: {gross:.1f}%  Net: {net:.1f}%")
    print(f"Sector concentration: max {max_sector:.1f}%")
    print(f"Net beta: {opt_result.get('portfolio_beta', 0):.3f}")
    print(f"Expected return: {opt_result.get('expected_return', 0)*100:.1f}%")
    ev = opt_result.get("expected_volatility", 0)
    if ev:
        print(f"Expected vol:    {ev*100:.1f}%")
    print(f"Trades: {len(trade_result.get('trades', {}))}")
    print(f"Est. transaction cost: ${trade_result.get('total_cost', 0):,.0f}")
    print(f"Turnover: {trade_result.get('turnover_pct', 0):.1f}%")
    if advice.get("warnings"):
        print("\nSchedule Warnings:")
        for w in advice["warnings"]:
            print(f"  ! {w}")
    print(f"\nPositions saved as 'pending' (Layer 6 executes trades)")
    print("=" * 60)


if __name__ == "__main__":
    main()
