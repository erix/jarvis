# JARVIS — Layer 5 Prompt

Build Layer 5 of the JARVIS long/short equity hedge fund system. Layers 1-4 are complete.

This layer is **Risk Management** — absolute veto power over portfolio construction and execution.

## Files to Create

```
risk/
├── factor_risk_model.py    # Barra-style factor decomposition
├── pre_trade.py            # 8 absolute veto checks
├── circuit_breakers.py     # Daily/weekly/drawdown limits
├── factor_monitor.py       # Factor spread monitoring
├── correlation_monitor.py # Pairwise correlation alerts
├── tail_risk.py            # VIX/credit-based exposure reduction
├── stress.py               # 6 stress test scenarios
├── state.py                # Risk state persistence
└── __init__.py
run_risk_check.py
```

---

## 1. Factor Risk Model (risk/factor_risk_model.py)

**Barra-style cross-sectional regression:**

```python
# Each day: r_i,t = alpha_t + sum_k(beta_k,t * F_k,i) + epsilon_i,t
# F_k,i = stock i's standardized factor exposure (z-scored from 0-100 sector ranks)

factor_variance = exposures @ factor_cov_matrix @ exposures.T
specific_variance = sum(w_i**2 * specific_var_i)
total_variance = factor_variance + specific_variance

MCTR_i = w_i * cov(r_i, r_portfolio) / portfolio_vol
# Flag where MCTR_pct > 1.5 * weight_pct (disproportionate risk contributor)
```

Implementation using sklearn or numpy:
1. Run OLS regression of stock returns on factor returns (cross-sectional, daily)
2. Compute factor covariance matrix (from factor time series, 60d)
3. For current portfolio weights, compute:
   - `factor_var = w^T @ X @ F_cov @ X^T @ w` where X = factor exposures matrix
   - `specific_var = sum((w_i * specific_std_i)**2)`
4. Decomposition: `% factor = factor_var / total_var` and `% specific = specific_var / total_var`
5. Compute MCTR for each position

**Target:** ~20% factor / ~80% specific risk (most from stock picks, not factor bets)

Functions: `decompose_portfolio(weights_dict, scores_df, returns_df)` returns risk_decomposition dict

---

## 2. Pre-Trade Veto (risk/pre_trade.py)

**8 checks — ANY failure = REJECT trade:**

```python
1. halt_lock_exists() → If lock file exists (from circuit breakers), reject ALL new trades
2. earnings_blackout(ticker) → If earnings within 5 days, allow at 50% size only
3. liquidity_check(ticker, shares) → Shares ≤ 5% of Average Daily Volume (20-day)
4. position_limit(ticker, target_shares, aum) → Position % ≤ max_position_pct (default 5%)
5. sector_limit(ticker, sector, target_shares, aum) → Sector % ≤ max_sector_pct (default 25%)
6. gross_net_exposure(target_positions) → Gross ≤ 165%, net in [-10%, +15%]
7. beta_limit(target_positions) → |net portfolio beta| ≤ 0.20
8. correlation_check(ticker, existing_positions) → Max pairwise correlation ≤ 0.80
```

**Closing/covering trades ALWAYS approved** (unwinding is always allowed).

**Log all rejections to SQLite:**
```sql
CREATE TABLE rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    ticker TEXT NOT NULL,
    reason TEXT NOT NULL,
    check_number INTEGER,
    attempted_shares REAL
);
```

Function: `pre_trade_veto(ticker, shares, current_positions, config)` returns {"approved": bool, "reason": string, "checks": [...]}

---

## 3. Circuit Breakers (risk/circuit_breakers.py)

**Circuit breakers do NOT automatically close positions — they only block new trades and alert the operator.**

Check actual P&L thresholds (updated daily):

| Event | Threshold | Action |
|-------|-----------|--------|
| Daily loss | > 1.5% of AUM | **Alert:** Log warning, flag for manual review |
| Daily loss | > 2.5% of AUM | **Alert:** Log CRITICAL warning, recommend manual close |
| Weekly loss | > 4% of AUM | **Alert:** Log warning, flag for manual review |
| Drawdown (from peak) | > 8% | **KILL_SWITCH:** Create lock file, block ALL new trades until `--clear-halt` |
| Single position | > 3% of NAV | **Alert:** Flag position for manual review |

**Lock file:** `cache/halt.lock`
- When kill switch fires, write a JSON file: `{"halted": true, "reason": "drawdown > 8%", "timestamp": "..."}`
- To clear: `run_risk_check.py --clear-halt` (MANUAL OPERATOR INTERVENTION REQUIRED)
- Check for this file at the start of EVERY pre_trade_veto() call
- The lock file ONLY blocks NEW trades. Existing positions are NEVER auto-closed.

**P&L tracking:**
Read from `portfolio_history` table or compute from `positions` table:
- Daily P&L = current portfolio value - yesterday's value
- Weekly P&L = trailing 5-day change
- Drawdown = current_value / peak_value - 1

