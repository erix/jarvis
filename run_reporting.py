"""Layer 7 reporting entry point.

Usage:
  python run_reporting.py --date 2026-05-04   # Full daily report suite
  python run_reporting.py --weekly             # Weekly commentary only
  python run_reporting.py --letter             # LP letter only
"""
import argparse
import logging
import os
import sys
from datetime import date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("run_reporting")

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def run_daily(target_date: date):
    logger.info("=== Running daily reports for %s ===", target_date)

    # P&L attribution
    try:
        from reporting.pnl_attribution import run_attribution
        df = run_attribution()
        if df is not None and not df.empty:
            logger.info("Attribution: %d rows written to output/daily_attribution.csv", len(df))
        else:
            logger.warning("Attribution: no portfolio history available yet")
    except Exception as exc:
        logger.error("Attribution failed: %s", exc)

    # Win/loss
    try:
        from reporting.win_loss import analyze_win_loss
        wl = analyze_win_loss()
        logger.info(
            "Win/loss: win_rate=%.1f%%, pl_ratio=%.2f, trades=%d",
            wl.get("win_rate", 0), wl.get("pl_ratio", 0), wl.get("total_trades", 0),
        )
    except Exception as exc:
        logger.error("Win/loss failed: %s", exc)

    # Sector alpha
    try:
        from reporting.sector_alpha import compute_sector_alpha
        df = compute_sector_alpha()
        if df is not None and not df.empty:
            logger.info("Sector alpha: %d sectors computed", len(df))
    except Exception as exc:
        logger.error("Sector alpha failed: %s", exc)

    # Turnover
    try:
        from reporting.turnover import compute_turnover
        t = compute_turnover()
        logger.info(
            "Turnover: 30d=%.1f%%, ann=%.0f%%, tax_liability=$%.0f",
            t.get("turnover_30d_pct", 0), t.get("turnover_annualized_pct", 0),
            t.get("tax_liability_usd", 0),
        )
    except Exception as exc:
        logger.error("Turnover failed: %s", exc)

    # Tear sheet
    try:
        from reporting.tear_sheet import generate_tear_sheet
        path = generate_tear_sheet(end_date=target_date)
        logger.info("Tear sheet: %s", path)
    except Exception as exc:
        logger.error("Tear sheet failed: %s", exc)

    # LP letter
    try:
        from reporting.lp_letter import generate_lp_letter
        path = generate_lp_letter(target_date=target_date)
        logger.info("LP letter: %s", path)
    except Exception as exc:
        logger.error("LP letter failed: %s", exc)

    logger.info("=== Daily reports complete ===")


def run_weekly():
    logger.info("=== Generating weekly commentary ===")
    try:
        from reporting.weekly_commentary import generate_weekly_commentary
        path = generate_weekly_commentary(force=True)
        logger.info("Weekly commentary: %s", path)
    except Exception as exc:
        logger.error("Weekly commentary failed: %s", exc)


def run_letter(target_date: date):
    logger.info("=== Generating LP letter for %s ===", target_date)
    try:
        from reporting.lp_letter import generate_lp_letter
        path = generate_lp_letter(target_date=target_date, force=True)
        logger.info("LP letter: %s", path)
        print(f"\nLetter saved to: {path}\n")
    except Exception as exc:
        logger.error("LP letter failed: %s", exc)


def main():
    parser = argparse.ArgumentParser(description="JARVIS Reporting Engine")
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="Report date (YYYY-MM-DD), default today")
    parser.add_argument("--weekly", action="store_true",
                        help="Generate weekly commentary only")
    parser.add_argument("--letter", action="store_true",
                        help="Generate LP letter only")
    args = parser.parse_args()

    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        logger.error("Invalid date format: %s (expected YYYY-MM-DD)", args.date)
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, "letters"), exist_ok=True)

    if args.weekly:
        run_weekly()
    elif args.letter:
        run_letter(target_date)
    else:
        run_daily(target_date)


if __name__ == "__main__":
    main()
