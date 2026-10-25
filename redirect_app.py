"""Lightweight redirection bridge for legacy Streamlit Community Cloud domain."""

import urllib.parse
import streamlit as st

st.set_page_config(
    page_title="RegalPro — Moving to regalpro.app",
    page_icon="🎬",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Preserve incoming query parameters (e.g. ?theater=1487&date=2026-10-24)
raw_params = st.query_params.to_dict()
encoded_query = urllib.parse.urlencode(raw_params) if raw_params else ""
target_url = f"https://regalpro.app/?{encoded_query}" if encoded_query else "https://regalpro.app/"

# Custom styling with a prominent top-level redirect button
st.html(f"""
<style>
    #MainMenu, header, footer {{ visibility: hidden; }}
    .redirect-card {{
        padding: 3rem 2rem;
        border-radius: 16px;
        background: linear-gradient(135deg, rgba(151,166,195,0.12) 0%, rgba(151,166,195,0.03) 100%);
        border: 1px solid rgba(151,166,195,0.25);
        text-align: center;
        margin-top: 3rem;
        box-shadow: 0 8px 32px rgba(0,0,0,0.12);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}
    .redirect-badge {{
        display: inline-block;
        background: rgba(255, 75, 75, 0.12);
        color: #ff4b4b;
        border: 1px solid rgba(255, 75, 75, 0.3);
        border-radius: 20px;
        padding: 4px 16px;
        font-size: 0.85rem;
        font-weight: 600;
        margin-bottom: 1rem;
        letter-spacing: 0.5px;
        text-transform: uppercase;
    }}
    .redirect-title {{
        font-size: 2rem;
        font-weight: 800;
        margin-bottom: 1rem;
        line-height: 1.2;
    }}
    .redirect-desc {{
        font-size: 1.1rem;
        color: #6b7280;
        margin-bottom: 2rem;
        line-height: 1.6;
        max-width: 520px;
        margin-left: auto;
        margin-right: auto;
    }}
    .redirect-btn {{
        display: inline-block;
        background-color: #ff4b4b;
        color: #ffffff !important;
        font-size: 1.15rem;
        font-weight: 700;
        padding: 14px 36px;
        border-radius: 10px;
        text-decoration: none !important;
        box-shadow: 0 4px 14px rgba(255, 75, 75, 0.4);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }}
    .redirect-btn:hover {{
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(255, 75, 75, 0.5);
        color: #ffffff !important;
    }}
    .countdown-text {{
        font-size: 0.9rem;
        color: #9ca3af;
        margin-top: 2rem;
    }}
</style>

<div class="redirect-card">
    <div class="redirect-badge">Domain Update</div>
    <div class="redirect-title">RegalPro Has Moved</div>
    <div class="redirect-desc">
        We have upgraded to our permanent standalone domain at <strong>regalpro.app</strong>.<br>
        Please update your bookmarks for faster access, new features and upcoming updates.
    </div>
    <div>
        <a href="{target_url}" target="_top" class="redirect-btn">
            Continue to regalpro.app &rarr;
        </a>
    </div>
    <div class="countdown-text">
        Redirecting automatically in 5 seconds...
    </div>
</div>

<img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3C/svg%3E" onload="
    setTimeout(function() {{
        try {{
            window.top.location.href = '{target_url}';
        }} catch(e) {{
            try {{
                window.parent.location.href = '{target_url}';
            }} catch(e2) {{
                var a = document.createElement('a');
                a.href = '{target_url}';
                a.target = '_top';
                document.body.appendChild(a);
                a.click();
            }}
        }}
    }}, 4000);
" style="display:none;" />
""")
