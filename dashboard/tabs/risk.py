"""Page III — Risk: Circuit breakers, factor decomposition, stress tests."""
import logging
import os
import sqlite3

import streamlit as st

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "cache", "jarvis.db")


@st.cache_data(ttl=300)
def _load_risk_state() -> dict:
    try:
        from risk.circuit_breakers import get_halt_info
        from risk.state import get_latest_risk_state
        halt = get_halt_info()
        state = get_latest_risk_state() or {}
        return {"halt": halt, "state": state}
    except Exception as exc:
        logger.warning("Risk state load error: %s", exc)
        return {}


@st.cache_data(ttl=300)
def _load_stress_tests() -> list[dict]:
    try:
        from risk.stress import run_stress_tests
        return run_stress_tests() or []
    except Exception as exc:
        logger.warning("Stress tests error: %s", exc)
        return []


@st.cache_data(ttl=300)
def _load_factor_risk() -> dict:
    try:
        from risk.factor_risk_model import compute_factor_risk
        return compute_factor_risk() or {}
    except Exception as exc:
        logger.warning("Factor risk error: %s", exc)
        return {}


@st.cache_data(ttl=300)
def _load_positions() -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ticker, shares, current_price, entry_price, beta, sector "
            "FROM positions WHERE is_active=1"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _circuit_bar(label: str, current: float, limit: float, unit: str = "%"):
    pct = min(abs(current) / abs(limit), 1.0) if limit else 0
    if pct < 0.6:
        color = "#10b981"
        status = "SAFE"
    elif pct < 0.85:
        color = "#f59e0b"
        status = "WARNING"
    else:
        color = "#f43f5e"
        status = "TRIGGERED"

    width_pct = int(pct * 100)
    st.markdown(
        f"""<div style="margin-bottom:12px;">
        <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
          <span style="font-size:13px;color:#94a3b8;">{label}</span>
          <span style="font-size:13px;font-weight:700;color:{color};">
            {current:.1f}{unit} / {limit:.1f}{unit} — {status}
          </span>
        </div>
        <div style="background:#1e2d45;border-radius:4px;height:16px;overflow:hidden;">
          <div style="background:{color};width:{width_pct}%;height:100%;border-radius:4px;
          transition:width 0.3s;"></div>
        </div>
        </div>""",
        unsafe_allow_html=True,
    )