Function: `check_circuit_breakers(aum, portfolio_value, positions_df)` returns {"action": string, "message": string}

---

## 4. Factor Monitor (risk/factor_monitor.py)

- For each of 8 factor scores, compute Z-score of current portfolio exposure vs historical
- `factor_z = (current_exposure - mean) / std`
- If `|z| > 1.5` for any factor → flag as overconcentrated
- Cross-reference with crowding alerts from Layer 2

Function: `check_factor_spread(portfolio_factor_exposures, historical_exposures_df)` returns alerts list

---

## 5. Correlation Monitor (risk/correlation_monitor.py)

- Compute pairwise 60-day return correlations for all positions within long book AND within short book
- Calculate "effective bets": `1 / sum(w_i^2 / (w_l^2))` — higher = more diversified
- Alert if average within-book correlation &gt; 0.40
- Flag any pair with correlation &gt; 0.85
- Display: "Long book diversification: X effective bets / Y positions" (as in video)

Function: `check_correlations(returns_df, weights_dict)` returns {"long_book": alerts, "short_book": alerts, "effective_bets": dict}

---

## 6. Tail Risk Monitor (risk/tail_risk.py)

Reduce gross exposure based on VIX level:
- VIX &lt; 15: No action
- VIX 15-20: Reduce gross by 5%
- VIX 20-25: Reduce gross by 10%
- VIX 25-30: Reduce gross by 20%
- VIX &gt; 30: Reduce gross by 30%

Credit spread widening (if FRED API key available, else skip):
- High yield spread (HYG/TLT proxy) widening &gt; 2 z-scores → same reduction as VIX 20-25

Function: `check_tail_risk(vix_value, credit_spread=None)` returns exposure_adjustment dict

---

## 7. Stress Testing (risk/stress.py)

6 historical/synthetic scenarios:

| Scenario | Description | P&L Method |
|----------|-------------|------------|
| crisis_2008 | 2008 Financial Crisis | Apply -60% stock / +50% VIX shock |
| covid_2020 | 2020 Covid Crash | Apply -35% stock / +60% VIX shock |
| rate_hikes_2022 | 2022 Rate Hikes | Apply +20% banking, -30% tech |
| sector_shock | Single sector -30% | Worst sector in portfolio |
| momentum_reversal | Momentum crash | Long momentum -25%, short momentum +25% |
| short_squeeze | Short squeeze on top short | Top short +100% in 1 day |

For each scenario, estimate portfolio P&L by shocking individual position returns.
Store results: `stress_results` dict

Function: `run_stress_test(positions_df, returns_df, weights_dict)` returns results_df

---

## 8. Risk State (risk/state.py)

SQLite table additions:
```sql
CREATE TABLE risk_state (
    date TEXT PRIMARY KEY,
    daily_pnl REAL,
    weekly_pnl REAL,
    drawdown_pct REAL,
    gross_exposure REAL,
    net_exposure REAL,
    portfolio_beta REAL,
    factor_risk_pct REAL,
    specific_risk_pct REAL,
    max_mctr_ticker TEXT,
    vix REAL,
    circuit_breaker_triggered TEXT,
    num_rejections INTEGER
);
```

Daily update entry point will write to this table.

---

## Entry Point (run_risk_check.py)

```bash
python run_risk_check.py                    # Full risk report
python run_risk_check.py --stress          # Stress tests only
python run_risk_check.py --tail-only       # Tail risk only
python run_risk_check.py --clear-halt      # Clear kill-switch lock file
python run_risk_check.py --pre-trade TICKER SHARES  # Check single trade
```

Output:
```
=== RISK REPORT ===
Portfolio Factor Risk: X.X%
Portfolio Specific Risk: X.X%
Top MCTR: TICKER (X.X%)
Circuit breakers: ALL CLEAR / TRIGGERED: {action}
Tail risk: VIX=X.X, gross adjustment: -X%
Stress test worst case: {scenario} (-XX.X%)
Correlations: long avg=X.XX, short avg=X.XX
72hr alerts: X
```

---

## Implementation Notes
- Read current positions from `portfolio.positions` table (from Layer 4)
- Read scores from `scores` table (from Layer 2)
- Read prices from `daily_prices` table (from Layer 1)
- The `halt.lock` file is CRITICAL — must be checked before ALL order submissions
- Correlation requires at least 60 days of returns for each ticker; if less, skip that pair
- Position P&L uses mark-to-market: (current_price - entry_price) * shares
- Drawdown uses NAV peak — store in risk_state table
- This layer operates as a "veto layer" — it can reject any trade from any layer
- Required packages: scipy (for optimization already in L4), pandas, numpy

## What This Builds
- Barra-style factor risk model with factor vs specific decomposition
- Marginal risk contribution tracking (MCTR)
- 8 absolute pre-trade veto checks
- Circuit breakers with automatic actions (size down, close all, kill switch)
- Tail risk auto-reduction on VIX/credit spikes
- 6 stress test scenarios
- Correlation effective-bets monitoring
- Lock file mechanism for fail-safe trading halt
