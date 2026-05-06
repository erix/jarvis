# Repository Guidelines

## Project Overview

JARVIS is a Python long/short equity hedge fund research, risk, portfolio, execution, and Streamlit dashboard system. It is organized as seven layers:

- `data/`: universe, market data, fundamentals, filings, transcripts, short interest.
- `factors/`: 8-factor scoring engine and regime weights.
- `analysis/`: OpenRouter/Claude-powered qualitative analysis and caching.
- `portfolio/`: construction, optimizers, exposure, rebalance state.
- `risk/`: factor risk, pre-trade veto checks, circuit breakers, stress/tail risk.
- `execution/`: IBKR broker/order execution flow.
- `dashboard/` and `reporting/`: Streamlit UI, tear sheets, LP letters, attribution.

Entry points are top-level `run_*.py` scripts. Configuration is in `config.yaml`; secrets belong in `.env`, which is gitignored.

## Setup

Use a virtual environment and install the repo requirements:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Common environment variables:

```bash
OPENROUTER_API_KEY=
IB_GATEWAY_HOST=
IB_GATEWAY_PORT=4001
IB_CLIENT_ID=1
POLYGON_API_KEY=
FMP_API_KEY=
FRED_API_KEY=
JARVIS_MODEL=anthropic/claude-sonnet-4-6
```

## Common Commands

Run focused data refreshes while developing:

```bash
python run_data.py --tickers AAPL MSFT --prices-only
python run_data.py --tickers AAPL MSFT --no-filings --no-13f
```

Run scoring and portfolio workflows:

```bash
python run_scoring.py
python run_portfolio.py --whatif --optimize-method mvo
python run_risk_check.py --tail-only
```

Run execution only in dry-run mode unless the user explicitly asks for live execution:

```bash
python run_execution.py --dry-run
```

Launch the dashboard:

```bash
python run_dashboard.py
```

There is no test suite or pytest config currently checked in.

## Safety Rules

- Treat this as financial/trading software. Keep changes conservative and preserve risk checks.
- Do not run `python run_execution.py --execute` unless the user explicitly requests it for this session.
- Do not change default IBKR behavior from paper/dry-run to live execution.
- Do not add, print, or commit secrets from `.env`.
- Be careful with commands that fetch broad datasets or run AI analysis; they may be slow, hit external APIs, or incur costs.
- Preserve manual confirmations and halt/kill-switch behavior.

## Coding Conventions

- Keep changes scoped to the relevant layer and follow existing module patterns.
- Use standard Python modules and structured parsers/APIs where available.
- Use `logging` for runtime diagnostics in entry points and services.
- Keep cache/output artifacts out of git; `.gitignore` already excludes DBs, logs, reports, generated CSV/PDF/PNG files, virtualenvs, and `.env`.
- If adding tests, prefer focused pytest tests near the changed behavior and document the command used to run them.
