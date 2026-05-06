"""Page II — Research: Factor heatmap + candidate cards."""
from __future__ import annotations

import json
import logging
import os
import sqlite3

import streamlit as st
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "cache", "jarvis.db")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

FACTOR_COLS = [
    ("momentum", "momentum_score"),
    ("value", "value_score"),
    ("quality", "quality_score"),
    ("growth", "growth_score"),
    ("revisions", "revisions_score"),
    ("short_interest", "short_interest_score"),
    ("insider", "insider_score"),
    ("institutional", "institutional_score"),
]


def _db_mtime() -> float:
    return os.path.getmtime(DB_PATH) if os.path.exists(DB_PATH) else 0.0


def _score_pct(score: float | None) -> float:
    return float(score or 0.0)


def _score_unit(score: float | None) -> float:
    return max(0.0, min(_score_pct(score) / 100.0, 1.0))


@st.cache_data(ttl=300)
def _load_scores(db_mtime: float = 0.0) -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT s.ticker, s.composite_raw, s.composite_score, s.date AS scored_at, "
            "s.momentum_score, s.value_score, s.quality_score, s.growth_score, "
            "s.revisions_score, s.short_interest_score, s.insider_score, s.institutional_score, "
            "s.is_long_candidate, s.is_short_candidate, "
            "t.sector, f.market_cap "
            "FROM scores s LEFT JOIN tickers t ON s.ticker=t.symbol "
            "LEFT JOIN fundamentals f ON f.rowid = ("
            "  SELECT f2.rowid FROM fundamentals f2 WHERE f2.ticker=s.ticker "
            "  ORDER BY f2.report_date DESC, CASE f2.period WHEN 'quarterly' THEN 0 ELSE 1 END "
            "  LIMIT 1"
            ") "
            "WHERE s.date = (SELECT MAX(s2.date) FROM scores s2 WHERE s2.ticker=s.ticker) "
            "ORDER BY s.composite_score DESC"
        ).fetchall()
        conn.close()
        seen = set()
        scores = []
        for row in rows:
            item = dict(row)
            ticker = item.get("ticker")
            if ticker in seen:
                continue
            seen.add(ticker)
            scores.append(item)
        return scores
    except Exception as exc:
        logger.warning("Scores load error: %s", exc)
        return []


@st.cache_data(ttl=300)
def _load_candidates(db_mtime: float = 0.0) -> dict:
    """Return top 10 long and top 10 short candidates with position data."""
    scores = _load_scores(db_mtime)
    if not scores:
        return {"long": [], "short": []}

    longs = sorted(
        [s for s in scores if int(s.get("is_long_candidate") or 0) == 1],
        key=lambda x: x.get("composite_raw") or x.get("composite_score") or 0,
        reverse=True,
    )[:10]
    shorts = sorted(
        [s for s in scores if int(s.get("is_short_candidate") or 0) == 1],
        key=lambda x: x.get("composite_raw") or x.get("composite_score") or 0,
    )[:10]

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
                        "SELECT piotroski_f_score, altman_z_score FROM fundamentals WHERE ticker=? "
                        "ORDER BY report_date DESC LIMIT 1",
                        (ticker,),
                    ).fetchone()
                    if fund:
                        c["piotroski"] = fund["piotroski_f_score"]
                        c["altman_z"] = fund["altman_z_score"]
                except Exception:
                    pass

                # Claude analysis from cache
                try:
                    from analysis.cache import get_cached
                    cached = get_cached(ticker, "dashboard_candidate")
                    if cached:
                        c["ai_analysis"] = cached
                except Exception:
                    pass
        conn.close()
    except Exception as exc:
        logger.warning("Candidate enrichment error: %s", exc)

    return {"long": longs, "short": shorts}


@st.cache_data(ttl=300)
def _load_factor_heatmap_data(db_mtime: float = 0.0) -> tuple:
    """Return (tickers, factor_names, matrix) for heatmap."""
    scores = _load_scores(db_mtime)
    if not scores:
        return [], [], []

    longs = [
        s["ticker"]
        for s in sorted(
            [s for s in scores if int(s.get("is_long_candidate") or 0) == 1],
            key=lambda x: x.get("composite_raw") or x.get("composite_score") or 0,
            reverse=True,
        )[:30]
    ]
    shorts = [
        s["ticker"]
        for s in sorted(
            [s for s in scores if int(s.get("is_short_candidate") or 0) == 1],
            key=lambda x: x.get("composite_raw") or x.get("composite_score") or 0,
        )[:30]
    ]

    tickers = longs + shorts

    factor_names = [name for name, _ in FACTOR_COLS]

    if not tickers or not os.path.exists(DB_PATH):
        return tickers, factor_names, []

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        matrix = []
        for ticker in tickers:
            row_data = []
            row = conn.execute(
                "SELECT momentum_score, value_score, quality_score, growth_score, "
                "revisions_score, short_interest_score, insider_score, institutional_score "
                "FROM scores WHERE ticker=? ORDER BY date DESC LIMIT 1",
                (ticker,),
            ).fetchone()
            for _, col in FACTOR_COLS:
                row_data.append(_score_unit(row[col]) if row else 0.5)
            matrix.append(row_data)
        conn.close()
        return tickers, factor_names, matrix
    except Exception as exc:
        logger.warning("Heatmap data error: %s", exc)
        return tickers, factor_names, [[0.5] * 8 for _ in tickers]


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


