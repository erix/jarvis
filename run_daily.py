"""Daily automation script — run at 17:15 UTC on weekdays via cron/launchd.

Schedule (example crontab):
  15 17 * * 1-5 cd /home/erix/Projects/jarvis && .venv/bin/python run_daily.py

Does NOT auto-rebalance the portfolio — human approval is always required.
"""
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("run_daily")

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable


def run(cmd: list[str], label: str) -> bool:
    logger.info("--- %s ---", label)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        logger.error("%s exited with code %d", label, result.returncode)
        return False
    return True


def main():
    now = datetime.now(timezone.utc)
    logger.info("Daily automation started at %s UTC", now.isoformat())

    # Step 1: Incremental data refresh (~10 min, skip slow filings + 13f)
    run(
        [PYTHON, "run_data.py", "--no-filings", "--no-13f"],
        "Data refresh (incremental)",
    )

    # Step 2: Rescore all tickers
    run(
        [PYTHON, "run_scoring.py"],
        "Factor scoring",
    )

    # Step 3: Generate daily reports (attribution, tear sheet, LP letter)
    run(
        [PYTHON, "run_reporting.py"],
        "Daily reporting",
    )

    # Portfolio rebalance is NOT automated — run run_portfolio.py manually
    logger.info("Daily automation complete. Rebalance requires manual approval.")
    logger.info("To rebalance: python run_portfolio.py")
    logger.info("To execute:   python run_execution.py --dry-run")


if __name__ == "__main__":
    main()
