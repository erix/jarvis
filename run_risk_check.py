#!/usr/bin/env python3
"""Layer 5 Risk Management entry point.

Usage:
  python run_risk_check.py                         # Full risk report
  python run_risk_check.py --stress                # Stress tests only
  python run_risk_check.py --tail-only             # Tail risk only
  python run_risk_check.py --clear-halt            # Clear kill-switch lock file
  python run_risk_check.py --pre-trade TICKER SHARES  # Check single trade
"""
import argparse
import logging
import os
import sys

import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

AUM = 10_000_000.0  # Default AUM; override via --aum


def _load_positions() -> list[dict]:
    from portfolio.state import get_current_positions
    return get_current_positions()


def _positions_to_weights(positions: list[dict], aum: float) -> dict[str, float]:
    """Convert position list to weight dict {ticker: notional_weight}."""
    weights = {}
    for p in positions:
        price = p.get("current_price") or p.get("entry_price") or 0
        if price > 0:
            weights[p["ticker"]] = p["shares"] * price / aum
    return weights


def run_full_report(aum: float) -> None:
    from risk.factor_risk_model import decompose_portfolio
    from risk.circuit_breakers import check_circuit_breakers, halt_lock_exists, get_halt_info
    from risk.factor_monitor import check_factor_spread, get_portfolio_factor_exposures
    from risk.correlation_monitor import check_correlations
    from risk.tail_risk import check_tail_risk
    from risk.stress import run_stress_test
    from risk.state import upsert_risk_state, get_today_rejections

    positions = _load_positions()
    weights = _positions_to_weights(positions, aum)
    positions_df = pd.DataFrame(positions) if positions else pd.DataFrame()

    print("\n" + "=" * 60)
    print("           JARVIS RISK REPORT")
    print("=" * 60)

    # --- Kill switch status ---
    if halt_lock_exists():
        info = get_halt_info()
        print(f"\n*** KILL SWITCH ACTIVE *** Reason: {info.get('reason')} @ {info.get('timestamp')}")
        print("    All new trades BLOCKED. Run --clear-halt to resume.")

    # --- Factor risk decomposition ---
    print("\n[1] FACTOR RISK MODEL")
    risk_decomp = {"factor_pct": 0.0, "specific_pct": 100.0, "max_mctr_ticker": None, "portfolio_vol_annualized": 0.0}
    if weights:
        risk_decomp = decompose_portfolio(weights)
        print(f"  Portfolio Factor Risk:   {risk_decomp['factor_pct']:.1f}%")
        print(f"  Portfolio Specific Risk: {risk_decomp['specific_pct']:.1f}%")
        print(f"  Portfolio Volatility:    {risk_decomp['portfolio_vol_annualized']:.1f}% (annualized)")
        if risk_decomp.get("max_mctr_ticker"):
            print(f"  Top MCTR:                {risk_decomp['max_mctr_ticker']}")
        if risk_decomp.get("alerts"):
            print(f"  MCTR Alerts: {len(risk_decomp['alerts'])} positions with disproportionate risk")
            for a in risk_decomp["alerts"][:3]:
                print(f"    - {a['ticker']}: MCTR {a['mctr_pct']:.1f}% vs weight {a['weight_pct']:.1f}% (ratio {a['ratio']:.1f}x)")
    else:
        print("  No active positions — risk decomposition skipped")

    # --- Circuit breakers ---
    print("\n[2] CIRCUIT BREAKERS")
    cb = check_circuit_breakers(aum=aum, positions_df=positions_df if not positions_df.empty else None)
    action = cb.get("action", "OK")
    if action == "OK":
        print(f"  Status: ALL CLEAR")
    else:
        print(f"  Status: {action}")
        print(f"  Message: {cb.get('message')}")
    if cb.get("alerts"):
        for alert in cb["alerts"]:
            print(f"    {alert}")
    print(f"  Daily P&L:   {cb.get('daily_pnl_pct', 'N/A')}%")
    print(f"  Weekly P&L:  {cb.get('weekly_pnl_pct', 'N/A')}%")
    print(f"  Drawdown:    {cb.get('drawdown_pct', 'N/A')}%")

    # --- Tail risk ---
    print("\n[3] TAIL RISK")
    tail = check_tail_risk()
    vix = tail.get("vix")
    reduction = tail.get("suggested_reduction", 0.0)
    print(f"  VIX: {vix if vix else 'N/A'}")
    print(f"  Regime: {tail.get('vix_regime', 'N/A').replace('_', ' ')}")
    if reduction > 0:
        print(f"  SUGGESTION: Reduce gross exposure by {reduction*100:.0f}%")
        print(f"  {tail.get('recommendation')}")
    else:
        print("  Gross adjustment: None (VIX normal)")

    # --- Factor monitor ---
    print("\n[4] FACTOR EXPOSURE MONITOR")
    factor_alerts = []
    if weights:
        factor_exposures = get_portfolio_factor_exposures(weights)
        factor_alerts = check_factor_spread(portfolio_factor_exposures=factor_exposures)
        if factor_alerts:
            print(f"  {len(factor_alerts)} overconcentration alert(s):")
            for a in factor_alerts:
                print(f"    [{a['level']}] {a['message']}")
        else:
            print("  No factor overconcentration detected")

    # --- Correlation monitor ---
    print("\n[5] CORRELATION MONITOR")
    corr_result = {"long_book": {}, "short_book": {}, "high_corr_pairs": []}
    if weights:
        corr_result = check_correlations(weights_dict=weights)
        lb = corr_result.get("long_book", {})
        sb = corr_result.get("short_book", {})
        print(f"  Long book:  avg corr={lb.get('avg_corr', 0):.2f}, "
              f"{lb.get('effective_bets', 0):.1f} eff bets / {lb.get('n_positions', 0)} positions")
        print(f"  Short book: avg corr={sb.get('avg_corr', 0):.2f}, "
              f"{sb.get('effective_bets', 0):.1f} eff bets / {sb.get('n_positions', 0)} positions")
        all_corr_alerts = lb.get("alerts", []) + sb.get("alerts", [])
        if all_corr_alerts:
            for a in all_corr_alerts:
                print(f"    [{a.get('level')}] {a.get('message')}")
        hp = corr_result.get("high_corr_pairs", [])
        if hp:
            print(f"  High-corr pairs (>{0.85}): {len(hp)}")
            for p in hp[:5]:
                print(f"    {p['ticker_a']}/{p['ticker_b']}: {p['correlation']:.2f} ({p['book']})")

    # --- Stress tests ---
    print("\n[6] STRESS TESTS")
    stress_df = run_stress_test(aum=aum)
    if not stress_df.empty:
        worst = stress_df.iloc[0]
        print(f"  Worst case: {worst['scenario']} ({worst['description']}) → {worst['pnl_pct']:.1f}%")
        print(f"  {'Scenario':<25} {'P&L':>10} {'% AUM':>8}")
        print(f"  {'-'*45}")
        for _, row in stress_df.iterrows():
            print(f"  {row['scenario']:<25} ${row['pnl']:>8,.0f}  {row['pnl_pct']:>6.1f}%")
    else:
        print("  No positions — stress test skipped")

    # --- Rejections today ---
    num_rejections = get_today_rejections()
    print(f"\n[7] PRE-TRADE REJECTIONS TODAY: {num_rejections}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Circuit breakers: {'TRIGGERED: ' + action if action != 'OK' else 'ALL CLEAR'}")
    print(f"  Tail risk: VIX={vix if vix else 'N/A'}, gross adjustment: -{reduction*100:.0f}%")
    if not stress_df.empty:
        worst = stress_df.iloc[0]
        print(f"  Stress worst case: {worst['scenario']} ({worst['pnl_pct']:.1f}%)")
    lb = corr_result.get("long_book", {})
    sb = corr_result.get("short_book", {})
    print(f"  Correlations: long avg={lb.get('avg_corr', 0):.2f}, short avg={sb.get('avg_corr', 0):.2f}")
    total_alerts = len(factor_alerts) + len(corr_result.get("high_corr_pairs", []))
    print(f"  72hr alerts: {total_alerts}")
    print("=" * 60)

    # --- Persist risk state ---
    _persist_state(risk_decomp, cb, tail, num_rejections, weights, aum)


