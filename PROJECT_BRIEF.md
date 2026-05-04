# JARVIS — Long/Short Equity Hedge Fund System
## Project Brief (Extracted from Video Screenshots & Transcript)

---

## 1. System Overview

A full long-short equity hedge fund system that scores all **503 S&P 500 stocks** across **8 quantitative factors** and **27 sub-factors**. Built with Claude as the AI analyst layer, backed by live data from 5 sources. Includes portfolio optimization, risk management, broker execution, and a 6-page Streamlit dashboard.

**Name:** JARVIS (Jarvis Analyst Insider Hedge Fund)  
**Alternative project name in prompts:** Meridian Capital Partners  
**Universe:** S&P 500 (503 stocks)  
**Data freshness:** Daily auto-refresh (~10 min incremental after ~1-2 hour first run)  
**Cost per full AI run:** $2-5 using Claude Sonnet  
**AI analysis caching:** Per filing period (TTL-based eviction)

---

## 2. Architecture — 7 Layers

### L1 — Data Infrastructure
**5 sources, 390K price bars, 50K insider transactions, 20K institutional holdings**

**Project Structure:**
```
data/           — This layer
factors/        — Layer 2 (scoring)
analysis/       — Layer 3 (Claude AI)
portfolio/      — Layer 4 (construction)
risk/           — Layer 5 (risk management)
execution/      — Layer 6 (Alpaca)
reporting/      — Layer 7 (reports)
dashboard/      — Layer 7 (Streamlit)
cache/          — SQLite + cached files
output/         — CSVs, logs, reports
config.yaml     — All parameters
.env            — API keys (gitignored)
```

**5 Data Sources:**

1. **Universe** (`data/universe.py`)
   - Scrape S&P 500 list from Wikipedia
   - Store: ticker, company name, GICS sector, sub-industry
   - Cache locally, refresh weekly
   - Benchmark tickers: SPY, QQQ, IWM, DIA, sector ETFs (XLK, XLF, XLV, XLE, XLI, XLC, XLY, XLP, XLB, XLRE, XLU), ^VIX, TLT, HYG

2. **Market Data + Fundamentals** (`data/market_data.py` + `data/fundamentals.py`)
   - Daily OHLCV via yfinance for all universe + benchmarks, 3yr lookback
   - Incremental updates — only new data since last stored date
   - SQLite table `daily_prices`
   - Fundamentals: quarterly + annual income stmt, balance sheet, cash flow via yfinance
   - **24 derived ratios:** ROE, ROA, gross/operating/net margin, revenue growth YoY/QoQ, earnings growth YoY/QoQ, debt/equity, FCF yield, current ratio, AR/revenue, CFO/NI, accruals ratio, retained earnings, working capital, total liabilities, EBIT, R&D expense, shares outstanding, dividends paid, buybacks, asset turnover

3. **SEC Filings** (`data/filings.py`)
   - 10-K, 10-Q, 8-K for text analysis
   - **Form 4** — all insider transaction filings
   - **13F filings** — track what every other fund is doing (Citadel, 72, Bridgewater)

4. **Earnings Transcripts** (`data/transcripts.py`)
   - Via Financial Modeling Prep (FMP) API
   - Requires `FMP_API_KEY` in `.env`

5. **Short Interest Data** (`data/short_interest.py`)
   - Short percent of float, days to cover, change vs prior period

6. **Provider Abstraction** (`data/providers.py`)
   - Routes to best available data source
   - Checks API keys: Polygon, FMP, FRED
   - Fallback to yfinance

**Entry Point:** `run_data.py`
- Arguments: `--no-filings`, `--no-13f`
- Order: universe → prices → fundamentals → filings → short interest → estimates → calendar
- Logging to `output/run.log`
- Summary: tickers updated, price bars added, filings cached, insider transactions parsed

---

### L2 — Scoring Engine
**8 factors · 27 sub-factors · sector-relative ranking · crowding detection**

