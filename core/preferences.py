"""User preferences and client-side cookie management for Regal Pro."""

import base64
import copy
import json
from typing import Any, Dict, List, Optional, Set


COOKIE_NAME = "regal_pro_prefs"

DEFAULT_PREFERENCES: Dict[str, Any] = {
    "home_theater": None,          # str or None (e.g. "1487")
    "favorite_theaters": [],       # List[str] of theater codes
    "watched_movies": [],          # List[str] of master movie codes
    "filters": {
        "hide_past": True,         # bool
        "new_only": False,         # bool
        "hide_watched": False,     # bool
        "only_here": False,        # bool
    },
    "scheduler": {
        "buffer": 15,              # int (0 to 60)
        "gap_cap": 120,            # int (0 to 240)
        "unlimited": True,         # bool
        "exclude_watched": False,  # bool
    }
}


def get_default_preferences() -> Dict[str, Any]:
    """Return a deep copy of the default preferences dictionary."""
    return copy.deepcopy(DEFAULT_PREFERENCES)


def encode_preferences(prefs: Dict[str, Any]) -> str:
    """Encode preferences dictionary to a URL-safe Base64 string for safe cookie storage."""
    clean_prefs = sanitize_preferences(prefs)
    json_bytes = json.dumps(clean_prefs, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(json_bytes).decode("ascii")


def decode_preferences(raw_val: Optional[str]) -> Dict[str, Any]:
    """Decode a Base64-encoded preferences string, validating and merging with defaults."""
    if not raw_val or not isinstance(raw_val, str):
        return get_default_preferences()
    
    try:
        # Support both urlsafe base64 and standard base64 or raw JSON fallback
        padded = raw_val + "=" * (-len(raw_val) % 4)
        try:
            decoded_json = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        except Exception:
            decoded_json = raw_val
        
        parsed = json.loads(decoded_json)
        if isinstance(parsed, dict):
            return sanitize_preferences(parsed)
    except Exception:
        pass
    
    return get_default_preferences()


def sanitize_preferences(prefs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sanitize and merge user preferences against the schema with strict types."""
    base = get_default_preferences()
    if not isinstance(prefs, dict):
        return base
    
    # Home theater (str or None)
    ht = prefs.get("home_theater")
    if ht is not None and isinstance(ht, (str, int)):
        base["home_theater"] = str(ht).strip()
    else:
        base["home_theater"] = None

    # Favorite theaters (list of strings)
    favs = prefs.get("favorite_theaters")
    if isinstance(favs, (list, set, tuple)):
        clean_favs = []
        for code in favs:
            s_code = str(code).strip()
            if s_code and s_code not in clean_favs:
                clean_favs.append(s_code)
        base["favorite_theaters"] = clean_favs

    # Watched movies (list of strings)
    watched = prefs.get("watched_movies")
    if isinstance(watched, (list, set, tuple)):
        clean_watched = []
        for code in watched:
            s_code = str(code).strip()
            if s_code and s_code not in clean_watched:
                clean_watched.append(s_code)
        base["watched_movies"] = clean_watched

    # Filters
    filters_in = prefs.get("filters", {})
    if isinstance(filters_in, dict):
        if "hide_past" in filters_in:
            base["filters"]["hide_past"] = bool(filters_in["hide_past"])
        if "new_only" in filters_in:
            base["filters"]["new_only"] = bool(filters_in["new_only"])
        if "hide_watched" in filters_in:
            base["filters"]["hide_watched"] = bool(filters_in["hide_watched"])
        if "only_here" in filters_in:
            base["filters"]["only_here"] = bool(filters_in["only_here"])

    # Scheduler defaults
    sched_in = prefs.get("scheduler", {})
    if isinstance(sched_in, dict):
        if "buffer" in sched_in:
            try:
                base["scheduler"]["buffer"] = max(0, min(60, int(sched_in["buffer"])))
            except (ValueError, TypeError):
                pass
        if "gap_cap" in sched_in:
            try:
                base["scheduler"]["gap_cap"] = max(0, min(240, int(sched_in["gap_cap"])))
            except (ValueError, TypeError):
                pass
        if "unlimited" in sched_in:
            base["scheduler"]["unlimited"] = bool(sched_in["unlimited"])
        if "exclude_watched" in sched_in:
            base["scheduler"]["exclude_watched"] = bool(sched_in["exclude_watched"])

    return base


def set_home_theater(prefs: Dict[str, Any], theater_code: Optional[str]) -> Dict[str, Any]:
    """Set or unset the user's home theater."""
    updated = copy.deepcopy(prefs)
    updated["home_theater"] = str(theater_code).strip() if theater_code else None
    return sanitize_preferences(updated)


def toggle_favorite_theater(prefs: Dict[str, Any], theater_code: str) -> Dict[str, Any]:
    """Toggle a theater in the user's favorite theaters list."""
    updated = copy.deepcopy(prefs)
    t_str = str(theater_code).strip()
    favs = set(updated.get("favorite_theaters", []))
    if t_str in favs:
        favs.remove(t_str)
    else:
        favs.add(t_str)
    updated["favorite_theaters"] = sorted(list(favs))
    return sanitize_preferences(updated)


def toggle_watched_movie(prefs: Dict[str, Any], movie_code: str) -> Dict[str, Any]:
    """Toggle a movie in the user's watched movies list."""
    updated = copy.deepcopy(prefs)
    m_str = str(movie_code).strip()
    watched = set(updated.get("watched_movies", []))
    if m_str in watched:
        watched.remove(m_str)
    else:
        watched.add(m_str)
    updated["watched_movies"] = sorted(list(watched))
    return sanitize_preferences(updated)


def is_movie_watched(prefs: Dict[str, Any], movie_code: str) -> bool:
    """Check if a movie is marked as watched in user preferences."""
    if not movie_code:
        return False
    return str(movie_code).strip() in prefs.get("watched_movies", [])


def update_filter_defaults(
    prefs: Dict[str, Any],
    hide_past: Optional[bool] = None,
    new_only: Optional[bool] = None,
    hide_watched: Optional[bool] = None,
    only_here: Optional[bool] = None
) -> Dict[str, Any]:
    """Update default filter values."""
    updated = copy.deepcopy(prefs)
    if hide_past is not None:
        updated.setdefault("filters", {})["hide_past"] = bool(hide_past)
    if new_only is not None:
        updated.setdefault("filters", {})["new_only"] = bool(new_only)
    if hide_watched is not None:
        updated.setdefault("filters", {})["hide_watched"] = bool(hide_watched)
    if only_here is not None:
        updated.setdefault("filters", {})["only_here"] = bool(only_here)
    return sanitize_preferences(updated)


def update_scheduler_defaults(
    prefs: Dict[str, Any],
    buffer: Optional[int] = None,
    gap_cap: Optional[int] = None,
    unlimited: Optional[bool] = None,
    exclude_watched: Optional[bool] = None
) -> Dict[str, Any]:
    """Update default scheduler values."""
    updated = copy.deepcopy(prefs)
    s = updated.setdefault("scheduler", {})
    if buffer is not None:
        s["buffer"] = buffer
    if gap_cap is not None:
        s["gap_cap"] = gap_cap
    if unlimited is not None:
        s["unlimited"] = unlimited
    if exclude_watched is not None:
        s["exclude_watched"] = exclude_watched
    return sanitize_preferences(updated)
