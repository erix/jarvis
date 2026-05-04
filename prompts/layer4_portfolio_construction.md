# JARVIS — Layer 4 Prompt

Build Layer 4 of the JARVIS long/short equity hedge fund system. Layers 1 (data) and 2 (scoring) are complete.

This layer is **Portfolio Construction** — building an optimized long/short portfolio from scored tickers. Two methods: MVO and Conviction-Tilt.

## Files to Create

```
portfolio/
├── mvo_optimizer.py         # Markowitz Mean-Variance Optimization
├── optimizer.py             # Conviction-tilt simpler method
├── transaction_costs.py   # Cost model (commission, spread, impact)
├── rebalance_schedule.py  # Check for earnings, FOMC, opex
├── state.py                # SQLite position tracking
├── beta.py                 # Rolling 60d beta vs SPY
├── factor_exposure.py     # Portfolio factor exposure check
├── rebalance.py           # Compare current vs target, generate trades
└── __init__.py
run_portfolio.py
```

## Config Parameters (add to config.yaml)

```yaml
portfolio:
  num_longs: 20
  num_shorts: 20
  max_position_pct: 5.0          # Max % of AUM per position
  max_sector_pct: 25.0           # Max % of AUM per sector
  gross_exposure: 165.0          # Total long + |short| exposure
  net_exposure_min: -10.0        # Net long/short exposure range
  net_exposure_max: 15.0
  max_portfolio_beta: 0.20
  turnover_budget_pct: 30.0      # Annual turnover cap
  mvo_risk_aversion: 1.0
  rebalance_freq_days: 7          # Weekly rebalance
  ```

---

## 1. Transaction Cost Model (portfolio/transaction_costs.py)

Estimate cost for each trade:
```python
commission = 0.005 * shares          # $0.005 per share (IBKR-like)
spread_cost = 0.5 * (ask - bid) * shares
market_impact = coeff * (shares / ADV) ** 0.6
# default coeff = 0.10, ADV from daily volume data
```

Function: `estimate_cost(ticker, shares, current_price, avg_daily_volume)` returns total_estimated_cost

---

## 2. MVO Optimizer (portfolio/mvo_optimizer.py)

**Markowitz optimization with sector, beta, and position constraints.**

Inputs:
- Expected returns vector (map composite score 0-100 → -15% to +15% annual return)
- Covariance matrix (from 60d price returns of selected tickers + benchmarks)
- Risk aversion lambda (default 1.0)
- Transaction costs (from transaction_costs.py)

Objective: `maximize (mu^T w - lambda * w^T Sigma * w)`

Constraints:
- For each position: `-max_position_pct/100 &lt;= w_i &lt;= max_position_pct/100`
- Sector: `sum(|w_i| for sector s) &lt;= max_sector_pct/100` for each sector
- Gross: `sum(|w_i|) == gross_exposure/100`
- Net: `net_exposure_min/100 &lt;= sum(w_i) &lt;= net_exposure_max/100`
- Beta: `|sum(w_i * beta_i)| &lt;= max_portfolio_beta`

**Long-only in practice for MVO:** Video mentions long candidates + short candidates. For this system: allow negative weights for short positions.

Return: target_weights dict {ticker: weight}, expected_return, expected_volatility, sector_neutrality_score

Use scipy.optimize.minimize (SLSQP method) for constrained optimization.

---

## 3. Conviction-Tilt Optimizer (portfolio/optimizer.py)

Simpler fallback approach:
1. Take top N long candidates and bottom N short candidates from scores table
2. Weight = base_weight + conviction_adjustment where conviction is derived from composite score
3. Liquidity constraint: position size &lt;= 1% of 20-day ADV
4. Earnings blackout: if earnings within 5 days, flag but don't block (advisory only)
5. Beta adjustment: if portfolio beta &gt; max, reduce highest-beta positions

Return: similar structure to MVO output.

---

## 4. Rebalance Schedule (portfolio/rebalance_schedule.py)

Check for market events that suggest delaying rebalance:
- Earnings dates: if any target ticker has earnings within +/- 2 days → advisory warning
- FOMC meetings: check known dates (stored in config or simple dates list)
- Monthly options expiration: third Friday of each month