All scores are **0-100 percentile rank WITHIN each GICS sector**. Equal-weight sub-factors within each parent factor.

#### Factor 1: Momentum (`factors/momentum.py`) — 6 sub-factors
1. 12-1 month return (skip recent 1mo to avoid reversal)
2. 6-month return
3. 3-month return
4. Acceleration (recent 3m minus older 3m)
5. 52-week-high proximity (price / 52w high — George & Hwang 2004)
6. Relative strength vs sector ETF (6m stock return minus sector ETF return)

#### Factor 2: Value (`factors/value.py`) — 6 sub-factors
1. Forward earnings yield (1/forward P/E)
2. Book-to-price
3. FCF yield
4. EV/EBITDA (inverted)
5. Shareholder yield (TTM buybacks + dividends / mkt cap)
6. Sales-to-EV (revenue / EV)

#### Factor 3: Quality (`factors/quality.py`) — 8 sub-factors
1. ROE stability (std dev of 12Q ROEs, inverted)
2. Gross margin level
3. Gross margin trend (latest minus 4Q ago)
4. Debt/equity (inverted)
5. CFO/NI (higher = real cash earnings)
6. Accruals ratio ((NI-CFO)/TA, inverted — high accruals predict underperformance)
7. **Piotroski F-Score** (1-9): 9 binary signals — positive ROA, positive CFO, rising ROA, CFO > NI, falling D/E, rising current ratio, no dilution, rising gross margin, rising asset turnover
   - Green >= 7, Amber <= 3
8. **Altman Z-Score**: 1.2*(WC/TA) + 1.4*(RE/TA) + 3.3*(EBIT/TA) + 0.6*(MktCap/TL) + 1.0*(Sales/TA)
   - > 2.99 = "safe" (green)
   - 1.81-2.99 = "grey zone"
   - < 1.81 = "distress" (amber)

#### Factor 4: Growth (`factors/growth.py`) — 5 sub-factors
1. Revenue growth YoY
2. Earnings growth YoY
3. Revenue growth acceleration (latest YoY minus 4Q-ago YoY)
4. R&D intensity (R&D expense / revenue)
5. Free cash flow growth YoY

#### Factor 5: Estimate Revisions (`factors/revisions.py`) — 3 sub-factors
1. 30-day change in consensus next-Q EPS
2. 60-day change
3. 90-day change
- Degenerate (all scores = 50) until ~30 days of snapshots accumulate

#### Factor 6: Short Interest (`factors/short_interest.py`) — 3 sub-factors
1. Short percent of float
2. Days to cover
3. Change in short interest vs prior period
- For LONGS: declining short interest scores higher
- For SHORTS: increasing scores higher

#### Factor 7: Insider Activity (`factors/insider.py`) — 3 sub-factors
1. Net dollar flow over 90 days from Form 4 data
2. CEO/CFO open-market purchases weighted 3x vs other insiders
3. Cluster-buy flag (3+ insiders within 30 days) = bonus
- Only count transaction code P (purchase) and S (sale), ignore A/M/F
- No data = sector median (50)

#### Factor 8: Institutional Flow (`factors/institutional.py`) — 3 sub-factors
1. Number of tracked funds holding
2. Net change in aggregate holdings vs prior quarter
3. Multi-fund simultaneous opening flag (3+ funds opening new positions same ticker)

#### 9. Composite Score (`factors/composite.py`)
Weighted blend:
- Momentum: 0.20
- Quality: 0.15
- Value: 0.15
- Estimate Revisions: 0.15
- Insider Activity: 0.10
- Growth: 0.10
- Short Interest: 0.10
- Institutional Flow: 0.05
- Re-rank within sectors
- Output: `scored_universe_latest.csv`

