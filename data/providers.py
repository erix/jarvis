"""Provider abstraction layer: FMP → Polygon → yfinance fallback."""
import logging
import os
from typing import Optional, Dict, Any

import requests
import yfinance as yf

logger = logging.getLogger(__name__)


class DataProvider:
    """Abstract base for data providers."""

    name: str = "base"

    def get_price(self, ticker: str) -> Optional[Dict]:
        raise NotImplementedError

    def get_fundamentals(self, ticker: str) -> Optional[Dict]:
        raise NotImplementedError

    def get_short_interest(self, ticker: str) -> Optional[Dict]:
        raise NotImplementedError


class FMPProvider(DataProvider):
    name = "fmp"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base = "https://financialmodelingprep.com/api/v3"

    def _get(self, path: str, params: Optional[Dict] = None) -> Optional[Any]:
        p = {"apikey": self.api_key, **(params or {})}
        try:
            resp = requests.get(f"{self.base}{path}", params=p, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug("FMP request failed for %s: %s", path, e)
            return None

    def get_price(self, ticker: str) -> Optional[Dict]:
        data = self._get(f"/quote/{ticker}")
        if data and isinstance(data, list) and data:
            q = data[0]
            return {
                "ticker": ticker,
                "price": q.get("price"),
                "volume": q.get("volume"),
                "source": "fmp",
            }
        return None

    def get_fundamentals(self, ticker: str) -> Optional[Dict]:
        data = self._get(f"/income-statement/{ticker}", {"limit": 1})
        if data and isinstance(data, list) and data:
            return {"source": "fmp", "income": data[0]}
        return None

    def get_short_interest(self, ticker: str) -> Optional[Dict]:
        data = self._get(f"/v4/short-interest", {"symbol": ticker})
        if data and isinstance(data, list) and data:
            return {"source": "fmp", **data[0]}
        return None


class PolygonProvider(DataProvider):
    name = "polygon"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base = "https://api.polygon.io/v2"

    def _get(self, path: str, params: Optional[Dict] = None) -> Optional[Any]:
        p = {"apiKey": self.api_key, **(params or {})}
        try:
            resp = requests.get(f"{self.base}{path}", params=p, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug("Polygon request failed for %s: %s", path, e)
            return None

    def get_price(self, ticker: str) -> Optional[Dict]:
        data = self._get(f"/last/trade/{ticker}")
        if data and data.get("status") == "OK":
            result = data.get("results", {})
            return {
                "ticker": ticker,
                "price": result.get("p"),
                "volume": result.get("s"),
                "source": "polygon",
            }
        return None

    def get_fundamentals(self, ticker: str) -> Optional[Dict]:
        return None  # Polygon financials endpoint requires separate plan

    def get_short_interest(self, ticker: str) -> Optional[Dict]:
        return None  # Not available on standard Polygon plans


class YFinanceProvider(DataProvider):
    name = "yfinance"

    def get_price(self, ticker: str) -> Optional[Dict]:
        try:
            t = yf.Ticker(ticker)
            info = t.fast_info
            price = getattr(info, "last_price", None)
            volume = getattr(info, "three_month_average_volume", None)
            return {"ticker": ticker, "price": price, "volume": volume, "source": "yfinance"}
        except Exception as e:
            logger.debug("yfinance price failed for %s: %s", ticker, e)
            return None

    def get_fundamentals(self, ticker: str) -> Optional[Dict]:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            return {"source": "yfinance", "market_cap": info.get("marketCap")}
        except Exception as e:
            logger.debug("yfinance fundamentals failed for %s: %s", ticker, e)
            return None

    def get_short_interest(self, ticker: str) -> Optional[Dict]:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            return {
                "source": "yfinance",
                "short_pct_float": info.get("shortPercentOfFloat"),
                "short_ratio": info.get("shortRatio"),
            }
        except Exception as e:
            logger.debug("yfinance short interest failed for %s: %s", ticker, e)
            return None


class ProviderRouter:
    """Routes data requests to the best available provider."""

    def __init__(self, priority: Optional[list] = None):
        self._providers: list[DataProvider] = []
        fmp_key = os.environ.get("FMP_API_KEY", "")
        polygon_key = os.environ.get("POLYGON_API_KEY", "")

        priority = priority or ["fmp", "polygon", "yfinance"]
        available: Dict[str, DataProvider] = {}

        use_fmp = os.environ.get("JARVIS_ENABLE_FMP_DATA", "").lower() in {"1", "true", "yes"}
        if use_fmp and fmp_key:
            available["fmp"] = FMPProvider(fmp_key)
        if polygon_key:
            available["polygon"] = PolygonProvider(polygon_key)
        available["yfinance"] = YFinanceProvider()

        for name in priority:
            if name in available:
                self._providers.append(available[name])

        logger.info("Provider order: %s", [p.name for p in self._providers])

    def get_price(self, ticker: str) -> Optional[Dict]:
        for provider in self._providers:
            result = provider.get_price(ticker)
            if result:
                logger.debug("Price for %s via %s", ticker, provider.name)
                return result
        return None

    def get_fundamentals(self, ticker: str) -> Optional[Dict]:
        for provider in self._providers:
            result = provider.get_fundamentals(ticker)
            if result:
                logger.debug("Fundamentals for %s via %s", ticker, provider.name)
                return result
        return None

    def get_short_interest(self, ticker: str) -> Optional[Dict]:
        for provider in self._providers:
            result = provider.get_short_interest(ticker)
            if result:
                logger.debug("Short interest for %s via %s", ticker, provider.name)
                return result
        return None


_router: Optional[ProviderRouter] = None


def get_router(priority: Optional[list] = None) -> ProviderRouter:
    global _router
    if _router is None:
        _router = ProviderRouter(priority)
    return _router


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    router = get_router()
    print(router.get_price("AAPL"))
