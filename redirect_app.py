"""Lightweight redirection bridge for legacy Streamlit Community Cloud domain."""

import urllib.parse
import streamlit as st

st.set_page_config(
    page_title="RegalPro — Redirecting to regalpro.app",
    page_icon="🎬",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Preserve incoming query parameters (e.g. ?theater=1487&date=2026-10-24)
raw_params = st.query_params.to_dict()
encoded_query = urllib.parse.urlencode(raw_params) if raw_params else ""
target_url = f"https://regalpro.app/?{encoded_query}" if encoded_query else "https://regalpro.app/"

# Custom minimal styling
st.html("""
<style>
    #MainMenu, header, footer { visibility: hidden; }
    .redirect-card {
        padding: 2.5rem 2rem;
        border-radius: 12px;
        background: linear-gradient(135deg, rgba(151,166,195,0.08) 0%, rgba(151,166,195,0.02) 100%);
        border: 1px solid rgba(151,166,195,0.25);
        text-align: center;
        margin-top: 2rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
    }
    .redirect-title {
        font-size: 1.85rem;
        font-weight: 700;
        margin-bottom: 0.75rem;
        line-height: 1.2;
    }
    .redirect-desc {
        font-size: 1.05rem;
        color: #6b7280;
        margin-bottom: 1.5rem;
        line-height: 1.5;
    }
    .countdown-text {
        font-size: 0.9rem;
        color: #9ca3af;
        margin-top: 1.25rem;
    }
</style>
""")

st.markdown(f"""
<div class="redirect-card">
    <div class="redirect-title">RegalPro Has Moved to a New Home</div>
    <div class="redirect-desc">
        We have upgraded to our permanent standalone domain at <strong>regalpro.app</strong>.<br>
        Please update your bookmarks for faster access, new features and upcoming updates.
    </div>
</div>
""", unsafe_allow_html=True)

st.write("")
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    st.link_button("Go to regalpro.app Now", target_url, type="primary", use_container_width=True)

st.markdown(f"""
<div class="countdown-text" style="text-align: center;">
    Redirecting automatically in 5 seconds...
</div>

<meta http-equiv="refresh" content="3;url={target_url}">
<script>
    setTimeout(function() {{
        try {{
            window.top.location.href = "{target_url}";
        }} catch (e) {{
            window.location.href = "{target_url}";
        }}
    }}, 5000);
</script>
""", unsafe_allow_html=True)
