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
    ("Portfolio", "portfolio"),
    ("Research", "research"),
    ("Risk", "risk"),
    ("Performance", "performance"),
    ("Execution", "execution"),
    ("Letter", "letter"),
    ("Settings", "settings"),
    ("Ops", "operations"),
]

if "active_tab" not in st.session_state:
    st.session_state.active_tab = TABS[0][1]
elif st.session_state.active_tab not in {tab_id for _, tab_id in TABS}:
    # Migrate older session labels such as "I  PORTFOLIO".
    old_label = str(st.session_state.active_tab).lower()
    for label, tab_id in TABS:
        if label.lower() in old_label:
            st.session_state.active_tab = tab_id
            break
    else:
        st.session_state.active_tab = TABS[0][1]

# Top navigation bar
nav_cols = st.columns(len(TABS))
for i, (col, (label, tab_id)) in enumerate(zip(nav_cols, TABS)):
    with col:
        is_active = st.session_state.active_tab == tab_id
        if st.button(
            label,
            key=f"nav_{i}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            st.session_state.active_tab = tab_id
            st.rerun()

st.markdown("<hr>", unsafe_allow_html=True)

active = st.session_state.active_tab

if active == "portfolio":
    from dashboard.tabs.portfolio import render
    render()
elif active == "research":
    from dashboard.tabs.research import render
    render()
elif active == "risk":
    from dashboard.tabs.risk import render
    render()
elif active == "performance":
    from dashboard.tabs.performance import render
    render()
elif active == "execution":
    from dashboard.tabs.execution import render
    render()
elif active == "letter":
    from dashboard.tabs.letter import render
    render()
elif active == "settings":
    from dashboard.tabs.settings import render
    render()
elif active == "operations":
    from dashboard.tabs.operations import render
    render()
