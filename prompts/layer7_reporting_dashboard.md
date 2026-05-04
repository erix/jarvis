# JARVIS — Layer 7 Prompt

Build Layer 7 of the JARVIS long/short equity hedge fund system. Layers 1-6 are complete.

This layer is the **Reporting Engine + Streamlit Dashboard** with JARVIS AI analyst persona.

## Files to Create

```
reporting/
├── pnl_attribution.py     # Daily P&L = beta + sector + factor + alpha
├── win_loss.py            # Win/loss analytics
├── sector_alpha.py        # Sector-relative performance
├── turnover.py            # Turnover analytics + tax estimate
├── tear_sheet.py          # Institutional format report
├── weekly_commentary.py  # Claude weekly summary
├── lp_letter.py           # Daily investor letter
└── __init__.py

dashboard/
├── app.py                 # Streamlit app entry
├── style.py             # CSS/custom theme
├── tabs/
│   ├── portfolio.py        # Page I — JARVIS cover + chat
│   ├── research.py         # Page II — Heatmap + candidates
│   ├── risk.py             # Page III — Risk decomposition
│   ├── performance.py      # Page IV — Returns, drawdown, alpha
│   ├── execution.py        # Page V — Orders, slippage
│   └── letter.py           # Page VI — LP letter
└── __init__.py

run_dashboard.py       # Launch Streamlit
run_reporting.py       # Generate all reports
```

---

## REPORTING ENGINE

### 1. Daily P&L Attribution (reporting/pnl_attribution.py)

**Formula:** `daily_return = beta_component + sector_component + factor_component + alpha_residual`

```python
beta_component = net_portfolio_beta * spy_daily_return
sector_component = Brinson_style_attribution(portfolio, sector_etfs)
factor_component = regression_on_factor_return_spreads(portfolio, factors)
alpha_residual = total_return - (beta + sector + factor)
```

Method:
- **Beta:** net portfolio beta × SPY return
- **Sector:** Brinson attribution — (portfolio sector weights - benchmark sector weights) × sector returns
- **Factor:** Regress daily portfolio return on factor return spreads
- **Alpha:** What's left after subtracting beta + sector + factor

Output: `output/daily_attribution.csv`
Columns: date, total_return, beta_component, sector_component, factor_component, alpha_residual

---

### 2. Win/Loss Analysis (reporting/win_loss.py)

Per round-trip trade (FIFO):
- Win rate = num winners / total trades
- P/L ratio = avg winner / avg loser

**Slice by:**
- Side (long/short)
- Holding period: 1-5d, 5-20d, 20-60d, 60d+
- Sector
- VIX regime at entry (low/normal/high)
- Factor quintile at entry
- Win/loss streaks

Function: `analyze_win_loss(trades_df)` returns dict of metrics

---

### 3. Sector-Relative Performance (reporting/sector_alpha.py)

For each sector (90-day window):
```python
# Your picks vs sector ETF = stock-selection alpha
sector_alpha = sum(w_i * (r_i - sector_etf_return))
```

- Sum across all sectors = total stock-selection alpha
- Track winner sectors vs loser sectors counts
- Example output: "IT picks returned 19% vs XLK 10.9% → +8.1% alpha"

Output: `output/sector_alpha.csv`

---

### 4. Turnover Analytics (reporting/turnover.py)

- Trailing 30d turnover: sum(|trades|) / AUM
- Trailing 90d turnover
- Annualized = 30d × (252 trading days / 30)
- vs budget from config (default 30%)

**Tax Estimate:**
- FIFO-based P&L calculation
- Short-term (&lt;1 year): 37% rate
- Long-term (≥1 year): 20% rate
- Estimated tax liability from realized gains

---

### 5. Tear Sheet (reporting/tear_sheet.py)

Generate markdown institutional tear sheet:

```markdown
# Meridian Capital Partners — Performance Report
Period: {start_date} to {end_date}

## Returns
- Fund: X.X%
- SPY: X.X%
- Alpha: X.X%
- Rolling 12mo Sharpe: X.XX

## Monthly Returns Grid
|     | Jan | Feb | ... |
|-----|-----|-----|-----|
| 2026| +1.2| -0.5| ... |

## Risk Metrics
- Max Drawdown: -X.X%
- Volatility (ann): X.X%
- Beta: X.XX

## Factor Exposures
- Momentum: +X.X
- Value: +X.X
- ...

## Sector Allocation
- IT: X%
- Health Care: X%
- ...

## Turnover
30d: X.X% | Annualized: XX%
```

Save to: `output/tear_sheet_{date}.md`

---

### 6. Claude Weekly Commentary (reporting/weekly_commentary.py)

**Fires on configurable weekday (default Friday).**

