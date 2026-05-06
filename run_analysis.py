#!/usr/bin/env python3
"""
Layer 3 Entry Point — AI Analysis
Usage:
  python run_analysis.py --estimate-cost          # Estimate cost only
  python run_analysis.py --ticker AAPL            # Single ticker
  python run_analysis.py --sector Technology       # Full sector
  python run_analysis.py --candidates             # Top 20 long + 20 short
  python run_analysis.py                          # Full run (candidates)
"""
import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(_ENV_PATH)

sys.path.insert(0, os.path.dirname(__file__))

from analysis.api_client import APIClient, DEFAULT_MODEL, DEFAULT_PROVIDER
from analysis.cost_tracker import CostTracker
from analysis import cache as analysis_cache
from analysis import (
    earnings_analyzer,
    filing_analyzer,
    risk_analyzer,
    insider_analyzer,
    sector_analysis,
    report_generator,
)
from analysis.combined import (
    compute_combined_score,
    load_latest_scores,
    save_combined_scores,
    get_candidates,
    _signal_from_score,
)


def _setup_logging() -> None:
    os.makedirs("output/logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("output/logs/analysis.log", mode="a", encoding="utf-8"),
        ],
        force=True,
    )


def _estimate_cost(tickers: list[str]) -> None:
    """Print cost estimate without making API calls."""
    import sqlite3
    db_path = os.path.join(os.path.dirname(__file__), "cache", "jarvis.db")

    # Estimate per-analyzer token usage
    earnings_avg_tokens = 15_000    # ~60K chars transcript, cached system
    filing_avg_tokens = 4_000       # fundamental text
    risk_avg_tokens = 20_000        # 80K chars filing text, cached system
    insider_avg_tokens = 2_000      # transaction list

    output_tokens_per_call = 300

    # Price per token
    input_price = 3.00 / 1_000_000
    output_price = 15.00 / 1_000_000
    cache_write_price = 3.75 / 1_000_000

    print(f"\nCost Estimate for {len(tickers)} tickers")
    print("=" * 50)

    # Check data availability
    try:
        conn = sqlite3.connect(db_path)
        has_transcripts = conn.execute(
            f"SELECT COUNT(DISTINCT ticker) FROM earnings_transcripts WHERE ticker IN ({','.join('?'*len(tickers))})",
            tickers
        ).fetchone()[0]
        has_fundamentals = conn.execute(
            f"SELECT COUNT(DISTINCT ticker) FROM fundamentals WHERE ticker IN ({','.join('?'*len(tickers))})",
            tickers
        ).fetchone()[0]
        has_filings = conn.execute(
            f"SELECT COUNT(DISTINCT ticker) FROM filings WHERE form_type='10-K' AND ticker IN ({','.join('?'*len(tickers))})",
            tickers
        ).fetchone()[0]
        has_insider = conn.execute(
            f"SELECT COUNT(DISTINCT ticker) FROM insider_transactions WHERE ticker IN ({','.join('?'*len(tickers))})",
            tickers
        ).fetchone()[0]
        conn.close()
    except Exception as e:
        has_transcripts = has_fundamentals = has_filings = has_insider = 0
        print(f"  (DB not populated yet — using worst-case estimates)")

    n = len(tickers)
    total_cost = 0.0
    rows = []

    def analyzer_cost(name, coverage, avg_tokens):
        n_with_data = min(coverage, n)
        cost = n_with_data * (avg_tokens * input_price + output_tokens_per_call * output_price)
        # Prompt caching saves ~80% on system prompt after first call
        cache_saving = cost * 0.15  # rough 15% saving from caching
        net = cost - cache_saving
        return name, n_with_data, net

    for row in [
        analyzer_cost("Earnings Call", has_transcripts, earnings_avg_tokens),
        analyzer_cost("Filing/Fundamentals", has_fundamentals, filing_avg_tokens),
        analyzer_cost("Risk Factors", has_filings, risk_avg_tokens),
        analyzer_cost("Insider Analysis", has_insider, insider_avg_tokens),
    ]:
        name, coverage, cost = row
        total_cost += cost
        rows.append((name, coverage, cost))

    for name, coverage, cost in rows:
        print(f"  {name:25s}: {coverage}/{n} tickers — ~${cost:.2f}")

    print("-" * 50)
    print(f"  {'Estimated Total':25s}: ~${total_cost:.2f}")
    print(f"  Model: {DEFAULT_MODEL}")
    print(f"  Note: Cache hits reduce cost significantly on re-runs")
    print("=" * 50)


