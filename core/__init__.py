"""Regal Pro Core Domain Package.
Contains pure business logic, data models, and scheduling algorithms decoupled from Streamlit.
"""

from .constants import (
    UNLIMITED_GAP_MINS,
    FUDGE_FACTOR_MINS,
    DEFAULT_MOVIE_DURATION,
    DEFAULT_DRIVE_MINS,
    DEFAULT_DRIVE_MILES,
    PAST_SHOW_CUTOFF_MINS,
)
from .models import SchedulerConstraints, PathScore
from .engine import SchedulerEngine
from .text_utils import clean_movie_title, generate_regal_url, normalize_search_text
from .share_utils import (
    serialize_showing,
    deserialize_showing,
    encode_plan_token,
    decode_plan_token,
    format_single_day_summary,
    build_plan_share_url,
    build_search_share_url,
)

__all__ = [
    "UNLIMITED_GAP_MINS",
    "FUDGE_FACTOR_MINS",
    "DEFAULT_MOVIE_DURATION",
    "DEFAULT_DRIVE_MINS",
    "DEFAULT_DRIVE_MILES",
    "PAST_SHOW_CUTOFF_MINS",
    "SchedulerConstraints",
    "PathScore",
    "SchedulerEngine",
    "clean_movie_title",
    "generate_regal_url",
    "normalize_search_text",
    "serialize_showing",
    "deserialize_showing",
    "encode_plan_token",
    "decode_plan_token",
    "format_single_day_summary",
    "build_plan_share_url",
    "build_search_share_url",
]
