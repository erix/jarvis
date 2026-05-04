"""Page II — Research: Factor heatmap + candidate cards."""
import json
import logging
import os
import sqlite3

import streamlit as st

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "cache", "jarvis.db")


@st.cache_data(ttl=300)
def _load_scores() -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT s.ticker, s.composite_score, s.scored_at, "
            "t.sector, t.market_cap "
            "FROM scores s LEFT JOIN tickers t ON s.ticker=t.symbol "
            "WHERE s.scored_at = (SELECT MAX(s2.scored_at) FROM scores s2 WHERE s2.ticker=s.ticker) "
            "ORDER BY s.composite_score DESC LIMIT 200"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Scores load error: %s", exc)
        return []


@st.cache_data(ttl=300)
def _load_candidates() -> dict:
    """Return top 10 long and top 10 short candidates with position data."""
    scores = _load_scores()
    if not scores:
        return {"long": [], "short": []}

    longs = [s for s in scores if (s.get("composite_score") or 0) >= 0.5][:10]
    shorts = [s for s in scores if (s.get("composite_score") or 0) < 0.5]
    shorts = sorted(shorts, key=lambda x: x.get("composite_score") or 0)[:10]

    if not os.path.exists(DB_PATH):
        return {"long": longs, "short": shorts}

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        for candidate_list in (longs, shorts):
            for c in candidate_list:
                ticker = c["ticker"]
                pos = conn.execute(
                    "SELECT shares, entry_price, current_price, pnl, pnl_pct, beta, "
                    "approval_status FROM positions WHERE ticker=? AND is_active=1",
                    (ticker,),
                ).fetchone()
                if pos:
                    c.update(dict(pos))

                # Fundamental quality metrics from DB if available
                try:
                    fund = conn.execute(
                        "SELECT piotroski_score, altman_z FROM fundamentals WHERE ticker=? "
                        "ORDER BY date DESC LIMIT 1",
                        (ticker,),
                    ).fetchone()
                    if fund:
                        c["piotroski"] = fund["piotroski_score"]
                        c["altman_z"] = fund["altman_z"]
                except Exception:
                    pass

                # Claude analysis from cache
                try:
                    analysis = conn.execute(
                        "SELECT result_json FROM analysis_cache WHERE ticker=? "
                        "ORDER BY created_at DESC LIMIT 1",
                        (ticker,),
                    ).fetchone()
                    if analysis:
                        c["claude_analysis"] = analysis["result_json"]
                except Exception:
                    pass
        conn.close()
    except Exception as exc:
        logger.warning("Candidate enrichment error: %s", exc)

    return {"long": longs, "short": shorts}


@st.cache_data(ttl=300)
def _load_factor_heatmap_data() -> tuple:
    """Return (tickers, factor_names, matrix) for heatmap."""
    scores = _load_scores()
    if not scores:
        return [], [], []

    longs = [s["ticker"] for s in scores if (s.get("composite_score") or 0) >= 0.5][:30]
    shorts = [s["ticker"] for s in scores if (s.get("composite_score") or 0) < 0.5]
    shorts = [s["ticker"] for s in sorted(
        [s for s in scores if (s.get("composite_score") or 0) < 0.5],
        key=lambda x: x.get("composite_score") or 0
    )][:30]

    tickers = longs + shorts

    FACTOR_COLS = ["momentum", "value", "quality", "growth", "short_interest",
                   "crowding", "insider", "revisions"]

    if not tickers or not os.path.exists(DB_PATH):
        return tickers, FACTOR_COLS, []

    try:
        conn = sqlite3.connect(DB_PATH)
        matrix = []
        for ticker in tickers:
            row_data = []
            for factor in FACTOR_COLS:
                try:
                    val = conn.execute(
                        f"SELECT {factor}_score FROM factor_scores WHERE ticker=? "
                        "ORDER BY scored_at DESC LIMIT 1",
                        (ticker,),
                    ).fetchone()
                    row_data.append(float(val[0]) if val and val[0] is not None else 0.5)
                except Exception:
                    row_data.append(0.5)
            matrix.append(row_data)
        conn.close()
        return tickers, FACTOR_COLS, matrix
    except Exception as exc:
        logger.warning("Heatmap data error: %s", exc)
        return tickers, FACTOR_COLS, [[0.5] * 8 for _ in tickers]