def _persist_state(
    risk_decomp: dict,
    cb: dict,
    tail: dict,
    num_rejections: int,
    weights: dict,
    aum: float,
) -> None:
    from risk.state import upsert_risk_state
    from portfolio.beta import calculate_portfolio_beta

    portfolio_beta = 0.0
    gross = 0.0
    net = 0.0
    if weights:
        portfolio_beta = calculate_portfolio_beta(weights)
        gross = sum(abs(w) for w in weights.values())
        net = sum(weights.values())

    upsert_risk_state({
        "daily_pnl": cb.get("daily_pnl_pct"),
        "weekly_pnl": cb.get("weekly_pnl_pct"),
        "drawdown_pct": cb.get("drawdown_pct"),
        "gross_exposure": round(gross * aum, 2),
        "net_exposure": round(net * aum, 2),
        "portfolio_beta": round(portfolio_beta, 4),
        "factor_risk_pct": risk_decomp.get("factor_pct"),
        "specific_risk_pct": risk_decomp.get("specific_pct"),
        "max_mctr_ticker": risk_decomp.get("max_mctr_ticker"),
        "vix": tail.get("vix"),
        "circuit_breaker_triggered": cb.get("action") if cb.get("action") != "OK" else None,
        "num_rejections": num_rejections,
    })