def _factor_summary(c: dict) -> list[tuple[str, float]]:
    rows = []
    for label, col in FACTOR_COLS:
        if c.get(col) is not None:
            rows.append((label.replace("_", " ").title(), float(c[col])))
    return rows


def _analysis_payload(c: dict, side: str) -> dict:
    return {
        "ticker": c.get("ticker"),
        "side": side,
        "sector": c.get("sector"),
        "composite_score": c.get("composite_score"),
        "factors": {label: score for label, score in _factor_summary(c)},
        "piotroski": c.get("piotroski"),
        "altman_z": c.get("altman_z"),
        "shares": c.get("shares"),
        "price": c.get("current_price") or c.get("entry_price"),
        "beta": c.get("beta"),
    }


def _run_candidate_analysis(c: dict, side: str, force: bool = False) -> dict:
    ticker = c.get("ticker")
    if not ticker:
        return {"error": "Missing ticker"}

    from analysis.cache import get_cached, set_cache

    if not force:
        cached = get_cached(ticker, "dashboard_candidate")
        if cached:
            return cached

    load_dotenv(os.path.join(ROOT, ".env"))
    from analysis.api_client import APIClient

    payload = _analysis_payload(c, side)
    system_prompt = (
        "You are JARVIS, a long/short equity research analyst. Explain why a stock "
        "is appearing in the top or bottom candidate list. Be concise, investment-focused, "
        "and explicit about factor support and risks. Return valid JSON only."
    )
    user_prompt = (
        "Analyze this candidate and return JSON with keys: thesis, factor_drivers, "
        "risk_flags, decision_notes, confidence. Use 2-4 short bullet strings for "
        "factor_drivers and risk_flags.\n\n"
        f"{json.dumps(payload, default=str, indent=2)}"
    )
    client = APIClient()
    text = client.chat_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=700,
        ticker=ticker,
        analyzer_type="dashboard_candidate",
    )
    if not text:
        provider = os.getenv("JARVIS_AI_PROVIDER", "openrouter")
        result = {
            "error": (
                f"No response from AI provider '{provider}'. Check the Settings tab, "
                "run Codex login if using codex, or set OPENROUTER_API_KEY and "
                "JARVIS_AI_PROVIDER=openrouter."
            )
        }
        return result

    result = APIClient._extract_json(text)
    if not result:
        result = {
            "thesis": text.strip()[:1200],
            "factor_drivers": [],
            "risk_flags": [],
            "decision_notes": "AI returned text instead of JSON; showing raw summary.",
            "confidence": "unparsed",
        }
    set_cache(ticker, "dashboard_candidate", result, artifact=json.dumps(payload, sort_keys=True), ttl=168)
    return result


def _render_analysis_result(result: dict) -> None:
    if result.get("error"):
        st.error(result["error"])
        return
    if result.get("thesis"):
        st.markdown(f"**Thesis**  \n{result['thesis']}")
    if result.get("factor_drivers"):
        st.markdown("**Factor Drivers**")
        for item in result["factor_drivers"]:
            st.markdown(f"- {item}")
    if result.get("risk_flags"):
        st.markdown("**Risk Flags**")
        for item in result["risk_flags"]:
            st.markdown(f"- {item}")
    if result.get("decision_notes"):
        st.markdown(f"**Decision Notes**  \n{result['decision_notes']}")
    if result.get("confidence"):
        st.caption(f"Confidence: {result['confidence']}")


