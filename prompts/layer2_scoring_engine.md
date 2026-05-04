# JARVIS — Layer 2 Prompt

Build Layer 2 of the JARVIS long/short equity hedge fund system. Layer 1 (data infrastructure) is already built at `/home/erix/Projects/jarvis/` with working SQLite DB, universe of 521 tickers (503 SP500 + benchmarks), daily prices, fundamentals, and more.

This layer is the **scoring engine** — 8 factors with 27 sub-factors. All scores are 0-100 percentile rank WITHIN each GICS sector. Equal-weight sub-factors within each parent factor.

## Project Files to Create (in `/home/erix/Projects/jarvis/`)

```
jARVIS/
├── factors/
│   ├── momentum.py            # Factor 1: 6 sub-factors
│   ├── value.py               # Factor 2: 6 sub-factors
│   ├── quality.py             # Factor 3: 8 sub-factors
│   ├── growth.py              # Factor 4: 5 sub-factors
│   ├── revisions.py           # Factor 5: 3 sub-factors
│   ├── short_interest.py      # Factor 6: 3 sub-factors
│   ├── insider.py             # Factor 7: 3 sub-factors
│   ├── institutional.py       # Factor 8: 3 sub-factors
│   ├── composite.py           # Blend all 8 factors into score
│   ├── regime_weights.py      # VIX-based weight adjustment
│   └── crowding.py            # Detect factor crowding
├── run_scoring.py           # Entry point
├── config.yaml              # Update with scoring config
```

## Database Schema Already Exists (jarvis.db)
Tables: tickers, daily_prices, fundamentals, filings, insider_transactions, institutional_holdings, short_interest, earnings_transcripts

Read from `data/` modules and `cache/jarvis.db` directly.

---

## FACTOR 1: MOMENTUM (factors/momentum.py) — 6 sub-factors

For each stock, compute:

1. **12-1 month return** — Return from 12 months ago to 1 month ago (skip recent 1mo to avoid short-term reversal)
2. **6-month return** — Raw 6-month return
3. **3-month return** — Raw 3-month return
4. **Acceleration** — Most recent 3mo minus older 3mo (momentum of momentum)
5. **52-week-high proximity** — current_price / 52_week_high (George & Hwang 2004 signal)
6. **Relative strength vs sector ETF** — Stock's 6m return minus its sector ETF's 6m return (isolates stock-specific momentum from sector beta)

For each sub-factor, rank 0-100 percentile within sector. Average to get Momentum score.

---

## FACTOR 2: VALUE (factors/value.py) — 6 sub-factors

Read from fundamentals table or compute from market_data:

1. **Forward earnings yield** — 1/forward_PE (or trailing PE if forward unavailable)
2. **Book-to-price** — Book Value per Share / Price
3. **FCF yield** — FCF / Market Cap
4. **EV/EBITDA** (inverted for scoring — lower = higher score)
5. **Shareholder yield** — (TTM buybacks + dividends) / Market Cap
6. **Sales-to-EV** — Revenue / EV (works where P/E breaks on negative earnings)

---

## FACTOR 3: QUALITY (factors/quality.py) — 8 sub-factors

Read from fundamentals table:

1. **ROE stability** — Std dev of last 12 quarters ROE, inverted (lower volatility = higher score)
2. **Gross margin level** — Latest gross margin
3. **Gross margin trend** — Latest minus 4 quarters ago
4. **Debt/equity** — Inverted (lower D/E = higher score)
5. **CFO/NI** — Operating Cash Flow / Net Income (higher = more real earnings)
6. **Accruals ratio** — (NI - CFO) / Total Assets, inverted (high accruals predict underperformance)
7. **Piotroski F-Score** — 9 binary signals already stored in fundamentals table. Normalized 0-9 → 0-100.
   - Store in tickers table with color codes: >=7 green, 4-6 neutral, amber if distress
8. **Altman Z-Score** — Already computed in fundamentals. Convert to score: >2.99=safe (green), 1.81-2.99=grey zone, amber if distress (already stored)

---

## FACTOR 4: GROWTH (factors/growth.py) — 5 sub-factors

Read from fundamentals or compute:

1. **Revenue growth YoY**
2. **Earnings growth YoY**
3. **Revenue growth acceleration** — Latest YoY minus 4Q-ago YoY
4. **R&D intensity** — R&D expense / Revenue (higher in tech/healthcare tends to outperform long-term)
5. **FCF growth YoY** — Harder to manipulate than earnings

---

## FACTOR 5: ESTIMATE REVISIONS (factors/revisions.py) — 3 sub-factors