def run_stress_only(aum: float) -> None:
    from risk.stress import run_stress_test
    print("\n=== STRESS TESTS ===")
    df = run_stress_test(aum=aum)
    if df.empty:
        print("No active positions.")
        return
    print(f"  {'Scenario':<25} {'Description':<35} {'P&L':>10} {'% AUM':>8}")
    print(f"  {'-'*80}")
    for _, row in df.iterrows():
        print(f"  {row['scenario']:<25} {row['description']:<35} ${row['pnl']:>8,.0f}  {row['pnl_pct']:>6.1f}%")


def run_tail_only() -> None:
    from risk.tail_risk import check_tail_risk
    print("\n=== TAIL RISK ===")
    result = check_tail_risk()
    print(f"VIX: {result.get('vix')}")
    print(f"Regime: {result.get('vix_regime', 'N/A').replace('_', ' ')}")
    print(f"Recommended reduction: {result.get('suggested_reduction', 0)*100:.0f}%")
    print(f"Note: {result.get('recommendation')}")


def run_pre_trade_check(ticker: str, shares: float, aum: float) -> None:
    from risk.pre_trade import pre_trade_veto
    positions = _load_positions()
    print(f"\n=== PRE-TRADE CHECK: {ticker} ({shares:+.0f} shares) ===")
    result = pre_trade_veto(ticker, shares, positions, aum=aum)
    status = "APPROVED" if result["approved"] else "REJECTED"
    print(f"Decision: {status}")
    print(f"Reason:   {result['reason']}")
    if not result["approved"]:
        cn = result.get("check_number")
        print(f"Failed check #{cn}")
    print("\nCheck details:")
    for chk in result.get("checks", []):
        passed = chk.get("passed", True)
        name = chk.get("name", chk.get("check", "?"))
        mark = "✓" if passed else "✗"
        note = chk.get("note") or chk.get("message") or ""
        print(f"  [{mark}] {name}" + (f": {note}" if note else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS Layer 5 Risk Check")
    parser.add_argument("--stress", action="store_true", help="Run stress tests only")
    parser.add_argument("--tail-only", action="store_true", help="Run tail risk check only")
    parser.add_argument("--clear-halt", action="store_true", help="Clear kill-switch lock file")
    parser.add_argument("--pre-trade", nargs=2, metavar=("TICKER", "SHARES"),
                        help="Run pre-trade veto check for a single ticker")
    parser.add_argument("--aum", type=float, default=AUM, help=f"Portfolio AUM (default: ${AUM:,.0f})")
    args = parser.parse_args()

    if args.clear_halt:
        from risk.circuit_breakers import clear_halt_lock, halt_lock_exists
        if halt_lock_exists():
            clear_halt_lock()
            print("Kill switch cleared. New trades are now permitted.")
        else:
            print("No halt lock found — system is already active.")
        return

    if args.tail_only:
        run_tail_only()
        return

    if args.stress:
        run_stress_only(args.aum)
        return

    if args.pre_trade:
        ticker, shares_str = args.pre_trade
        try:
            shares = float(shares_str)
        except ValueError:
            print(f"Error: SHARES must be a number, got '{shares_str}'")
            sys.exit(1)
        run_pre_trade_check(ticker, shares, args.aum)
        return

    # Default: full report
    run_full_report(args.aum)


if __name__ == "__main__":
    main()
