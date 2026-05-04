"""Page V — Execution: Orders, slippage, short availability."""
import logging
import os
import sqlite3
from datetime import datetime, timedelta

import streamlit as st

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "cache", "jarvis.db")


@st.cache_data(ttl=60)
def _load_slippage_metrics() -> dict:
    try:
        from execution.costs import get_slippage_metrics
        return get_slippage_metrics(days=30)
    except Exception as exc:
        logger.warning("Slippage metrics error: %s", exc)
        return {}


@st.cache_data(ttl=60)
def _load_orders(limit: int = 200) -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, submitted_at, ticker, action, qty, signal_price, limit_price, "
            "fill_price, slippage_bps, commission, status, order_id "
            "FROM orders ORDER BY submitted_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Orders load error: %s", exc)
        return []


@st.cache_data(ttl=60)
def _load_open_orders() -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM orders WHERE status IN ('pending','partial') ORDER BY submitted_at DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


@st.cache_data(ttl=300)
def _load_daily_notional() -> list[dict]:
    """Daily notional turnover for last 30 days."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT date(submitted_at) as day, SUM(ABS(qty * fill_price)) as notional "
            "FROM orders WHERE status='filled' "
            "AND submitted_at >= date('now','-30 days') "
            "GROUP BY day ORDER BY day",
        ).fetchall()
        conn.close()
        return [{"date": r[0], "notional": r[1] or 0} for r in rows]
    except Exception:
        return []


@st.cache_data(ttl=300)
def _load_short_positions() -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ticker, shares FROM positions WHERE shares < 0 AND is_active=1"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def render():
    import plotly.graph_objects as go
    from dashboard.style import PLOTLY_LAYOUT, COLORS

    slip = _load_slippage_metrics()
    orders = _load_orders()
    open_orders = _load_open_orders()

    # KPI row
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Filled Orders (30d)", slip.get("count", 0))
    k2.metric(
        "Avg Slippage",
        f"{slip.get('avg_bps', 0):.1f} bps",
        delta=f"p95: {slip.get('p95_bps', 0):.1f} bps",
    )
    k3.metric("Total Slippage Cost (30d)", f"${slip.get('total_cost_usd', 0):,.0f}")
    k4.metric("Open Orders", len(open_orders))

    st.markdown("<hr>", unsafe_allow_html=True)

    # Open orders table
    if open_orders:
        st.markdown("### Open Orders")
        import pandas as pd
        df_open = pd.DataFrame(open_orders)
        st.dataframe(df_open, use_container_width=True, hide_index=True)
    else:
        st.markdown("### Open Orders")
        st.caption("No open orders.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Recent trades log
    st.markdown("### Recent Trades (last 200)")
    if orders:
        import pandas as pd
        df_orders = pd.DataFrame(orders)
        display_cols = [c for c in ["submitted_at", "ticker", "action", "qty",
                                     "limit_price", "fill_price", "slippage_bps",
                                     "commission", "status"] if c in df_orders.columns]
        st.dataframe(
            df_orders[display_cols],
            use_container_width=True,
            hide_index=True,
            height=400,
        )
    else:
        st.info("No trades yet. Orders will appear here after execution.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Worst 5 fills
    worst = slip.get("worst_5", [])
    if worst:
        st.markdown("### Worst 5 Fills (Highest Slippage)")
        import pandas as pd
        st.dataframe(pd.DataFrame(worst), use_container_width=True, hide_index=True)

    st.markdown("<hr>", unsafe_allow_html=True)

    # Short availability
    shorts = _load_short_positions()
    if shorts:
        st.markdown("### Short Availability")
        import pandas as pd
        rows = []
        for pos in shorts:
            ticker = pos["ticker"]
            rows.append({
                "Ticker": ticker,
                "Shares Short": pos["shares"],
                "HTB/ETB": "ETB (assumed)",
                "Note": "Live check requires IBKR connection",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("<hr>", unsafe_allow_html=True)

    # Daily notional turnover chart
    st.markdown("### Daily Notional Turnover (30d)")
    daily = _load_daily_notional()
    if daily:
        import pandas as pd
        df_daily = pd.DataFrame(daily)
        fig = go.Figure(go.Bar(
            x=df_daily["date"],
            y=df_daily["notional"],
            marker_color=COLORS["accent"],
        ))
        layout = dict(**PLOTLY_LAYOUT)
        layout["height"] = 240
        layout["yaxis"]["title"] = "Notional ($)"
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("No execution data yet.")
