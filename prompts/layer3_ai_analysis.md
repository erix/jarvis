# JARVIS — Layer 3 Prompt

Build Layer 3 of the JARVIS long/short equity hedge fund system. Layers 1 (data) and 2 (scoring) are complete at `/home/erix/Projects/jarvis/`.

This layer is the **Claude AI Analysis** — qualitative/fundamental analysis powered by Claude API. It reads earnings calls, financials, risk factors, and insider data to generate a 60/40 blend with the quantitative scores.

## Project Files to Create

```
analysis/
├── api_client.py           # Anthropic SDK wrapper
├── cost_tracker.py         # Track usage, enforce ceiling
├── cache.py               # SQLite analysis cache (TTL)
├── earnings_analyzer.py   # Earnings call transcript analysis
├── filing_analyzer.py     # 10-K financial forensics
├── risk_analyzer.py       # 10-K risk factor analysis
├── insider_analyzer.py    # Form 4 insider pattern analysis
├── sector_analysis.py     # Aggregate sector-level analysis
├── combined.py            # 60/40 quant + Claude blend
├── report_generator.py    # Markdown reports per ticker
└── __init__.py
run_analysis.py
```

## Requirements
- Requires `ANTHROPIC_API_KEY` from `.env`
- Default model: `claude-sonnet-4-5` (configurable)
- Use prompt caching on system prompts
- Estimated cost: $2-5 per full run (20 long + 20 short candidates)
- Cache results per filing period to avoid re-analysis

---

## 1. API Client (analysis/api_client.py)

- Anthropic SDK wrapper
- Prompt caching (cache system prompts for 1h)
- Retry logic with exponential backoff (3 retries)
- JSON extraction helper — if Claude returns text with JSON inside code blocks, extract it
- Token count estimation for cost tracking
- Function: `analyze(ticker, analyzer_type, system_prompt, user_prompt, cache=True)` returns dict

---

## 2. Cost Tracker (analysis/cost_tracker.py)

- Monitor after each API call: input tokens, output tokens, cache tokens
- Hard cost ceiling: $10 per run (configurable)
- If ceiling hit, skip remaining tickers and log warning
- Track total spend per run

---

## 3. Analysis Cache (analysis/cache.py)

- SQLite table: `analysis_cache(id, ticker, analyzer, artifact_hash, result_json, created_at, ttl_hours)`
- Key by: (analyzer_type, ticker, artifact_id=filing_period_hash)
- TTL-based eviction: if newer filing available, invalidate old cache
- Function: `get_cached(ticker, analyzer)`, `set_cache(ticker, analyzer, result, ttl=168)`

---

## 4. Earnings Call Analyzer (analysis/earnings_analyzer.py)

**Input:** Earnings transcript text from `earnings_transcripts` table
**Prompt to Claude:**
```
Analyze this earnings call transcript for {TICKER}. Focus on:
1. Management confidence level (1-10)
2. Revenue guidance: beat/miss/maintain vs expectations
3. Margin outlook: expanding/contracting/stable
4. Capital allocation priorities
5. Key risks or concerns mentioned
6. Any guidance changes
7. Overall tone: bullish/neutral/bearish

Return JSON with: {management_confidence, revenue_guidance, margin_outlook, capital_allocation, key_risks, guidance_change, overall_tone, one_line_summary}
```

**Output JSON fields:**
- `management_confidence`: float 1-10
- `revenue_guidance`: "beat" | "miss" | "maintain"
- `margin_outlook`: "expanding" | "contracting" | "stable"
- `capital_allocation`: list of priorities
- `key_risks`: list of strings
- `guidance_change`: "up" | "down" | "unchanged"
- `overall_tone`: "bullish" | "neutral" | "bearish"
- `one_line_summary`: string

---

## 5. Filing Analyzer (analysis/filing_analyzer.py)

**Input:** Fundamentals data (from `fundamentals` table)
**Prompt to Claude:**
```
Perform a forensic accounting review of {TICKER} based on these fundamentals:
- Financial Health (1-10): assess overall quality
- Earnings Quality (1-10): is net income supported by operating cash flow?
- Revenue Quality (1-10): are earnings recurring or one-time?
- Balance Sheet Health (1-10): debt levels, liquidity
- Accruals Commentary: any red flags?

Red Flags: CFO < NI, AR growing faster than revenue, inventory piling, goodwill > 50% assets, operating lease explosion.
Green Flags: FCF > NI for 4+ quarters, buybacks > dilution, insider buying.

Return JSON with: {financial_health, earnings_quality, revenue_quality, balance_sheet_health, red_flags, green_flags, accruals_commentary, one_line_summary}
```

**Output JSON fields:**
- `financial_health`: float 1-10
- `earnings_quality`: float 1-10
- `revenue_quality`: float 1-10
- `balance_sheet_health`: float 1-10
- `red_flags`: list of strings
- `green_flags`: list of strings
- `accruals_commentary`: string
- `one_line_summary`: string

---