Prompt to Claude:
```
Write a weekly performance summary for Meridian Capital Partners.
Data: {portfolio_summary_json}

Include:
1. Overall return vs SPY
2. Best and worst performing positions
3. Risk events this week
4. Changes to factor exposures
5. Outlook for next week

Style: Concise, institutional, direct. Write as JARVIS, the hedge fund's AI analyst.
```

Store: `output/weekly_commentary_{date}.md`

---

### 7. Daily LP Letter (reporting/lp_letter.py)

Generate formal daily investor letter:

**Letterhead:**
```
Meridian Capital Partners
Delaware Limited Partnership
Inception: January 2026
AUM: $XXX,XXX

CONFIDENTIAL — LIMITED PARTNERS ONLY
Doc ID: MCP-IM-2026-{MMDD}
Date: {date}
```

**Body (3-4 paragraphs from Claude):**
Prompt:
```
Write a daily investor letter for Meridian Capital Partners LP.

Today's data: {json snapshot}
Include:
- Portfolio return today
- Key contributors/detractors
- Risk metrics (drawdown, factor concentration)
- Any trades executed
- Brief market context

Voice: Institutional hedge fund letter. Not overly optimistic. Direct about losses.
Sign off as JARVIS, Chief Investment Analyst.
```

**Signature block:**
```
Respectfully,
JARVIS
Chief Investment Analyst
Meridian Capital Partners

COMPLIANCE NOTICE: Past performance does not guarantee future results. This letter is for limited partners only.
```

Save to: `output/letters/daily_{date}.md`
Cache: Don't regenerate if letter for date already exists (unless user clicks "regenerate")

---

## STREAMLIT DASHBOARD

**Serve at:** `streamlit run dashboard/app.py —server.port 8502`

### Visual Theme
```python
# dashboard/style.py
CSS = """
.stApp {
    background-color: #0b0e17;
    color: #e2e8f0;
}
.metric-card {
    background: linear-gradient(135deg, #131827 0%, #1a2035 100%);
    border-radius: 8px;
    padding: 16px;
}
.long-color { color: #10b981; }
.short-color { color: #f43f5e; }
.accent { color: #6366f1; }
"""
fonts: "Plus Jakarta Sans" (body), "JetBrains Mono" (numbers)
hide_streamlit_chrome = True
```

Use `st.markdown(CSS, unsafe_allow_html=True)` to apply theme.

### Navigation
Roman numeral pill bar at top:
```
[ I PORTFOLIO ] [ II RESEARCH ] [ III RISK ] [ IV PERFORMANCE ] [ V EXECUTION ] [ VI LETTER ]
```
Active tab highlighted with indigo gradient `#6366f1`.

### Page I — Portfolio (Cover)

Layout:
- **Left 50%:** Dark gradient background
  - "JARVIS" in 92px font (or largest available)
  - "LONG/SHORT HEDGE FUND ANALYST" in small caps
  - "Ask anything..." input bar
  - "ASK JARVIS" button (indigo gradient)
  - JARVIS — build ~19KB JSON snapshot of system state (universe, positions, scores, risk), send as cached context to Claude API, display response
- **Right 50%:** White robot figure or dark gradient fallback

**10 Metrics Row:**
| Metric | Value |
|--------|-------|
| Universe | 503 |
| Long Cand. | N |
| Short Cand. | N |
| Positions | N |
| Crowding | N |
| Insider Events | N |
| CEO/CFO Buys | N |
| Cluster Buys | N |
| VIX | XX.X |
| Earnings -7D | N |

**Status strip:** VIX regime badge (low/normal/high) + "All data live" indicator

### Page II — Research

- KPIs row (top scores, crowding warnings)
- Rebalance advisory banner (earnings/FOMC/opex coming)
- **Optimization toggle:** Radio: [ MVO ] [ Conviction ]  
  MVO selected by default
- **Factor heatmap:** Top 30 long + bottom 30 short × 8 factors  
  Green = strong, Red = weak. Tooltip on hover shows score.
- **Top 10 long + top 10 short candidate cards:**
  Each card:
  - Ticker, sector, shares, $ value, % of book
  - Beta
  - Piotroski: X/9 (colored: green>=7, amber&lt;=3)
  - Altman-Z: X.X (safe/distress)
  - **Approve** / **Reject** / **Reset** buttons
  - Expandable "Claude analysis" section (per ticker, from L3 cache)
- **Execute** button: calls pre-trade veto (8 checks) → if approved, calls L6 executor
  - If rejected, show veto reason in red banner

### Page III — Risk

- **Circuit breaker bars:** Horizontal bars showing distance to trigger
  - Daily loss: [||||||          ] (safe)
  - Weekly loss: [|||||||||        ] (approaching)
  - Drawdown from peak: [||||          ] (safe)
  Colors: green (safe) → yellow (warning) → red (triggered)
  
- **Tail-risk KPIs:** VIX level + credit spread (if available)

- **Risk decomposition donut:** Factor (green, ~20%) vs Specific (blue, ~80%)

- **Factor risk contributions table:** Each factor's share of total factor variance, sorted