#### 10. Regime-Conditional Weights (`factors/regime_weights.py`)
Adjust weights based on VIX:
- **VIX < 15 (low vol):** Momentum +0.05, Growth +0.05, Value -0.05, Short Interest -0.05
- **VIX 15-25 (normal):** Standard weights
- **VIX > 25 (high vol):** Quality +0.05, Value +0.05, Momentum -0.05, Growth -0.05

#### 11. Crowding Detection (`factors/crowding.py`)
- Synthesize daily factor returns from cross-sectional regression
- Calculate pairwise correlations between factor returns (90d rolling)
- If any factor pair correlation > 0.7 and deviation > 0.4 from academic baseline → flag
- Exposures: `output/crowding_alerts.json`

**Entry Point:** `run_scoring.py --ticker AAPL` (single stock mode)

---

### L3 — Claude AI Analysis
**4 analyzers · $2-5 per run · cached per filing period**

Default model: claude-sonnet-4-5 (configurable). Use prompt caching on system prompts.

#### 1. API Client (`analysis/api_client.py`)
- Anthropic SDK wrapper
- Prompt caching
- Retry logic (exponential backoff)
- JSON extraction
- Token count estimation

#### 2. Cost Tracker (`analysis/cost_tracker.py`)
- Tracks usage after each call
- Monitors input/output/cache tokens
- Hard cost ceiling enforcement

#### 3. Analysis Cache (`analysis/cache.py`)
- SQLite table keyed by analyzer + ticker + artifact ID
- TTL-based eviction

#### 4. Earnings Call Analyzer (`analysis/earnings_analyzer.py`)
- **Input:** Transcript text
- **Output JSON:**
  - Management Confidence (1-10)
  - Revenue Guidance (beat/miss/maintain)
  - Margin Outlook (expanding/contracting/stable)
  - Capital Allocation (buybacks, capex, M&A)
  - Key Risks Mentioned (list)
  - Guidance Change (up/down/unchanged)
  - Overall Tone (bullish/neutral/bearish)

#### 5. Filing Analyzer (`analysis/filing_analyzer.py`)
- **Input:** Fundamentals from L1
- **Output JSON:**
  - Scoring: Financial Health (1-10), Earnings Quality (1-10), Revenue Quality (1-10), Balance Sheet Health (1-10)
  - Red Flags: CFO < NI, AR growing faster than revenue, inventory piling, good will > 50% assets, operating lease explosion
  - Green Flags: FCF > NI for 4+ quarters, buybacks > dilution, insider buying
  - Accruals Commentary

#### 6. Risk Analyzer (`analysis/risk_analyzer.py`)
- **Input:** Latest 10-K "Risk Factors" section
- **Output JSON:**
  - New Risks (not in prior 10-K)
  - Material Risks (severity: LOW/MEDIUM/HIGH)
  - Litigation Exposure
  - Regulatory Risk
  - Macro Sensitivity
  - Overall Risk Trend (improving/stable/worsening)

#### 7. Insider Analyzer (`analysis/insider_analyzer.py`)
- **Input:** Parsed Form 4 data (90d)
- **Output JSON:**
  - Pattern Classification (accumulation/distribution/neutral)
  - Key Transactions (notable purchases/sales)
  - Confidence Score (0.0-1.0)
  - Timing Analysis (pre-earnings, post-guidance, routine)
  - Cluster Summary

#### 8. Sector Analysis (`analysis/sector_analysis.py`)
- Aggregate sector-level scoring
- Compare sector composite vs benchmark

#### 9. Combined Score (`analysis/combined.py`)
- Blend: 60% quantitative composite (L2) + 40% Claude fundamental score
- Generates STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL signals

#### 10. Report Generator (`analysis/report_generator.py`)
- Combines all analyzer outputs into single per-ticker report

**Entry Point:** `run_analysis.py`
- Flags: `--estimate-cost`, `--ticker AAPL`, `--sector Technology`
- Full run: 20 long + 20 short candidates, ~$2-5 using Sonnet

