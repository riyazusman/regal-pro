"""Lightweight redirection bridge for legacy Streamlit Community Cloud domain."""

import urllib.parse
import streamlit as st

st.set_page_config(
    page_title="RegalPro — Moved to regalpro.app",
    page_icon="🎬",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Preserve incoming query parameters (e.g. ?theater=1487&date=2026-10-24)
raw_params = st.query_params.to_dict()
encoded_query = urllib.parse.urlencode(raw_params) if raw_params else ""
target_url = f"https://regalpro.app/?{encoded_query}" if encoded_query else "https://regalpro.app/"

# Custom modern transition styling
st.html(f"""
<style>
    #MainMenu, header, footer {{ visibility: hidden; }}
    .stApp {{
        background-color: #0e1117;
    }}
    .redirect-card {{
        padding: 3rem 2rem;
        border-radius: 16px;
        background: linear-gradient(135deg, rgba(255, 75, 75, 0.08) 0%, rgba(151, 166, 195, 0.04) 100%);
        border: 1px solid rgba(255, 75, 75, 0.3);
        text-align: center;
        margin-top: 2rem;
        box-shadow: 0 8px 32px rgba(0,0,0,0.25);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}
    .redirect-badge {{
        display: inline-block;
        background: rgba(255, 75, 75, 0.15);
        color: #ff4b4b;
        border: 1px solid rgba(255, 75, 75, 0.4);
        border-radius: 20px;
        padding: 6px 18px;
        font-size: 0.85rem;
        font-weight: 700;
        margin-bottom: 1.25rem;
        letter-spacing: 0.5px;
        text-transform: uppercase;
    }}
    .redirect-title {{
        font-size: 2.2rem;
        font-weight: 800;
        color: #ffffff;
        margin-bottom: 1rem;
        line-height: 1.2;
    }}
    .redirect-desc {{
        font-size: 1.15rem;
        color: #97a6c3;
        margin-bottom: 1.5rem;
        line-height: 1.6;
        max-width: 540px;
        margin-left: auto;
        margin-right: auto;
    }}
    .redirect-url-box {{
        background: rgba(0, 0, 0, 0.35);
        border: 1px dashed rgba(255, 75, 75, 0.5);
        border-radius: 8px;
        padding: 10px 20px;
        display: inline-block;
        font-family: monospace;
        font-size: 1.2rem;
        color: #ff4b4b;
        font-weight: 700;
        margin-bottom: 2rem;
        word-break: break-all;
    }}
    .redirect-btn {{
        display: inline-block;
        background-color: #ff4b4b;
        color: #ffffff !important;
        font-size: 1.2rem;
        font-weight: 700;
        padding: 16px 40px;
        border-radius: 10px;
        text-decoration: none !important;
        box-shadow: 0 4px 18px rgba(255, 75, 75, 0.45);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }}
    .redirect-btn:hover {{
        transform: translateY(-2px);
        box-shadow: 0 6px 24px rgba(255, 75, 75, 0.6);
        color: #ffffff !important;
    }}
    .bookmark-hint {{
        font-size: 0.95rem;
        color: #8892b0;
        margin-top: 1.75rem;
        line-height: 1.5;
    }}
</style>

<div class="redirect-card">
    <div class="redirect-badge">🚀 Important Update</div>
    <div class="redirect-title">RegalPro has moved!</div>
    <div class="redirect-desc">
        We have moved to a new domain. Please launch the app below to open the new URL and update your bookmarks:
    </div>
    <div>
        <div class="redirect-url-box">{target_url}</div>
    </div>
    <div>
        <a href="{target_url}" target="_blank" rel="noopener noreferrer" class="redirect-btn">
            Open regalpro.app &rarr;
        </a>
    </div>
</div>
""")
