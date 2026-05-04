"""Page I — JARVIS Cover + Chat + Metrics Row."""
import json
import logging
import os
import sqlite3
import time
from datetime import date, datetime

import streamlit as st

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "cache", "jarvis.db")


@st.cache_data(ttl=300)
def _load_metrics() -> dict:
    if not os.path.exists(DB_PATH):
        return {}
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        universe = conn.execute("SELECT COUNT(*) FROM tickers WHERE is_active=1").fetchone()[0]
        positions_row = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE is_active=1"
        ).fetchone()
        positions_count = positions_row[0] if positions_row else 0

        scores = conn.execute(
            "SELECT ticker, composite_score FROM scores ORDER BY scored_at DESC LIMIT 503"
        ).fetchall()
        long_cand = sum(1 for s in scores if (s["composite_score"] or 0) > 0.6)
        short_cand = sum(1 for s in scores if (s["composite_score"] or 0) < 0.4)

        # Crowding proxy: count high short-interest tickers
        crowding = 0
        try:
            crowding = conn.execute(
                "SELECT COUNT(*) FROM short_interest WHERE short_interest_pct > 20 "
                "AND date >= date('now','-7 days')"
            ).fetchone()[0]
        except Exception:
            pass

        # Insider events
        insider_events = 0
        ceo_buys = cluster_buys = 0
        try:
            insider_events = conn.execute(
                "SELECT COUNT(*) FROM insider_transactions WHERE transaction_date >= date('now','-30 days')"
            ).fetchone()[0]
            ceo_buys = conn.execute(
                "SELECT COUNT(*) FROM insider_transactions "
                "WHERE title LIKE '%CEO%' OR title LIKE '%CFO%' "
                "AND transaction_type='P' AND transaction_date >= date('now','-30 days')"
            ).fetchone()[0]
            cluster_buys = conn.execute(
                "SELECT COUNT(DISTINCT ticker) FROM insider_transactions "
                "WHERE transaction_type='P' AND transaction_date >= date('now','-7 days') "
                "GROUP BY ticker HAVING COUNT(*)>=3"
            ).fetchone()[0] if conn.execute(
                "SELECT COUNT(DISTINCT ticker) FROM insider_transactions "
                "WHERE transaction_type='P' AND transaction_date >= date('now','-7 days') "
                "GROUP BY ticker HAVING COUNT(*)>=3"
            ).fetchone() else 0
        except Exception:
            pass

        # VIX proxy from daily_prices
        vix = None
        try:
            row = conn.execute(
                "SELECT adj_close FROM daily_prices WHERE ticker='^VIX' ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row:
                vix = float(row[0])
        except Exception:
            pass

        # Earnings within 7 days
        earnings_7d = 0
        try:
            earnings_7d = conn.execute(
                "SELECT COUNT(DISTINCT ticker) FROM earnings_transcripts "
                "WHERE date BETWEEN date('now') AND date('now','+7 days')"
            ).fetchone()[0]
        except Exception:
            pass

        conn.close()
        return {
            "universe": universe,
            "long_cand": long_cand,
            "short_cand": short_cand,
            "positions": positions_count,
            "crowding": crowding,
            "insider_events": insider_events,
            "ceo_cfo_buys": ceo_buys,
            "cluster_buys": cluster_buys,
            "vix": vix,
            "earnings_7d": earnings_7d,
        }
    except Exception as exc:
        logger.warning("Metrics load error: %s", exc)
        return {}


@st.cache_data(ttl=300)
def _build_system_snapshot() -> str:
    """Build ~19KB JSON snapshot of system state for JARVIS chat context."""
    if not os.path.exists(DB_PATH):
        return json.dumps({"status": "no_database"})
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        positions = conn.execute(
            "SELECT ticker, shares, entry_price, current_price, pnl, pnl_pct, sector, beta "
            "FROM positions WHERE is_active=1 LIMIT 50"
        ).fetchall()

        top_scores = conn.execute(
            "SELECT ticker, composite_score, scored_at FROM scores ORDER BY scored_at DESC LIMIT 30"
        ).fetchall()

        history = conn.execute(
            "SELECT date, total_value FROM portfolio_history ORDER BY date DESC LIMIT 10"
        ).fetchall()

        recent_orders = []
        try:
            recent_orders = conn.execute(
                "SELECT ticker, action, qty, fill_price, status, submitted_at "
                "FROM orders ORDER BY submitted_at DESC LIMIT 10"
            ).fetchall()
        except Exception:
            pass

        conn.close()

        aum = sum(
            abs(p["shares"] or 0) * (p["current_price"] or p["entry_price"] or 0)
            for p in positions
        )
        net_exposure = sum(
            (p["shares"] or 0) * (p["current_price"] or p["entry_price"] or 0)
            for p in positions
        )

        return json.dumps({
            "generated_at": datetime.utcnow().isoformat(),
            "fund": "Meridian Capital Partners — Long/Short Equity",
            "aum_usd": round(aum, 2),
            "net_exposure_usd": round(net_exposure, 2),
            "positions": [dict(p) for p in positions],
            "top_scores": [dict(s) for s in top_scores],
            "portfolio_history_10d": [dict(h) for h in history],
            "recent_orders": [dict(o) for o in recent_orders],
        }, default=str, indent=2)[:19_000]  # cap at 19KB
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _call_jarvis(question: str, context_json: str) -> str:
    """Send question + system snapshot to Claude, return text response."""
    try:
        import openai as _oi
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return "OPENROUTER_API_KEY not set — set it to enable JARVIS chat."
        client = _oi.OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        resp = client.chat.completions.create(
            model="anthropic/claude-sonnet-4-6",
            max_tokens=800,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are JARVIS, the AI investment analyst for Meridian Capital Partners, "
                        "a long/short equity hedge fund. You have access to the live portfolio state "
                        "shown in the context JSON. Answer questions directly and concisely. "
                        "Be analytical and precise. Use numbers when they're available in the data."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Portfolio context:\n{context_json}\n\nQuestion: {question}",
                },
            ],
            extra_headers={"HTTP-Referer": "https://jarvis.internal", "X-Title": "JARVIS"},
        )
        return resp.choices[0].message.content or "No response."
    except Exception as exc:
        return f"Error calling JARVIS: {exc}"