Function: `get_rebalance_advice()` returns {"proceed": bool, "warnings": [string]}

---

## 5. Portfolio State (portfolio/state.py)

SQLite table additions:
```sql
CREATE TABLE positions (
    ticker TEXT PRIMARY KEY,
    shares REAL NOT NULL,  -- Positive for long, negative for short
    entry_price REAL,
    current_price REAL,
    sector TEXT,
    beta REAL,
    factor_exposures TEXT, -- JSON string of factor scores
    pnl REAL,
    pnl_pct REAL,
    approval_status TEXT, -- approved/pending/rejected
    is_active INTEGER DEFAULT 1
);

CREATE TABLE portfolio_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    action TEXT, -- buy/sell/cover/short
    shares REAL,
    price REAL,
    cost_basis REAL,
    sector TEXT,
    reason TEXT
);
```

Functions: `get_current_positions()`, `add_position(ticker, shares, price)`, `close_position(ticker)`

---

## 6. Beta Calculator (portfolio/beta.py)

- Rolling 60-day beta for each stock vs SPY using daily returns
- Formula: `cov(stock_returns, spy_returns) / var(spy_returns)`
- For stocks without 60 days of history, use sector ETF beta as proxy
- Portfolio beta = weighted average of position betas

Function: `calculate_beta(ticker, days=60)` returns float

---

## 7. Factor Exposure Calculator (portfolio/factor_exposure.py)

- For the target portfolio, compute weighted average of each factor score
- `factor_exposure_k = sum(w_i * score_ik)` where w_i is position weight
- Flag if any factor spread &gt; 1 standard deviation from zero (overconcentration)

Function: `calculate_exposure(target_weights, scores_df)` returns exposure dict

---

## 8. Rebalance Generator (portfolio/rebalance.py)

Compare current vs target:
1. Current positions from `positions` table
2. Target from chosen optimizer (MVO or conviction-tilt)
3. Calculate trade list: `(target_shares - current_shares)` for each ticker
4. Apply turnover budget:
   - sum(|trades| * price) / AUM &lt;= turnover_budget_pct / 100
   - If exceeds, scale down proportionally
5. Estimate total transaction costs
6. Generate "what-if" analysis

Output: trade list dict:
```python
{
    "ticker": {"action": "buy"/"sell"/"short"/"cover", "shares": X, "estimated_cost": Y}
}
```

---

## Entry Point (run_portfolio.py)

```bash
python run_portfolio.py --rebalance              # Full rebalance
python run_portfolio.py --whatif                 # Dry run, show trades only
python run_portfolio.py --current                # Show current portfolio
python run_portfolio.py --optimize-method mvo    # Use MVO (default)
python run_portfolio.py --optimize-method conviction  # Use conviction-tilt
```

Steps:
1. Load config
2. Load scored universe from `scores` table
3. Load current positions from `positions` table
4. Get rebalance advice (earnings/FOMC/opex warnings)
5. Choose optimization method
6. Generate target portfolio
7. Calculate factor exposures
8. Generate trade list with costs
9. If `--whatif`, print trades and exit
10. If `--rebalance`, save target to positions table (as "pending"), generate trade list
11. Print summary:
```
Portfolio Rebalance
Positions: X long / Y short
Sector concentration: max Z%
Net beta: X.XX
Expected return: X.X%
Expected vol: X.X%
Trades: X
Est. transaction cost: $X,XXX
Turnover: X.X%
```

---

## Implementation Notes
- IMPORTANT: This layer does NOT execute trades. It only generates targets.
- Execution happens in Layer 6 (IBKR). Portfolio just creates the plan.
- For sector constraints, read GICS sector from tickers table
- Use pandas DataFrames for matrix operations
- Handle missing betas gracefully (use sector proxies)
- Turnover tracking: store in positions table and update on each rebalance
- The `positions` table uses "shares" — positive = long, negative = short (mirror L5 pre-trade checks)

## What This Builds
- MVO optimizer with sector, beta, and position constraints
- Conviction-tilt fallback for simpler portfolios
- Transaction cost model
- Portfolio state tracking (positions, history, beta, exposures)
- Rebalance generation with turnover budgets
- "what-if" dry-run mode
- Full integration with L1 DB and L2 scores
