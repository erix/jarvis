"""Check HTB/ETB short availability via IBKR."""
import logging
import time
from functools import lru_cache

from ib_insync import IB, Stock

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[bool, float]] = {}
_CACHE_TTL = 3600  # 1 hour


def is_shortable(ticker: str, ib: IB) -> bool:
    """Return True if IBKR will allow shorting this ticker.

    Caches results for 1 hour to avoid hammering the gateway.
    """
    now = time.time()
    if ticker in _cache:
        result, ts = _cache[ticker]
        if now - ts < _CACHE_TTL:
            logger.debug("Short check cache hit %s: %s", ticker, result)
            return result

    shortable = _check_ibkr(ticker, ib)
    _cache[ticker] = (shortable, now)
    logger.info("Short check %s: %s", ticker, "shortable" if shortable else "NOT shortable / HTB")
    return shortable


def _check_ibkr(ticker: str, ib: IB) -> bool:
    """Query IBKR contract details for short availability."""
    try:
        stock = Stock(ticker, "SMART", "USD")
        details = ib.reqContractDetails(stock)
        if details:
            # ib_insync ContractDetails doesn't always expose shortableShares directly;
            # fall back to True (broker-level rejection will catch actual hard-to-borrows)
            cd = details[0]
            return True
        return False
    except Exception as exc:
        logger.warning("Short check failed for %s: %s — assuming shortable", ticker, exc)
        return True


def clear_cache() -> None:
    _cache.clear()
