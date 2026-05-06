"""Page IV — Performance: Equity curve, attribution, win/loss."""
import logging
import os
import sqlite3
from datetime import datetime

import streamlit as st

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "cache", "jarvis.db")


@st.cache_data(ttl=300)
def _load_portfolio_history():
    if not os.path.exists(DB_PATH):
        return None
    try:
        import pandas as pd
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(
            "SELECT date, total_value FROM portfolio_history ORDER BY date", conn
        )
        conn.close()
        if df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")
    except Exception as exc:
        logger.warning("Portfolio history error: %s", exc)
        return None


@st.cache_data(ttl=300)
def _load_spy_history():
    if not os.path.exists(DB_PATH):
        return None
    try:
        import pandas as pd
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(
            "SELECT date, adj_close FROM daily_prices WHERE ticker='SPY' ORDER BY date", conn
        )
        conn.close()
        if df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")["adj_close"]
    except Exception:
        return None


@st.cache_data(ttl=300)
def _load_attribution():
    try:
        import pandas as pd
        path = os.path.join(os.path.dirname(__file__), "..", "..", "output", "daily_attribution.csv")
        if os.path.exists(path):
            return pd.read_csv(path)
        from reporting.pnl_attribution import run_attribution
        return run_attribution()
    except Exception as exc:
        logger.warning("Attribution load error: %s", exc)
        return None


@st.cache_data(ttl=300)
def _load_win_loss():
    try:
        from reporting.win_loss import analyze_win_loss
        return analyze_win_loss()
    except Exception:
        return {}


@st.cache_data(ttl=300)
def _load_sector_alpha():
    try:
        from reporting.sector_alpha import compute_sector_alpha
        df = compute_sector_alpha()
        return df
    except Exception:
        return None


@st.cache_data(ttl=300)
def _load_turnover():
    try:
        from reporting.turnover import compute_turnover
        return compute_turnover()
    except Exception:
        return {}