## 6. Risk Analyzer (analysis/risk_analyzer.py)

**Input:** 10-K "Risk Factors" section (from cached filings in `filings` table)
**Prompt to Claude:**
```
Analyze the Risk Factors section of {TICKER}'s latest 10-K filing.
- Strip HTML (if raw), cap at 80K characters
- Separate material risks from boilerplate language
- Flag new risks vs prior filing
- Assess severity: LOW/MEDIUM/HIGH
- Identify macro sensitivity

Return JSON: {new_risks, material_risks, boilerplate_percentage, risk_severity, macro_sensitivity, risk_trend, one_line_summary}
```

**Output JSON fields:**
- `new_risks`: list of strings
- `material_risks`: list of strings
- `boilerplate_percentage`: float 0-1
- `risk_severity`: "LOW" | "MEDIUM" | "HIGH"
- `macro_sensitivity`: list of factors
- `risk_trend`: "improving" | "stable" | "worsening"
- `one_line_summary`: string
- Return `None` if no 10-K cached

---

## 7. Insider Analyzer (analysis/insider_analyzer.py)

**Input:** Form 4 data from `insider_transactions` table (last 90 days)
**Prompt to Claude:**
```
Analyze insider trading activity for {TICKER} over the last 90 days.
- Transaction codes P (purchase) and S (sale) only
- Weight CEO/CFO purchases 3x
- Look for cluster buys (3+ insiders within 30 days)
- Distinguish routine selling vs meaningful buying

Return JSON: {signal_strength, confidence, key_transactions, pattern, timing_analysis, cluster_summary, one_line_summary}
```

**Output JSON fields:**
- `signal_strength`: "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG_SELL"
- `confidence`: float 0.0-1.0
- `key_transactions`: list of dicts
- `pattern`: "accumulation" | "distribution" | "neutral"
- `timing_analysis`: string
- `cluster_summary`: string
- `one_line_summary`: string
- Return `None` if no insider data

---

## 8. Sector Analysis (analysis/sector_analysis.py)

For each sector:
- Gather all Claude analysis results for tickers in that sector
- Rank tickers by fundamental quality + positioning
- Identify top long idea and top short idea per sector
- Output sector outlook string

---

## 9. Combined Score (analysis/combined.py)

Blend quantitative + Claude:
- `final_score = 0.60 * quantitative_composite(L2) + 0.40 * claude_fundamental_avg`
- Claude fundamental avg = average of available non-None analyzer scores
- If no Claude analysis available, use 100% quantitative (no penalty)
- Re-rank within sector
- Add to `scores` table: `claude_score`, `combined_score`, `signal` columns

Signal mapping:
- combined_score >= 80: "STRONG_BUY"
- 70-79: "BUY"
- 40-69: "HOLD"
- 30-39: "SELL"
- < 30: "STRONG_SELL"

---

## 10. Report Generator (analysis/report_generator.py)

For each long/short candidate:
- Markdown report with:
  - All quantitative scores (from Layer 2)
  - Claude analysis summaries (earnings, filing, risk, insider)
  - Upcoming catalysts (earnings dates from calendar data)
  - Risk flags
- Save to: `output/reports_{timestamp}/{TICKER}.md`

---

## Entry Point (run_analysis.py)

```bash
python run_analysis.py --estimate-cost          # Show estimated cost only
python run_analysis.py --ticker AAPL             # Analyze single ticker
python run_analysis.py --sector Technology        # Analyze all in sector  
python run_analysis.py --candidates               # Analyze top 20 long + 20 short from L2 scores
python run_analysis.py                           # Full run with auto-candidate selection
```

Execution flow:
1. Read scores table to identify top candidates
2. For each candidate:
   a. Check cache — skip if fresh
   b. Run analyzers (earnings, filing, risk, insider) in parallel where possible
   c. Calculate combined score
   d. Generate markdown report
3. Save combined scores to DB
4. Print summary:
```
Layer 3 complete: X tickers analyzed
Claude API calls: X | Cost: $X.XX
Cache hits: X | Misses: X
Reports generated: X
Runtime: Xm Ys
```

---

## Implementation Notes
- Use the existing `ANTHROPIC_API_KEY` from `.env`
- Must install `anthropic` package into the `.venv`
- The Anthropic client needs `anthropic.Anthropic()` with the API key
- System prompts should be cached (use `cache_control` in Anthropic API)
- If any single analyzer fails for a ticker, skip that analyzer but continue with others
- Limit filing text to 80K chars to stay within context window
- For files table lookups, join with `tickers` table to get CIK for SEC filings
- Save raw Claude responses alongside JSON for debugging
- Cost estimates: ~$0.05-0.15 per ticker (depending on filing length), $2-5 for 40 tickers

## What This Builds
- 4 Claude-powered analyzers reading earnings, financials, risk factors, and insider data
- Combined 60/40 quantitative + fundamental score
- TTL-cached per filing period
- Markdown reports per candidate
- Estimated cost: $2-5 per full run using Sonnet
