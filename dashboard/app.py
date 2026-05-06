"""JARVIS Dashboard — Streamlit entry point."""
import streamlit as st

st.set_page_config(
    page_title="JARVIS — Meridian Capital Partners",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

import os
import sys

# Make project root importable when running via `streamlit run dashboard/app.py`
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from dashboard.style import CSS
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)

# Auto-refresh during market hours (requires streamlit-autorefresh)
try:
    import pytz
    from datetime import datetime, time as _time
    ny_tz = pytz.timezone("America/New_York")
    now_ny = datetime.now(ny_tz)
    if _time(9, 30) <= now_ny.time() <= _time(16, 0):
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=5 * 60 * 1000, key="market_refresh")
except Exception:
    pass

TABS = [
    "I  PORTFOLIO",
    "II  RESEARCH",
    "III  RISK",
    "IV  PERFORMANCE",
    "V  EXECUTION",
    "VI  LETTER",
    "VII  SETTINGS",
]

if "active_tab" not in st.session_state:
    st.session_state.active_tab = TABS[0]

# Pill navigation bar
nav_cols = st.columns(len(TABS))
for i, (col, tab) in enumerate(zip(nav_cols, TABS)):
    with col:
        is_active = st.session_state.active_tab == tab
        if st.button(
            tab,
            key=f"nav_{i}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            st.session_state.active_tab = tab
            st.rerun()

st.markdown("<hr>", unsafe_allow_html=True)

active = st.session_state.active_tab

if active == TABS[0]:
    from dashboard.tabs.portfolio import render
    render()
elif active == TABS[1]:
    from dashboard.tabs.research import render
    render()
elif active == TABS[2]:
    from dashboard.tabs.risk import render
    render()
elif active == TABS[3]:
    from dashboard.tabs.performance import render
    render()
elif active == TABS[4]:
    from dashboard.tabs.execution import render
    render()
elif active == TABS[5]:
    from dashboard.tabs.letter import render
    render()
elif active == TABS[6]:
    from dashboard.tabs.settings import render
    render()