def render():
    import plotly.graph_objects as go
    import numpy as np
    from dashboard.style import PLOTLY_LAYOUT, COLORS

    portfolio_hist = _load_portfolio_history()
    spy_hist = _load_spy_history()

    # --- Equity curve ---
    st.markdown("### Equity Curve")
    if portfolio_hist is not None and not portfolio_hist.empty:
        import pandas as pd

        # Rebase both to 100
        fund_rebased = portfolio_hist["total_value"] / portfolio_hist["total_value"].iloc[0] * 100

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=fund_rebased.index, y=fund_rebased.values,
            name="Fund", line=dict(color=COLORS["accent"], width=2),
        ))

        if spy_hist is not None:
            aligned_spy = spy_hist.reindex(fund_rebased.index, method="ffill").dropna()
            spy_rebased = aligned_spy / aligned_spy.iloc[0] * 100 if len(aligned_spy) > 0 else None
            if spy_rebased is not None:
                fig.add_trace(go.Scatter(
                    x=spy_rebased.index, y=spy_rebased.values,
                    name="SPY", line=dict(color=COLORS["muted"], width=1, dash="dot"),
                ))

        layout = dict(**PLOTLY_LAYOUT)
        layout["height"] = 300
        layout["yaxis"]["title"] = "Value (rebased to 100)"
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)

        # --- Drawdown chart ---
        st.markdown("### Drawdown from Peak")
        cum_max = fund_rebased.cummax()
        drawdown = (fund_rebased - cum_max) / cum_max * 100

        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(
            x=drawdown.index, y=drawdown.values,
            fill="tozeroy",
            name="Drawdown",
            line=dict(color=COLORS["red"], width=1),
            fillcolor="rgba(244,63,94,0.15)",
        ))
        layout_dd = dict(**PLOTLY_LAYOUT)
        layout_dd["height"] = 200
        layout_dd["yaxis"]["title"] = "Drawdown %"
        fig_dd.update_layout(**layout_dd)
        st.plotly_chart(fig_dd, use_container_width=True)

        # Monthly returns grid
        st.markdown("### Monthly Returns")
        returns = fund_rebased.pct_change().dropna()
        monthly = returns.resample("ME").apply(lambda x: (1 + x).prod() - 1)
        monthly_df = monthly.reset_index()
        monthly_df.columns = ["date", "return"]
        monthly_df["year"] = monthly_df["date"].dt.year
        monthly_df["month"] = monthly_df["date"].dt.strftime("%b")

        MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        pivot = monthly_df.pivot(index="year", columns="month", values="return")
        pivot = pivot.reindex(columns=[m for m in MONTHS if m in pivot.columns])

        st.dataframe(pivot.style.format("{:.1%}"), use_container_width=True)

    else:
        st.info("No portfolio history yet. Run `python run_portfolio.py` to generate history.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # --- P&L attribution bars ---
    st.markdown("### P&L Attribution")
    attribution = _load_attribution()
    if attribution is not None and not attribution.empty:
        import pandas as pd
        attr_df = attribution.tail(60)
        fig_attr = go.Figure()
        for col, color, name in [
            ("beta_component", COLORS["cyan"], "Beta"),
            ("sector_component", COLORS["yellow"], "Sector"),
            ("factor_component", COLORS["accent"], "Factor"),
            ("alpha_residual", COLORS["green"], "Alpha"),
        ]:
            if col in attr_df.columns:
                fig_attr.add_trace(go.Bar(
                    x=attr_df["date"], y=attr_df[col],
                    name=name, marker_color=color,
                ))
        layout_attr = dict(**PLOTLY_LAYOUT)
        layout_attr["barmode"] = "stack"
        layout_attr["height"] = 280
        layout_attr["yaxis"]["title"] = "Return %"
        fig_attr.update_layout(**layout_attr)
        st.plotly_chart(fig_attr, use_container_width=True)

    st.markdown("<hr>", unsafe_allow_html=True)

    # Rolling 12mo Sharpe
    if portfolio_hist is not None and not portfolio_hist.empty:
        st.markdown("### Rolling 12-Month Sharpe")
        returns = portfolio_hist["total_value"].pct_change().dropna()
        rolling_sharpe = (
            returns.rolling(252).mean() * 252 /
            (returns.rolling(252).std() * np.sqrt(252))
        )
        fig_sh = go.Figure()
        fig_sh.add_trace(go.Scatter(
            x=rolling_sharpe.index, y=rolling_sharpe.values,
            name="Sharpe", line=dict(color=COLORS["accent"], width=2),
        ))
        fig_sh.add_hline(y=1.0, line_color=COLORS["green"], line_dash="dot",
                         annotation_text="1.0 target")
        layout_sh = dict(**PLOTLY_LAYOUT)
        layout_sh["height"] = 220
        fig_sh.update_layout(**layout_sh)
        st.plotly_chart(fig_sh, use_container_width=True)

    st.markdown("<hr>", unsafe_allow_html=True)

    # Sector alpha
    st.markdown("### Sector-Relative Alpha")
    sector_alpha = _load_sector_alpha()
    if sector_alpha is not None and not sector_alpha.empty:
        fig_sa = go.Figure(go.Bar(
            x=sector_alpha.get("alpha_pct", sector_alpha.iloc[:, 1] if len(sector_alpha.columns) > 1 else []),
            y=sector_alpha.get("sector", sector_alpha.index),
            orientation="h",
            marker=dict(
                color=[COLORS["green"] if v >= 0 else COLORS["red"]
                       for v in (sector_alpha.get("alpha_pct", []) or [])],
            ),
        ))
        layout_sa = dict(**PLOTLY_LAYOUT)
        layout_sa["height"] = 280
        fig_sa.update_layout(**layout_sa)
        st.plotly_chart(fig_sa, use_container_width=True)
    else:
        st.info("Sector alpha not computed yet.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Turnover panel
    st.markdown("### Turnover & Tax")
    turnover = _load_turnover()
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("30d Turnover", f"{turnover.get('turnover_30d_pct', 0):.1f}%")
    t2.metric("Annualized", f"{turnover.get('turnover_annualized_pct', 0):.0f}%")
    t3.metric("Budget", f"{turnover.get('budget_pct', 30):.0f}%")
    t4.metric("Est. Tax Liability", f"${turnover.get('tax_liability_usd', 0):,.0f}")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Win/loss panel
    st.markdown("### Win/Loss Analytics")
    wl = _load_win_loss()
    if wl.get("total_trades", 0) > 0:
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Win Rate", f"{wl.get('win_rate', 0):.1f}%")
        w2.metric("P/L Ratio", f"{wl.get('pl_ratio', 0):.2f}")
        w3.metric("Total Trades", wl.get("total_trades", 0))
        w4.metric("Total P&L", f"${wl.get('total_pnl', 0):,.0f}")

        by_side = wl.get("by_side", {})
        if by_side:
            import pandas as pd
            rows = []
            for side, data in by_side.items():
                rows.append({"Side": side, **data})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        streaks = wl.get("streaks", {})
        if streaks:
            s1, s2 = st.columns(2)
            s1.metric("Max Win Streak", streaks.get("max_win", 0))
            s2.metric("Max Loss Streak", streaks.get("max_loss", 0))
    else:
        st.info("No completed round-trip trades yet.")
