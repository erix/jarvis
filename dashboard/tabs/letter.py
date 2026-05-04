"""Page VI — LP Letter: Daily investor letter with regenerate button."""
import logging
import os
from datetime import date

import streamlit as st

logger = logging.getLogger(__name__)


def render():
    st.markdown("### Daily Investor Letter")

    target_date = st.date_input("Letter date", value=date.today())

    col_left, col_right = st.columns([3, 1])
    with col_right:
        regenerate = st.button("↺ Regenerate Letter", type="primary", use_container_width=True)

    # Check if letter already exists
    letters_dir = os.path.join(os.path.dirname(__file__), "..", "..", "output", "letters")
    letter_path = os.path.join(letters_dir, f"daily_{target_date}.md")
    letter_exists = os.path.exists(letter_path)

    force = regenerate

    with col_left:
        if not letter_exists and not force:
            st.info("No letter for this date yet. Click **Regenerate Letter** to generate one.")
            if st.button("Generate Letter", use_container_width=True):
                force = True

    if force or letter_exists:
        with st.spinner("Generating letter via Claude..." if force else "Loading letter..."):
            try:
                from reporting.lp_letter import get_letter_content
                content = get_letter_content(target_date=target_date, force=force)
                if force:
                    st.cache_data.clear()
            except Exception as exc:
                st.error(f"Letter generation failed: {exc}")
                return

        st.markdown("<hr>", unsafe_allow_html=True)

        # Render letter in styled container
        st.markdown(
            f"""<div style="background:#131827;border:1px solid #1e2d45;border-radius:12px;
            padding:40px 48px;max-width:800px;margin:0 auto;
            font-family:'Plus Jakarta Sans',sans-serif;line-height:1.7;font-size:14px;">
            </div>""",
            unsafe_allow_html=True,
        )
        st.markdown(content)

        st.markdown("<hr>", unsafe_allow_html=True)

        # List available letters
        st.markdown("### Previous Letters")
        try:
            if os.path.exists(letters_dir):
                letter_files = sorted(
                    [f for f in os.listdir(letters_dir) if f.startswith("daily_")],
                    reverse=True,
                )[:10]
                if letter_files:
                    for fname in letter_files:
                        fdate = fname.replace("daily_", "").replace(".md", "")
                        fpath = os.path.join(letters_dir, fname)
                        fsize = os.path.getsize(fpath) // 1024
                        st.caption(f"📄 {fdate} — {fsize}KB — `{fpath}`")
                else:
                    st.caption("No previous letters.")
        except Exception:
            pass
