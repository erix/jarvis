# JARVIS — Long/Short Equity Hedge Fund System

> **J**arvis **A**nalyst **R**isk-managed **V**alue-**I**ntegrated **S**ystem

A full-stack long/short equity hedge fund research and execution platform. Scores 500+ US equities across 8 quantitative factors, runs Claude-powered fundamental analysis, optimizes portfolios with mean-variance or conviction-weighted methods, enforces strict pre-trade risk vetoes, and executes via IBKR — all visible through a dark-themed Streamlit dashboard.

Built from a 7-layer specification extracted frame-by-frame from a video walkthrough.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 7 — Reporting + Streamlit Dashboard                  │
│  JARVIS chat, P&L attribution, tear sheets, LP letters   │
├─────────────────────────────────────────────────────────────┤
│  LAYER 6 — Execution (IBKR Gateway)                        │
│  Dry-run default, limit orders, slippage tracking         │
├─────────────────────────────────────────────────────────────┤
│  LAYER 5 — Risk Management                                 │
│  Barra-style factor model, MCTR, 8 veto checks, alerts    │
├─────────────────────────────────────────────────────────────┤
│  LAYER 4 — Portfolio Construction                          │
│  MVO (scipy SLSQP) + Conviction-Tilt optimizer            │
├─────────────────────────────────────────────────────────────┤
│  LAYER 3 — Claude AI Analysis (OpenRouter)                │
│  Earnings call, filing forensics, insider pattern analysis│
├─────────────────────────────────────────────────────────────┤
│  LAYER 2 — Scoring Engine                                  │
│  8 factors × 27 sub-factors, sector-relative 0–100 ranks  │
├─────────────────────────────────────────────────────────────┤
│  LAYER 1 — Data Infrastructure                             │
│  S&P 500 universe, OHLCV, fundamentals, insider, 13F, VIX │
└─────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Clone & Enter

```bash
cd /home/erix/Projects/jarvis   # or your preferred path
```

### 2. Virtual Environment

```bash
./scripts/setup_environment.sh
source .venv/bin/activate
```

### 3. Environment Variables

Copy `.env.example` to `.env` and set your keys:

```bash
# Required for Layer 3 (Claude analysis) and Layer 7 (JARVIS chat)
JARVIS_AI_PROVIDER=openrouter     # openrouter | codex
OPENROUTER_API_KEY=sk-or-v1-...
CODEX_MODEL=gpt-5.5              # Used when JARVIS_AI_PROVIDER=codex

# Required for Layer 6 (IBKR execution)
IB_GATEWAY_HOST=192.168.11.202
IB_GATEWAY_PORT=4001          # 4001 = paper, 4002 = live
IB_CLIENT_ID=1

# Optional — enhances data quality
POLYGON_API_KEY=
FMP_API_KEY=
FRED_API_KEY=

# Model selection (OpenRouter naming)
JARVIS_MODEL=anthropic/claude-sonnet-4-6
```

To use a Codex subscription instead of OpenRouter API calls:

```bash
python run_codex_login.py
export JARVIS_AI_PROVIDER=codex
export JARVIS_MODEL=gpt-5.5
```

Codex OAuth tokens are stored locally in `.jarvis/codex_oauth.json` and are gitignored.
You can also do this from the dashboard under **VII Settings**.

### 4. Initialise Database

```bash
python run_data.py          # First run: ~1–2 hours for full S&P 500
# Subsequent runs: ~10 min (incremental)
```

### 5. Score Universe

```bash
python run_scoring.py       # Calculates all 8 factor scores per ticker
```

### 6. Launch Dashboard

```bash
python run_dashboard.py     # Opens http://localhost:8502
```

---

## Entry Points

| Script | What it does | Typical use |
|--------|-------------|-------------|
| `run_data.py` | Refresh market data & fundamentals | Daily before market open |
| `run_scoring.py` | Recalculate factor scores | After `run_data.py` |
| `run_analysis.py` | Claude AI qualitative analysis | On-demand per ticker |
| `run_portfolio.py` | Generate target portfolio | `--whatif` for preview |
| `run_risk_check.py` | Risk report & veto checks | Before any rebalance |
| `run_execution.py` | Place orders via IBKR | `--dry-run` → then `--execute` |
| `run_reporting.py` | Tear sheet, LP letter, win/loss | Weekly or after rebalance |
| `run_dashboard.py` | Launch Streamlit UI | As needed |
| `run_daily.py` | Full pipeline (data + score + report) | Cron/launchd |

---

## Dashboard — 6 Tabs

| Tab | Content |
|-----|---------|
| **I Portfolio** | JARVIS AI chat, 10 live metrics, VIX regime |
| **II Research** | Factor heatmap, candidate cards (approve/reject/execute) |
| **III Risk** | Decomposition donut, MCTR, stress tests, correlation heatmap |
| **IV Performance** | Equity curve vs SPY, drawdown, monthly grid, rolling Sharpe |
| **V Execution** | Slippage KPIs, order log, worst fills, short availability |
| **VI Letter** | Daily LP letter with regenerate button |

Theme: `#0b0e17` background, `#6366f1` indigo accent, `#10b981` long, `#f43f5e` short.

---

## Safety & Discipline

This system is built with several hard constraints to prevent accidental live trading:

1. **`--dry-run` is the default** — `run_execution.py` does nothing until you explicitly pass `--execute`.
2. **Manual confirmation** — every `--execute` run requires typing `y` at a confirmation prompt.
3. **Pre-trade veto** — Layer 5 runs 8 absolute checks (liquidity, position size, sector, beta, correlation, etc.). Any failure rejects the trade.
4. **Circuit breakers alert only** — they log warnings and create a `halt.lock` file. They **never** auto-close positions. New trades are blocked until you run `--clear-halt`.
5. **Kill switch** — drawdown > 8 % triggers a lock file. Manual operator intervention (`run_risk_check.py --clear-halt`) is required to resume.
6. **Paper default** — IBKR Gateway port 4001 (paper). Port 4002 (live) requires explicit confirmation.
7. **Positions saved as `pending`** — Layer 4 generates targets, but they remain in `pending` status until you click **Execute** in the dashboard.

---

## Data Sources

| Source | Data | Module |
|--------|------|--------|
| Wikipedia | S&P 500 constituents | `data/universe.py` |
| yfinance | Daily OHLCV, fundamentals | `data/market_data.py`, `data/fundamentals.py` |
| Financial Modeling Prep | Earnings transcripts, SEC filings | `data/transcripts.py` |
| EDGAR | 10-K risk factors | `data/filings.py` |
| Yahoo / FRED | VIX, credit spreads | risk model |

---

## Key Design Decisions

- **SQLite as single source of truth** — 15 tables, zero external DB dependencies.
- **Sector-relative scoring** — all percentiles computed **within** each GICS sector.
- **OpenRouter, not Anthropic direct** — flexible model switching, lower latency.
- **Regime-conditional weights** — factor blend shifts automatically with VIX.
- **Incremental updates** — `run_data.py` skips already-cached bars on subsequent runs.

---

## Project Structure

```
jarvis/
├── analysis/         # Layer 3 — Claude AI analysis
├── cache/            # SQLite DB + cached files
├── dashboard/        # Layer 7 — Streamlit app + tabs
├── data/             # Layer 1 — Ingestion
├── execution/        # Layer 6 — IBKR order execution
├── factors/          # Layer 2 — Quantitative scoring
├── output/           # Reports, letters, logs, CSVs
├── portfolio/        # Layer 4 — Construction + optimisation
├── prompts/          # Original build specifications (reference)
├── reporting/        # Layer 7 — Tear sheets, P&L attribution
├── risk/             # Layer 5 — Factor model + veto layer
├── run_*.py          # One entry point per layer
├── config.yaml       # All tunable parameters
├── requirements.txt
└── .env              # API keys (gitignored)
```

---

## Configuration

All tunable parameters live in `config.yaml`:

```yaml
portfolio:
  num_longs: 20
  num_shorts: 20
  max_position_pct: 5.0
  gross_exposure: 165.0
  net_exposure: [-10.0, 15.0]
  max_portfolio_beta: 0.20
  turnover_budget_pct: 30.0

risk:
  daily_loss_alert: 1.5
  drawdown_kill_switch: 8.0
  max_sector_pct: 25.0

execution:
  default_port: 4001        # paper
  confirmation_required: true
```

---

## Example Session

```bash
# 1. Morning data refresh
python run_data.py --no-filings --no-13f

# 2. Re-score universe
python run_scoring.py

# 3. Dry-run rebalance
python run_portfolio.py --whatif --optimize-method mvo

# 4. Risk check
python run_risk_check.py --tail-only

# 5. If approved: review in dashboard, then execute
python run_execution.py --execute
# → "You are about to place N orders... Confirm: y/N:"

# 6. Generate reports
python run_reporting.py --date $(date +%Y-%m-%d)
```

---

## Testing

Each layer ships with a `--dry-run` or `--whatif` mode:

```bash
python run_portfolio.py --whatif --optimize-method mvo
python run_execution.py --dry-run
python run_risk_check.py --stress
python run_analysis.py --estimate-cost --ticker AAPL
```

---

## Daily Automation

A `run_daily.py` script is provided for cron or macOS `launchd`:

```bash
# crontab — weekdays at 17:15 UTC
15 17 * * 1-5 cd /home/erix/Projects/jarvis && .venv/bin/python run_daily.py >> output/logs/cron.log 2>&1
```

`run_daily.py` refreshes data, re-scores, and generates reports. It **does not** trigger portfolio rebalances or order execution — those remain manual.

---

## Tech Stack

| Component | Tool |
|-----------|------|
| Data ingestion | yfinance, requests, BeautifulSoup |
| Database | SQLite3 |
| Scoring | pandas, numpy |
| Optimisation | scipy.optimize (SLSQP) |
| AI analysis | OpenRouter → Claude (anthropic/claude-sonnet-4-6) |
| Risk model | numpy + scipy.linalg |
| Execution | ib_insync |
| Dashboard | Streamlit + plotly |
| Broker | IBKR Gateway (paper/live) |

---

## License

MIT — use at your own risk. This is a research and educational system. Past performance of the scoring model does not guarantee future results.

---

## Acknowledgements

- Architecture inspired by a video walkthrough of a quantitative hedge fund system.
- Factor definitions draw heavily from Quantitative Momentum, Quality Minus Junk, and the Barra USE4 risk model.
- Execution layer uses `ib_insync` by Ewald de Wit.

---

> *"The playbook says X is a valid argument."* — JARVIS