def _set_approval(ticker: str, status: str):
    """Write approval_status to positions table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE positions SET approval_status=? WHERE ticker=?",
            (status, ticker),
        )
        # Insert if not exists as a candidate row
        if conn.execute(
            "SELECT COUNT(*) FROM positions WHERE ticker=?", (ticker,)
        ).fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO positions (ticker, shares, approval_status, is_active) VALUES (?,0,?,0)",
                (ticker, status),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        st.error(f"DB error: {exc}")


def _render_candidate_card(c: dict, side: str):
    ticker = c.get("ticker", "")
    score = c.get("composite_score") or 0
    sector = c.get("sector") or "—"
    shares = c.get("shares") or 0
    price = c.get("current_price") or c.get("entry_price") or 0
    beta = c.get("beta") or "—"
    piotroski = c.get("piotroski")
    altman_z = c.get("altman_z")
    approval = c.get("approval_status") or "pending"

    # Piotroski color
    if piotroski is not None:
        p_color = "#10b981" if piotroski >= 7 else ("#f59e0b" if piotroski <= 3 else "#e2e8f0")
        p_str = f'<span style="color:{p_color};font-weight:700;">{piotroski}/9</span>'
    else:
        p_str = "—"

    # Altman-Z label
    if altman_z is not None:
        z_label = "safe" if altman_z > 2.99 else ("grey" if altman_z > 1.81 else "distress")
        z_color = "#10b981" if z_label == "safe" else ("#f59e0b" if z_label == "grey" else "#f43f5e")
        z_str = f'<span style="color:{z_color};">{altman_z:.1f} ({z_label})</span>'
    else:
        z_str = "—"

    color = "#10b981" if side == "long" else "#f43f5e"
    score_pct = f"{score*100:.0f}"

    with st.container():
        st.markdown(
            f"""<div style="background:#131827;border:1px solid #1e2d45;border-radius:8px;
            padding:12px 16px;margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <span style="font-size:18px;font-weight:700;color:{color};">{ticker}</span>
              <span style="font-family:'JetBrains Mono';font-size:13px;color:#6366f1;">
                score: {score_pct}</span>
            </div>
            <div style="font-size:12px;color:#64748b;margin-top:4px;">
              {sector} &nbsp;|&nbsp; {shares:,.0f} sh &nbsp;|&nbsp;
              ${price*abs(shares):,.0f} &nbsp;|&nbsp; β={beta}
            </div>
            <div style="font-size:12px;margin-top:6px;">
              Piotroski: {p_str} &nbsp;|&nbsp; Altman-Z: {z_str}
            </div>
            </div>""",
            unsafe_allow_html=True,
        )

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("✓ Approve", key=f"approve_{ticker}_{side}", use_container_width=True):
                _set_approval(ticker, "approved")
                st.cache_data.clear()
                st.rerun()
        with b2:
            if st.button("✗ Reject", key=f"reject_{ticker}_{side}", use_container_width=True):
                _set_approval(ticker, "rejected")
                st.cache_data.clear()
                st.rerun()
        with b3:
            if st.button("↺ Reset", key=f"reset_{ticker}_{side}", use_container_width=True):
                _set_approval(ticker, "pending")
                st.cache_data.clear()
                st.rerun()

        # Claude analysis expander
        if c.get("claude_analysis"):
            with st.expander("Claude analysis"):
                try:
                    parsed = json.loads(c["claude_analysis"])
                    st.json(parsed)
                except Exception:
                    st.markdown(str(c["claude_analysis"])[:1000])


def render():
    import plotly.graph_objects as go
    from dashboard.style import PLOTLY_LAYOUT, COLORS

    candidates = _load_candidates()

    # KPI row
    scores = _load_scores()
    if scores:
        top_score = scores[0]["composite_score"] or 0
        bottom_score = scores[-1]["composite_score"] or 0
    else:
        top_score = bottom_score = 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Long Candidates", len([s for s in scores if (s.get("composite_score") or 0) >= 0.5]))
    k2.metric("Short Candidates", len([s for s in scores if (s.get("composite_score") or 0) < 0.5]))
    k3.metric("Top Score", f"{top_score*100:.0f}")
    k4.metric("Bottom Score", f"{bottom_score*100:.0f}")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Optimizer toggle
    opt_mode = st.radio(
        "Optimizer",
        ["MVO", "Conviction"],
        horizontal=True,
        index=0,
    )
    st.session_state["optimizer_mode"] = opt_mode

    # Factor heatmap
    st.markdown("### Factor Heatmap")
    tickers, factor_names, matrix = _load_factor_heatmap_data()

    if tickers and matrix:
        # Color: 0=red, 0.5=grey, 1=green
        import numpy as np
        z = np.array(matrix).T.tolist() if matrix else []
        labels = tickers

        fig = go.Figure(go.Heatmap(
            z=z,
            x=labels,
            y=factor_names,
            colorscale=[
                [0.0, "#f43f5e"],
                [0.5, "#1e2d45"],
                [1.0, "#10b981"],
            ],
            zmin=0, zmax=1,
            text=[[f"{v:.2f}" for v in row] for row in z] if z else None,
            hovertemplate="<b>%{x}</b><br>%{y}: %{z:.2f}<extra></extra>",
            colorbar=dict(
                tickfont=dict(color="#e2e8f0"),
                outlinecolor="#1e2d45",
                thickness=12,
            ),
        ))
        layout = dict(**PLOTLY_LAYOUT)
        layout["height"] = 300
        layout["xaxis"] = dict(tickfont=dict(size=10, color="#94a3b8"), tickangle=-45)
        layout["yaxis"] = dict(tickfont=dict(size=11, color="#94a3b8"))
        layout["title"] = dict(text="Top 30 Long | Bottom 30 Short × 8 Factors", font=dict(size=13))
        fig.update_layout(**layout)

        # Vertical line separating longs and shorts
        if len([s for s in scores if (s.get("composite_score") or 0) >= 0.5]) > 0:
            split = min(30, len([s for s in scores if (s.get("composite_score") or 0) >= 0.5]))
            if split < len(labels):
                fig.add_vline(x=split - 0.5, line_color="#6366f1", line_width=1, line_dash="dot")

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No factor scores available yet — run `python run_scoring.py` first.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Candidate cards
    long_col, short_col = st.columns(2)
    with long_col:
        st.markdown("### 🟢 Top 10 Long Candidates")
        for c in candidates.get("long", []):
            _render_candidate_card(c, "long")

    with short_col:
        st.markdown("### 🔴 Top 10 Short Candidates")
        for c in candidates.get("short", []):
            _render_candidate_card(c, "short")

    if not candidates.get("long") and not candidates.get("short"):
        st.info("No candidates yet — run `python run_scoring.py` to generate scores.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Execute button
    st.markdown("### Execute")
    approved = [c for c in (candidates.get("long", []) + candidates.get("short", []))
                if c.get("approval_status") == "approved"]
    st.markdown(f"**{len(approved)} approved trades** waiting for execution.")

    if st.button("⚡ Run Pre-Trade Veto + Generate Trade List", type="primary"):
        if not approved:
            st.warning("No approved trades. Approve candidates above first.")
        else:
            with st.spinner("Running pre-trade veto..."):
                import subprocess
                import sys
                result = subprocess.run(
                    [sys.executable, "run_execution.py", "--dry-run"],
                    capture_output=True, text=True,
                    cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
                )
            if result.returncode == 0:
                st.success("Pre-trade veto passed. Trade list generated.")
                st.code(result.stdout)
            else:
                st.error("Pre-trade veto rejected some orders.")
                st.code(result.stdout + result.stderr)