Since we don't have a full analyst estimates API yet, implement with placeholder that:
- Reads estimate data from fundamentals or computes from price momentum as proxy
- 30-day, 60-day, 90-day change in next-Q EPS estimate
- **Degenerate mode**: If no estimates available, all scores = 50 (neutral)
- After ~30 days of price data snapshots, can derive from price changes as proxy

---

## FACTOR 6: SHORT INTEREST (factors/short_interest.py) — 3 sub-factors

Read from short_interest table:

1. **Short percent of float**
2. **Days to cover**
3. **Change in short interest vs prior period**
- For LONGS: declining short interest → higher score
- For SHORTS: increasing short interest → higher score

---

## FACTOR 7: INSIDER ACTIVITY (factors/insider.py) — 3 sub-factors

Read from insider_transactions table:

1. **Net dollar flow over 90 days** from Form 4 data (only transaction codes P and S)
2. **CEO/CFO open-market purchases** weighted 3x vs other insiders
3. **Cluster-buy flag** (3+ insiders buying within 30 days) = bonus
- No data → sector median (50)

---

## FACTOR 8: INSTITUTIONAL FLOW (factors/institutional.py) — 3 sub-factors

Read from institutional_holdings table:

1. Number of tracked funds holding the stock
2. Net change in aggregate holdings vs prior quarter
3. Multi-fund simultaneous opening flag (3+ funds opening new positions same ticker same quarter)

---

## COMPOSITE SCORE (factors/composite.py)

Weighted blend of all 8 factors:

| Factor | Weight |
|--------|--------|
| Momentum | 0.20 |
| Quality | 0.15 |
| Value | 0.15 |
| Estimate Revisions | 0.15 |
| Insider Activity | 0.10 |
| Growth | 0.10 |
| Short Interest | 0.10 |
| Institutional Flow | 0.05 |

Multiply each factor's percentile score by weight, sum, then re-rank within sectors to get 0-100 composite.

Output table in DB: `scores(ticker, date, momentum, value, quality, growth, revisions, short_interest, insider, institutional, composite, sector, is_long_candidate, is_short_candidate)`

Top 30 per sector = long candidates, bottom 30 = short candidates (or just global top 107 long / 96 short as in original)

---

## REGIME-CONDITIONAL WEIGHTS (factors/regime_weights.py)

Adjust composite weights based on VIX level (from daily_prices for ^VIX):

- **VIX &lt; 15 (low vol):** Momentum +0.05, Growth +0.05, Value -0.05, Short Interest -0.05
- **VIX 15-25 (normal):** Standard weights
- **VIX &gt; 25 (high vol):** Quality +0.05, Value +0.05, Momentum -0.05, Growth -0.05

Store current regime in scores table or print at runtime.

---

## CROWDING DETECTION (factors/crowding.py)

1. Compute daily factor returns by running cross-sectional regression: `r_i = alpha + sum_k(factor_k_i * return_of_factor_k) + epsilon`
2. Calculate pairwise correlations between factor returns (90d rolling)
3. If any factor pair correlation &gt; 0.7 AND deviation from academic baseline &gt; 0.4 → flag as crowded
4. Output: `output/crowding_alerts.json`

---

## Entry Point (run_scoring.py)

```bash
python run_scoring.py              # Score all 521 tickers
python run_scoring.py --ticker AAPL  # Single stock mode
```

Steps:
1. Load universe from DB
2. For each ticker, compute all 8 factor scores (percentile within sector)
3. Calculate composite with regime weights
4. Save to `scores` table
5. Generate `output/scored_universe_latest.csv`
6. Run crowding detection
7. Print summary:
```
Layer 2 complete: X tickers scored
Top long candidates: TICKER1, TICKER2, ...
Top short candidates: TICKER1, TICKER2, ...
VIX regime: {low|normal|high}
Crowding alerts: X
Runtime: Xm Ys
```

---

## Implementation Notes
- ALL percentile ranks are WITHIN each GICS sector. Use `scipy.stats.rankdata()` or pandas `rank(pct=True)*100`
- For factors with missing data (e.g., revisions), use sector median (50)
- Store scores in SQLite `scores` table (add to DB schema on first run)
- Each factor module should expose: `calculate(ticker_symbol, cursor_or_db_path, lookback_days=90)` returning a score dict
- Composite module reads all factor scores and blends them
- Use pandas for calculations, numpy for math operations
- `run_scoring.py` should handle full run and `--ticker` single-stock mode
- Profile performance — this is the heaviest layer computationally
- Leave revision scores as 50 if insufficient data exists — don’t crash
- Piotroski and Altman already exist in fundamentals table thanks to Layer 1 — just read them

## What This Builds
- 8 factor scoring modules with 27 sub-factors
- Composite score with sector-relative percentile ranking
- VIX regime-conditional weight switching
- Crowding detection
- SQLite scores table + CSV export