- **MCTR table:** Top 12 positions contributing most risk. Flag if &gt;1.5x weight.

- **Factor exposure bars:** Current factor exposure vs historical range. 1.5-sigma warning.

- **Stress test table:** 6 scenarios with estimated P&L

- **Correlation heatmap:** Long-book × Short-book correlation matrix. Show effective bets count.

- **72hr alerts:** Upcoming earnings, FOMC, macro events

### Page IV — Performance

- **Equity curve:** Fund value vs SPY (both rebased to 100 at start)
- **Monthly returns grid:** Green/red heatmap. Institutional standard format.
- **Drawdown chart:** Area chart showing drawdown from peak over time
- **P&L attribution bars:** Beta | Sector | Factor | Alpha stacked bars
- **Rolling 12mo Sharpe:** Line chart with horizontal at 1.0
- **Sector-relative alpha:** Horizontal bar chart, sectors ordered by contribution. Total alpha KPI with winner/loser counts.
- **Turnover panel:** 30d rate, annualized, vs budget, estimated tax liability
- **Transaction cost panel:** Estimated vs actual vs model error
- **Best/worst 5 contributors:** Table with ticker, return, weight
- **Win/loss panel:** Win rate, P/L ratio, sliced by regime

### Page V — Execution

- **KPI row:**
  - Filled orders (30d): N
  - Avg slippage: X.X bps (p95: Y.Y bps)
  - Total slippage cost (30d): $XX
  - Open orders: N

- **Open orders table:** Live polling from Alpaca/IBKR (or SQLite)

- **Recent trades log (last 200):**
  | Date | Ticker | Action | Qty | Limit | Fill | Slippage |

- **Worst 5 fills:** Table showing highest slippage bps

- **Short availability:** Per current short position, HTB/ETB status

- **Daily notional turnover:** Bar chart of last 30 days

### Page VI — Letter

- **Formal daily LP letter** rendered in markdown
- Letterhead: Meridian Capital Partners, Delaware LP, Inception, AUM
- Doc ID + Date + "CONFIDENTIAL" stamp
- "Dear Limited Partners," + 3-4 paragraph body
- Signature block
- Compliance footer
- **"Regenerate letter"** button — re-calls Claude
- Cache by date (don't regenerate if exists unless requested)

---

## Auto-Refresh
```python
# In app.py
import time
from datetime import datetime, timezone
import pytz

ny_tz = pytz.timezone("America/New_York")
market_open = time(9, 30)
market_close = time(16, 0)

# Refresh every 5 minutes during market hours
if market_open &lt;= now.time() &lt;= market_close:
    st_autorefresh(interval=5 * 60 * 1000)  # 5 minutes in ms
```

## Daily Automation (not part of Streamlit, but part of this layer's scope)

Create a script `run_daily.py` that runs at 17:15 UTC on weekdays:
```python
# Pseudo schedule: run via cron or launchd
python run_data.py --no-filings --no-13f   # ~10 min incremental
python run_scoring.py                         # Rescore all
# Portfolio is NOT auto-rebalanced — human approval required
```

---

## Entry Points

```bash
# Launch dashboard
python run_dashboard.py
# or directly: streamlit run dashboard/app.py --server.port 8502

# Generate all reports
python run_reporting.py --date 2026-05-04
# Generates: P&L attribution, win/loss, sector alpha, tear sheet, LP letter

# Weekly commentary only
python run_reporting.py --weekly
```

---

## Implementation Notes
- Install: `streamlit`, `plotly`, `matplotlib`, "pillow" (for robot image), `pytz`
- Use plotly for interactive charts (heatmap, equity curves, donut charts)
- Use st.dataframe() for tables with sorting
- Cache database queries with `@st.cache_data(ttl=300)` — 5 minute cache
- JARVIS chat sends JSON snapshot to Claude API — this costs API calls. Cache response for 1 minute.
- Robot image: if not found, use a CSS gradient as fallback
- Mobile responsive: Streamlit handles most, but test on narrow screens
- Theme CSS injection uses `st.markdown(..., unsafe_allow_html=True)` — this is standard for Streamlit theming
- All page tabs are implemented as separate function calls within `app.py`, toggled via `st.session_state.active_tab`
- The "Approve/Reject" buttons write to the `positions` table (approval_status column) but don't trigger trades automatically
- For executing approved trades, the Execute button calls Layer 6 `run_execution.py --execute`
- LP letter uses the same Claude API client from Layer 3 (`analysis/api_client.py`)

## What This Builds
- Full P&L attribution engine (beta/sector/factor/alpha)
- Institutional tear sheet generation
- 6-page Streamlit dashboard with dark theme
- JARVIS AI analyst chat interface
- Daily LP letters
- Weekly Claude-authored commentary
- Auto-refresh during market hours
- Sector-relative alpha tracking
- Turnover and tax estimate analytics