def _analyze_ticker(
    ticker: str,
    scores_row: dict,
    client: APIClient,
    cost_tracker: CostTracker,
    force: bool = False,
) -> dict:
    """Run all analyzers for a single ticker. Returns results dict."""
    logger = logging.getLogger("analyze_ticker")
    results = {}

    # Run analyzers, skipping failures gracefully
    for name, fn in [
        ("earnings", earnings_analyzer.analyze),
        ("filing", filing_analyzer.analyze),
        ("risk", risk_analyzer.analyze),
        ("insider", insider_analyzer.analyze),
    ]:
        if cost_tracker.ceiling_hit:
            logger.warning("[%s] Cost ceiling hit — skipping %s analyzer", ticker, name)
            break
        try:
            results[name] = fn(ticker, client, force=force)
        except Exception as e:
            logger.error("[%s] %s analyzer error: %s", ticker, name, e)
            results[name] = None

    # Compute individual scores
    claude_scores = {
        "earnings": earnings_analyzer.score_from_result(results.get("earnings")),
        "filing": filing_analyzer.score_from_result(results.get("filing")),
        "risk": risk_analyzer.score_from_result(results.get("risk")),
        "insider": insider_analyzer.score_from_result(results.get("insider")),
    }

    composite_score = scores_row.get("composite_score")
    claude_avg, combined = compute_combined_score(composite_score, claude_scores)
    signal = _signal_from_score(combined) if combined is not None else None

    return {
        "ticker": ticker,
        "analyses": results,
        "claude_scores": claude_scores,
        "claude_score": round(claude_avg, 1) if claude_avg is not None else None,
        "composite_score": composite_score,
        "combined_score": combined,
        "signal": signal,
        "sector": scores_row.get("sector"),
        "date": scores_row.get("date"),
        "scores_row": scores_row,
    }


