"""Movie title sanitization and text utilities for Regal Pro.
Synchronized with daily metadata sweep routines in regal-pro-admin.
"""

import re
from typing import Optional


# Pre-compiled regex patterns for maximum performance
LEADING_PREFIX_PATTERNS = [
    re.compile(r'^[A-Z0-9]{2,8}:\s*'),                        # Uppercase format codes: IMAX:, RPX:, 4DX:, etc.
    re.compile(r'^Popcorn Reef:\s*', re.IGNORECASE),           # e.g., Popcorn Reef: Bloodsport
    re.compile(r'^Fangoria Staff Picks:\s*', re.IGNORECASE),   # e.g., Fangoria Staff Picks: Sinister
]

EVENT_SUFFIX_PATTERNS = [
    re.compile(r'[:\-\+]*\s*(\d+th\s+)?Anniversary.*', re.IGNORECASE),
    re.compile(r'[:\-\+]*\s*(\d+th\s+)?Anniv\.*', re.IGNORECASE),
    re.compile(r'[:\-\+]*\s*Early\s+Access.*', re.IGNORECASE),
    re.compile(r'[:\-\+]*\s*(Live(stream)?\s+)?(Q&A|Intro).*', re.IGNORECASE),  # Live Intro w/Cast, Livestream Q&A
    re.compile(r':\s*Live\s*$', re.IGNORECASE),                                  # Catches ": Live" at end
    re.compile(r'[:\-\+]*\s*Hong\s+Kong\s+Cinema\s+Classics.*', re.IGNORECASE),
    re.compile(r'[:\-\+]*\s*70mm(\s+Film\s+Reissue)?.*', re.IGNORECASE),
    re.compile(r'\s*\b(3D|4K)\b.*', re.IGNORECASE),                             # " 3D", " 4K", " 4K Re-release"
    re.compile(r'[:\-\+]*\s*Prime Member.*', re.IGNORECASE),                    # "- Prime Member"
    re.compile(r'[:\-\+]*\s*Fan Event.*', re.IGNORECASE),                       # "- Fan Event"
    re.compile(r'[:\-\+]*\s*Re-?release.*', re.IGNORECASE),                     # Re-release / Rerelease
    re.compile(r'[:\-\+]*\s*Reissue.*', re.IGNORECASE),                         # Reissue
]

PARENTHETICAL_PATTERN = re.compile(r'\s*\([^)]*\)')


def clean_movie_title(title: Optional[str]) -> str:
    """Sanitize and standardize movie titles by removing promo prefixes, format tags, and event labels.

    Synchronized with regal-pro-admin/pages/01_daily_sweep.py.

    Args:
        title: Raw title string from API or schedule feed.

    Returns:
        Clean, canonical movie title string.
    """
    if not title:
        return ""

    # 1. Strip leading prefixes
    for pref in LEADING_PREFIX_PATTERNS:
        title = pref.sub('', title)

    # 2. Strip event suffixes, format labels, and anniversary markers
    for pattern in EVENT_SUFFIX_PATTERNS:
        title = pattern.sub('', title)

    # 3. Strip parenthetical descriptors
    title = PARENTHETICAL_PATTERN.sub('', title)

    return title.strip()


def generate_regal_url(title: Optional[str], master_code: Optional[str]) -> str:
    """Generate the canonical Regal movies URL slug for a given title and master code.

    Synchronized with regal-pro-admin/pages/01_daily_sweep.py.

    Args:
        title: Movie title.
        master_code: 4-character Regal master movie code.

    Returns:
        Full https://www.regmovies.com/movies/... URL string.
    """
    if not title or not master_code:
        return ""

    # Drop ', /, ., ,, &, -, and !
    url_slug = re.sub(r"['/.,!&-]", "", title.lower())
    # Replace runs of whitespace, colons, parentheses, and plus signs with a single hyphen
    url_slug = re.sub(r"[\s():+]+", "-", url_slug).strip("-")
    # Force master_code to lower case
    return f"https://www.regmovies.com/movies/{url_slug}-{master_code.lower()}"


def normalize_search_text(text: Optional[str]) -> str:
    """Normalize text for case-insensitive and punctuation-agnostic search comparisons."""
    if not text:
        return ""
    # Lowercase and strip non-alphanumeric characters except spaces
    return re.sub(r'[^a-z0-9\s]', '', text.lower()).strip()
