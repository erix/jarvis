# JARVIS — Layer 1 Prompt

Build Layer 1 of a long/short equity hedge fund system called "Meridian Capital Partners."
Project folder: `ls_equity_fund`. This layer handles ALL data ingestion — no scoring, no analysis — just pulling data from multiple sources into a local SQLite database.

## Quick Project Structure (this layer creates files in):
```
ls_equity_fund/
├── data/
│   ├── universe.py           # S&P 500 list + benchmark tickers
│   ├── market_data.py        # Daily OHLCV via yfinance
│   ├── fundamentals.py       # Financial statements + 24 derived ratios
│   ├── filings.py            # SEC EDGAR (10-K, 10-Q, 8-K, Form 4, 13F)
│   ├── transcripts.py        # Earnings transcripts via FMP
│   ├── short_interest.py     # Short interest data
│   └── providers.py          # Provider abstraction layer
├── cache/
│   └── jarvis.db             # SQLite database
├── output/
│   └── logs/                 # Run logs
├── config.yaml               # All parameters
├── .env                      # API keys (gitignored)
├── requirements.txt
└── run_data.py               # Entry point
```

## Detailed Spec

### 1. Universe (data/universe.py)
- Scrape current S&P 500 list from Wikipedia (https://en.wikipedia.org/wiki/List_of_S%26P_500_companies)
- Parse: ticker, company name, GICS sector, sub-industry
- Cache ticker list locally to avoid re-scraping (refresh weekly)
- Maintain benchmark tickers: SPY, QQQ, IWM, DIA, VIX (use ^VIX), TLT, HYG
- Sector ETFs: XLK, XLF, XLV, XLE, XLI, XLC, XLY, XLP, XLB, XLRE, XLU
- Universe table in SQLite: `tickers(id, symbol, name, sector, sub_industry, is_benchmark, updated_at)`
- Function: `get_universe(force_refresh=False)` returns list of dicts

### 2. Market Data (data/market_data.py)
- Fetch daily OHLCV for all universe + benchmark tickers via `yfinance`
- 3-year lookback from config
- Incremental updates: compare latest date in SQLite, only fetch missing dates
- SQLite table: `daily_prices(ticker, date, open, high, low, close, volume, adj_close)`
- Handle data gaps gracefully (log warning, don't crash)
- Function: `update_prices(lookback_years=3, tickers=None)`
- Also fetch VIX data (^VIX) — needed for regime detection in Layer 2

### 3. Fundamentals (data/fundamentals.py)
- Fetch quarterly + annual financials via `yfinance`: income statement, balance sheet, cash flow
- For each stock, store:
  - Revenue (TTM, quarterly trailing)
  - Net Income
  - EBITDA
  - Operating Income
  - Gross Profit
  - Total Assets
  - Total Liabilities
  - Total Equity (Book Value)
  - Current Assets / Current Liabilities
  - Cash + Short-term Investments
  - Accounts Receivable
  - Inventory
  - Goodwill
  - Retained Earnings
  - Working Capital
  - Total Debt
  - Shares Outstanding
  - Dividends Paid
  - Capital Expenditures
  - R&D Expense
  - Operating Cash Flow
  - Free Cash Flow
- Calculate and store 24 derived ratios:
  1. ROE = Net Income / Total Equity (TTM)
  2. ROA = Net Income / Total Assets (TTM)
  3. Gross Margin = Gross Profit / Revenue
  4. Operating Margin = Operating Income / Revenue
  5. Net Margin = Net Income / Revenue
  6. Revenue Growth YoY = (Current Revenue - Prior Year Revenue) / Prior Year Revenue
  7. Revenue Growth QoQ = (Current Q - Prior Q) / Prior Q
  8. Earnings Growth YoY = (Current NI - Prior Year NI) / Prior Year NI
  9. Earnings Growth QoQ = (Current NI - Prior Q NI) / Prior Q NI
  10. Debt/Equity = Total Debt / Total Equity
  11. Current Ratio = Current Assets / Current Liabilities
  12. FCF Yield = Free Cash Flow / Market Cap
  13. EV/EBITDA = Enterprise Value / EBITDA
  14. AR/Revenue = Accounts Receivable / Revenue
  15. CFO/NI = Operating Cash Flow / Net Income (earnings quality)
  16. Accruals Ratio = (Net Income - CFO) / Total Assets
  17. Asset Turnover = Revenue / Total Assets
  18. Interest Coverage = Operating Income / Interest Expense
  19. Piotroski F-Score = build all 9 binary signals, output 0-9
  20. Altman Z-Score = 1.2*(WC/TA) + 1.4*(RE/TA) + 3.3*(EBIT/TA) + 0.6*(MktCap/TL) + 1.0*(Sales/TA)
  21. Shareholder Yield = (Buybacks + Dividends) / Market Cap
  22. Buyback Yield = Buybacks / Market Cap
  23. Dividend Yield = TTM Dividends / Market Cap
  24. PE Ratio = Market Cap / Net Income (TTM)
- SQLite table: `fundamentals(ticker, report_date, period, ...all raw fields and 24 ratios)`
- Store both quarterly (most recent 8 quarters) and annual (most recent 4 years) data
- Function: `update_fundamentals(tickers=None)`

### 4. Filings (data/filings.py)
- Query SEC EDGAR for:
  a) 10-K, 10-Q, 8-K filings (metadata + text)
  b) Form 4 insider transactions (parse XML/JSON from EDGAR)
  c) 13F institutional holdings (quarterly)
