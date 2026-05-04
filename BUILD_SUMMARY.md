# JARVIS Build Summary
## Full System — 7 Layers Complete

**Built:** Monday, May 4, 2026
**Location:** `/home/erix/Projects/jarvis/`
**Total Cost:** $8.60 (Claude Code builds)
**Build API:** OpenRouter (anthropic/claude-sonnet-4-6)

---

## What Was Built

| Layer | Files | Status | Build Cost |
|-------|-------|--------|------------|
| L1 Data Infrastructure | 9 | ✅ | $0.90 |
| L2 Scoring Engine | 10 | ✅ | $1.18 |
| L3 Claude AI Analysis | 8 | ✅ | $2.15 |
| L4 Portfolio Construction | 9 | ✅ | $1.20 |
| L5 Risk Management | 10 | ✅ | $1.47 |
| L6 IBKR Execution | 4 | ✅ | (part of L6+L7) |
| L7 Dashboard + Reports | 12 | ✅ | $1.70 |
| **Total** | **~83 Python files** | **✅ All Complete** | **$8.60** |

---

## Database — 15 Tables

```sql
tickers              — 503 S&P 500 tickers + benchmarks
daily_prices         — 3yr OHLCV, ~390K bars
fundamentals         — 24 derived ratios from income/balance/cash flow
filings              — Cached 10-K risk factors
insider_transactions — Form 4 data (P/S codes only)
institutional_holdings — 13F filings
short_interest       — Short float + days to cover
earnings_transcripts — FMP earnings call transcripts
scores               — 8 factor scores + composite (0-100) per ticker
analysis_cache     — Claude analysis results (TTL-based)
positions            — Current portfolio (shares: +long/-short)
portfolio_history  — Order/trade history
risk_state           — Daily P&L, drawdown, exposures
rejections           — Pre-trade veto rejection log
```

---

## Entry Points

| Script | Purpose | Example |
|--------|---------|---------|
| `run_data.py` | Refresh market data | `--no-filings --no-13f` for quick update |
| `run_scoring.py` | Recalculate factor scores | `--ticker AAPL` single-ticker mode |
| `run_analysis.py` | Claude AI analysis | `--estimate-cost`, `--ticker AAPL` |
| `run_portfolio.py` | Portfolio construction | `--whatif --optimize-method mvo` |
| `run_risk_check.py` | Risk report + veto checks | `--pre-trade TICKER SHARES` |
| `run_execution.py` | Order execution | `--dry-run` (default) / `--execute` |
| `run_reporting.py` | Tear sheet + LP letter | `--weekly` for commentary |
| `run_dashboard.py` | Launch Streamlit UI | Opens on port 8502 |
| `run_daily.py` | Full daily pipeline | For cron/launchd scheduling |

---

## Safety Features

- **NO auto-order submission** — default is `--dry-run`; `--execute` requires explicit "y/N" confirmation
- **Circuit breakers only ALERT** — they block new trades via lock file, never auto-close positions
- **Kill switch** — creates halt.lock; manual `--clear-halt` required to resume
- **Pre-trade veto** — 8 checks, any failure = REJECT trade (closing trades always approved)
- **Paper trading default** — port 4001; live port 4002 requires explicit override
- **Position approvals required** — all new positions saved as `pending` until manual Execute clicked

---

## Dashboard Pages (6 tabs)

1. **Portfolio** — JARVIS AI chat + 10 metrics (VIX, crowding, insider events)
2. **Research** — Factor heatmap + candidate cards (approve/reject/execute)
3. **Risk** — Decomposition donut + MCTR + stress tests + correlation heatmap
4. **Performance** — Equity curve, monthly grid, drawdown, Sharpe, sector alpha
5. **Execution** — Slippage KPIs, orders log, worst fills, short availability
6. **Letter** — Daily LP letter with "Regenerate" button

---

## IBKR Connection

- **Host:** 192.168.11.202
- **Paper:** port 4001 (default)
- **Live:** port 4002 (requires explicit confirmation)
- **Protocol:** ib_insync
- **Test:** `python run_execution.py --dry-run`

---

## Environment Variables (.env)

```bash
OPENROUTER_API_KEY=sk-or-v1-...
IB_GATEWAY_HOST=192.168.11.202
IB_GATEWAY_PORT=4001  # 4002 for live
IB_CLIENT_ID=1
JARVIS_MODEL=anthropic/claude-sonnet-4-6
```

---

## Next Steps (Post-Build)

1. [ ] Test IBKR connection with `python run_execution.py --dry-run`
2. [ ] Verify dashboard launches: `python run_dashboard.py`
3. [ ] Run first full pipeline: `python run_daily.py`
4. [ ] Test Claude analysis with `python run_analysis.py --estimate-cost`
5. [ ] Set up daily cron job for 17:15 UTC
6. [ ] Review and approve first rebalance candidates in dashboard
7. [ ] Paper trade for 2 weeks before considering live

---

## Notes

- All layers use SQLite as the single source of truth
- Analysis results cached per filing period to minimize API costs
- The system is currently configured for **paper trading only**
- All prompts and specs preserved in `/home/erix/Projects/jarvis/prompts/`
