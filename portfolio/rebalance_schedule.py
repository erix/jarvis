"""Rebalance schedule advisor: checks earnings, FOMC dates, and options expiry."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

# Known 2025-2026 FOMC meeting dates (final day of each meeting)
FOMC_DATES = {
    date(2025, 1, 29),
    date(2025, 3, 19),
    date(2025, 5, 7),
    date(2025, 6, 18),
    date(2025, 7, 30),
    date(2025, 9, 17),
    date(2025, 10, 29),
    date(2025, 12, 10),
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
}

FOMC_BLACKOUT_DAYS = 1  # avoid rebalancing on FOMC day and day after


def _third_friday(year: int, month: int) -> date:
    """Return the third Friday of a given month (monthly options expiry)."""
    c = calendar.monthcalendar(year, month)
    fridays = [week[calendar.FRIDAY] for week in c if week[calendar.FRIDAY] != 0]
    return date(year, month, fridays[2])


def _is_near_opex(today: date, window: int = 2) -> bool:
    opex = _third_friday(today.year, today.month)
    return abs((today - opex).days) <= window


def _is_near_fomc(today: date, window: int = FOMC_BLACKOUT_DAYS) -> bool:
    for d in FOMC_DATES:
        if 0 <= (d - today).days <= window:
            return True
    return False


def _earnings_warnings(target_tickers: list[str], today: date, window: int = 2) -> list[str]:
    """Check if any target ticker has earnings within ±window days.

    Uses the earnings_transcripts table if available; purely advisory.
    """
    import sqlite3
    import os

    db_path = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
    warnings = []
    try:
        conn = sqlite3.connect(db_path)
        low = (today - timedelta(days=window)).isoformat()
        high = (today + timedelta(days=window)).isoformat()
        placeholders = ",".join("?" * len(target_tickers))
        rows = conn.execute(
            f"""
            SELECT DISTINCT ticker FROM earnings_transcripts
            WHERE ticker IN ({placeholders})
              AND date BETWEEN ? AND ?
            """,
            target_tickers + [low, high],
        ).fetchall()
        conn.close()
        for (ticker,) in rows:
            warnings.append(f"{ticker}: earnings within {window} days of rebalance")
    except Exception:
        pass
    return warnings


def get_rebalance_advice(
    target_tickers: list[str] | None = None,
    today: date | None = None,
) -> dict:
    """Return rebalance advisory.

    Returns {"proceed": bool, "warnings": [str]}
    proceed is True unless a hard block exists (none currently — all advisories).
    """
    if today is None:
        today = date.today()

    warnings = []

    if _is_near_fomc(today):
        warnings.append(f"FOMC meeting on or within {FOMC_BLACKOUT_DAYS} day(s) — consider delaying")

    if _is_near_opex(today):
        opex = _third_friday(today.year, today.month)
        warnings.append(f"Monthly options expiration {opex.isoformat()} — elevated volatility expected")

    if target_tickers:
        warnings.extend(_earnings_warnings(target_tickers, today))

    return {"proceed": True, "warnings": warnings}
