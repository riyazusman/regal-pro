"""Utilities for compressing, generating, and formatting shareable itinerary links and text summaries."""

import base64
import json
import urllib.parse
import zlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


def serialize_showing(showing: Dict[str, Any], theater_name_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Convert a showing dictionary to a compact serializable representation."""
    name_map = theater_name_map or {}
    tc = str(showing.get('TheaterCode', ''))
    t_name = name_map.get(tc, f"Regal {tc}")

    st_val = showing.get('Showtime')
    if isinstance(st_val, datetime):
        st_str = st_val.strftime('%Y-%m-%dT%H:%M:%S')
    else:
        st_str = str(st_val)

    return {
        'mc': showing.get('master_code', ''),
        't': showing.get('Title', ''),
        'tc': tc,
        'tn': t_name,
        'st': st_str,
        'dur': int(showing.get('Duration', 120)),
        'r': str(showing.get('Rating', 'NR')),
        'fmt': showing.get('ScreenType', '2D'),
        'a': str(showing.get('Auditorium', '?')),
    }


def deserialize_showing(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a compact showing dict back to internal showing structure."""
    st_str = raw.get('st', '')
    try:
        st_obj = datetime.strptime(st_str, '%Y-%m-%dT%H:%M:%S')
    except Exception:
        try:
            st_obj = datetime.fromisoformat(st_str)
        except Exception:
            st_obj = datetime.now()

    return {
        'master_code': raw.get('mc', ''),
        'Title': raw.get('t', ''),
        'TheaterCode': raw.get('tc', ''),
        'TheaterName': raw.get('tn', ''),
        'Showtime': st_obj,
        'Duration': int(raw.get('dur', 120)),
        'Rating': str(raw.get('r', 'NR')),
        'ScreenType': raw.get('fmt', '2D'),
        'Auditorium': str(raw.get('a', '?')),
    }


def encode_plan_token(plan_dict: Dict[str, Any]) -> str:
    """Compress an itinerary payload into a compact URL-safe base64 string."""
    raw_json = json.dumps(plan_dict, separators=(',', ':'))
    compressed = zlib.compress(raw_json.encode('utf-8'), level=9)
    return base64.urlsafe_b64encode(compressed).decode('ascii').rstrip('=')


def decode_plan_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Decompress a URL-safe base64 token back into an itinerary dictionary."""
    if not token or not isinstance(token, str):
        return None

    try:
        token_clean = token.strip()
        padding = 4 - (len(token_clean) % 4)
        if padding < 4:
            token_clean += '=' * padding

        compressed = base64.urlsafe_b64decode(token_clean.encode('ascii'))
        decompressed = zlib.decompress(compressed)
        data = json.loads(decompressed.decode('utf-8'))
        if isinstance(data, dict) and 'v' in data:
            return data
    except Exception:
        pass
    return None


def format_single_day_summary(
    path: List[Dict[str, Any]],
    label: str,
    date_display: str,
    theater_name_map: Optional[Dict[str, str]] = None,
) -> str:
    """Format a single day itinerary as a clean text summary for messaging."""
    name_map = theater_name_map or {}
    lines = [
        "🍿 *Regal Movie Marathon Plan*",
        f"📅 *{date_display}* — {label}",
        "",
    ]

    for idx, s in enumerate(path):
        tc = str(s.get('TheaterCode', ''))
        t_name = s.get('TheaterName') or name_map.get(tc, f"Regal {tc}")
        st_obj = s.get('Showtime')
        if isinstance(st_obj, datetime):
            start_fmt = st_obj.strftime('%I:%M %p').lstrip('0')
            dur = s.get('Duration', 120)
            end_fmt = (st_obj + timedelta(minutes=dur)).strftime('%I:%M %p').lstrip('0')
            time_txt = f"{start_fmt} - {end_fmt}"
        else:
            time_txt = str(st_obj)

        fmt = s.get('ScreenType', '2D')
        fmt_str = f" [{fmt}]" if fmt and fmt != "2D" else ""
        audi = f" (Audi {s.get('Auditorium')})" if s.get('Auditorium') and s.get('Auditorium') != '?' else ""

        lines.append(f"{idx + 1}. *{s.get('Title')}*{fmt_str}")
        lines.append(f"   🕒 {time_txt}{audi} @ {t_name}")

        if idx < len(path) - 1:
            nxt = path[idx + 1]
            nxt_st = nxt.get('Showtime')
            if isinstance(st_obj, datetime) and isinstance(nxt_st, datetime):
                end_curr = st_obj + timedelta(minutes=s.get('Duration', 120))
                gap = int((nxt_st - end_curr).total_seconds() / 60)
                hop = f" (Drive to {name_map.get(str(nxt.get('TheaterCode')), 'next theater')})" if nxt.get('TheaterCode') != tc else ""
                lines.append(f"   ⏳ {gap}m gap{hop}")

    return "\n".join(lines)


def format_multi_day_summary(
    multi_itinerary: Dict[str, List[Dict[str, Any]]],
    label: str,
    theater_name_map: Optional[Dict[str, str]] = None,
) -> str:
    """Format a multi-day itinerary into clean, human-readable text."""
    name_map = theater_name_map or {}
    lines = [f"🎬 *{label}*", ""]

    sorted_days = sorted(
        multi_itinerary.keys(),
        key=lambda x: datetime.strptime(x, '%m-%d-%Y') if '-' in x else x
    )
    for d_str in sorted_days:
        path = multi_itinerary[d_str]
        try:
            d_obj = datetime.strptime(d_str, '%m-%d-%Y')
            d_display = d_obj.strftime('%A, %b %d')
        except Exception:
            d_display = d_str
        lines.append(f"📅 *{d_display}* ({len(path)} Movies)")
        for idx, s in enumerate(path):
            tc = str(s.get('TheaterCode', ''))
            t_name = s.get('TheaterName') or name_map.get(tc, f"Regal {tc}")
            st_obj = s.get('Showtime')
            if isinstance(st_obj, datetime):
                start_fmt = st_obj.strftime('%I:%M %p').lstrip('0')
                dur = s.get('Duration', 120)
                end_fmt = (st_obj + timedelta(minutes=dur)).strftime('%I:%M %p').lstrip('0')
                time_txt = f"{start_fmt} - {end_fmt}"
            else:
                time_txt = str(st_obj)
            fmt = s.get('ScreenType', '2D')
            fmt_str = f" [{fmt}]" if fmt and fmt != "2D" else ""
            lines.append(f"  • {s.get('Title')}{fmt_str} ({time_txt} @ {t_name})")
        lines.append("")

    return "\n".join(lines).strip()


def build_plan_share_url(base_url: str, plan_token: str, theater_code: Optional[str] = None, date_str: Optional[str] = None) -> str:
    """Build a complete deep-link URL pointing to the shared plan."""
    params = {'tab': 'scheduler', 'plan': plan_token}
    if theater_code:
        params['theater'] = str(theater_code)
    if date_str:
        params['date'] = str(date_str)

    query = urllib.parse.urlencode(params)
    clean_base = base_url.split('?')[0].rstrip('/')
    return f"{clean_base}?{query}"


def build_search_share_url(
    base_url: str,
    theater_code: str,
    date_str: str,
    target_movies: List[str],
    target_theaters: Optional[List[str]] = None,
    target_days: Optional[List[str]] = None,
) -> str:
    """Build a complete deep-link URL reproducing a scheduler search setup."""
    params: Dict[str, str] = {
        'tab': 'scheduler',
        'theater': str(theater_code),
        'date': str(date_str),
    }
    if target_movies:
        params['targets'] = ','.join(target_movies)
    if target_theaters and len(target_theaters) > 1:
        params['sched_theaters'] = ','.join(target_theaters)
    if target_days and len(target_days) > 1:
        params['sched_days'] = ','.join(target_days)

    query = urllib.parse.urlencode(params)
    clean_base = base_url.split('?')[0].rstrip('/')
    return f"{clean_base}?{query}"