---

### L4 — Portfolio Construction
**Mean-Variance Optimization · sector neutral · beta adjusted · transaction costs**

#### 1. MVO Optimizer (`portfolio/mvo_optimizer.py`)
- **Markowitz optimization**
- Inputs: expected returns, covariance matrix, risk aversion lambda, transaction costs
- Objective: maximize `mu*w - lambda*w*Sigma*w`
- Constraints:
  - Long/short weights (per-position limits)
  - Sector constraints (sector neutral)
  - Beta constraints
  - Gross/net exposure
- Handle non-convergence gracefully
- Risk aversion: lambda = 1.0 default
- Score-to-expected-return mapping: score 100 = +15%/yr
- CLI: `--optimize-method mvo`

#### 2. Conviction-Tilt Optimizer (`portfolio/optimizer.py`)
- Equal weight base + score adjustments
- Liquidity constraints (ADV check)
- Earnings blackout consideration
- Beta adjustment

#### 3. Transaction Cost Model (`portfolio/transaction_costs.py`)
- Commission (per share)
- Spread cost
- Market impact: `coef * (shares / ADV)^0.6`
- Default coef = 0.10

#### 4. Rebalance Schedule (`portfolio/rebalance_schedule.py`)
- Check for earnings, FOMC meetings, options expiration
- Advisory warnings (don't block, just warn)

#### 5. Portfolio State (`portfolio/state.py`)
- SQLite tables: positions, history, approvals
- Track: ticker, shares, price, sector, factor exposures, P&L

#### 6. Beta Calculator (`portfolio/beta.py`)
- Rolling 60-day beta per stock vs SPY
- Portfolio-level beta metrics

#### 7. Factor Exposure Calculator (`portfolio/factor_exposure.py`)
- Weighted average of factor scores
- Flag spreads exceeding 1 standard deviation

#### 8. Rebalance Generator (`portfolio/rebalance.py`)
- Compare current vs target portfolio
- Generate trade lists
- Apply turnover budgets
- Estimate transaction costs
- `--whatif` mode (dry run)

**Config Parameters:**
```yaml
num_longs: 20
num_shorts: 20
max_position: 5%
max_sector: 25%
gross: 165%
net: [-10%, +15%]
max_beta: 0.20
turnover_budget: 30%
mvo_risk_aversion: 1.0
```

**Entry Point:** `run_portfolio.py`
- Flags: `--rebalance`, `--whatif`, `--current`, `--optimize-method mvo`

---

### L5 — Risk Management
**Absolute veto · 8 pre-trade checks · Barra-style factor risk model · MCTR**

#### 1. Factor Risk Model (`risk/factor_risk_model.py`)
- **Barra-style cross-sectional regression:**
  - `r_i,t = alpha_t + sum_k beta_k,t * F_k,i + epsilon_i,t`
  - `F_k,i` = stock i standardized factor exposure (z-scored from 0-100 sector ranks)
  - `Portfolio factor_var = exp * F * exp`
  - `specific_var = sum(w_i^2 * spec_var_i)`
  - `total_var = factor_var + specific_var`
  - `MCTR_i = w_i * cov(r_i, r_p) / sigma_p`
  - Flag where MCTR% > 1.5x weight%
- Feed predicted covariance matrix (`X*F*X + diag(specific)`) to Layer 4 MVO optimizer

#### 2. Pre-Trade Veto (`risk/pre_trade.py`)
**8 checks — ANY failure = REJECT:**
1. Halt lock exists?
2. Earnings blackout (5d = 50% size cut)
3. Liquidity <= 5% ADV
4. Position <= 5% AUM
5. Sector <= 25%
6. Gross <= 165%, net [-10%, +15%]
7. |net beta| <= 0.20
8. Pairwise correlation <= 0.80 with existing positions
- Closing/covering trades always approved
- Log every rejection with timestamp and reason

#### 3. Circuit Breakers (`risk/circuit_breakers.py`)
- Daily > 1.5% → SIZE_DOWN 30%
- Daily > 2.5% → CLOSE_ALL_TODAY
- Weekly > 4% → SIZE_DOWN 30%
- Drawdown > 8% → KILL_SWITCH (lock file, `--clear-halt` to reset)
- Single position > 3% NAV → force-close immediately

#### 4. Factor Monitor (`risk/factor_monitor.py`)
- Z-score of factor spreads
- Cross-check against crowding warnings
- Alert if |z| > 1.5 sigma

#### 5. Correlation Monitor (`risk/correlation_monitor.py`)
- Pairwise correlations within long/short books
- Alert if average within-book correlation > 0.40
- Flag pairs > 0.85

#### 6. Tail Risk Monitor (`risk/tail_risk.py`)
- Reduce gross exposure based on VIX levels
- Credit spread widening alert
- Auto-reduce on VIX/credit spikes

#### 7. Stress Testing (`risk/stress.py`)
6 scenarios:
1. crisis_2008 (Financial Crisis)
2. covid_2020 (Covid Crash)
3. rate_hikes_2022 (Rate Hikes)
4. sector_shock (Sector Shock)
5. momentum_reversal (Momentum Reversal)
6. short_squeeze (Short Squeeze)
- Calculate portfolio P&L under each scenario

#### 8. Risk State (`risk/state.py`)
- Maintain cache with daily/weekly P&L, drawdown, circuit breaker usage, factor exposures
- Persist to SQLite

**Entry Point:** `run_risk_check.py`
- Flags: `--stress`, `--tail-only`, `--clear-halt`

---

### L6 — Execution
**IBKR Gateway paper trading · limit orders · slippage tracking · short availability**

#### 1. Broker Connection (`execution/broker.py`)
- IBKR Gateway via `ib_insync` with connection to `192.168.11.202:4001` (paper) / `4002` (live)
- Config from `.env`: `IB_GATEWAY_HOST`, `IB_GATEWAY_PORT`, `IB_CLIENT_ID`
- Default to paper trading (port 4001)
- Live trading: require explicit confirmation of risk understanding
- Sync portfolio state with L4 via `ib.positions()` and `ib.accountSummary()`
- Exponential backoff on connection errors
- Handle IB Gateway disconnection gracefully

#### 2. Order Executor (`execution/executor.py`)
- Pre-trade veto checks (L5) before every order
- Short availability check via `ib.reqShortableShares()` or HTB/ETB status
- Limit order submission via `ib.placeOrder()`
- Limit price calculation: signal price ± buffer (based on bid-ask spread)
- Time-in-force: DAY (default), GTC for pending
- Polling frequency: every 5 seconds via `ib.sleep()` + `trade.orderStatus`
- Timeout: 5 minutes
- Log: signal price, fill price, slippage, timestamp, permId

#### 3. Slippage Tracker (`execution/costs.py`)
- Formula: `(avgFillPrice - signal_price) / signal_price * 10,000`
- Metrics: average, median, p95
- Surface worst 5 fills for dashboard

#### 4. Short Availability (`execution/short_check.py`)
- Check IBKR `shortableShares` and `hardToBorrow` flags via `reqContractDetails`
- Cache results (TTL)
- Log unavailable shorts

#### 5. Order Manager (`execution/order_manager.py`)
- Track order status: pending, partial, filled, cancelled
- Handle keyboard interrupt gracefully
- Position sync with L4

**Entry Point:** `run_execution.py`
- `--dry-run` (log what would happen)
- `--execute` (place orders)

---

### L7 — Reporting & Dashboard
**P&L attribution · sector-relative alpha · JARVIS analyst · 6-page dashboard**

#### Reporting Engine

**1. Daily P&L Attribution** (`reporting/pnl_attribution.py`)
- Formula: `daily_return = beta + sector + factor + alpha_residual`
- Beta: `net_beta * SPY_return`
- Sector: Brinson-style attribution
- Factor: Regression on factor return spreads
- Alpha: Residual after subtracting other three
- Output: `output/daily_attribution.csv`

**2. Position Attribution**
- Mark-to-market
- FIFO round-trips
- Best/worst per side
- Predictive power: Spearman correlation between entry-time score and realized return

**3. Win/Loss Analysis**
- Win rate, P/L ratio
- Sliced by: side, holding period (1-5d / 5-20d / 20-60d / 60d+), sector, VIX regime at entry, factor quintile at entry, streaks

**4. Sector-Relative Performance**
- Per sector 90d: your picks vs sector ETF = stock-selection alpha
- Sum across sectors = total alpha
- Track winner/loser sector counts

**5. Turnover Analytics**
- Trailing 30/90d turnover, annualized
- vs budget from config
- Tax estimate: FIFO, short-term gains @ 37%, long-term @ 20%

**6. Tear Sheet**
- Markdown institutional format
- Metrics vs SPY, monthly returns grid, equity curve, drawdown, rolling 12mo Sharpe, factor + sector exposures, turnover

**7. Claude Weekly Commentary**
- JARVIS-authored
- Fires on configurable weekday (default Friday)
- Summarizes weekly performance

**8. Daily LP Letter** (`reporting/lp_letter.py`)
- 3-4 paragraphs
- Letterhead: fund name, domicile (Delaware), inception, AUM
- Doc ID: `MCP-IM-{YYYY}-{MMDD}`
- Date
- "CONFIDENTIAL — LIMITED PARTNERS ONLY" stamp
- "Dear Limited Partners," + body from Claude in JARVIS voice
- Signature block + compliance footer
- "Regenerate letter" button
- Cache by date

#### Streamlit Dashboard

**Serve at:** `http://localhost:8502`

**Visual Style:**
- Background: `#0b0e17`
- Card gradient: `#131827` to `#1a2035`
- Accent: Indigo `#6366f1`
- Long color: `#10b981`
- Short color: `#f43f5e`
- Fonts: Plus Jakarta Sans, JetBrains Mono
- Hide all Streamlit chrome
- Roman-numeral pill bar navigation

**Page I — PORTFOLIO (Cover):**
- Right 56%: Robot image or dark gradient fallback
- Left: "JARVIS" (92px), "LONG/SHORT HEDGE FUND ANALYST" (11px small caps)
- Ask JARVIS chat (input + response, preserve 6 turns)
- 10 metrics: Universe, Long/Short Cand, Positions, Crowding, Insider Events, CEO/CFO Buys, Cluster Buys, VIX, Earnings -7D
- Status strip: VIX regime badge + data source indicator
- JARVIS chat: build ~19KB JSON snapshot of system state, send as cached context to Claude

**Page II — RESEARCH:**
- KPIs, crowding warnings
- Rebalance advisory banner (earnings/FOMC/opex)
- Optimization toggle (MVO/conviction radio)
- Factor heatmap (top 30 + bottom 30 × 8 factors)
- Approval banner with Execute button
- 10 long + 10 short candidate cards
  - Each card: ticker, sector, shares, $ value, % of book, beta, Piotroski score, Altman Z-Score
  - Approve / Reject / Reset buttons
  - Expandable "Claude analysis" per ticker
- Execute → pre-trade veto (8 checks) → Alpaca
- Rejected trades show veto reason

**Page III — RISK:**
- Circuit breaker bars (daily/weekly/drawdown)
- Tail-risk KPIs (VIX + credit spread)
- Risk decomposition donut (factor vs specific variance)
- Factor risk contributions table
- MCTR table with disproportionate-risk flag
- Factor exposure bars with 1.5-sigma warnings
- Stress test table (6 scenarios)
- Correlation heatmap + effective bets
- 72hr alerts

**Page IV — PERFORMANCE:**
- Equity curve vs SPY (rebased to 100)
- Monthly returns grid (green/red heatmap)
- Drawdown chart
- P&L attribution bars (Beta/Sector/Factor/Alpha)
- Rolling 12mo Sharpe
- Sector-relative alpha chart with total alpha KPI + winner/loser counts
- Turnover panel (30d/annualized/budget/tax)
- Transaction cost panel (estimated vs actual vs model error)
- Best/worst 5 contributors
- Win/loss panel
- Claude weekly commentary card

**Page V — EXECUTION:**
- KPI row: filled orders 30d, avg slippage bps, total slippage $, open orders count
- Open orders table (polling Alpaca)
- Recent trades log (last 200 orders)
- Worst 5 fills
- Short availability panel per current short
- Daily notional turnover table

**Page VI — LETTER:**
- Formal daily LP letter
- Letterhead: fund name, domicile (Delaware), inception, AUM
- Doc ID `MCP-IM-{YYYY}-{MMDD}`, date
- "CONFIDENTIAL — LIMITED PARTNERS ONLY" stamp
- "Dear Limited Partners," + 3-4 paragraph body from Claude in JARVIS voice
- Signature block + compliance footer
- "Regenerate letter" button
- Cache by date

**Auto-Refresh:** Every 5 minutes during market hours (9:30am - 4:00pm ET)

**Daily Automation:**
- macOS launchd plist at `~/Library/LaunchAgents/com.user.hedgefund.daily.plist`
- Weekdays at 17:15 local
- Runs: `run_scoring.py --no-filings --no-13f`
- Refreshes prices, short interest, estimates, calendar, rescores all factors (~10 min)

---

## 3. Environment Variables (`.env`)

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# IBKR Gateway
IB_GATEWAY_HOST=192.168.11.202
IB_GATEWAY_PORT=4001
IB_CLIENT_ID=1

# Optional (enhanced data)
POLYGON_API_KEY=
FMP_API_KEY=
FRED_API_KEY=

# Config overrides
DATA_LOOKBACK_YEARS=3
SCORING_MODEL=claude-sonnet-4-5
PAPER_TRADING=true
```

---

## 4. Key Dependencies

```
yfinance
pandas
numpy
scipy
scikit-learn
streamlit
anthropic
alpaca-trade-api
requests
beautifulsoup4
plotly
matplotlib
seaborn
PyYAML
python-dotenv
```

---

## 5. Data Pipeline Flow

```
Daily (17:15 local, weekdays):
  1. run_data.py --no-filings --no-13f
     → Refresh prices, short interest, estimates, calendar
     → ~10 minutes

  2. run_scoring.py
     → Rescore all 503 stocks across 8 factors
     → Generate composite scores
     → Detect crowding

  3. (Optional) run_analysis.py --estimate-cost
     → Run Claude AI analysis on top 20 long + 20 short
     → ~$2-5

  4. run_portfolio.py --rebalance
     → Generate target portfolio
     → Run pre-trade risk checks

  5. (Manual) Approve/reject candidates in dashboard

  6. run_execution.py --execute
     → Place orders via Alpaca
     → Track fills and slippage
```

---

## 6. Risk Model Summary

- **Factor Risk:** Barra-style decomposition (factor var + specific var)
- **Target Ratio:** ~20% factor / ~80% specific (most risk from stock picks, not factor bets)
- **MCTR:** Flag positions contributing >1.5x their weight in risk
- **Pre-Trade:** 8 absolute veto checks
- **Circuit Breakers:** Daily/weekly/drawdown limits with auto-actions
- **Stress Tests:** 6 historical + synthetic scenarios
- **Correlation:** Flag pairs >0.85, alert if avg within-book >0.40

---

*Brief compiled from video transcript + 16 screenshots of prompt slides and dashboard*
*Date: 2026-05-04*