def _render_ai_expander(c: dict, side: str, idx: int) -> None:
    ticker = c.get("ticker", "")
    cached = c.get("ai_analysis")
    label = f"{ticker} — AI analysis" if cached else f"{ticker} — analyze with AI"
    with st.expander(label, expanded=bool(cached)):
        if cached:
            _render_analysis_result(cached)
        else:
            st.caption("Generate a concise explanation of why this name is in the candidate tail.")

        button_label = "Re-run AI analysis" if cached else "Run AI analysis"
        if st.button(button_label, key=f"ai_{ticker}_{side}_{idx}", use_container_width=True):
            with st.spinner(f"Analyzing {ticker}..."):
                try:
                    result = _run_candidate_analysis(c, side, force=True)
                    c["ai_analysis"] = result
                    st.cache_data.clear()
                    _render_analysis_result(result)
                except Exception as exc:
                    st.error(f"AI analysis failed: {exc}")


def _render_candidate_card(c: dict, side: str, idx: int = 0):
    ticker = c.get("ticker", "")
    score = _score_pct(c.get("composite_score"))
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
    score_pct = f"{score:.0f}"

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
            if st.button("✓ Approve", key=f"approve_{ticker}_{side}_{idx}", use_container_width=True):
                _set_approval(ticker, "approved")
                st.cache_data.clear()
                st.rerun()
        with b2:
            if st.button("✗ Reject", key=f"reject_{ticker}_{side}_{idx}", use_container_width=True):
                _set_approval(ticker, "rejected")
                st.cache_data.clear()
                st.rerun()
        with b3:
            if st.button("↺ Reset", key=f"reset_{ticker}_{side}_{idx}", use_container_width=True):
                _set_approval(ticker, "pending")
                st.cache_data.clear()
                st.rerun()

        _render_ai_expander(c, side, idx)


def render():
    import plotly.graph_objects as go
    from dashboard.style import PLOTLY_LAYOUT, COLORS

    db_mtime = _db_mtime()
    candidates = _load_candidates(db_mtime)

    # KPI row
    scores = _load_scores(db_mtime)
    if scores:
        top_score = _score_pct(scores[0]["composite_score"])
        bottom_score = _score_pct(scores[-1]["composite_score"])
    else:
        top_score = bottom_score = 0

    long_count = sum(1 for s in scores if int(s.get("is_long_candidate") or 0) == 1)
    short_count = sum(1 for s in scores if int(s.get("is_short_candidate") or 0) == 1)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Long Candidates", long_count)
    k2.metric("Short Candidates", short_count)
    k3.metric("Top Score", f"{top_score:.0f}")
    k4.metric("Bottom Score", f"{bottom_score:.0f}")

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
    tickers, factor_names, matrix = _load_factor_heatmap_data(db_mtime)

    if tickers and matrix:
        # Color: 0=red, 0.5=grey, 1=green
        fig = go.Figure(go.Heatmap(
            z=matrix,
            x=[name.replace("_", " ").title() for name in factor_names],
            y=tickers,
            colorscale=[
                [0.0, "#f43f5e"],
                [0.5, "#1e2d45"],
                [1.0, "#10b981"],
            ],
            zmin=0, zmax=1,
            text=[[f"{v*100:.0f}" for v in row] for row in matrix],
            hovertemplate="<b>%{y}</b><br>%{x}: %{z:.2f}<extra></extra>",
            colorbar=dict(
                tickfont=dict(color="#e2e8f0"),
                outlinecolor="#1e2d45",
                thickness=12,
            ),
        ))
        layout = dict(**PLOTLY_LAYOUT)
        layout["height"] = 760
        layout["margin"] = dict(l=70, r=54, t=96, b=28)
        layout["xaxis"] = dict(
            tickfont=dict(size=12, color="#cbd5e1"),
            side="top",
            tickangle=0,
            automargin=True,
        )
        layout["yaxis"] = dict(
            tickfont=dict(size=10, color="#cbd5e1"),
            autorange="reversed",
            automargin=True,
        )
        layout["title"] = dict(
            text="Factor Scoring Heatmap (Top + Bottom by Composite)",
            font=dict(size=15),
            x=0,
            xanchor="left",
            y=0.995,
            yanchor="top",
        )
        fig.update_layout(**layout)

        # Horizontal line separating longs and shorts
        if scores:
            split = min(30, len(scores))
            if split < len(tickers):
                fig.add_hline(y=split - 0.5, line_color="#6366f1", line_width=1, line_dash="dot")

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No factor scores available yet — run `python run_scoring.py` first.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # Candidate cards
    long_col, short_col = st.columns(2)
    with long_col:
        st.markdown("### 🟢 Top 10 Long Candidates")
        for idx, c in enumerate(candidates.get("long", [])):
            _render_candidate_card(c, "long", idx)

    with short_col:
        st.markdown("### 🔴 Top 10 Short Candidates")
        for idx, c in enumerate(candidates.get("short", [])):
            _render_candidate_card(c, "short", idx)

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