- Use SEC EDGAR API (https://www.sec.gov/files/edgar/filings-overview.pdf)
- IMPORTANT: Rate limit to 10 requests/sec per SEC fair access policy. Add User-Agent header with contact email.
- For Form 4: parse transaction table, extract: 
  - reportingOwner (name, title: CEO/CFO/director/etc.)
  - transactionDate
  - transactionCode (P=purchase, S=sale, A=grant, M=exercise, F=tax)
  - transactionShares (positive=buy, negative=sell)
  - transactionPricePerShare
  - sharesOwnedFollowingTransaction
- Only keep codes P and S for scoring (insider.py in Layer 2 needs this)
- 13F: parse quarterly holdings from 13F-HR filings
- Cache filed documents as text to avoid re-downloading
- SQLite tables:
  - `filings(ticker, form_type, filing_date, accession_number, cached_path)`
  - `insider_transactions(ticker, owner_name, owner_title, transaction_date, transaction_code, shares, price_per_share, shares_after, filing_date)`
  - `institutional_holdings(ticker, fund_name, cik, quarter, shares, value, filing_date)`
- Function: `update_filings(tickers=None, no_13f=False)` with `--no-filings` and `--no-13f` flags at CLI level

### 5. Earnings Transcripts (data/transcripts.py)
- Fetch earnings call transcripts via Financial Modeling Prep API
- Requires FMP_API_KEY from .env (optional — skip if missing, log warning)
- Store: ticker, date, quarter, transcript text (truncated if very long)
- SQLite table: `earnings_transcripts(ticker, date, quarter, transcript_text)`
- Function: `update_transcripts(tickers=None)` — skip silently if no FMP key

### 6. Short Interest (data/short_interest.py)
- Try to fetch short interest data from multiple sources:
  a) FMP API (if key available)
  b) Fallback: estimate from yfinance (sharesShort, shortRatio, shortPercentOfFloat)
- Store: ticker, date, short_pct_float, days_to_cover, short_ratio
- SQLite table: `short_interest(ticker, date, short_pct_float, days_to_cover, short_ratio, source)`
- Function: `update_short_interest(tickers=None)`