def render():
    import plotly.graph_objects as go
    from dashboard.style import PLOTLY_LAYOUT, COLORS

    risk_data = _load_risk_state()
    state = risk_data.get("state", {})
    halt = risk_data.get("halt")

    # Halt banner
    if halt:
        st.error(f"⛔ KILL SWITCH ACTIVE: {halt.get('reason', 'Unknown')} — All new trades blocked.")

    # --- Circuit breakers ---
    st.markdown("### Circuit Breakers")

    daily_loss = state.get("daily_loss_pct", 0.0)
    weekly_loss = state.get("weekly_loss_pct", 0.0)
    drawdown = state.get("max_drawdown_pct", 0.0)

    _circuit_bar("Daily Loss", daily_loss, -2.0)
    _circuit_bar("Weekly Loss", weekly_loss, -4.0)
    _circuit_bar("Drawdown from Peak", drawdown, -8.0)

    st.markdown("<hr>", unsafe_allow_html=True)

    # Tail-risk KPIs
    st.markdown("### Tail-Risk Indicators")
    positions = _load_positions()
    aum = sum(abs(p.get("shares", 0) or 0) * (p.get("current_price") or p.get("entry_price") or 0)
              for p in positions)

    tk1, tk2, tk3, tk4 = st.columns(4)
    vix_val = state.get("vix")
    tk1.metric("VIX", f"{vix_val:.1f}" if vix_val else "—")
    tk2.metric("Gross Exposure", f"{state.get('gross_exposure_pct', 0):.0f}%")
    tk3.metric("Net Exposure", f"{state.get('net_exposure_pct', 0):.1f}%")
    tk4.metric("Net Beta", f"{state.get('net_beta', 0):.2f}")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Two columns: donut + factor table
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown("### Risk Decomposition")
        factor_risk = _load_factor_risk()
        factor_pct = factor_risk.get("factor_risk_pct", 20)
        specific_pct = 100 - factor_pct

        fig = go.Figure(go.Pie(
            labels=["Factor Risk", "Specific Risk"],
            values=[factor_pct, specific_pct],
            hole=0.6,
            marker=dict(colors=[COLORS["green"], COLORS["accent"]]),
            textfont=dict(color="#e2e8f0"),
        ))
        layout = dict(**PLOTLY_LAYOUT)
        layout["height"] = 280
        layout["showlegend"] = True
        layout["annotations"] = [dict(
            text=f"{factor_pct:.0f}%<br>Factor",
            font=dict(size=14, color="#e2e8f0"),
            showarrow=False,
        )]
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.markdown("### Factor Risk Contributions")
        contributions = factor_risk.get("factor_contributions", [])
        if contributions:
            import pandas as pd
            df = pd.DataFrame(contributions)
            st.dataframe(
                df.style.background_gradient(cmap="RdYlGn", subset=["contribution_pct"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Factor risk model not yet computed.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # MCTR table
    st.markdown("### Marginal Contribution to Risk (Top 12)")
    mctr_data = factor_risk.get("mctr", [])
    if mctr_data:
        import pandas as pd
        df_mctr = pd.DataFrame(mctr_data[:12])
        if "weight_pct" in df_mctr.columns and "mctr_pct" in df_mctr.columns:
            df_mctr["flag"] = df_mctr.apply(
                lambda r: "⚠" if r["mctr_pct"] > r["weight_pct"] * 1.5 else "", axis=1
            )
        st.dataframe(df_mctr, use_container_width=True, hide_index=True)
    else:
        st.info("MCTR data not available.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Factor exposure bars
    st.markdown("### Factor Exposures vs Historical Range")
    factor_exposures = state.get("factor_exposures", {})
    factor_history = state.get("factor_exposure_history", {})
    if factor_exposures:
        import pandas as pd
        rows = []
        for factor, current in factor_exposures.items():
            hist = factor_history.get(factor, {})
            rows.append({
                "Factor": factor,
                "Current": current,
                "1σ Low": hist.get("mean", 0) - hist.get("std", 0.5),
                "1σ High": hist.get("mean", 0) + hist.get("std", 0.5),
                "Warning": "⚠ >1.5σ" if abs(current - hist.get("mean", 0)) > 1.5 * hist.get("std", 0.5) else "",
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("<hr>", unsafe_allow_html=True)

    # Stress test table
    st.markdown("### Stress Tests — Estimated P&L")
    stress = _load_stress_tests()
    if stress:
        import pandas as pd
        df_stress = pd.DataFrame(stress)
        st.dataframe(df_stress, use_container_width=True, hide_index=True)
    else:
        st.info("Stress tests not available.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Correlation heatmap
    st.markdown("### Correlation Heatmap (Long × Short Book)")
    try:
        from risk.correlation_monitor import compute_correlation_matrix
        corr_result = compute_correlation_matrix()
        if corr_result and corr_result.get("matrix") is not None:
            import numpy as np
            mat = corr_result["matrix"]
            labels = corr_result.get("tickers", [])

            fig = go.Figure(go.Heatmap(
                z=mat,
                x=labels,
                y=labels,
                colorscale=[
                    [0.0, COLORS["green"]],
                    [0.5, COLORS["card"]],
                    [1.0, COLORS["red"]],
                ],
                zmin=-1, zmax=1,
                hovertemplate="%{x} vs %{y}: %{z:.2f}<extra></extra>",
                colorbar=dict(tickfont=dict(color="#e2e8f0"), thickness=12),
            ))
            layout = dict(**PLOTLY_LAYOUT)
            layout["height"] = 400
            layout["xaxis"] = dict(tickfont=dict(size=9, color="#94a3b8"), tickangle=-45)
            layout["yaxis"] = dict(tickfont=dict(size=9, color="#94a3b8"))
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True)

            eff_bets = corr_result.get("effective_bets")
            if eff_bets:
                st.caption(f"Effective independent bets: **{eff_bets:.1f}**")
        else:
            st.info("Correlation data not available.")
    except Exception as exc:
        st.info(f"Correlation matrix: {exc}")

    st.markdown("<hr>", unsafe_allow_html=True)

    # 72hr alerts
    st.markdown("### 72hr Alerts")
    alerts = state.get("upcoming_events", [])
    if alerts:
        for event in alerts:
            st.warning(f"**{event.get('type', 'Event')}** — {event.get('description', '')} "
                      f"({event.get('date', '')})")
    else:
        try:
            if os.path.exists(DB_PATH):
                conn = sqlite3.connect(DB_PATH)
                earnings = conn.execute(
                    "SELECT ticker, date FROM earnings_transcripts "
                    "WHERE date BETWEEN date('now') AND date('now','+3 days') ORDER BY date"
                ).fetchall()
                conn.close()
                if earnings:
                    for ticker, edate in earnings:
                        st.warning(f"📅 **{ticker}** earnings expected {edate}")
                else:
                    st.success("No major events in next 72 hours.")
        except Exception:
            st.success("No major events in next 72 hours.")