def _get_sector_tickers(sector: str) -> list[str]:
    """Get all tickers for a given sector from scores table."""
    rows = load_latest_scores()
    return [r["ticker"] for r in rows if r.get("sector", "").lower() == sector.lower()]


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS Layer 3 — AI Analysis")
    parser.add_argument("--estimate-cost", action="store_true", help="Estimate cost only")
    parser.add_argument("--ticker", help="Analyze a single ticker")
    parser.add_argument("--sector", help="Analyze all tickers in a sector")
    parser.add_argument("--candidates", action="store_true", help="Analyze top 20 long + 20 short")
    parser.add_argument("--force", action="store_true", help="Bypass cache")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER, choices=["openrouter", "codex"],
                        help=f"AI provider (default: {DEFAULT_PROVIDER})")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"AI model (default: {DEFAULT_MODEL})")
    parser.add_argument("--cost-ceiling", type=float, default=10.0, help="Hard cost ceiling in $ (default: $10)")
    parser.add_argument("--no-reports", action="store_true", help="Skip markdown report generation")
    args = parser.parse_args()

    _setup_logging()
    logger = logging.getLogger("run_analysis")
    start_time = time.time()

    logger.info("=" * 60)
    logger.info("JARVIS Layer 3 — AI Analysis starting")
    logger.info("Provider: %s", args.provider)
    logger.info("Model: %s", args.model)
    logger.info("=" * 60)

    # Determine which tickers to analyze
    if args.ticker:
        tickers = [args.ticker.upper()]
        logger.info("Mode: single ticker — %s", tickers[0])
    elif args.sector:
        tickers = _get_sector_tickers(args.sector)
        if not tickers:
            print(f"No tickers found for sector: {args.sector}")
            sys.exit(1)
        logger.info("Mode: sector — %s (%d tickers)", args.sector, len(tickers))
    elif args.candidates or True:  # default: candidates mode
        longs, shorts = get_candidates(n_long=20, n_short=20)
        tickers = list(dict.fromkeys(longs + shorts))  # deduplicate, preserve order
        logger.info("Mode: candidates — %d longs + %d shorts = %d unique tickers",
                    len(longs), len(shorts), len(tickers))
        if not tickers:
            logger.warning("No candidates found in scores table. Run run_scoring.py first.")

    # Cost estimate mode
    if args.estimate_cost:
        _estimate_cost(tickers)
        return

    if not tickers:
        logger.error("No tickers to analyze")
        sys.exit(1)

    # Load L2 scores for these tickers
    all_scores = load_latest_scores(tickers)
    scores_by_ticker = {r["ticker"]: r for r in all_scores}

    # For tickers missing from scores table, use empty dict
    for t in tickers:
        if t not in scores_by_ticker:
            scores_by_ticker[t] = {"ticker": t, "sector": "Unknown", "composite_score": None}

    # Determine long/short sets for report generation
    longs_set: set = set()
    shorts_set: set = set()
    if args.ticker:
        row = scores_by_ticker.get(args.ticker.upper(), {})
        if row.get("is_long_candidate"):
            longs_set.add(args.ticker.upper())
        elif row.get("is_short_candidate"):
            shorts_set.add(args.ticker.upper())
        else:
            longs_set.add(args.ticker.upper())  # default to long for single ticker
    else:
        longs, shorts = get_candidates()
        longs_set = set(longs)
        shorts_set = set(shorts)

    # Set up API client and cost tracker
    cost_tracker = CostTracker(ceiling=args.cost_ceiling)
    try:
        client = APIClient(cost_tracker=cost_tracker, model=args.model, provider=args.provider)
    except RuntimeError as e:
        logger.error("API client error: %s", e)
        sys.exit(1)

    # Create output directory for reports
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = report_generator.create_output_dir(ts)
    logger.info("Reports will be saved to: %s", output_dir)

    # Analyze each ticker
    ticker_results: dict[str, dict] = {}
    cache_hits = 0
    cache_misses = 0
    reports_generated = 0
    errors = 0

    logger.info("Analyzing %d tickers...", len(tickers))

    for ticker in tickers:
        if cost_tracker.ceiling_hit:
            logger.warning("Cost ceiling hit — stopping early at %s", ticker)
            break

        logger.info("[%s] Starting analysis...", ticker)
        scores_row = scores_by_ticker.get(ticker, {"ticker": ticker})

        try:
            result = _analyze_ticker(
                ticker=ticker,
                scores_row=scores_row,
                client=client,
                cost_tracker=cost_tracker,
                force=args.force,
            )
            ticker_results[ticker] = result

            # Count cache hits/misses (rough: if cost didn't change much, it was cached)
            any_analysis = any(v is not None for v in result["analyses"].values())
            if any_analysis:
                cache_misses += 1
            else:
                cache_hits += 1

            # Generate markdown report
            if not args.no_reports:
                try:
                    filepath = report_generator.generate_report(
                        ticker=ticker,
                        scores_row=scores_row,
                        analyses=result["analyses"],
                        claude_score=result["claude_score"],
                        combined_score=result["combined_score"],
                        signal=result["signal"],
                        is_long=ticker in longs_set,
                        output_dir=output_dir,
                    )
                    reports_generated += 1
                    logger.debug("[%s] Report: %s", ticker, filepath)
                except Exception as e:
                    logger.error("[%s] Report generation failed: %s", ticker, e)

        except Exception as e:
            logger.error("[%s] Analysis failed: %s", ticker, e)
            errors += 1
            ticker_results[ticker] = {"ticker": ticker, "error": str(e)}

    # Save combined scores to DB
    updates = []
    for ticker, result in ticker_results.items():
        if "error" in result:
            continue
        date = scores_by_ticker.get(ticker, {}).get("date")
        if not date:
            continue
        updates.append({
            "ticker": ticker,
            "date": date,
            "claude_score": result.get("claude_score"),
            "combined_score": result.get("combined_score"),
            "signal": result.get("signal"),
        })

    saved = save_combined_scores(updates)
    logger.info("Combined scores saved to DB: %d rows", saved)

    # Sector analysis
    sector_summaries = sector_analysis.build_sector_summary(ticker_results, None)

    # Print summary
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    cost_summary = cost_tracker.summary()

    print()
    print("=" * 60)
    print(f"Layer 3 complete: {len(ticker_results)} tickers analyzed")
    print(
        f"AI calls: {cost_summary['api_calls']} | "
        f"API cost: ${cost_summary['total_cost']:.4f} | Provider: {args.provider}"
    )
    print(f"Cache hits: {cache_hits} | Misses: {cache_misses}")
    print(f"Reports generated: {reports_generated} → {output_dir}")
    print(f"Errors: {errors}")
    print(f"Runtime: {minutes}m {seconds}s")
    print()

    # Top signals
    strong_buys = [t for t, r in ticker_results.items() if r.get("signal") == "STRONG_BUY"]
    strong_sells = [t for t, r in ticker_results.items() if r.get("signal") == "STRONG_SELL"]
    if strong_buys:
        print(f"STRONG BUY:  {', '.join(strong_buys)}")
    if strong_sells:
        print(f"STRONG SELL: {', '.join(strong_sells)}")

    # Sector summary
    if sector_summaries:
        print()
        print(sector_analysis.format_sector_report(sector_summaries))

    print("=" * 60)

    logger.info(
        "Layer 3 complete: %d tickers, %d AI calls, $%.4f API cost, %d reports in %dm %ds",
        len(ticker_results), cost_summary["api_calls"], cost_summary["total_cost"],
        reports_generated, minutes, seconds,
    )


if __name__ == "__main__":
    main()