def render():
    m = _load_metrics()

    # Two-column layout: left = JARVIS hero + chat, right = gradient fill
    left, right = st.columns([1, 1])

    with left:
        st.markdown(
            '<div class="jarvis-hero">JARVIS</div>'
            '<div class="jarvis-subtitle">Long/Short Hedge Fund Analyst</div>',
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        # Chat input
        question = st.text_input(
            "",
            placeholder="Ask anything about the portfolio...",
            key="jarvis_question",
            label_visibility="collapsed",
        )
        ask_btn = st.button("ASK JARVIS", use_container_width=True, type="primary")

        if ask_btn and question:
            with st.spinner("JARVIS is analyzing..."):
                context = _build_system_snapshot()
                # Cache response for 60s per question
                cache_key = f"jarvis_resp_{hash(question)}"
                if cache_key not in st.session_state:
                    st.session_state[cache_key] = _call_jarvis(question, context)
                    st.session_state[f"{cache_key}_ts"] = time.time()
                elif time.time() - st.session_state.get(f"{cache_key}_ts", 0) > 60:
                    st.session_state[cache_key] = _call_jarvis(question, context)
                    st.session_state[f"{cache_key}_ts"] = time.time()
                response = st.session_state[cache_key]

            st.markdown(
                f'<div style="background:#131827;border:1px solid #1e2d45;border-radius:8px;'
                f'padding:16px;margin-top:12px;font-size:14px;line-height:1.6;">'
                f'{response}</div>',
                unsafe_allow_html=True,
            )

    with right:
        # Dark gradient panel with fund name
        st.markdown(
            """<div style="background:linear-gradient(135deg,#131827 0%,#1a2035 50%,#0f172a 100%);
            border:1px solid #1e2d45;border-radius:12px;padding:48px 32px;
            height:280px;display:flex;flex-direction:column;justify-content:center;">
            <div style="font-size:13px;letter-spacing:4px;color:#6366f1;text-transform:uppercase;
            font-weight:700;">Meridian Capital Partners</div>
            <div style="font-size:24px;font-weight:700;color:#e2e8f0;margin-top:8px;">
            Delaware Limited Partnership</div>
            <div style="font-size:13px;color:#64748b;margin-top:4px;">Inception: January 2026</div>
            </div>""",
            unsafe_allow_html=True,
        )

    # Status strip
    vix_val = m.get("vix")
    if vix_val:
        if vix_val < 15:
            vix_regime = "LOW"
            vix_class = "badge-green"
        elif vix_val < 25:
            vix_regime = "NORMAL"
            vix_class = "badge-yellow"
        else:
            vix_regime = "HIGH"
            vix_class = "badge-red"
        vix_badge = f'<span class="badge {vix_class}">VIX {vix_regime} ({vix_val:.1f})</span>'
    else:
        vix_badge = '<span class="badge badge-blue">VIX N/A</span>'

    st.markdown(
        f'<div style="display:flex;gap:12px;align-items:center;margin:8px 0;">'
        f'{vix_badge} &nbsp;'
        f'<span class="badge badge-live">● ALL DATA LIVE</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # 10-metric row
    st.markdown("<hr>", unsafe_allow_html=True)
    cols = st.columns(10)
    metrics = [
        ("Universe", m.get("universe", "—")),
        ("Long Cand.", m.get("long_cand", "—")),
        ("Short Cand.", m.get("short_cand", "—")),
        ("Positions", m.get("positions", "—")),
        ("Crowding", m.get("crowding", "—")),
        ("Insider Events", m.get("insider_events", "—")),
        ("CEO/CFO Buys", m.get("ceo_cfo_buys", "—")),
        ("Cluster Buys", m.get("cluster_buys", "—")),
        ("VIX", f"{vix_val:.1f}" if vix_val else "—"),
        ("Earnings -7D", m.get("earnings_7d", "—")),
    ]
    for col, (label, val) in zip(cols, metrics):
        col.metric(label, val)