### 7. Provider Abstraction (data/providers.py)
- Routing layer that selects the best data source based on API key availability
- Priority: FMP → Polygon (if key available) → yfinance (always available fallback)
- Abstract methods: `get_price(ticker)`, `get_fundamentals(ticker)`, `get_short_interest(ticker)`
- Log which provider is used

### 8. Config (config.yaml)
```yaml
universe:
  sp500_wikipedia_url: "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
  benchmarks: [SPY, QQQ, IWM, DIA, XLK, XLF, XLV, XLE, XLI, XLC, XLY, XLP, XLB, XLRE, XLU, ^VIX, TLT, HYG]

data:
  lookback_years: 3
  db_path: "cache/jarvis.db"
  cache_dir: "cache"
  output_dir: "output"
  
sec:
  user_agent: "Meridian Capital Partners your-email@example.com"
  rate_limit_per_sec: 10
  
market_data:
  provider_priority: ["fmp", "polygon", "yfinance"]
  
logging:
  level: INFO
  file: "output/logs/run.log"
  format: "%(asctime)s | %(levelname)s | %(message)s"
```

### 9. Entry Point (run_data.py)
- CLI via argparse:
  ```bash
  python run_data.py --no-filings    # Skip SEC filings (faster)
  python run_data.py --no-13f        # Skip 13F filings
  python run_data.py                 # Full run
  ```
- Execution order:
  1. universe (scrape/cache S&P 500 list)
  2. prices (fetch OHLCV via yfinance)
  3. fundamentals (financial statements + ratios)
  4. filings (SEC EDGAR — unless --no-filings)
  5. short_interest
  6. transcripts (via FMP, optional)
- Print summary at end:
  ```
  === Layer 1 Complete ===
  Tickers in universe: 503
  Price bars added: X
  Fundamental records: X
  Filings cached: X
  Insider transactions parsed: X
  Short interest records: X
  Transcripts fetched: X
  Errors: X
  Runtime: Xm Ys
  ```
- Log everything to `output/logs/run.log`

### 10. .env (create template)
```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# IBKR Gateway (used in Layer 6)
IB_GATEWAY_HOST=192.168.11.202
IB_GATEWAY_PORT=4001
IB_CLIENT_ID=1

# Optional (enhances data quality)
FMP_API_KEY=                    # Financial Modeling Prep — earnings transcripts
POLYGON_API_KEY=                # Polygon.io — better price data
FRED_API_KEY=                   # FRED — macro data

# Config
PAPER_TRADING=true
```

### 11. requirements.txt
```
yfinance>=0.2.28
pandas>=2.0
numpy>=1.24
requests>=2.31
beautifulsoup4>=4.12
lxml>=4.9
PyYAML>=6.0
python-dotenv>=1.0
sec-edgar-downloader>=5.0
ib-insync>=0.9.70
```

## Implementation Notes
- Use `sqlite3` from stdlib for the database. No SQLAlchemy needed for simplicity.
- Create DB schema on first run via `CREATE TABLE IF NOT EXISTS`.
- Use `tqdm` for progress bars on long operations (optional, but nice).
- Handle network errors gracefully: retry with exponential backoff, max 3 retries.
- For SEC EDGAR: respect rate limits. Sleep 0.1s between requests. Include proper User-Agent.
- For yfinance: if a ticker fails, log it and continue with others. Don't crash the whole run.
- First run will be slow (~1-2 hours for full universe). Daily updates should be ~10 minutes.
- All Python files should use `if __name__ == "__main__":` guards for functions that can be tested independently.
- Add type hints to all public functions for maintainability.

## What This Builds
A complete data pipeline pulling from 5 sources into a SQLite database:
- S&P 500 universe + 17 benchmark tickers
- 3 years of daily prices (~390K bars)
- Quarterly + annual fundamentals with 24 derived ratios
- SEC filings (10-K, 10-Q, 8-K, Form 4, 13F)
- Short interest + earnings transcripts (where API keys available)

This is Layer 1 of 7. Layers 2-7 will be built subsequently and will depend on this database schema.
