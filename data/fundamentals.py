"""Financial statements + 24 derived ratios via yfinance."""
import sqlite3
import logging
import os
import time
from datetime import datetime
from typing import List, Optional, Dict, Any

import numpy as np
import pandas as pd
import yaml
import yfinance as yf
from tqdm import tqdm

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _get_db(config: dict) -> sqlite3.Connection:
    db_path = os.path.join(os.path.dirname(__file__), "..", config["data"]["db_path"])
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals (
            ticker TEXT NOT NULL,
            report_date TEXT NOT NULL,
            period TEXT NOT NULL,
            revenue REAL, net_income REAL, ebitda REAL, operating_income REAL,
            gross_profit REAL, total_assets REAL, total_liabilities REAL,
            total_equity REAL, current_assets REAL, current_liabilities REAL,
            cash REAL, accounts_receivable REAL, inventory REAL,
            goodwill REAL, retained_earnings REAL, working_capital REAL,
            total_debt REAL, shares_outstanding REAL, dividends_paid REAL,
            capex REAL, rd_expense REAL, operating_cash_flow REAL,
            free_cash_flow REAL, interest_expense REAL, ebit REAL,
            roe REAL, roa REAL, gross_margin REAL, operating_margin REAL,
            net_margin REAL, revenue_growth_yoy REAL, revenue_growth_qoq REAL,
            earnings_growth_yoy REAL, earnings_growth_qoq REAL,
            debt_equity REAL, current_ratio REAL, fcf_yield REAL,
            ev_ebitda REAL, ar_revenue REAL, cfo_ni REAL,
            accruals_ratio REAL, asset_turnover REAL, interest_coverage REAL,
            piotroski_f_score REAL, altman_z_score REAL,
            shareholder_yield REAL, buyback_yield REAL, dividend_yield REAL,
            pe_ratio REAL, market_cap REAL,
            PRIMARY KEY (ticker, report_date, period)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fund_ticker ON fundamentals(ticker)")
    conn.commit()


def _safe(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return default
        return float(val)
    except Exception:
        return default


def _get_value(df: pd.DataFrame, keys: List[str], col_idx: int = 0) -> Optional[float]:
    """Try multiple key names against a DataFrame column by position."""
    if df is None or df.empty:
        return None
    for key in keys:
        if key in df.index:
            try:
                val = df.iloc[df.index.get_loc(key), col_idx]
                return _safe(val)
            except Exception:
                continue
    return None


def _compute_ratios(r: Dict, market_cap: Optional[float]) -> Dict:
    """Compute all 24 derived ratios. Returns dict with ratio values."""
    ni = r.get("net_income")
    equity = r.get("total_equity")
    assets = r.get("total_assets")
    liabilities = r.get("total_liabilities")
    gp = r.get("gross_profit")
    rev = r.get("revenue")
    oi = r.get("operating_income")
    cur_assets = r.get("current_assets")
    cur_liab = r.get("current_liabilities")
    fcf = r.get("free_cash_flow")
    cfo = r.get("operating_cash_flow")
    ebitda = r.get("ebitda")
    ar = r.get("accounts_receivable")
    total_debt = r.get("total_debt")
    divs = r.get("dividends_paid")
    re = r.get("retained_earnings")
    wc = r.get("working_capital")
    ebit = r.get("ebit")
    interest = r.get("interest_expense")
    capex = r.get("capex")
    shares = r.get("shares_outstanding")

    mc = market_cap

    ratios: Dict[str, Optional[float]] = {}

    ratios["roe"] = _safe(ni / equity) if ni and equity else None
    ratios["roa"] = _safe(ni / assets) if ni and assets else None
    ratios["gross_margin"] = _safe(gp / rev) if gp and rev else None
    ratios["operating_margin"] = _safe(oi / rev) if oi and rev else None
    ratios["net_margin"] = _safe(ni / rev) if ni and rev else None
    ratios["debt_equity"] = _safe(total_debt / equity) if total_debt and equity else None
    ratios["current_ratio"] = _safe(cur_assets / cur_liab) if cur_assets and cur_liab else None
    ratios["fcf_yield"] = _safe(fcf / mc) if fcf and mc else None
    ratios["ar_revenue"] = _safe(ar / rev) if ar and rev else None
    ratios["cfo_ni"] = _safe(cfo / ni) if cfo and ni else None
    ratios["accruals_ratio"] = _safe((ni - cfo) / assets) if ni is not None and cfo is not None and assets else None
    ratios["asset_turnover"] = _safe(rev / assets) if rev and assets else None
    ratios["interest_coverage"] = _safe(oi / abs(interest)) if oi and interest and interest != 0 else None

    # EV/EBITDA: enterprise value approximation
    ev = (mc or 0) + (total_debt or 0) - (r.get("cash") or 0)
    ratios["ev_ebitda"] = _safe(ev / ebitda) if ebitda and ebitda != 0 else None

    # Buyback = negative capex-like adjustment; approximate from shares + price
    # We don't have buyback directly so leave as None (will be updated from cash flow)
    ratios["buyback_yield"] = None
    ratios["dividend_yield"] = _safe(abs(divs) / mc) if divs and mc else None
    ratios["shareholder_yield"] = ratios["dividend_yield"]  # improved when buyback available

    ratios["pe_ratio"] = _safe(mc / ni) if mc and ni else None

    # Altman Z-Score
    try:
        if all(v is not None for v in [wc, re, ebit, mc, liabilities, rev, assets]) and assets != 0 and liabilities != 0:
            z = (1.2 * (wc / assets) + 1.4 * (re / assets) +
                 3.3 * (ebit / assets) + 0.6 * (mc / liabilities) + 1.0 * (rev / assets))
            ratios["altman_z_score"] = _safe(z)
        else:
            ratios["altman_z_score"] = None
    except Exception:
        ratios["altman_z_score"] = None

    # Piotroski F-Score: 9 binary signals
    ratios["piotroski_f_score"] = None  # requires multi-period data; computed below if available

    # Growth ratios require prior-period data — set None here, updated in multi-period pass
    ratios["revenue_growth_yoy"] = None
    ratios["revenue_growth_qoq"] = None
    ratios["earnings_growth_yoy"] = None
    ratios["earnings_growth_qoq"] = None

    ratios["market_cap"] = _safe(mc)
    return ratios


def _compute_growth_and_piotroski(records: List[Dict]) -> List[Dict]:
    """Compute growth rates and Piotroski F-Score requiring multi-period data."""
    if not records:
        return records

    # Sort by date descending
    records = sorted(records, key=lambda r: r["report_date"], reverse=True)

    for i, rec in enumerate(records):
        rev = rec.get("revenue")
        ni = rec.get("net_income")

        # YoY: compare to record 4 periods back (quarterly) or 1 back (annual)
        step_yoy = 4 if rec["period"] == "quarterly" else 1
        if i + step_yoy < len(records):
            prior_yoy = records[i + step_yoy]
            prev_rev = prior_yoy.get("revenue")
            prev_ni = prior_yoy.get("net_income")
            rec["revenue_growth_yoy"] = _safe((rev - prev_rev) / abs(prev_rev)) if rev and prev_rev else None
            rec["earnings_growth_yoy"] = _safe((ni - prev_ni) / abs(prev_ni)) if ni and prev_ni else None

        # QoQ
        if i + 1 < len(records):
            prior = records[i + 1]
            prev_rev = prior.get("revenue")
            prev_ni = prior.get("net_income")
            rec["revenue_growth_qoq"] = _safe((rev - prev_rev) / abs(prev_rev)) if rev and prev_rev else None
            rec["earnings_growth_qoq"] = _safe((ni - prev_ni) / abs(prev_ni)) if ni and prev_ni else None

        # Piotroski F-Score
        score = 0
        roa = rec.get("roa")
        cfo = rec.get("operating_cash_flow")
        assets = rec.get("total_assets")
        cfo_ni = rec.get("cfo_ni")
        debt_eq = rec.get("debt_equity")
        cur_ratio = rec.get("current_ratio")
        shares = rec.get("shares_outstanding")
        gm = rec.get("gross_margin")
        at = rec.get("asset_turnover")

        if roa is not None:
            score += 1 if roa > 0 else 0
        if cfo is not None and assets:
            score += 1 if (cfo / assets) > 0 else 0
        if cfo_ni is not None:
            score += 1 if cfo_ni > 1 else 0  # CFO > NI

        if i + step_yoy < len(records):
            prior = records[i + step_yoy]
            if roa is not None and prior.get("roa") is not None:
                score += 1 if roa > prior["roa"] else 0
            if debt_eq is not None and prior.get("debt_equity") is not None:
                score += 1 if debt_eq < prior["debt_equity"] else 0
            if cur_ratio is not None and prior.get("current_ratio") is not None:
                score += 1 if cur_ratio > prior["current_ratio"] else 0
            if shares is not None and prior.get("shares_outstanding") is not None:
                score += 1 if shares <= prior["shares_outstanding"] else 0
            if gm is not None and prior.get("gross_margin") is not None:
                score += 1 if gm > prior["gross_margin"] else 0
            if at is not None and prior.get("asset_turnover") is not None:
                score += 1 if at > prior["asset_turnover"] else 0

        rec["piotroski_f_score"] = float(score)

    return records


def _parse_financials(ticker: str, t: yf.Ticker) -> List[Dict]:
    """Extract raw financials for quarterly + annual periods."""
    records = []

    def extract(inc_df, bal_df, cf_df, period: str) -> None:
        if inc_df is None or inc_df.empty:
            return
        cols = inc_df.columns.tolist()
        for col_idx, col in enumerate(cols[:8] if period == "quarterly" else cols[:4]):
            report_date = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)[:10]

            def g(df, keys):
                return _get_value(df, keys, col_idx)

            rev = g(inc_df, ["Total Revenue", "Revenue"])
            ni = g(inc_df, ["Net Income", "Net Income Common Stockholders"])
            ebitda = g(inc_df, ["EBITDA", "Normalized EBITDA"])
            oi = g(inc_df, ["Operating Income", "Total Operating Income As Reported"])
            gp = g(inc_df, ["Gross Profit"])
            ta = g(bal_df, ["Total Assets"])
            tl = g(bal_df, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
            eq = g(bal_df, ["Stockholders Equity", "Total Equity Gross Minority Interest", "Common Stock Equity"])
            ca = g(bal_df, ["Current Assets"])
            cl = g(bal_df, ["Current Liabilities"])
            cash = g(bal_df, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])
            ar = g(bal_df, ["Accounts Receivable", "Net Receivables"])
            inv = g(bal_df, ["Inventory"])
            gw = g(bal_df, ["Goodwill"])
            re = g(bal_df, ["Retained Earnings"])
            td = g(bal_df, ["Total Debt", "Long Term Debt And Capital Lease Obligation"])
            shares = g(bal_df, ["Ordinary Shares Number", "Share Issued"])
            divs = g(cf_df, ["Cash Dividends Paid", "Common Stock Dividend Paid"])
            capex = g(cf_df, ["Capital Expenditure", "Purchase Of PPE"])
            rd = g(inc_df, ["Research And Development", "Research & Development"])
            cfo = g(cf_df, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
            interest = g(inc_df, ["Interest Expense", "Interest Expense Non Operating"])
            ebit = g(inc_df, ["EBIT"])

            fcf = None
            if cfo is not None and capex is not None:
                fcf = cfo + capex  # capex is usually negative in CF statement
            wc = None
            if ca is not None and cl is not None:
                wc = ca - cl

            if ebit is None and oi is not None:
                ebit = oi

            r: Dict = {
                "ticker": ticker,
                "report_date": report_date,
                "period": period,
                "revenue": rev,
                "net_income": ni,
                "ebitda": ebitda,
                "operating_income": oi,
                "gross_profit": gp,
                "total_assets": ta,
                "total_liabilities": tl,
                "total_equity": eq,
                "current_assets": ca,
                "current_liabilities": cl,
                "cash": cash,
                "accounts_receivable": ar,
                "inventory": inv,
                "goodwill": gw,
                "retained_earnings": re,
                "working_capital": wc,
                "total_debt": td,
                "shares_outstanding": shares,
                "dividends_paid": divs,
                "capex": capex,
                "rd_expense": rd,
                "operating_cash_flow": cfo,
                "free_cash_flow": fcf,
                "interest_expense": interest,
                "ebit": ebit,
            }
            records.append(r)

    try:
        inc_q = t.quarterly_income_stmt
        bal_q = t.quarterly_balance_sheet
        cf_q = t.quarterly_cash_flow
        extract(inc_q, bal_q, cf_q, "quarterly")
    except Exception as e:
        logger.warning("Error fetching quarterly financials for %s: %s", ticker, e)

    try:
        inc_a = t.income_stmt
        bal_a = t.balance_sheet
        cf_a = t.cash_flow
        extract(inc_a, bal_a, cf_a, "annual")
    except Exception as e:
        logger.warning("Error fetching annual financials for %s: %s", ticker, e)

    return records


def update_fundamentals(tickers: Optional[List[str]] = None) -> int:
    """Fetch and store fundamentals + ratios. Returns total records stored."""
    config = _load_config()
    conn = _get_db(config)
    _ensure_schema(conn)

    if tickers is None:
        rows = conn.execute("SELECT symbol FROM tickers WHERE is_benchmark=0").fetchall()
        tickers = [r[0] for r in rows]

    total = 0
    for ticker in tqdm(tickers, desc="Fetching fundamentals", unit="ticker"):
        try:
            t = yf.Ticker(ticker)
            info = t.fast_info
            try:
                market_cap = float(info.market_cap) if hasattr(info, "market_cap") and info.market_cap else None
            except Exception:
                market_cap = None

            records = _parse_financials(ticker, t)
            if not records:
                logger.warning("No fundamental data for %s", ticker)
                continue

            # Split and compute ratios
            quarterly = [r for r in records if r["period"] == "quarterly"]
            annual = [r for r in records if r["period"] == "annual"]

            quarterly = _compute_growth_and_piotroski(quarterly)
            annual = _compute_growth_and_piotroski(annual)

            all_records = quarterly + annual
            for rec in all_records:
                ratios = _compute_ratios(rec, market_cap)
                rec.update(ratios)

            # Insert
            for rec in all_records:
                conn.execute("""
                    INSERT OR REPLACE INTO fundamentals (
                        ticker, report_date, period,
                        revenue, net_income, ebitda, operating_income,
                        gross_profit, total_assets, total_liabilities,
                        total_equity, current_assets, current_liabilities,
                        cash, accounts_receivable, inventory,
                        goodwill, retained_earnings, working_capital,
                        total_debt, shares_outstanding, dividends_paid,
                        capex, rd_expense, operating_cash_flow,
                        free_cash_flow, interest_expense, ebit,
                        roe, roa, gross_margin, operating_margin,
                        net_margin, revenue_growth_yoy, revenue_growth_qoq,
                        earnings_growth_yoy, earnings_growth_qoq,
                        debt_equity, current_ratio, fcf_yield,
                        ev_ebitda, ar_revenue, cfo_ni,
                        accruals_ratio, asset_turnover, interest_coverage,
                        piotroski_f_score, altman_z_score,
                        shareholder_yield, buyback_yield, dividend_yield,
                        pe_ratio, market_cap
                    ) VALUES (
                        :ticker, :report_date, :period,
                        :revenue, :net_income, :ebitda, :operating_income,
                        :gross_profit, :total_assets, :total_liabilities,
                        :total_equity, :current_assets, :current_liabilities,
                        :cash, :accounts_receivable, :inventory,
                        :goodwill, :retained_earnings, :working_capital,
                        :total_debt, :shares_outstanding, :dividends_paid,
                        :capex, :rd_expense, :operating_cash_flow,
                        :free_cash_flow, :interest_expense, :ebit,
                        :roe, :roa, :gross_margin, :operating_margin,
                        :net_margin, :revenue_growth_yoy, :revenue_growth_qoq,
                        :earnings_growth_yoy, :earnings_growth_qoq,
                        :debt_equity, :current_ratio, :fcf_yield,
                        :ev_ebitda, :ar_revenue, :cfo_ni,
                        :accruals_ratio, :asset_turnover, :interest_coverage,
                        :piotroski_f_score, :altman_z_score,
                        :shareholder_yield, :buyback_yield, :dividend_yield,
                        :pe_ratio, :market_cap
                    )
                """, rec)
            conn.commit()
            total += len(all_records)
            logger.debug("Stored %d fundamental records for %s", len(all_records), ticker)

        except Exception as e:
            logger.error("Error processing fundamentals for %s: %s", ticker, e)

    conn.close()
    logger.info("Fundamentals update complete: %d records stored for %d tickers", total, len(tickers))
    return total


def get_fundamental_count(config: dict) -> int:
    conn = _get_db(config)
    row = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()
    conn.close()
    return row[0] if row else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = update_fundamentals(tickers=["AAPL", "MSFT"])
    print(f"Stored {count} fundamental records")
