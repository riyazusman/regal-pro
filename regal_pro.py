# v2.7 RC 
import streamlit as st
import json
import math
import pgeocode
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import time
import os
import sys
import re
import gzip
import io
import gc
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, time as dt_time
from curl_cffi import requests as c_requests
from streamlit_js_eval import get_geolocation, set_cookie, get_cookie
from supabase import create_client, Client
from core.engine import SchedulerEngine
from core.models import SchedulerConstraints
from core.text_utils import clean_movie_title, generate_regal_url
from core.date_utils import parse_and_sort_dates
from core.share_utils import (
    serialize_showing,
    deserialize_showing,
    encode_plan_token,
    decode_plan_token,
    format_single_day_summary,
    format_multi_day_summary,
    build_plan_share_url,
    build_search_share_url,
)

IS_CLOUD = "STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION" in os.environ
#IS_CLOUD = True # For local Proxy Testing

debug_mode = st.query_params.get("debug") if st.query_params.get("debug") else False

# --- Resource Path Resolution for Desktop Executable ---
def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- Page Configuration ---
st.set_page_config(page_title="Regal Pro", layout="wide", page_icon="🎬")

# --- CSS for Navigation ---
st.markdown("""
    <style>
    .stRadio > div[role="radiogroup"] {
            display: flex;
            flex-direction: row;
            flex-wrap: wrap !important;
            gap: 0.75rem !important; 
            background-color: rgba(151, 166, 195, 0.15);
            padding: 8px 16px; border-radius: 10px; 
            margin-bottom: 16px; border: 1px solid rgba(151, 166, 195, 0.2);
    }
    .stRadio [data-testid="stMarkdownContainer"] p { 
            font-size: 1rem; font-weight: 600; margin: 0;
    }
    @media (max-width: 640px) {
        .stRadio > div[role="radiogroup"] {
            gap: 0.4rem !important;
            padding: 6px 10px;
        }
        .stRadio [data-testid="stMarkdownContainer"] p {
            font-size: 0.88rem !important;
        }
    }

    .movie-header-card {
        display: flex;
        flex-direction: row;
        gap: 14px;
        align-items: flex-start;
        margin-bottom: 10px;
    }
    .movie-poster-box {
        width: 65px;
        min-width: 65px;
        height: 98px;
        border-radius: 6px;
        overflow: hidden;
        flex-shrink: 0;
        box-shadow: 0 2px 6px rgba(0,0,0,0.25);
    }
    .movie-poster-box img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
    }
    @media (min-width: 768px) {
        .movie-header-card {
            gap: 20px;
        }
        .movie-poster-box {
            width: 135px;
            min-width: 135px;
            height: 202px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.25);
        }
    }
    </style>
""", unsafe_allow_html=True)

# --- Constants & Headers ---
THEATERS_FILE = get_resource_path("theater_list.json")
SPECIALS_FILE = get_resource_path("monthly_specials.json")
proxy_zip_code = None

AJAX_HEADERS = {
    "Host": "www.regmovies.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.regmovies.com/"
}

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "AS": "American Samoa", "DC":"Washington D.C.", "GU":"Guam"
}

# --- Deep-Linking & Parameter Navigation Mappings ---
TAB_MAP = {
    "theater": "🔎 Theater Explorer",
    "movies": "🎬 Movie Explorer",
    "scheduler": "🗓️ Smart Scheduler"
}
TAB_REVERSE_MAP = {v: k for k, v in TAB_MAP.items()}

THEATER_VIEW_MAP = {
    "now-playing": "🍿 Now Playing",
    "nearby": "🚗 Playing Nearby",
    "upcoming": "📅 Upcoming"
}
THEATER_VIEW_REV_MAP = {v: k for k, v in THEATER_VIEW_MAP.items()}

MOVIE_VIEW_MAP = {
    "nearby": "🎥 Playing Nearby",
    "nationwide": "🌍 Playing Nationwide"
}
MOVIE_VIEW_REV_MAP = {v: k for k, v in MOVIE_VIEW_MAP.items()}

VIEW_MAP = {**THEATER_VIEW_MAP, **MOVIE_VIEW_MAP}
VIEW_REVERSE_MAP = {**THEATER_VIEW_REV_MAP, **MOVIE_VIEW_REV_MAP}

def sync_query_param(key, value):
    """Safely synchronizes a query parameter without redundant mutations."""
    if value is None or value == "":
        if key in st.query_params:
            del st.query_params[key]
    else:
        str_val = str(value)
        if st.query_params.get(key) != str_val:
            st.query_params[key] = str_val

# --- Utility Functions ---

def init_registry():
    if "registry" not in st.session_state:
        st.session_state.registry = {
            "theaters": {},
            "schedule": {},
            "movies": {}, 
            "loaded_dates": set(),
            "active_ckey": None,
            "attr_map": {}
        }
    st.session_state.global_movie_catalog = st.session_state.registry['movies']

def update_schedule_session_state(all_flat_data, flat_data, movie_meta, attr_map, future_movies, drive_map):
    st.session_state.update({
        "all_flat_data": all_flat_data,
        "flat_data": flat_data,
        "movie_meta": movie_meta,
        "attr_map": attr_map,
        "future_movies": future_movies,
        "drive_map": drive_map
    })

@st.cache_resource
def init_supabase() -> Client:
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])

@st.cache_data(ttl=3600)
def get_db_metadata_cached(codes):
    if not codes: return {}
    res = db.table("movie_metadata").select("*").in_("master_code", list(codes)).execute()
    return {row['master_code']: row for row in res.data} if res.data else {}


def get_db_metadata(codes):
    if not codes:
        return {}
    codes_tuple = tuple(sorted(codes))
    try:
        return get_db_metadata_cached(codes_tuple)
    except Exception as e:
            if debug_mode: st.error(f"Metadata Fetch Error: {e}")
            return {}

def registry_ingest(raw_payload):
    all_mc = set()
    gaps=set()  
    for d_str, day_data in raw_payload.items():
        for show in day_data.get('shows', []):
            for film in show.get('Film', []):
                if film.get('MasterMovieCode'): all_mc.add(film['MasterMovieCode'])
        all_mc.update(fs.get('hoCode') for fs in day_data.get('futureShows', []) if fs.get('hoCode'))
    
    missing_mc = [mc for mc in all_mc if mc not in st.session_state.registry['movies']]
    if missing_mc:
        db_meta = get_db_metadata(tuple(sorted(missing_mc)))
        translated_meta = {}
        placeholder_updates = {}
        
        for mc in missing_mc:
            if not mc in db_meta:
                gaps.add(mc)
                placeholder_updates[mc] = {
                    'title': f"New Release ({mc})",
                    'rating': "NR",
                    'duration': 120,
                    'studio': 'Unknown Studio',
                    'is_new': True
                }
        st.session_state.registry['movies'].update(placeholder_updates)
        
        for mc, m_info in db_meta.items():
            translated_meta[mc] = {
                **m_info,
                'rt_critics': m_info.get('rt_critics_score'),
                'rt_users': m_info.get('rt_users_score'),
                'metacritic': m_info.get('metacritic_score'),
                'letterboxd': m_info.get('letterboxd_score')
            }

            od = m_info.get('opening_date')
            if od:
                try:
                    open_date = datetime.strptime(od[:10], "%Y-%m-%d").date()
                    translated_meta[mc]['is_new'] = (
                        datetime.now().date() - timedelta(days=7) <= 
                        open_date <= 
                        datetime.now().date() + timedelta(days=14)
                    )
                except:
                    translated_meta[mc]['is_new'] = False
            else:
                translated_meta[mc]['is_new'] = False
        st.session_state.registry['movies'].update(translated_meta)
    for d_str, day_data in raw_payload.items():
        flat_day, am, _, future_map = flatten_data(day_data)
        st.session_state.registry['schedule'][d_str] = flat_day
        st.session_state.registry['loaded_dates'].add(d_str)
        st.session_state.registry['attr_map'].update(am)
        
        for tc_key, f_list in future_map.items():
            if tc_key not in st.session_state.registry['theaters']:
                st.session_state.registry['theaters'][tc_key] = {'future': []}
            
            existing_mc = {m['master_code'] for m in st.session_state.registry['theaters'][tc_key]['future']}
            for f_movie in f_list:
                if f_movie['master_code'] not in existing_mc:
                    st.session_state.registry['theaters'][tc_key]['future'].append(f_movie)

    del raw_payload
    gc.collect()
    log_visit(t_item, metadata_gaps=gaps)

def load_cluster_from_storage(cluster_id, path):
    try:
        response = db.storage.from_(cluster_id).download(path)
        if response:
            with gzip.GzipFile(fileobj=io.BytesIO(response), mode='rb') as f:
                return json.loads(f.read().decode('utf-8'))
    except Exception as e:
        return None

def save_future_to_storage(bucket, file_path, payload):
    try:
        if 'movies' in payload: del payload['movies'] # Prune heavy metadata
        json_bytes = json.dumps(payload).encode('utf-8')
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode='wb') as f:
            f.write(json_bytes)
        
        db.storage.from_(bucket).upload(
            path=file_path, file=buf.getvalue(),
            file_options={"content-type": "application/gzip", "upsert": "true"}
        )
        return True
    except Exception as e: return False

@st.cache_data(ttl=3600)
def load_app_config():
    try:
        with open('app_config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        st.error("Missing app_config.json. Please run the sync_app_config routine.")
        return None

def sync_theater_selection():
    if "temp_theater_select" in st.session_state:
        selected_name = st.session_state.temp_theater_select
        if 'opts' in globals() and selected_name in opts:
            new_code = opts[selected_name]['theater_code']
            
            if new_code != st.session_state.active_theater_code:
                st.session_state.active_theater_code = new_code
                st.query_params["theater"] = new_code

def load_monthly_specials():
    try:
        if os.path.exists(SPECIALS_FILE):
            with open(SPECIALS_FILE, "r") as f:
                return json.load(f).get("series_list", [])
        return []
    except Exception: return []

@st.cache_data(ttl=3600)
def get_nationwide_active_movies_cached():
    res = db.rpc("get_nationwide_active_movies_rpc").execute()
    if res.data:
        return {row['master_code']: row['theater_count'] for row in res.data}
    return {}

def get_nationwide_active_movies(status_context=None):
    try:
        return get_nationwide_active_movies_cached()
    except Exception as e:
        if status_context:
            status_context.write(f"⚠️ RPC Error (nationwide_movies): {e}")
        return {}

@st.cache_data(ttl=3600)
def get_theaters_playing_movie_cached(m_code, local_codes):
    res = db.rpc("get_theaters_by_movie", {"target_m_code": m_code}).execute()
    if not res.data: return set()
    all_found = {row['t_code'] for row in res.data}
    return all_found - set(local_codes)

def get_theaters_playing_movie(m_code, local_codes, status_context=None):
    try:
        return get_theaters_playing_movie_cached(m_code, local_codes)
    except Exception as e:
        if status_context:
            status_context.write(f"⚠️ RPC Error (theaters_by_movie): {e}")
        return set()

def update_theater_from_input():
    code_in = st.session_state.theater_code_input
    if code_in:
        match = next((t for t in st.session_state.app_config['theaters'] 
                     if str(t['theater_code']) == str(code_in)), None)
        if match:
            st.session_state.active_theater_code = match['theater_code']
            st.query_params["theater"] = match['theater_code']

def is_dst(dt):
    year = dt.year
    dst_start = datetime(year, 3, 8) + timedelta(days=(6 - datetime(year, 3, 8).weekday()))
    dst_end = datetime(year, 11, 1) + timedelta(days=(6 - datetime(year, 11, 1).weekday()))
    return dst_start <= dt.replace(tzinfo=None) < dst_end

def get_location_cookie():
    with st.container(height=1, border=False):
        st.html("<style>div[height='1']{display:none;}</style>")
        location_cookie = get_cookie('RegalProUserGeoLocation')
        latitude = get_cookie('RegalProUserGeoLatitude')
        longitude = get_cookie('RegalProUserGeoLongitude')
        zip_code = get_cookie('RegalProUserZipCode')

    if location_cookie and latitude is not None and longitude is not None:
        try:
            with st.container(height=1, border=False):
                st.html("<style>div[height='1']{display:none;}</style>")
                set_cookie('RegalProUserGeoLocation', True, 400)
                set_cookie('RegalProUserGeoLatitude', latitude, 400)
                set_cookie('RegalProUserGeoLongitude', longitude, 400)
                set_cookie('RegalProUserZipCode', zip_code, 400)
            
            return location_cookie, float(latitude), float(longitude), zip_code
        except (ValueError, TypeError):
            return None, None, None, None
    else:
        location = get_geolocation()
        if location and location.get('coords'):
            lat = location['coords']['latitude']
            lon = location['coords']['longitude']
            z_code = get_zip_code_from_lat_lon(lat, lon)
            
            with st.container(height=1, border=False):
                st.html("<style>div[height='1']{display:none;}</style>")
                set_cookie('RegalProUserGeoLocation', True, 400)
                set_cookie('RegalProUserGeoLatitude', lat, 400)
                set_cookie('RegalProUserGeoLongitude', lon, 400)
                set_cookie('RegalProUserZipCode', z_code, 400)
            return True, float(lat), float(lon), z_code
        else:
            return None, None, None, None

def get_zip_code_from_lat_lon(latitude, longitude):
    geolocator = Nominatim(user_agent="regal_pro_v1.4")
    geocode = RateLimiter(geolocator.reverse, min_delay_seconds=1)
    coordinates = (latitude, longitude)

    location = geocode(coordinates)

    if location and 'address' in location.raw and 'postcode' in location.raw['address']:
        return location.raw['address']['postcode']
    else:
        return None

def get_offset_from_lon(lon, state=None, target_date=None):
    if state in ['OH', 'WV', 'VA', 'NC', 'SC', 'GA', 'PA', 'NY', 'NJ', 'MD', 'DE', 'CT', 'RI', 'MA', 'VT', 'NH', 'ME', 'IN']: 
        base_offset = -5
    elif state in ['IL', 'WI', 'AL', 'MS', 'LA', 'AR', 'MO', 'IA', 'MN', 'OK']: 
        base_offset = -6
    elif state in ['CO', 'ID', 'MT', 'NM', 'UT', 'WY', 'AZ']: 
        base_offset = -7
    elif state in ['CA', 'NV', 'OR', 'WA']: 
        base_offset = -8
    elif state == 'AK':
        base_offset = -9
    elif state == 'HI':
        base_offset = -10
    elif state in ['FL','KY','TN','MI']:
        base_offset = -5 if lon > -86 else -6
    elif state in ['KS','NE','ND','SD']:
        base_offset = -6 if lon > -101 else -7
    elif state == 'TX':
        base_offset = -6 if lon > -105 else -7
    else: 
        base_offset = -5
    
    if state not in ['HI', 'AZ'] and target_date:
        if is_dst(target_date):
            base_offset += 1
            
    return base_offset

def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    R = 3958.8 
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlam = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def fetch_data(api_url, path_name, status_context, msg, max_retries=3):
    tier_label = "Local"
    if IS_CLOUD:
        try:
            scraping_key = st.secrets.get("decodo", {}).get("api_key")
        except KeyError:
            st.error("Proxy secrets not configured!")
            return None
        
        log_msg = "🚀 Fetching data..."
        msg.toast(log_msg)
        if status_context: status_context.write(log_msg)
        scraping_payload = {"url": api_url, "geo": "United States", "device_type": "desktop_chrome"}
        headers = {"accept": "application/json","content-type": "application/json","authorization": f"Basic {scraping_key}"}
        
        if status_context:
            with status_context:
                st.write("🛠️ Outgoing Request Log")
                st.json({
                    "API_URL": api_url,
                    "Headers": headers,
                    "Tier": tier_label,
                })
        try:
            s_resp = c_requests.post("https://scraper-api.decodo.com/v2/scrape", json=scraping_payload, headers=headers, timeout=45)
            if s_resp.status_code == 200:
                return json.loads(s_resp.json()['results'][0]['content'])
            else:
                if status_context:
                    with status_context:
                        st.write("🛠️ Response")
                        st.write(s_resp)
        except Exception as e:
            if debug_mode:
                st.error(f"Scraping API Error: {str(e)}")
            else:
                st.error("Regal is blocking request. Try again after sometime.")
    else:
        for attempt in range(max_retries):
            if "api_session" not in st.session_state:
                st.session_state.api_session = c_requests.Session()
            
            api_headers = AJAX_HEADERS.copy()
            api_headers["Referer"] = f"https://www.regmovies.com/theaters/{path_name}"
            theater_url = f"https://www.regmovies.com/theaters/{path_name}"

            if status_context:
                with status_context:
                    st.write("🛠️ Outgoing Request Log")
                    st.json({
                        "API_URL": api_url,
                        "Tier": tier_label,
                        "Attempt":attempt+1
                    })
        
            try:
                st.session_state.api_session.get(theater_url, impersonate="chrome124", timeout=15)
                response = st.session_state.api_session.get(
                    api_url, 
                    headers=api_headers, 
                    impersonate="chrome124",
                    timeout=30
                )
                if response.status_code == 200:
                    return response.json()
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait_time = int(retry_after) if retry_after and retry_after.isdigit() else 10
                    
                    log_msg = f"⏳ Rate Limited (429). Sleeping for {wait_time}s..."
                    msg.toast(log_msg)
                    if status_context: status_context.write(log_msg)
                    time.sleep(wait_time)
                    
                    st.session_state.proxy_session_id = os.urandom(4).hex()
                    continue
                if response.status_code == 403:
                    if status_context:
                        with status_context:
                            st.write("🛠️ Response")
                            st.write(response)
                    del st.session_state.api_session

                    log_msg = f"⚠️ {tier_label} Blocked. Regal 403 detected. Rotating..."
                    msg.toast(log_msg)
                    if status_context: status_context.write(log_msg)
                    time.sleep(2)
                response.raise_for_status()
            except Exception as e:
                if status_context:
                    with status_context:
                        st.error(f"Proxy Connection Error: {str(e)}")
                continue
    return None

def flatten_data(data, theater_code_override=None):
    if not isinstance(data, dict):
        return [], {}, st.session_state.registry['movies'], {}
    
    flat_list = []
    catalog = st.session_state.registry['movies']

    for m in data.get('movies', []):
        m_code = m.get('MasterMovieCode')
        if m_code:
            catalog[m_code] = catalog.get(m_code, {'rating': 'NR', 'duration': 0})
            catalog[m_code].update({
                'title': m.get('Title', 'Unknown'),
                'is_new': (lambda d: d and datetime.now().date() - timedelta(days=7) <= datetime.strptime(d[:10], "%Y-%m-%d").date() <= datetime.now().date() + timedelta(days=14))(m.get('OpeningDate')),
                'rating': m.get('Rating', catalog[m_code]['rating']),
                'duration': int(m.get('Duration', catalog[m_code]['duration']))
            })
    
    attr_map = {a.get('Acronym', '').strip(): a.get('ShortName', '').strip() for a in data.get('attributes', []) if a.get('Acronym')}

    for ts in data.get("shows", []):
        t_code = ts.get("TheatreCode")
        for film in ts.get("Film", []):
            m_code = film.get('MasterMovieCode')
            if not m_code: continue
            m_meta = catalog.get(m_code, {})
            for perf in film.get("Performances", []):
                flat_list.append({
                    "TheaterCode": t_code, 
                    "master_code": m_code,
                    "Title": m_meta.get('title', f"Unknown Movie ({m_code})"),
                    "Rating": m_meta.get('rating', "NR"),
                    "Duration": m_meta.get('duration', 120),
                    "Showtime": datetime.strptime(perf["CalendarShowTime"], "%Y-%m-%dT%H:%M:%S"),
                    "Auditorium": str(perf.get("Auditorium", "?")),
                    "ScreenType": perf.get("PerformanceGroup") or "2D",
                    "raw_attrs": set(attr_map.get(c.strip(), c) for c in perf.get("PerformanceAttributes", []))
                })
                
    theater_future_map = {}
    primary_t = theater_code_override or (data.get('shows')[0].get('theaterCode') if data.get('shows') else None)
    if primary_t:
        for fs in data.get("futureShows", []):
            m_code = fs.get('hoCode')
            if not m_code: continue
            date_objs = []
            for d in fs.get('dates', []):
                date_val = d.get('date') or d.get('calendarShowDate') # Fallback for different API versions
                if date_val:
                    try:
                        date_objs.append(datetime.strptime(date_val, "%m-%d-%Y"))
                    except: continue
            if date_objs:
                sorted_date_objs = sorted(list(set(date_objs)))
                formatted_dates = [d.strftime("%b %d") for d in sorted_date_objs]
                if primary_t not in theater_future_map: theater_future_map[primary_t] = []
                theater_future_map[primary_t].append({
                    'master_code': m_code, 'title': fs.get('title'),
                    'scheduled_dates': formatted_dates,
                    'first_date': sorted_date_objs[0].strftime("%Y-%m-%d"),
                    'rating': 'NR', 'duration': 0
                })

    return flat_list, attr_map, catalog, theater_future_map

def check_metadata_gaps(flat_list):
    gaps = {}
    for s in flat_list:
        if s['Duration'] == 0:
            if s['master_code'] not in gaps:
                gaps[s['master_code']] = s['TheaterCode']
    return gaps

def is_unlimited_excluded(m_code, attr_set, catalog=None):
    if catalog is None:
        catalog = st.session_state.get('global_movie_catalog', {})
    meta = catalog.get(m_code, {})
    return meta.get('studio') in ("Regal Cinemedia","Trafalgar Releasing, Ltd","Edward Gurrola III") or "No Pass/SS" in attr_set or "No Passes or Super Savers" in attr_set

def get_rating_badges(m_code, as_html=True):
    meta = st.session_state.get('global_movie_catalog', {}).get(m_code, {}) or st.session_state.get('registry', {}).get('movies', {}).get(m_code, {})
    badges = []
    badges_type = []
    ratings_map = [
        ('imdb_score', '⭐', 'imdb_url', 'IMDB'),
        ('rt_critics', '🍅', 'rt_url', 'Tomatometer'),
        ('rt_users', '🍿', 'rt_url', 'Popcornmeter'),
        ('metacritic', 'Ⓜ️', 'metacritic_url', 'Metacritic'),
        ('letterboxd', '🟢', 'letterboxd_url', 'Letterboxd')
    ]
    
    for score_key, icon, url_key, badge_type in ratings_map:
        score = meta.get(score_key)
        url = meta.get(url_key)
        if score:
            display_score = round(float(score), 1) if score_key == 'letterboxd' else score
            if url:
                if as_html:
                    badges.append(f'{icon} <a href="{url}" target="_blank" style="text-decoration: underline; color: inherit; font-weight: 600;">{display_score}</a>')
                else:
                    badges.append(f"{icon} [{display_score}]({url})")
            else:
                badges.append(f"{icon} {display_score}")
            badges_type.append(f"{icon} {badge_type}")
        else:
            if url:
                if as_html:
                    badges.append(f'{icon} <a href="{url}" target="_blank" style="text-decoration: underline; color: inherit; font-weight: 600;">N/A</a>')
                else:
                    badges.append(f"{icon} [N/A]({url})")
                badges_type.append(f"{icon} {badge_type}")
    return " | ".join(badges), " ".join(badges_type) if badges else ""

_POSTER_CACHE = {}

def is_poster_valid(url):
    """Check if poster URL exists and is not a 404/expired CDN blob (cached)."""
    if not url or not str(url).strip().startswith("http"):
        return False
    u = str(url).strip()
    if u in _POSTER_CACHE:
        return _POSTER_CACHE[u]
    try:
        req = urllib.request.Request(u, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=1.2) as r:
            valid = (r.status == 200)
    except Exception:
        valid = False
    _POSTER_CACHE[u] = valid
    return valid

def prefetch_poster_status(urls):
    """Pre-check a batch of poster URLs in parallel to avoid render stalls."""
    uncached = [str(u).strip() for u in urls if u and str(u).strip().startswith("http") and str(u).strip() not in _POSTER_CACHE]
    if not uncached:
        return
    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(is_poster_valid, uncached))

def get_movie_regal_url(m_code, title=None):
    """Retrieve canonical or computed Regal movie page URL."""
    meta = st.session_state.global_movie_catalog.get(m_code, {})
    regal_url = meta.get('regal_url')
    if regal_url and str(regal_url).strip().startswith("http"):
        return regal_url.strip()
    t = title or meta.get('title', '')
    if t and m_code:
        return generate_regal_url(clean_movie_title(t), m_code)
    return ""

def format_badges_and_regal_link(badges, regal_url, link_text="Regal Page ↗"):
    """Combine score badges and Regal link without leading/trailing pipe symbols."""
    regal_html = f'<a href="{regal_url}" target="_blank" style="text-decoration:none; color:#e67e22; font-weight:600;">🎟️ {link_text}</a>' if regal_url else ""
    clean_badges = (badges or "").strip()
    if clean_badges and regal_html:
        return f"{clean_badges} | {regal_html}"
    elif clean_badges:
        return clean_badges
    elif regal_html:
        return regal_html
    return ""

def render_movie_header_card(m_code, title=None, extra_tags=""):
    """Render a uniform, responsive movie card header with desktop/mobile adaptive poster, scores, and links."""
    meta = st.session_state.get('global_movie_catalog', {}).get(m_code, {}) or st.session_state.get('registry', {}).get('movies', {}).get(m_code, {})
    disp_title = title or meta.get('title', 'Unknown Title')
    duration = meta.get('duration', 0)
    rating = meta.get('rating', 'NR')
    studio = meta.get('studio', 'Unknown Studio')
    poster_url = meta.get('poster')
    has_poster = is_poster_valid(poster_url)
    
    regal_url = get_movie_regal_url(m_code, disp_title)
    badges, badges_type = get_rating_badges(m_code)
    badges_line = format_badges_and_regal_link(badges, regal_url, "Regal Page ↗")
    tooltip_attr = f' title="{badges_type}"' if badges_type else ""
    badges_html = f"<small{tooltip_attr}>{badges_line}</small>" if badges_line else ""
    
    clean_extra = f" {extra_tags}".strip()
    extra_str = f" {clean_extra}" if clean_extra else ""
    dur_str = f" | {duration} min" if duration else ""
    meta_line = f"<small style='color:grey;'>({rating}{dur_str}{extra_str}) • 🎥 {studio}</small>"
    
    img_html = f'<div class="movie-poster-box"><img src="{poster_url}" alt="" /></div>' if has_poster else ""
    
    card_html = (
        f'<div class="movie-header-card">'
        f'{img_html}'
        f'<div style="flex: 1; min-width: 0;">'
        f'<div style="font-size: 1.25rem; font-weight: 700; line-height: 1.2; margin-bottom: 2px;">{disp_title}</div>'
        f'<div style="margin-bottom: 4px;">{meta_line}</div>'
        f'<div>{badges_html}</div>'
        f'</div>'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

def format_scheduled_date_links(date_objs, theater_code, tab="theater", sub_view=None, movie_code=None):
    """Generate HTML hyperlinked date badges for quick date-switching."""
    links = []
    view_param = f"&tview={sub_view}" if tab == "theater" and sub_view else (f"&mview={sub_view}" if tab == "movies" and sub_view else "")
    movie_param = f"&movie={movie_code}" if movie_code else ""
    
    for dt in date_objs:
        d_val = dt if isinstance(dt, datetime) else datetime.strptime(str(dt), "%m-%d-%Y" if "-" in str(dt) else "%Y-%m-%d")
        d_str = d_val.strftime("%m-%d-%Y")
        d_display = d_val.strftime("%b %d")
        url = f"?theater={theater_code}&date={d_str}&tab={tab}{view_param}{movie_param}"
        links.append(f'<a href="{url}" target="_self" style="color: #e67e22; text-decoration: underline; font-weight: 500;">{d_display}</a>')
    return ", ".join(links)

def format_neighbor_theater_links(theater_codes, date_str, cluster_theaters, tab="theater", sub_view="now-playing", movie_code=None):
    """Generate HTML hyperlinked theater names for quick theater-switching."""
    links = []
    view_param = f"&tview={sub_view}" if tab == "theater" and sub_view else (f"&mview={sub_view}" if tab == "movies" and sub_view else "")
    movie_param = f"&movie={movie_code}" if movie_code else ""
    
    for tc in theater_codes:
        t_name = cluster_theaters.get(tc, f"Theater {tc}")
        url = f"?theater={tc}&date={date_str}&tab={tab}{view_param}{movie_param}"
        links.append(f'<a href="{url}" target="_self" style="color: #3498db; text-decoration: underline; font-weight: 500;">{t_name}</a>')
    if len(theater_codes) <= 8:
        return ", ".join(links)
    else:
        return f"{', '.join(links[:8])}, and {len(theater_codes) - 8} more"


def get_movie_group_info(m_code, catalog=None, screenings=None):
    """Retrieve group categorization prefix for a movie.
    
    Returns:
        tuple (priority_rank: int, prefix_icon: str)
    """
    if catalog is None:
        catalog = st.session_state.get('global_movie_catalog', {})
    if screenings is None:
        screenings = st.session_state.get('all_flat_data', [])
        
    meta = catalog.get(m_code, {})
    
    m_attrs = set()
    for s in screenings:
        if s.get('master_code') == m_code:
            m_attrs.update(s.get('raw_attrs', []))
    
    is_ineligible = is_unlimited_excluded(m_code, m_attrs, catalog=catalog)
    is_new = meta.get('is_new', False)
    
    if is_ineligible:
        return 2, "🎫 "
    elif is_new:
        return 0, "🔴 "
    return 1, "🎬 "
                
def format_movie_label_grouped(m_code, catalog=None, screenings=None):
    if catalog is None:
        catalog = st.session_state.get('global_movie_catalog', {})
    meta = catalog.get(m_code) or st.session_state.get('registry', {}).get('movies', {}).get(m_code, {})
    title = meta.get('title', f"Unknown ({m_code})")
    _, prefix = get_movie_group_info(m_code, catalog=catalog, screenings=screenings)
    return f"{prefix} {title}"

def get_attr_diff(s_attrs_set, common_attrs_set):
    diff = s_attrs_set - common_attrs_set
    if not diff: return ""
    return ", ".join(sorted(diff))

def get_time_options():

    times = []
    start = datetime.strptime("00:00", "%H:%M")
    for _ in range(288): 
        times.append(start.strftime("%H:%M")); start += timedelta(minutes=5)
    return times

def generate_ics(path, name_map=None):
    if name_map is None:
        theaters_list = st.session_state.app_config.get('theaters', []) if 'app_config' in st.session_state else []
        name_map = {t['theater_code']: t['name'] for t in theaters_list}
    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Regal Pro//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    for s in path:
        tc = s.get('TheaterCode')
        t_name = name_map.get(tc, f"Regal Theater {tc}")
        
        start_time = s['Showtime'].strftime('%Y%m%dT%H%M%S')
        end_time = (s['Showtime'] + timedelta(minutes=s['Duration'])).strftime('%Y%m%dT%H%M%S')
        
        ics_lines.extend([
            "BEGIN:VEVENT",
            f"DTSTART:{start_time}",
            f"DTEND:{end_time}",
            f"SUMMARY:{s['Title']}",
            f"LOCATION:{t_name} - Audi {s.get('Auditorium', '?')}",
            "END:VEVENT"
        ])
        
    ics_lines.append("END:VCALENDAR")
    return "\n".join(ics_lines)

def get_drive_stats(code_a, code_b, nearby_map=None):
    if code_a == code_b: 
        return 0, 0
    
    if nearby_map is None:
        nearby_map = st.session_state.app_config.get('nearby_map', {}) if 'app_config' in st.session_state else {}
    
    links = nearby_map.get(code_a, [])
    match = next((n for n in links if n['code'] == code_b), None)
    
    if match:
        return match.get('mins', 20), match.get('miles', 0)
    
    links_rev = nearby_map.get(code_b, [])
    match_rev = next((n for n in links_rev if n['code'] == code_a), None)
    
    if match_rev:
        return match_rev.get('mins', 20), match_rev.get('miles', 0)
        
    return 20, 5

def get_scheduler_engine():
    """Helper to instantiate SchedulerEngine with current app context."""
    theater_names = st.session_state.app_config.get('master_name_map', {}) if 'app_config' in st.session_state else {}
    if not theater_names and 'app_config' in st.session_state:
        theater_names = {t['theater_code']: t['name'] for t in st.session_state.app_config.get('theaters', [])}
    
    meta = st.session_state.registry.get('movies', {}) if 'registry' in st.session_state else {}
    return SchedulerEngine(
        drive_stats_fn=get_drive_stats,
        movie_meta=meta,
        theater_name_map=theater_names,
    )

def calculate_path_score(path, primary_code):
    engine = get_scheduler_engine()
    return engine.calculate_path_score(path, primary_code=primary_code)

def find_itineraries(current_path, remaining_codes, screenings, p, selected_date, drive_map=None):
    engine = get_scheduler_engine()
    p_dict = dict(p) if isinstance(p, dict) else p.to_dict()
    if 'extreme_mode' in globals() and 'extreme_mode' not in p_dict:
        p_dict['extreme_mode'] = extreme_mode
    curr_time = current_local_time if 'current_local_time' in globals() else None
    return engine.find_itineraries(
        current_path=current_path,
        remaining_codes=remaining_codes,
        screenings=screenings,
        params=p_dict,
        selected_date=selected_date,
        drive_stats_fn=get_drive_stats,
        current_time=curr_time,
    )

def run_anchored_search(anchor_show, target_codes, day_str, params, drive_map=None):
    engine = get_scheduler_engine()
    p_dict = dict(params) if isinstance(params, dict) else params.to_dict()
    if 'extreme_mode' in globals() and 'extreme_mode' not in p_dict:
        p_dict['extreme_mode'] = extreme_mode
    day_flat = st.session_state.registry.get('schedule', {}).get(day_str, [])
    d_obj = datetime.strptime(day_str, '%m-%d-%Y').date()
    curr_time = current_local_time if 'current_local_time' in globals() else None
    return engine.run_anchored_search(
        anchor_show=anchor_show,
        target_codes=target_codes,
        day_screenings=day_flat,
        params=p_dict,
        selected_date=d_obj,
        drive_stats_fn=get_drive_stats,
        current_time=curr_time,
    )

def find_multi_day_itineraries(target_codes, target_days, params, drive_map=None, anchor_show=None):
    engine = get_scheduler_engine()
    p_dict = dict(params) if isinstance(params, dict) else params.to_dict()
    if 'extreme_mode' in globals() and 'extreme_mode' not in p_dict:
        p_dict['extreme_mode'] = extreme_mode
    curr_time = current_local_time if 'current_local_time' in globals() else None
    day_screenings_map = st.session_state.registry.get('schedule', {})
    return engine.find_multi_day_itineraries(
        target_codes=target_codes,
        target_days=target_days,
        params=p_dict,
        day_screenings_map=day_screenings_map,
        anchor_show=anchor_show,
        drive_stats_fn=get_drive_stats,
        current_time=curr_time,
    )

def get_conflict_report(path, missing_codes, all_screenings, p, anchor_show=None):
    engine = get_scheduler_engine()
    return engine.get_conflict_report(
        path=path,
        missing_codes=missing_codes,
        all_screenings=all_screenings,
        params=p,
        anchor_show=anchor_show,
        drive_stats_fn=get_drive_stats,
    )

def generate_batch_ics(multi_itinerary, theater_name_map=None):
    if not theater_name_map:
        theater_name_map = {
            t['theater_code']: t['name'] 
            for t in st.session_state.app_config.get('theaters', [])
        }

    ics_lines = [
        "BEGIN:VCALENDAR", 
        "VERSION:2.0", 
        "PRODID:-//Regal Pro//EN", 
        "CALSCALE:GREGORIAN", 
        "METHOD:PUBLISH"
    ]
    
    sorted_days = sorted(multi_itinerary.keys(), key=lambda x: datetime.strptime(x, '%m-%d-%Y'))
    
    for d_str in sorted_days:
        path = multi_itinerary[d_str]
        for s in path:
            start_t = s['Showtime'].strftime("%Y%m%dT%H%M%S")
            end_t = (s['Showtime'] + timedelta(minutes=s['Duration'])).strftime("%Y%m%dT%H%M%S")
            
            tc = s.get('TheaterCode')
            t_name = theater_name_map.get(tc, f"Regal Theater {tc}")
            
            ics_lines.extend([
                "BEGIN:VEVENT", 
                f"DTSTART:{start_t}", 
                f"DTEND:{end_t}", 
                f"SUMMARY:{s['Title']} ({s['ScreenType']})", 
                f"LOCATION:{t_name} - Audi {s.get('Auditorium', '?')}", 
                "END:VEVENT"
            ])
            
    ics_lines.append("END:VCALENDAR")
    return "\n".join(ics_lines)

def sync_movie_metadata():
    all_codes = list(st.session_state.global_movie_catalog.keys())
    if not all_codes: return
    
    db_metadata = get_db_metadata(all_codes)
    for m_code, m_info in db_metadata.items():
        if m_code in st.session_state.global_movie_catalog:
            cat_entry = st.session_state.global_movie_catalog[m_code]
            update_dict = {
                'studio': m_info.get('studio') or cat_entry.get('studio'),
                'poster': m_info.get('poster') or cat_entry.get('poster'),
                'regal_url': m_info.get('regal_url') or cat_entry.get('regal_url'),
                'imdb_score': m_info.get('imdb_score'),
                'imdb_url': m_info.get('imdb_url'),
                'rt_critics': m_info.get('rt_critics_score'),
                'rt_users': m_info.get('rt_users_score'),
                'rt_url': m_info.get('rt_url'),
                'metacritic': m_info.get('metacritic_score'),
                'metacritic_url': m_info.get('metacritic_url'),
                'letterboxd': m_info.get('letterboxd_score'),
                'letterboxd_url': m_info.get('letterboxd_url')
            }
            if not cat_entry.get('duration') and m_info.get('duration'):
                update_dict['duration'] = m_info.get('duration')
            if (cat_entry.get('rating') in (None, 'NR', '')) and m_info.get('rating'):
                update_dict['rating'] = m_info.get('rating')
            cat_entry.update(update_dict)
    poster_urls = [m_info.get('poster') for m_info in db_metadata.values() if m_info.get('poster')]
    if poster_urls:
        prefetch_poster_status(poster_urls)

def log_visit(t_item, metadata_gaps=None):
    try:
        if not st.session_state.get('logged_this_session'):
            db.table("visitor_log").insert({
                "theater_code": t_item['theater_code'],
                "theater_name": t_item['name'],
                "is_mobile": IS_CLOUD,
                "metadata_gaps": list(metadata_gaps) if metadata_gaps else []
            }).execute()
            st.session_state.logged_this_session = True
    except Exception as e:
        if debug_mode: print(f"Log error: {e}")

# --- Main App ---
st.title("🎬 Regal Pro")
db = init_supabase()
init_registry()

if 'app_config' not in st.session_state:
    st.session_state.app_config = load_app_config()

theaters = st.session_state.app_config['theaters'] if st.session_state.app_config else []

if "init_complete" not in st.session_state:
    url_t_code = st.query_params.get("theater")
    st.session_state.search_mode_pref = "Theater Code" if url_t_code else "Zip Code"
    st.session_state.init_complete = True
    st.session_state.initial_url_code = url_t_code
    if url_t_code:
        st.session_state.theater_code_input = url_t_code
else:
    url_t_code = None

location, latitude,longitude, default_zip_code = get_location_cookie()

results = []
search_performed = False

st.sidebar.header("📍 Find Theater")

search_mode = st.sidebar.selectbox(
    "Search By", 
    ["Zip Code", "Theater Name", "Address/City", "Theater Code"],
    index=["Zip Code", "Theater Name", "Address/City", "Theater Code"].index(st.session_state.search_mode_pref),
    key="current_search_mode"
)

if search_mode == "Theater Code":
    target_active = st.session_state.get("active_theater_code")
    if target_active and st.session_state.get("theater_code_input") != target_active:
        st.session_state.theater_code_input = target_active
    code_in = st.sidebar.text_input("Theater Code", key="theater_code_input", on_change=update_theater_from_input)
    
    if "initial_url_code" in st.session_state and code_in != st.session_state.initial_url_code:
        st.session_state.initial_url_code = None

    if code_in:
        search_performed = True
        match = next((t for t in theaters if t['theater_code'] == code_in), None)
       
        if match:
            results = [match]
            nearby_data = st.session_state.app_config.get('nearby_map', {}).get(code_in, [])
            if nearby_data:
                nearby_codes = [n['code'] for n in nearby_data]
                results.extend([t for t in theaters if t['theater_code'] in nearby_codes])
elif search_mode == "Zip Code":
    zip_in = st.sidebar.text_input("Zip Code", placeholder="46201", value=default_zip_code)
    radius_in = st.sidebar.slider("Radius (miles)", 5, 200, 50)
    
    if zip_in:
        search_performed = True
        results = []
        nomi = pgeocode.Nominatim('us')
        z_data = nomi.query_postal_code(zip_in)
        if not math.isnan(z_data['latitude']):
            for t in theaters:
                d = calculate_haversine_distance(z_data['latitude'], z_data['longitude'], t['latitude'], t['longitude'])
                if d <= radius_in: results.append((t, d))
            results.sort(key=lambda x: x[1])
    elif location and not math.isnan(latitude):
        for t in theaters:
            d = calculate_haversine_distance(latitude, longitude, t['latitude'], t['longitude'])
            if d <= 50: results.append((t, d))
        results.sort(key=lambda x: x[1])
elif search_mode == "Theater Name":
    name_in = st.sidebar.text_input("Theater Name")
    if name_in: search_performed = True; results = [t for t in theaters if name_in.lower() in t['name'].lower()]
elif search_mode == "Address/City":
    addr_in = st.sidebar.text_input("Address, City, or State")
    if addr_in:
        search_performed = True
        term = addr_in.strip().lower()
        results = [
            t for t in theaters 
            if any(term in str(t.get(f) or '').lower() for f in ['address', 'city', 'state'])
        ]
if search_performed and not results: st.sidebar.warning("No theaters found matching your criteria.")

selected_theater = None

url_param_theater = st.query_params.get("theater")
if url_param_theater:
    st.session_state.active_theater_code = url_param_theater
elif "active_theater_code" not in st.session_state:
    st.session_state.active_theater_code = None

active_code = st.session_state.get("active_theater_code")
if active_code:
    match = next((t for t in theaters if t['theater_code'] == active_code), None)
    if match:
        current_result_codes = {r[0]['theater_code'] if isinstance(r, tuple) else r['theater_code'] for r in results}
        if match['theater_code'] not in current_result_codes:
            results = [match]
            nearby_data = st.session_state.app_config.get('nearby_map', {}).get(match['theater_code'], [])
            if nearby_data:
                nearby_codes = [n['code'] for n in nearby_data]
                results.extend([t for t in theaters if t['theater_code'] in nearby_codes])

if results and not st.session_state.get("change_theater_mode"):
    opts = {f"{r[0]['name'] if isinstance(r, tuple) else r['name']} - {r[0]['city'] if isinstance(r, tuple) else r['city']}": (r[0] if isinstance(r, tuple) else r) for r in results}    
    
    target_label = None
    for label, t in opts.items():
        if t['theater_code'] == st.session_state.get("active_theater_code"):
            target_label = label
            break

    if target_label:
        st.session_state.temp_theater_select = target_label
    elif list(opts.keys()):
        st.session_state.temp_theater_select = list(opts.keys())[0]

    sel_label = st.sidebar.selectbox(
        "Select Theater", 
        options=list(opts.keys()), 
        index=None,
        key="temp_theater_select",
        on_change=sync_theater_selection
    )
    selected_theater = opts.get(sel_label)
    if selected_theater:
        new_code = selected_theater['theater_code']
        st.session_state.active_theater_code = new_code
        sync_query_param("theater", new_code)

if selected_theater:
    st.session_state.theater = selected_theater
    t_item = selected_theater
    tc, ckey = t_item['theater_code'], t_item.get('cluster_key', 'cluster_99')

    cluster_theaters = {tc: t_item['name']}
    master_name_map = {t['theater_code']: t['name'] for t in theaters}
    drive_map = {tc: {'time': 0, 'dist': 0}}
    
    nearby_data = st.session_state.app_config.get('nearby_map', {}).get(tc, [])
    for nt in nearby_data:
        n_code = nt['code']
        cluster_theaters[n_code] = master_name_map.get(n_code, f"Theater {n_code}")
        drive_map[n_code] = {'time': nt.get('mins', 20), 'dist': nt.get('miles', 0)}

    tz_off = st.session_state.get('auto_tz_offset', -5)
    local_today = (datetime.now(timezone.utc) + timedelta(hours=tz_off)).date()
    
    # Parse date from query parameters if provided and valid within 10-day window
    initial_date = local_today
    raw_date_param = st.query_params.get("date")
    if raw_date_param:
        for fmt in ("%Y-%m-%d", "%m-%d-%Y"):
            try:
                candidate = datetime.strptime(raw_date_param, fmt).date()
                if local_today <= candidate <= local_today + timedelta(days=9):
                    initial_date = candidate
                break
            except ValueError:
                continue

    q_date = st.sidebar.date_input("Select Date", value=initial_date, min_value=local_today, max_value=local_today + timedelta(days=9), format="MM/DD/YYYY")
    sync_query_param("date", q_date.strftime("%Y-%m-%d"))
    d_str, today_cache_str = q_date.strftime('%m-%d-%Y'), local_today.strftime('%m-%d-%Y')

    t_lon = t_item.get('longitude')
    if t_lon:
        st.session_state.auto_tz_offset = get_offset_from_lon(
            t_lon, t_item.get('state'), 
            target_date=datetime.combine(q_date, dt_time(0,0))
        )

    is_new_cluster = st.session_state.registry.get('active_ckey') != ckey
    is_date_missing = d_str not in st.session_state.registry['loaded_dates']

    status_context = st.status("🛠️ Debug: Detailed Sync Log", expanded=True) if debug_mode else None
    msg = None
    
    if is_new_cluster or is_date_missing:
        log_msg = "🌱 Initializing App..."
        msg = st.toast(log_msg)
        if status_context: status_context.write(log_msg)
        log_msg = f"🔄 Synchronizing Cluster {ckey}..."
        msg.toast(log_msg)
        if status_context: status_context.write(log_msg)

        c_file = f"{ckey}_{today_cache_str}.json.gz"
        raw_payload = load_cluster_from_storage("theater-cache", c_file)

        if raw_payload:
            log_msg = f"⚡ Loading 10-Day cluster data from storage..."
            msg.toast(log_msg)
            if status_context: status_context.write(log_msg)
            registry_ingest(raw_payload)
            st.session_state.registry['loaded_dates'].update(raw_payload.keys())
        else:
            log_msg = f"🌐 Cache miss. Synchronizing 10-Day Data from Regal API..."
            msg.toast(log_msg)  
            if status_context: status_context.write(log_msg)
            target_codes = [tc] + [nt['code'] for nt in t_item.get('nearby_theaters', [])]
            new_payload = {}
            for i in range(10):
                fetch_date = (local_today + timedelta(days=i)).strftime('%m-%d-%Y')
                log_msg = f"🌐 Fetching {fetch_date}..."
                msg.toast(log_msg)
                if status_context: status_context.write(log_msg)
                
                api_url = f"https://www.regmovies.com/api/getShowtimes?theatres={','.join(target_codes)}&date={fetch_date}"
                day_data = fetch_data(api_url, t_item['path_name'], status_context if debug_mode else None, msg)
                if day_data:
                    log_msg = f"👍 {fetch_date} fetch successful."
                    msg.toast(log_msg)
                    if status_context: status_context.write(log_msg)

                    new_payload[fetch_date] = day_data
                else:
                    if fetch_date == today_cache_str: 
                        st.error(f"🚨 Connection Failure: Could not retrieve current data for {t_item['name']}. Aborting sync.")
                        break
                    else:
                        st.warning(f"Skipping {fetch_date} due to connection error.")
                        continue
                
                if new_payload:
                    registry_ingest(new_payload)     

        st.session_state.registry['active_ckey'] = ckey
        log_msg = "🎉 10-Day Sync Complete!"
        msg.toast(log_msg, duration="short")
        if status_context: status_context.update(label=log_msg, state="complete", expanded=False)

    schedule = st.session_state.registry['schedule'].get(d_str, [])
    if schedule:
        hollow = [s['master_code'] for s in schedule 
                     if s['master_code'] not in st.session_state.registry['movies'] 
                     or not st.session_state.registry['movies'][s['master_code']].get('studio')
                     or st.session_state.registry['movies'][s['master_code']].get('duration') == 0]
        
        if hollow:
            log_msg = "🩹 Hydrating missing metadata..."
            if msg:
                msg.toast(log_msg)
            else:
                msg = st.toast(log_msg)
            if status_context: status_context.write(log_msg)
            
            st.session_state.registry['movies'].update(get_db_metadata(hollow))

    if tc not in st.session_state.registry['theaters']:
        st.session_state.registry['theaters'][tc] = {}

    if 'future' not in st.session_state.registry['theaters'][tc]:
        log_msg = f"⚡ Loading upcoming schedule for {t_item['name']}"
        if msg:
            msg.toast(log_msg)
        else:
            msg = st.toast(log_msg)
        if status_context: status_context.write(log_msg)

        f_file = f"{tc}_{today_cache_str}.json.gz"
        f_payload = load_cluster_from_storage("future-shows", f_file)
        
        if f_payload:
            log_msg = f"⚡ Loading upcoming schedule for {t_item['name']} from Storage..."
            msg.toast(log_msg)
            if status_context: status_context.write(log_msg)
        else:
            log_msg = f"📡 Fetching unique upcoming schedule for {t_item['name']}..."
            msg.toast(log_msg)
            if status_context: status_context.write(log_msg)
            api_url = f"https://www.regmovies.com/api/getShowtimes?theatres={tc}&date={d_str}"
            f_payload = fetch_data(api_url, t_item['path_name'], status_context if debug_mode else None, msg)
            if f_payload:
                save_future_to_storage("future-shows", f_file, f_payload)
                log_msg = f"✅ Successfully cached future data for {t_item['name']} to storage."
                msg.toast(log_msg)
                if status_context: status_context.write(log_msg)
            
        if f_payload:
            _, _, _, f_map = flatten_data(f_payload, theater_code_override=tc)
            st.session_state.registry['theaters'][tc]['future'] = f_map.get(tc, [])
            
            f_codes = [m['master_code'] for m in f_map.get(tc, [])]
            missing_f = [mc for mc in f_codes if mc not in st.session_state.registry['movies']]
            
            if missing_f:
                st.session_state.registry['movies'].update(get_db_metadata(missing_f))

            if tc not in st.session_state.registry['theaters']:
                st.session_state.registry['theaters'][tc] = {}
            st.session_state.registry['theaters'][tc]['future'] = f_map.get(tc, [])

            del f_payload
            gc.collect()
        else:
            st.session_state.registry['theaters'][tc]['future'] = []

    all_flat_data = st.session_state.registry['schedule'].get(d_str, [])
    update_schedule_session_state(
        all_flat_data=all_flat_data,
        flat_data=[s for s in all_flat_data if s['TheaterCode'] == tc],
        movie_meta=st.session_state.registry['movies'],
        attr_map=st.session_state.registry.get('attr_map', {}),
        future_movies=st.session_state.registry['theaters'].get(tc, {}).get('future', []),
        drive_map=drive_map
    )


with st.sidebar.expander("⚙️ Advanced Settings", expanded=False):
    st.write("🕒 Timezone Settings")
    local_now = datetime.now()
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    system_offset = round((local_now - utc_now).total_seconds() / 3600)
    default_offset = st.session_state.get('auto_tz_offset', system_offset)
    tz_offset = st.number_input("Selected Location Offset from UTC", value=int(default_offset), step=1)
    current_local_time = (datetime.now(timezone.utc) + timedelta(hours=tz_offset)).replace(tzinfo=None)
    st.write(f"Local Time for Selected Location: **{current_local_time.strftime('%I:%M %p')}**")
    st.divider()
    if st.button("🔄 Force Refresh"): st.session_state.last_fetch_key = None
    print_mode = st.checkbox("🖨️ Print View")
    #debug_mode = st.checkbox("🐞 Debug Mode", value=debug_mode, help="Show raw API responses for troubleshooting.")
    extreme_mode = False
    if not IS_CLOUD:
        extreme_mode = st.checkbox("🔥 Extreme Scheduling Mode", help="Ignore runtimes, distances, and buffers. Just chain movies 90 mins apart.")
    
st.sidebar.link_button("🐞 Report a Bug","https://docs.google.com/forms/d/e/1FAIpQLSce6X3DtCwDJZUjf_Cc4IbJLA7q0Nvk_Grw7lOgyqLtxYIYPQ/viewform?usp=dialog")
st.sidebar.link_button("☕ Buy Me a Coffee","https://buymeacoffee.com/riyazusman")
st.sidebar.link_button("📃 Source on Github","https://github.com/riyazusman/regal-pro")

if selected_theater and schedule:
    #if debug_mode:
    #    with st.expander("🛠️ Raw API Debug Output", expanded=False):
    #        st.json(current_day_data)

    all_flat_data = st.session_state.registry['schedule'].get(d_str, [])
    movie_meta = st.session_state.registry['movies']
    attr_map = st.session_state.registry.get('attr_map', {})
    flat_data = [s for s in all_flat_data if s['TheaterCode'] == tc]
    future_movies = st.session_state.registry['theaters'].get(tc, {}).get('future', [])
        
    update_schedule_session_state(
        all_flat_data=all_flat_data,
        flat_data=flat_data,
        movie_meta=movie_meta,
        attr_map=attr_map,
        future_movies=future_movies,
        drive_map=st.session_state.get('drive_map', {})
    )

    t_key = t_item['theater_code']

    # --- Top Mobile-Friendly Theater & Cluster Banner ---
    with st.container(border=True):
        b_c1, b_c2 = st.columns([3, 1])
        with b_c1:
            st.markdown(f"### 📍 {t_item['name']}")
            cluster_count = len(cluster_theaters)
            st.caption(f"🗺️ {t_item.get('city', '')}, {t_item.get('state', '')} | 🏛️ **{cluster_count}** theaters in cluster | 🗓️ **{q_date.strftime('%a, %b %d')}**")
        with b_c2:
            if st.button("🔍 Change Theater", key="btn_change_theater", use_container_width=True):
                st.session_state.change_theater_mode = True
                if "temp_theater_select" in st.session_state:
                    del st.session_state.temp_theater_select
                st.rerun()

        # Quick 10-Day Date Strip (Mobile-first touch pills)
        date_options = [local_today + timedelta(days=i) for i in range(10)]
        date_str_options = [d.strftime("%Y-%m-%d") for d in date_options]

        def format_date_pill(d_iso):
            d = datetime.strptime(d_iso, "%Y-%m-%d").date()
            if d == local_today:
                return f"📍 Today ({d.strftime('%b %d')})"
            elif d == local_today + timedelta(days=1):
                return f"Tomorrow ({d.strftime('%b %d')})"
            return d.strftime("%a, %b %d")

        pills_key = f"date_pills_{tc}"
        current_d_iso = q_date.strftime("%Y-%m-%d")
        if st.session_state.get(pills_key) != current_d_iso:
            st.session_state[pills_key] = current_d_iso

        def on_date_pill_change():
            new_date = st.session_state.get(pills_key)
            if new_date:
                sync_query_param("date", new_date)

        st.pills(
            "Select Date",
            options=date_str_options,
            format_func=format_date_pill,
            key=pills_key,
            on_change=on_date_pill_change,
            label_visibility="collapsed"
        )

        if len(cluster_theaters) > 1:
            with st.expander(f"🔄 Switch Neighbor Theater ({len(cluster_theaters)} in Cluster)", expanded=False):
                nb_cols = st.columns(min(len(cluster_theaters), 4))
                for idx, (nb_c, nb_name) in enumerate(cluster_theaters.items()):
                    is_active = (nb_c == tc)
                    label = f"📍 {nb_name}" if is_active else nb_name
                    with nb_cols[idx % min(len(cluster_theaters), 4)]:
                        if st.button(label, key=f"quick_switch_{nb_c}", disabled=is_active, use_container_width=True):
                            st.session_state.active_theater_code = nb_c
                            st.session_state.change_theater_mode = False
                            sync_query_param("theater", nb_c)
                            if "temp_theater_select" in st.session_state:
                                del st.session_state.temp_theater_select
                            st.rerun()

    nav_key = f"nav_tab_{t_key}"

    tabs_list = ["🔎 Theater Explorer", "🎬 Movie Explorer", "🗓️ Smart Scheduler"]
    default_idx = 0
    if "nav_redirect" in st.session_state:
        st.session_state[nav_key] = st.session_state.nav_redirect
        del st.session_state.nav_redirect
    elif nav_key not in st.session_state:
        raw_tab_param = st.query_params.get("tab", "").lower()
        if raw_tab_param in TAB_MAP:
            st.session_state[nav_key] = TAB_MAP[raw_tab_param]

    nav_tab = st.radio(
            "Navigation", 
            ["🔎 Theater Explorer", "🎬 Movie Explorer", "🗓️ Smart Scheduler"], 
            horizontal=True, 
            label_visibility="collapsed",
            key=nav_key
        )
    sync_query_param("tab", TAB_REVERSE_MAP.get(nav_tab, "theater"))

    if nav_tab == "🔎 Theater Explorer":
        sync_query_param("movie", None)
        sync_query_param("targets", None)
        if print_mode: st.markdown("<style>[data-testid='stSidebar'], [data-testid='stHeader'] {display: none;} .stExpander {border: none !important;}</style>", unsafe_allow_html=True)
        st.subheader("🔎 Theater Explorer")
        st.info(f"Viewing: **{t_item['name']}** on **{q_date.strftime('%A, %b %d')}**")

        theater_views = ["🍿 Now Playing", "🚗 Playing Nearby", "📅 Upcoming"]
        sub_nav_key = f"sub_nav_{t_key}"
        if st.session_state.get(sub_nav_key) not in theater_views:
            raw_view_param = (st.query_params.get("tview") or st.query_params.get("view", "")).lower()
            st.session_state[sub_nav_key] = THEATER_VIEW_MAP.get(raw_view_param, "🍿 Now Playing")

        sub_nav = st.radio("Sub Navigation", 
                           theater_views, 
                           horizontal=True, 
                           label_visibility="collapsed",
                           key=sub_nav_key)
        sync_query_param("tview", THEATER_VIEW_REV_MAP.get(sub_nav, "now-playing"))
        sync_query_param("mview", None)
        sync_query_param("view", None)
        
        if sub_nav == "🍿 Now Playing":
            st.subheader("🍿 Now Playing")
            st.caption(f"These movies are playing today at {t_item['name']}.")

            with st.expander("🔍 Filters & Sorting", expanded=not print_mode):
                r_c1, r_c2 = st.columns(2)
                with r_c1:
                    sorted_codes = sorted(
                                        list(list(set(s['master_code'] for s in flat_data))), 
                                        key=lambda x: (
                                            get_movie_group_info(x)[0], 
                                            st.session_state.global_movie_catalog.get(x, {}).get('title', '').lower()
                                        )
                    )
                    f_code = st.multiselect("Movie", 
                                            options=sorted_codes,
                                            format_func=format_movie_label_grouped,
                                            placeholder="All",
                                            key=f"f_code_{t_key}",
                                            help = "🔴 New Release  🎬 Regular Release  🎫 Unlimited Excluded")
                    f_type = st.multiselect("Screen Type", 
                                            options=sorted(list(set(s['ScreenType'] for s in flat_data))), 
                                            placeholder="All",
                                            key=f"f_type_{t_key}")
                    t_ranges = {"8AM - 12N": (8, 12), "12N - 4PM": (12, 16), "4PM - 8PM": (16, 20), "8PM - 12M": (20, 24)}
                    f_times = st.multiselect("Time Window", 
                                            options=list(t_ranges.keys()), 
                                            placeholder="All",
                                            key=f"f_times_{t_key}")
                    chk_c1, chk_c2 = st.columns(2)
                    with chk_c1:
                        f_avail = st.checkbox("Hide past shows", value=True, key=f"f_avail_{t_key}")
                    with chk_c2:
                        f_new = st.checkbox("New Releases Only", value=False, key=f"f_new_{t_key}")
                with r_c2:
                    f_rating = st.multiselect("Rating", 
                                            options=sorted(list(set(s['Rating'] for s in flat_data))), 
                                            placeholder="All",
                                            key=f"f_rating_{t_key}")
                    f_audi = st.multiselect("Auditorium", 
                                            options=sorted(list(set(s['Auditorium'] for s in flat_data)), key=lambda x: int(x) if x.isdigit() else 999), 
                                            placeholder="All",
                                            key=f"f_audi_{t_key}")
                    current_st = set(f_type) if f_type else set(s['ScreenType'] for s in flat_data)
                    all_expanded_attrs = set(a for s in flat_data for a in s['raw_attrs'])
                    deduped_attrs = sorted([a for a in all_expanded_attrs if a not in current_st])
                    
                    f_attr = st.multiselect("Additional Filters", 
                                            options=deduped_attrs, 
                                            placeholder="All",
                                            key=f"f_attr_{t_key}")
                    s_c1, s_c2 = st.columns(2)
                    with s_c1:
                        sort_by = st.selectbox("Sort By", 
                                               ["Movie Title", "Showtime", "Auditorium"],
                                               key=f"sort_by_{t_key}")
                    with s_c2:
                        view_mode = st.selectbox("View Mode", 
                                                 ["Group by Movie", "Group by Auditorium", "Full Schedule"],
                                                 key=f"view_mode_{t_key}")
                    
            filtered = [s for s in flat_data if (
                not f_code or s['master_code'] in f_code) and
                (not f_type or s['ScreenType'] in f_type) and 
                (not f_rating or s['Rating'] in f_rating) and 
                (not f_audi or s['Auditorium'] in f_audi) and 
                (not f_attr or set(f_attr).issubset(s['raw_attrs'])) and 
                (not f_times or any(t_ranges[t][0] <= s['Showtime'].hour < t_ranges[t][1] for t in f_times)) and 
                (not f_avail or (current_local_time <= s['Showtime'] + timedelta(minutes=30) if q_date == current_local_time.date() else True)) and
                (not f_new or movie_meta.get(s['master_code'], {}).get('is_new', False))]
            
            if sort_by == "Movie Title": filtered.sort(key=lambda x: (x['Title'], x['Showtime']))
            elif sort_by == "Showtime": filtered.sort(key=lambda x: (x['Showtime'], x['Title']))
            elif sort_by == "Auditorium": filtered.sort(key=lambda x: (int(x['Auditorium']) if x['Auditorium'].isdigit() else 999, x['Showtime']))
            
            st.write(f"Showing **{len(set(s['Title'] for s in filtered))}** movies and **{len(filtered)}** screenings.")

            if view_mode == "Full Schedule":
                for s in filtered:
                    with st.container(border=True):
                        col_t, col_info = st.columns([1.3, 5])
                        is_past = (q_date == current_local_time.date() and s['Showtime'] < current_local_time)
                        t_str = f"<span style=\"text-decoration: line-through;\">{s['Showtime'].strftime('%I:%M %p')}</span>" if is_past else f"{s['Showtime'].strftime('%I:%M %p')}"
                        d_str = f"~~{s['Title']}~~" if is_past else s['Title']
                        col_t.markdown(f"""<div style="line-height: 1;"><p style="color: grey; font-size: 0.8rem; margin-bottom: 2px; text-transform: uppercase; font-weight: bold;">{s['ScreenType']}</p><p style="font-size: 1.4rem; font-weight: 700; margin: 0; white-space: nowrap;">{t_str}</p></div>""", unsafe_allow_html=True)
                        col_info.markdown(f"### {d_str}")
                        col_info.markdown(f"**{s['Rating']}** | **{s['Duration']} min** | Audi {s['Auditorium']}")
                        if s.get('raw_attrs'): 
                            attr_list = sorted(list(s['raw_attrs']))
                            st.markdown(f'<p style="color: grey; font-size: 0.85em; margin-top: -10px;">{", ".join(attr_list)}</p>', unsafe_allow_html=True)
            elif view_mode == "Group by Auditorium":
                for audi in sorted(list(set(s['Auditorium'] for s in filtered)), key=lambda x: int(x) if x.isdigit() else 999):
                    with st.expander(f"🖼️ Auditorium {audi}", expanded=True):
                        for s in sorted([s for s in filtered if s['Auditorium'] == audi], key=lambda x: x['Showtime']):
                            col_t, col_info = st.columns([1, 5])
                            is_past = (q_date == current_local_time.date() and s['Showtime'] < current_local_time)
                            t_str = f"~~{s['Showtime'].strftime('%I:%M %p')}~~" if is_past else f"**{s['Showtime'].strftime('%I:%M %p')}**"
                            d_str = f"~~{s['Title']} ({s['ScreenType']}) — {s['Duration']}m~~" if is_past else f"**{s['Title']}** ({s['ScreenType']}) — {s['Duration']}m"
                            col_t.markdown(t_str)
                            col_info.markdown(d_str)
            else: # Group by Movie
                st.caption("🔴 New Release  🎫 Unlimited Excluded  ⭐ IMDB  🍅 Tomatometer  🍿 Popcornmeter  Ⓜ️ Metacritic  🟢 Letterboxd")
                for m_code in list(dict.fromkeys([s['master_code'] for s in filtered])):
                    m_shows = [s for s in filtered if s['master_code'] == m_code]
                    title = m_shows[0]['Title']
                    
                    other_t_codes = sorted([c for c in set(s['TheaterCode'] for s in all_flat_data if s['master_code'] == m_code) 
                                           if c != t_item['theater_code'] and c in cluster_theaters])
                    
                    valid_date_objs = [datetime.strptime(date_key, "%m-%d-%Y") 
                        for date_key, day_flat in st.session_state.registry['schedule'].items() 
                        if any(s['master_code'] == m_code for s in day_flat)]
                    
                    new_tag = "| 🔴 New Release" if meta.get('is_new') else ""
                    unlimited_tag = "| 🎫 No Unlimited" if is_unlimited_excluded(m_code, m_shows[0]['raw_attrs']) else ""

                    with st.expander(f"🍿 {title} ({m_shows[0]['Rating']}) | 🕒 {m_shows[0]['Duration']} min {new_tag} {unlimited_tag}", expanded=True):
                        render_movie_header_card(m_code, title=title, extra_tags=f"{new_tag} {unlimited_tag}")

                        for mt in sorted(list(set(s['ScreenType'] for s in m_shows))):
                            ts = [s for s in m_shows if s['ScreenType'] == mt]
                            t_common = set.intersection(*(s['raw_attrs'] for s in ts)) if ts else set()
                            common_attribs = sorted(t_common - {mt})
                            st.markdown(f'<div style="margin-bottom: 6px;"><span style="background-color: rgba(151, 166, 195, 0.15); padding: 4px 12px; border-radius: 4px; border-left: 4px solid #ff4b4b;"><span style="font-weight: bold;">{mt}</span> <span style="color: grey; font-size: 0.85em; font-weight: normal; margin-left: 10px;">({", ".join(sorted(common_attribs)) if common_attribs else ""})</span></span></div>', unsafe_allow_html=True)

                            row = []
                            for s in ts:
                                is_past = (q_date == current_local_time.date() and s['Showtime'] < current_local_time)
                                delta_attribs = get_attr_diff(s['raw_attrs'], t_common)
                                t_str = s['Showtime'].strftime('%I:%M %p')
                                if is_past:
                                    final_time = f"<del>{t_str}</del>" 
                                    meta_text = f"  <small style='color:grey'><del>(Audi {s['Auditorium']}) {delta_attribs}</del></small>"
                                else:
                                    final_time = f"{t_str}" 
                                    meta_text = f"  <small style='color:grey'>(Audi {s['Auditorium']}) {delta_attribs}</small>"
                                row.append(f"{final_time}{meta_text}")
                            
                            st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{' | '.join(row)}", unsafe_allow_html=True)

                        footer_col, link_col = st.columns([4, 1])
                        with footer_col:
                            if valid_date_objs:
                                st.caption("🗓️ **Scheduled Dates:**")
                                sel_date = st.pills(
                                    "Scheduled Dates",
                                    options=sorted(valid_date_objs),
                                    format_func=lambda d: d.strftime("%b %d"),
                                    key=f"np_dates_{m_code}_{tc}",
                                    label_visibility="collapsed"
                                )
                                if sel_date and sel_date != q_date:
                                    d_iso = sel_date.strftime("%Y-%m-%d")
                                    sync_query_param("date", d_iso)
                                    st.rerun()

                            if other_t_codes:
                                st.caption("🚗 **Also Playing at:**")
                                sel_nb = st.pills(
                                    "Also Playing at",
                                    options=other_t_codes,
                                    format_func=lambda c: cluster_theaters.get(c, f"Theater {c}"),
                                    key=f"np_theaters_{m_code}_{tc}",
                                    label_visibility="collapsed"
                                )
                                if sel_nb:
                                    st.session_state.active_theater_code = sel_nb
                                    st.session_state.change_theater_mode = False
                                    sync_query_param("theater", sel_nb)
                                    st.rerun()

                        with link_col:
                            if other_t_codes or valid_date_objs:
                                if st.button("📅 Full Schedule", key=f"link_{m_code}_{t_item['theater_code']}", use_container_width=True):
                                    st.session_state.nav_redirect = "🎬 Movie Explorer"
                                    st.session_state.selected_movie_code = m_code
                                    st.rerun()

        elif sub_nav == "🚗 Playing Nearby":
            primary_codes_week = set()
            all_codes_week = set()
            
            for date_key in st.session_state.registry['schedule']:
                day_flat = st.session_state.registry['schedule'][date_key]
                for s in day_flat:
                    if s['TheaterCode'] not in cluster_theaters: continue
                    
                    if s['TheaterCode'] == tc:
                        primary_codes_week.add(s['master_code'])
                    all_codes_week.add(s['master_code'])
            
            nearby_only_codes = sorted(list(all_codes_week - primary_codes_week), key=lambda x: st.session_state.global_movie_catalog.get(x, {}).get('title', ''))

            if nearby_only_codes:
                st.subheader("🚗 Exclusive Nearby This Week")
                st.caption(f"These movies are NOT playing at {t_item['name']} any time this week.")
                st.caption("🔴 New Release  🎫 Unlimited Excluded  ⭐ IMDB  🍅 Tomatometer  🍿 Popcornmeter  Ⓜ️ Metacritic  🟢 Letterboxd")
                cols_per_row = 3
                for row_idx in range(0, len(nearby_only_codes), cols_per_row):
                    row_codes = nearby_only_codes[row_idx:row_idx + cols_per_row]
                    row_cols = st.columns(cols_per_row)
                    for col_idx, m_code in enumerate(row_codes):
                        theater_dates = {}
                        for d_str, day_flat in st.session_state.registry['schedule'].items():
                            for s in day_flat:
                                if s['master_code'] == m_code and s['TheaterCode'] in cluster_theaters:
                                    n_tc = s['TheaterCode']
                                    if n_tc not in theater_dates: theater_dates[n_tc] = []
                                    if d_str not in theater_dates[n_tc]: theater_dates[n_tc].append(d_str)
                        
                        m_attrs = set()
                        for s in all_flat_data:
                            if s['master_code'] == m_code:
                                m_attrs.update(s.get('raw_attrs', []))
                        
                        meta = st.session_state.global_movie_catalog.get(m_code, {'title': 'Unknown', 'rating': 'NR', 'duration': 0, 'is_new':False})
                        new_tag = " 🔴" if meta.get('is_new') else ""
                        unlimited_tag = " 🎫" if is_unlimited_excluded(m_code, m_attrs) else ""
                        badges, badges_type = get_rating_badges(m_code)
                        poster_url = meta.get('poster')
                        has_poster = is_poster_valid(poster_url)
                        regal_url = get_movie_regal_url(m_code, meta.get('title'))
                        badges_line = format_badges_and_regal_link(badges, regal_url, "Regal ↗")
                        badges_row = f'<div><small>{badges_line}</small></div>' if badges_line else ""

                        with row_cols[col_idx]:
                            with st.container(border=True):
                                img_html = f'<img src="{poster_url}" alt="" style="width:50px; min-width:50px; height:75px; object-fit:cover; border-radius:4px; box-shadow:0 1px 3px rgba(0,0,0,0.2);" onerror="this.style.display=\'none\';" />' if has_poster else ""
                                card_html = (
                                    f'<div style="display:flex; gap:10px; align-items:flex-start; margin-bottom:6px;">'
                                    f'{img_html}'
                                    f'<div style="flex:1; min-width:0;">'
                                    f'<div style="font-weight:700; font-size:0.95rem; line-height:1.2;">{meta.get("title", "New Release")} <span style="font-size:0.85rem; font-weight:normal; color:grey;">({meta.get("rating", "NR")})</span>{new_tag}{unlimited_tag}</div>'
                                    f'{badges_row}'
                                    f'<div style="font-size:0.75rem; color:grey; margin-top:2px;">⏱️ {meta.get("duration", 120)} min • 🎥 {meta.get("studio", "Unknown")}</div>'
                                    f'</div>'
                                    f'</div>'
                                )
                                st.markdown(card_html, unsafe_allow_html=True)

                                for n_tc, dates in theater_dates.items():
                                    t_name = cluster_theaters.get(n_tc, f"Theater {n_tc}")
                                    sorted_date_objs = sorted([datetime.strptime(d, "%m-%d-%Y") for d in dates])
                                    
                                    st.caption(f"📍 **{t_name}**")
                                    sel_ex_d = st.pills(
                                        f"Dates for {n_tc} {m_code}",
                                        options=sorted_date_objs,
                                        format_func=lambda d: d.strftime("%b %d"),
                                        key=f"ex_pills_{m_code}_{n_tc}",
                                        label_visibility="collapsed"
                                    )
                                    if sel_ex_d:
                                        st.session_state.active_theater_code = n_tc
                                        st.session_state.change_theater_mode = False
                                        sync_query_param("theater", n_tc)
                                        d_iso = sel_ex_d.strftime("%Y-%m-%d")
                                        sync_query_param("date", d_iso)
                                        st.rerun()
            else:
                st.info("No exclusive nearby movies found for the upcoming 7 days.")

        elif sub_nav == "📅 Upcoming":
            scoped_future_movies = st.session_state.registry['theaters'].get(tc, {}).get('future', [])
            if scoped_future_movies:
                specials_config = load_monthly_specials()
                active_series = [s for s in specials_config if s['Active']]

                if active_series:
                    st.write("###### 📅 Filter by Series")
                    filter_options = ["ALL"] + [s['Prefix'] for s in active_series]

                    filter_options.append("Fathom")
                    
                    if "upcoming_filter_prefix" not in st.session_state:
                        st.session_state.upcoming_filter_prefix = "ALL"
                    
                    btn_cols = st.columns(len(filter_options))
                    for i, opt in enumerate(filter_options):
                        is_sel = (opt == st.session_state.upcoming_filter_prefix)
                        btn_label = f"✅ {opt}" if is_sel else opt
                        if btn_cols[i].button(f"✅ {opt}" if is_sel else opt, key=f"up_filter_{opt}", use_container_width=True):
                            st.session_state.upcoming_filter_prefix = opt
                            st.rerun()

                sel_prefix = st.session_state.get("upcoming_filter_prefix", "ALL").upper()
                display_movies = []
                if sel_prefix == "FATHOM":
                    subheader = f"📅 Upcoming {sel_prefix} Specials"
                    for m in scoped_future_movies:
                        m_code = m['master_code']
                        meta = st.session_state.registry['movies'].get(m_code, {})
                        studio = str(meta.get('studio', '')).strip()
                        title = meta.get('title', '').upper()
                        if studio == "Regal Cinemedia" or "FATHOM" in title:
                            display_movies.append(m)
                elif sel_prefix != "ALL":
                    subheader = f"📅 Upcoming {sel_prefix} Specials"
                    for m in scoped_future_movies:
                        m_code = m['master_code']
                        meta = st.session_state.registry['movies'].get(m_code, {})
                        m_title = meta.get('title', '').upper().strip()
                        if m_title.startswith(f"{sel_prefix}:") or m_title.startswith(f"{sel_prefix} "):
                            display_movies.append(m)
                else:
                    subheader = "📅 Upcoming Movies"
                    display_movies = scoped_future_movies

                if not display_movies:
                    st.warning(f"No upcoming movies found for the '{sel_prefix}' series at this theater.")
                else:
                    up_h_col1, up_h_col2 = st.columns([3, 1])
                    with up_h_col1:
                        st.subheader(subheader)
                        st.caption(f"These movies are scheduled to show at {t_item['name']}.")
                    with up_h_col2:
                        up_sort_mode = st.selectbox(
                            "Sort Upcoming Movies",
                            options=["🗓️ Release Date", "🔤 Title (A-Z)"],
                            index=0,
                            key=f"up_sort_mode_{tc}",
                            label_visibility="collapsed"
                        )
                    st.caption("🎫 Unlimited Excluded  ⭐ IMDB  🍅 Tomatometer  🍿 Popcornmeter  Ⓜ️ Metacritic  🟢 Letterboxd")

                    if "Title" in up_sort_mode:
                        display_movies = sorted(
                            display_movies,
                            key=lambda m: clean_movie_title(
                                st.session_state.registry['movies'].get(m['master_code'], {}).get('title') or m.get('title', '')
                            ).lower()
                        )
                    else:
                        def _get_upcoming_date_key(m):
                            sorted_dates, first_dt = parse_and_sort_dates(m.get('scheduled_dates', []))
                            m['scheduled_dates'] = sorted_dates
                            title = clean_movie_title(
                                st.session_state.registry['movies'].get(m['master_code'], {}).get('title') or m.get('title', '')
                            ).lower()
                            return (first_dt, title)
                        display_movies = sorted(display_movies, key=_get_upcoming_date_key)

                    cols_per_row = 3
                    for row_idx in range(0, len(display_movies), cols_per_row):
                        row_movies = display_movies[row_idx:row_idx + cols_per_row]
                        row_cols = st.columns(cols_per_row)
                        for col_idx, f_movie in enumerate(row_movies):
                            m_code = f_movie['master_code']
                            meta = st.session_state.registry['movies'].get(m_code, {})
                            badges, badges_type = get_rating_badges(m_code)
                            display_title = meta.get('title') or f_movie.get('title', 'Unknown')
                            if sel_prefix != "ALL" and ":" in display_title:
                                display_title = display_title.split(":", 1)[1].strip()

                            poster_url = meta.get('poster')
                            has_poster = is_poster_valid(poster_url)
                            regal_url = get_movie_regal_url(m_code, display_title)
                            badges_line = format_badges_and_regal_link(badges, regal_url, "Regal ↗")
                            badges_row = f'<div><small>{badges_line}</small></div>' if badges_line else ""
                            unlimited_tag = " 🎫 " if is_unlimited_excluded(m_code, set()) else ""

                            sorted_dates, _ = parse_and_sort_dates(f_movie.get('scheduled_dates', []))
                            dates_str = ", ".join(sorted_dates)

                            with row_cols[col_idx]:
                                with st.container(border=True): 
                                    img_html = f'<img src="{poster_url}" alt="" style="width:50px; min-width:50px; height:75px; object-fit:cover; border-radius:4px; box-shadow:0 1px 3px rgba(0,0,0,0.2);" onerror="this.style.display=\'none\';" />' if has_poster else ""
                                    card_html = (
                                        f'<div style="display:flex; gap:10px; align-items:flex-start; margin-bottom:6px;">'
                                        f'{img_html}'
                                        f'<div style="flex:1; min-width:0;">'
                                        f'<div style="font-weight:700; font-size:0.95rem; line-height:1.2;">{display_title} <span style="font-size:0.85rem; font-weight:normal; color:grey;">({meta.get("rating", "NR")})</span>{unlimited_tag}</div>'
                                        f'{badges_row}'
                                        f'<div style="font-size:0.75rem; color:grey; margin-top:2px;">⏱️ {meta.get("duration", 0)} min • 🎥 {meta.get("studio", "Unknown")}</div>'
                                        f'</div>'
                                        f'</div>'
                                    )
                                    st.markdown(card_html, unsafe_allow_html=True)
                                    st.markdown(f"<small style='color:#e67e22;'>🗓️ <b>Dates:</b> {dates_str}</small>", unsafe_allow_html=True)
            else:
                st.warning("No upcoming movies listed for this theater.")
                          
    elif nav_tab == "🎬 Movie Explorer":
        sync_query_param("targets", None)
        st.subheader("🎬 Movie Explorer")

        movie_views = ["🎥 Playing Nearby", "🌍 Playing Nationwide"]
        m_sub_nav_key = f"movie_sub_nav_{t_key}"
        if st.session_state.get(m_sub_nav_key) not in movie_views:
            raw_m_view = (st.query_params.get("mview") or st.query_params.get("view", "")).lower()
            st.session_state[m_sub_nav_key] = MOVIE_VIEW_MAP.get(raw_m_view, "🎥 Playing Nearby")

        movie_sub_nav = st.radio(
            "Movie Sub Navigation",
            movie_views,
            horizontal=True,
            label_visibility="collapsed",
            key=m_sub_nav_key
        )
        sync_query_param("mview", MOVIE_VIEW_REV_MAP.get(movie_sub_nav, "nearby"))
        sync_query_param("tview", None)
        sync_query_param("view", None)

        st.markdown("""
            <style>
            /* Target both standard buttons and buttons with tooltips */
            div.stButton > button, 
            div[data-testid="stTooltipHoverTarget"] > button {
                width: 100% !important;
                height: 44px !important;
                border-radius: 8px !important;
                background-color: rgba(151, 166, 195, 0.1) !important;
                border: 1px solid rgba(151, 166, 195, 0.2) !important;
                transition: all 0.2s ease-in-out !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                padding: 4px 8px !important;
            }

            /* Hover effect for both types */
            div.stButton > button:hover, 
            div[data-testid="stTooltipHoverTarget"] > button:hover {
                background-color: rgba(151, 166, 195, 0.2) !important;
                border-color: #ff4b4b !important;
            }

            /* Target the text inside the button */
            div.stButton > button div p,
            div[data-testid="stTooltipHoverTarget"] > button div p {
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
                font-size: 0.82rem !important;
                font-weight: 600 !important;
                color: var(--text-color) !important;
                margin: 0 !important;
            }
            </style>
        """, unsafe_allow_html=True)

        nearby_data = st.session_state.app_config.get('nearby_map', {}).get(tc, [])
        allowed_codes = {tc} | {nt['code'] for nt in nearby_data}
        movie_meta = st.session_state.registry['movies']
        
        if movie_sub_nav == "🎥 Playing Nearby":
            all_dates_schedule = []
            for date_key in st.session_state.registry['schedule']:
                all_dates_schedule.extend(st.session_state.registry['schedule'][date_key])
            nearby_weekly_schedule = [s for s in all_dates_schedule if s['TheaterCode'] in allowed_codes]
            playing_today_codes = {
                s['master_code'] for s in nearby_weekly_schedule 
                if s['Showtime'].date() == q_date
            }

            theater_info = {tc: {"name": t_item['name'], "dist": 0, "time": 0}}
            
            for nt in nearby_data:
                n_code = nt['code']
                theater_info[n_code] = {
                    "name": master_name_map.get(n_code, f"Theater {n_code}"), 
                    "dist": nt.get('miles', 0), 
                    "time": nt.get('mins', 0)
                }

            movie_list_data = []
            seen_mc = set()
            for s in nearby_weekly_schedule:
                mc = s['master_code']
                if mc not in seen_mc:
                    meta = movie_meta.get(mc, {})
                    # Add the pin emoji if the movie is playing today
                    prefix = "📌 " if mc in playing_today_codes else ""
                    movie_list_data.append({
                        "code": mc, 
                        "title": meta.get('title', 'Unknown'), 
                        "label": f"{prefix}{meta.get('title')} ({meta.get('rating')})",
                        "playing_today": mc in playing_today_codes
                    })
                    seen_mc.add(mc)
            movie_list_data.sort(key=lambda x: x['title'])

            st.info(f"Showing {len(movie_list_data)} movies playing at **{t_item['name']}** and nearby theaters this week. 📌 indicates showtimes available on **{q_date.strftime('%b %d')}**.")

            st.markdown("###### 🍿 Select a Movie")

            nearby_codes = {m['code'] for m in movie_list_data}
            raw_movie_param = st.query_params.get("movie")
            if st.session_state.get("selected_movie_code") not in nearby_codes:
                if raw_movie_param and raw_movie_param in nearby_codes:
                    st.session_state.selected_movie_code = raw_movie_param
                elif movie_list_data:
                    st.session_state.selected_movie_code = movie_list_data[0]['code']
                else:
                    st.session_state.selected_movie_code = None

            with st.container(height=200, border=True):
                cols_per_row = 2
                for i in range(0, len(movie_list_data), cols_per_row):
                    row_cols = st.columns(cols_per_row)
                    for j, m_entry in enumerate(movie_list_data[i : i + cols_per_row]):
                        m_code = m_entry['code']
                        is_selected = (m_code == st.session_state.selected_movie_code)
                        label = f"✅ {m_entry['label']}" if is_selected else m_entry['label']
                        if row_cols[j].button(label, key=f"grid_{m_code}", help=m_entry['label'], use_container_width=True):
                            st.session_state.selected_movie_code = m_code
                            sync_query_param("movie", m_code)
                            st.rerun()

            sel_code = st.session_state.selected_movie_code
            if sel_code:
                sync_query_param("movie", sel_code)
            if sel_code:
                m_full_week_data = [s for s in nearby_weekly_schedule if s['master_code'] == sel_code]
                m_today_data = [s for s in m_full_week_data if s['Showtime'].date() == q_date]
                meta = movie_meta.get(sel_code, {})
                new_tag = " | 🔴 New Release" if meta.get('is_new') else ""
                unlimited_tag = "| 🎫 No Unlimited" if (m_today_data and is_unlimited_excluded(sel_code, m_today_data[0]['raw_attrs'])) else ""

                render_movie_header_card(sel_code, meta.get('title'), extra_tags=f"{new_tag} {unlimited_tag}")

                if not m_today_data:
                    st.warning(f"⚠️ **{meta.get('title')}** has no showtimes on **{q_date.strftime('%A, %b %d')}**.")
                    available_dates = sorted(list(set(s['Showtime'].date() for s in m_full_week_data)))
                    if available_dates:
                        st.caption("📅 **Scheduled Dates:**")
                        sel_avail = st.pills(
                            "Available Dates",
                            options=available_dates,
                            format_func=lambda d: d.strftime("%a, %b %d"),
                            key=f"m_avail_{sel_code}_{tc}",
                            label_visibility="collapsed"
                        )
                        if sel_avail and sel_avail != q_date:
                            d_iso = sel_avail.strftime("%Y-%m-%d")
                            sync_query_param("date", d_iso)
                            st.rerun()
                else:
                    with st.expander("🔍 Advanced Filters", expanded=False):
                        f_col1, f_col2, f_col3 = st.columns(3)
                        with f_col1:
                            m_formats = sorted(list(set(s['ScreenType'] for s in m_today_data)))
                            f_fmt = st.multiselect("Format", options=m_formats, placeholder="All")
                        with f_col2:
                            t_ranges = {"8AM-12N": (8, 12), "12N-4PM": (12, 16), "4PM-8PM": (16, 20), "8PM-12M": (20, 24)}
                            f_win = st.multiselect("Time Window", options=list(t_ranges.keys()))
                        with f_col3:
                            all_m_attrs = set(a for s in m_today_data for a in s['raw_attrs'])
                            f_extra = st.multiselect("Attributes", options=sorted(list(all_m_attrs - set(m_formats))))
                            f_hide = st.checkbox("Hide past shows", value=True)

                    filtered_m = [s for s in m_today_data if 
                            (not f_fmt or s['ScreenType'] in f_fmt) and
                            (not f_win or any(t_ranges[w][0] <= s['Showtime'].hour < t_ranges[w][1] for w in f_win)) and
                            (not f_extra or set(f_extra).issubset(s['raw_attrs'])) and
                            (not f_hide or (s['Showtime'] > current_local_time + timedelta(minutes=30) if q_date == current_local_time.date() else True))]

                    if not filtered_m:
                        st.warning(f"No showtimes found for **{meta.get('title')}** on {q_date.strftime('%b %d')} matching your filters.")
                        if f_hide and q_date == current_local_time.date():
                            st.info("Remaining shows for today may have already passed. Try unchecking **'Hide Past Shows'** in the filters above or selecting a different date.")
                    else:
                        fmts_to_show = sorted(list(set(s['ScreenType'] for s in filtered_m)))
                        
                        for fmt in fmts_to_show:
                            fmt_shows = [s for s in filtered_m if s['ScreenType'] == fmt]
                            
                            with st.expander(f"✨ {fmt}", expanded=True):
                                t_codes = sorted(list(set(s['TheaterCode'] for s in fmt_shows)), 
                                                key=lambda x: theater_info.get(x, {}).get('time', 999))
                                cols = st.columns(2)
                                for idx, curr_tc in enumerate(t_codes):
                                    with cols[idx % 2]:
                                        t_shows = sorted([s for s in fmt_shows if s['TheaterCode'] == curr_tc], key=lambda x: x['Showtime'])
                                        info = theater_info.get(curr_tc, {"name": f"Theater {curr_tc}", "dist": 0, "time": 0})
                                        
                                        is_primary = (curr_tc == t_item['theater_code'])
                                        t_icon = "📍" if is_primary else "🚗"
                                        dist_txt = "(Current)" if is_primary else f"({info['time']}m / {info['dist']}mi)"
                                        
                                        st.markdown(f"**{t_icon} {info['name']}** <small style='color:grey'>{dist_txt}</small>", unsafe_allow_html=True)
                                        
                                        playing_on_dates = []
                                        for date_key, day_flat in st.session_state.registry['schedule'].items():
                                            if any(s['master_code'] == sel_code and s['TheaterCode'] == curr_tc for s in day_flat):
                                                playing_on_dates.append(date_key)
                                        
                                        t_common = set.intersection(*(s['raw_attrs'] for s in t_shows)) if t_shows else set()
                                        common_attribs = sorted(t_common - {fmt})
                                        st.markdown(f"<p style='color:grey; font-size:0.8rem; margin-top:-10px;'>({', '.join(common_attribs) if common_attribs else ''})</p>", unsafe_allow_html=True)

                                        row_items = []
                                        for s in t_shows:
                                            t_str = s['Showtime'].strftime('%I:%M %p')
                                            delta_attribs = get_attr_diff(s['raw_attrs'], t_common)
                                            
                                            is_past = (q_date == current_local_time.date() and s['Showtime'] < current_local_time)
                                            if is_past:
                                                final_time = f"<del>{t_str}</del>" 
                                                meta_text = f" <small style='color:grey'><del>(Audi {s['Auditorium']}) {delta_attribs}</del></small>"
                                            else:    
                                                final_time = f"**{t_str}**"
                                                meta_text = f" <small style='color:grey'>(Audi {s['Auditorium']}) {delta_attribs}</small>"

                                            row_items.append(f"{final_time}{meta_text}")
                                        
                                        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{' | '.join(row_items)}", unsafe_allow_html=True)

                                        if playing_on_dates:
                                            unique_dates = sorted([datetime.strptime(d, "%m-%d-%Y") for d in set(playing_on_dates)])
                                            date_strs = ", ".join([d.strftime("%b %d") for d in unique_dates])
                                            st.markdown(f"<p style='font-size: 0.8rem; color: #e67e22; margin-top: -5px;'>🗓️ <b>Scheduled Dates:</b> {date_strs}</p>", unsafe_allow_html=True)

                                        st.divider()
        elif movie_sub_nav == "🌍 Playing Nationwide":
            local_weekly_codes = set()
            for date_key in st.session_state.registry['schedule']:
                for s in st.session_state.registry['schedule'][date_key]:
                    if s['TheaterCode'] in allowed_codes:
                        local_weekly_codes.add(s['master_code'])
            nationwide_data = get_nationwide_active_movies(status_context)
            nationwide_codes = set(nationwide_data.keys())
            elsewhere_only_codes = nationwide_codes - local_weekly_codes

            if not nationwide_codes:
                st.success("No exclusive 'Elsewhere' movies found today.")
            else:
                missing_meta = [c for c in elsewhere_only_codes if c not in movie_meta]
                if missing_meta:
                    st.session_state.registry['movies'].update(get_db_metadata((missing_meta)))

                elsewhere_display_list = []
                for m_code in nationwide_codes:
                    meta = st.session_state.registry['movies'].get(m_code, {})
                    count = nationwide_data.get(m_code, 0)
                    
                    prefix = "📍 " if m_code in local_weekly_codes else ""
                    
                    elsewhere_display_list.append({
                        "code": m_code, 
                        "title": f"{prefix}{meta.get('title', f'Movie {m_code}')}",
                        "count": count
                    })
                
                elsewhere_display_list.sort(key=lambda x: x['title'].replace("📍 ", ""))
                
                st.info(f"Showing **{len(elsewhere_display_list)}** movies playing across the US this week. 📍 indicates availability at theaters within your nearby radius.")

                raw_movie_param = st.query_params.get("movie")
                if st.session_state.get("selected_movie_code") not in nationwide_codes:
                    if raw_movie_param and raw_movie_param in nationwide_codes:
                        st.session_state.selected_movie_code = raw_movie_param
                    elif elsewhere_display_list:
                        st.session_state.selected_movie_code = elsewhere_display_list[0]['code']
                    else:
                        st.session_state.selected_movie_code = None

                with st.container(height=200, border=True):
                    cols_per_row = 2
                    for i in range(0, len(elsewhere_display_list), cols_per_row):
                        row_cols = st.columns(cols_per_row)
                        for j, m_entry in enumerate(elsewhere_display_list[i : i + cols_per_row]):
                            m_code = m_entry['code']
                            label = f"{m_entry['title']} ({m_entry['count']})"
                            is_sel = (m_code == st.session_state.selected_movie_code)
                            if row_cols[j].button(f"✅ {label}" if is_sel else f"{label}", 
                                                  key=f"else_btn_{m_code}", help=label, use_container_width=True):
                                st.session_state.selected_movie_code = m_code
                                sync_query_param("movie", m_code)
                                st.rerun()

                sel_code = st.session_state.selected_movie_code
                if sel_code:
                    sync_query_param("movie", sel_code)
                    render_movie_header_card(sel_code)
                    
                    target_theaters = get_theaters_playing_movie(sel_code, set(), status_context)
                    
                    if target_theaters:
                        state_map = {}
                        for t in theaters:
                            t_code = t['theater_code']
                            if t_code in target_theaters:
                                st_code = t.get('state', 'Other')
                                st_full_name = STATE_NAMES.get(st_code, st_code)
                                if st_full_name not in state_map: 
                                    state_map[st_full_name] = []
                                state_map[st_full_name  ].append(f"{t['name']} ({t['city']})")
                        
                        sorted_states = sorted(state_map.keys())
                        cols_per_row = 3
                        for row_idx in range(0, len(sorted_states), cols_per_row):
                            row_states = sorted_states[row_idx:row_idx + cols_per_row]
                            row_cols = st.columns(cols_per_row)
                            for col_idx, state in enumerate(row_states):
                                with row_cols[col_idx]:
                                    with st.expander(f"📍 {state} ({len(state_map[state])})"):
                                        for t_str in sorted(state_map[state]):
                                            st.markdown(f"<small>{t_str}</small>", unsafe_allow_html=True)

    elif nav_tab == "🗓️ Smart Scheduler":
        sync_query_param("view", None)
        sync_query_param("tview", None)
        sync_query_param("mview", None)
        sync_query_param("movie", None)
        st.subheader("🗓️ Smart Scheduler")
        st.info(f"Scheduling: **{t_item['name']}** and nearby theaters on **{q_date.strftime('%A, %b %d')}**")

        # --- Shared Itinerary Snapshot Viewer ---
        plan_token = st.query_params.get("plan")
        if plan_token:
            shared_plan = decode_plan_token(plan_token)
            if shared_plan and "shows" in shared_plan:
                with st.container(border=True):
                    st.markdown("### 🌟 Shared Marathon Itinerary")
                    p_label = shared_plan.get("label", "Marathon Plan")
                    p_date = shared_plan.get("date", "")
                    st.caption(f"Viewing snapshot: **{p_label}** for **{p_date}**")

                    deserialized_shows = [deserialize_showing(s) for s in shared_plan.get("shows", [])]
                    shows_by_day = {}
                    for s in deserialized_shows:
                        d_k = s['Showtime'].strftime('%m-%d-%Y')
                        shows_by_day.setdefault(d_k, []).append(s)

                    if len(shows_by_day) > 1:
                        for d_k in sorted(shows_by_day.keys(), key=lambda x: datetime.strptime(x, '%m-%d-%Y')):
                            day_shows = shows_by_day[d_k]
                            d_disp = datetime.strptime(d_k, '%m-%d-%Y').strftime('%A, %b %d')
                            st.markdown(f"##### 📅 {d_disp}")
                            for idx, s in enumerate(day_shows):
                                t_name = s.get('TheaterName') or cluster_theaters.get(s['TheaterCode'], f"Regal {s['TheaterCode']}")
                                start_t = s['Showtime'].strftime('%I:%M %p')
                                end_t = (s['Showtime'] + timedelta(minutes=s['Duration'])).strftime('%I:%M %p')
                                st.write(f"🕒 **{start_t} - {end_t}**: {s['Title']} (**{s['ScreenType']}**) @ {t_name} (Audi {s['Auditorium']})")
                                if idx < len(day_shows) - 1:
                                    nxt_s = day_shows[idx + 1]
                                    gap = int((nxt_s['Showtime'] - (s['Showtime'] + timedelta(minutes=s['Duration']))).total_seconds() / 60)
                                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;<small style='color:grey'>Gap: {gap} mins</small>", unsafe_allow_html=True)
                    else:
                        for idx, s in enumerate(deserialized_shows):
                            t_name = s.get('TheaterName') or cluster_theaters.get(s['TheaterCode'], f"Regal {s['TheaterCode']}")
                            start_t = s['Showtime'].strftime('%I:%M %p')
                            end_t = (s['Showtime'] + timedelta(minutes=s['Duration'])).strftime('%I:%M %p')
                            st.write(f"🕒 **{start_t} - {end_t}**: {s['Title']} (**{s['ScreenType']}**) @ {t_name} (Audi {s['Auditorium']})")
                            if idx < len(deserialized_shows) - 1:
                                nxt_s = deserialized_shows[idx + 1]
                                gap = int((nxt_s['Showtime'] - (s['Showtime'] + timedelta(minutes=s['Duration']))).total_seconds() / 60)
                                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;<small style='color:grey'>Gap: {gap} mins</small>", unsafe_allow_html=True)
                    
                    st.divider()
                    col_sh1, col_sh2, col_sh3 = st.columns([1, 1, 1])
                    with col_sh1:
                        st.download_button(
                            "📅 Download ICS",
                            generate_ics(deserialized_shows),
                            file_name=f"shared_marathon_{p_date}.ics",
                            mime="text/calendar",
                            key="dl_shared_plan_ics"
                        )
                    with col_sh2:
                        if st.button("🚀 Load into My Scheduler", key="btn_load_shared_plan"):
                            plan_primary_code = shared_plan.get("primary_code") or t_item['theater_code']
                            cfg = shared_plan.get("cfg", {})

                            # 1. Update Active Theater and reset selectbox state
                            st.session_state.active_theater_code = plan_primary_code
                            st.session_state.change_theater_mode = False
                            sync_query_param("theater", plan_primary_code)
                            if "temp_theater_select" in st.session_state:
                                del st.session_state.temp_theater_select
                            match_t = next((t for t in st.session_state.app_config.get('theaters', []) if t['theater_code'] == plan_primary_code), None)
                            if match_t:
                                st.session_state.theater = match_t

                            # 2. Update Active Date if provided
                            p_date_str = shared_plan.get("date")
                            if p_date_str:
                                try:
                                    d_obj = datetime.strptime(p_date_str, "%m-%d-%Y").date()
                                    st.session_state.selected_date = d_obj
                                    sync_query_param("date", d_obj.strftime("%Y-%m-%d"))
                                except Exception:
                                    pass

                            # 3. Clean existing target selections to avoid multi-day leakage
                            for k in list(st.session_state.keys()):
                                if k.startswith("target_theaters_") or k.startswith("target_days_") or k.startswith("target_movies_"):
                                    del st.session_state[k]

                            # Seed Target Theaters & Days
                            t_list = cfg.get("theaters") or list(set(s['TheaterCode'] for s in deserialized_shows if s.get('TheaterCode')) | {plan_primary_code})
                            st.session_state[f"target_theaters_{plan_primary_code}"] = t_list

                            d_list = cfg.get("days") or ([p_date_str] if p_date_str else [d_str])
                            st.session_state[f"target_days_{plan_primary_code}"] = d_list

                            # 4. Seed Target Movies
                            m_codes = cfg.get("movies") or [s['master_code'] for s in deserialized_shows if s.get('master_code')]
                            targets_k = f"target_movies_{plan_primary_code}"
                            st.session_state[targets_k] = m_codes
                            sync_query_param("targets", ",".join(m_codes))
                            sync_query_param("sched_theaters", None)
                            sync_query_param("sched_days", None)

                            # 5. Hydrate Movie Metadata into catalog & registry so labels format immediately
                            if 'global_movie_catalog' not in st.session_state:
                                st.session_state.global_movie_catalog = {}
                            if 'registry' not in st.session_state:
                                st.session_state.registry = {'movies': {}}
                            elif 'movies' not in st.session_state.registry:
                                st.session_state.registry['movies'] = {}

                            for s in deserialized_shows:
                                mc = s.get('master_code')
                                if mc:
                                    title_clean = s.get('Title') or f"Movie {mc}"
                                    meta_entry = {
                                        'title': title_clean,
                                        'rating': s.get('Rating', 'NR'),
                                        'duration': s.get('Duration', 120),
                                        'is_new': False
                                    }
                                    if mc not in st.session_state.global_movie_catalog:
                                        st.session_state.global_movie_catalog[mc] = meta_entry
                                    else:
                                        st.session_state.global_movie_catalog[mc]['title'] = title_clean
                                    if mc not in st.session_state.registry['movies']:
                                        st.session_state.registry['movies'][mc] = meta_entry
                                    else:
                                        st.session_state.registry['movies'][mc]['title'] = title_clean

                            # 6. Seed Filter Preferences
                            if "start" in cfg and cfg["start"]:
                                st.session_state[f"sched_start_{plan_primary_code}"] = cfg["start"]
                            if "end" in cfg and cfg["end"]:
                                st.session_state[f"sched_end_{plan_primary_code}"] = cfg["end"]
                            if "buff" in cfg and cfg["buff"] is not None:
                                st.session_state[f"sched_buff_{plan_primary_code}"] = int(cfg["buff"])
                            if "g_cap" in cfg and cfg["g_cap"] is not None:
                                st.session_state[f"sched_gcap_{plan_primary_code}"] = int(cfg["g_cap"])
                            if "unlimited" in cfg and cfg["unlimited"] is not None:
                                st.session_state[f"sched_unlimited_{plan_primary_code}"] = bool(cfg["unlimited"])
                            if "fudge" in cfg and cfg["fudge"] is not None:
                                st.session_state[f"sched_fudge_{plan_primary_code}"] = bool(cfg["fudge"])
                            if "b_after" in cfg:
                                st.session_state[f"sched_bafter_{plan_primary_code}"] = cfg["b_after"]
                            if "b_val" in cfg and cfg["b_val"] is not None:
                                st.session_state[f"sched_bval_{plan_primary_code}"] = int(cfg["b_val"])
                            if "fmts" in cfg and cfg["fmts"]:
                                st.session_state[f"target_formats_{plan_primary_code}"] = list(cfg["fmts"])
                            if "strat" in cfg and cfg["strat"]:
                                st.session_state[f"sched_strat_{plan_primary_code}"] = cfg["strat"]
                            if "max_day" in cfg and cfg["max_day"] is not None:
                                st.session_state[f"sched_maxday_{plan_primary_code}"] = int(cfg["max_day"])
                                st.session_state[f"sched_prev_n_{plan_primary_code}"] = -1

                            sync_query_param("plan", None)
                            st.rerun()
                    with col_sh3:
                        if st.button("✕ Dismiss Shared View", key="btn_close_shared_plan"):
                            sync_query_param("plan", None)
                            st.rerun()

        primary_code = t_item['theater_code']
        with st.expander("⚙️ Parameters", expanded=True):
            t_opts = list(cluster_theaters.keys())
            cached_dates = sorted(list(st.session_state.registry['loaded_dates']))

            # Support deep-linked sched_theaters and sched_days
            t_theaters_key = f"target_theaters_{t_item['theater_code']}"
            if t_theaters_key not in st.session_state:
                raw_st = st.query_params.get("sched_theaters")
                if raw_st:
                    valid_theaters = [tc.strip() for tc in raw_st.split(",") if tc.strip() in t_opts]
                    st.session_state[t_theaters_key] = valid_theaters if valid_theaters else [t_item['theater_code']]
                else:
                    st.session_state[t_theaters_key] = [t_item['theater_code']]

            t_days_key = f"target_days_{t_item['theater_code']}"
            if t_days_key not in st.session_state:
                raw_sd = st.query_params.get("sched_days")
                if raw_sd:
                    valid_days = [d.strip() for d in raw_sd.split(",") if d.strip() in cached_dates]
                    st.session_state[t_days_key] = valid_days if valid_days else [d_str]
                else:
                    st.session_state[t_days_key] = [d_str]

            r1_c1, r1_c2 = st.columns(2)
            
            with r1_c1:
                target_theaters = st.multiselect(
                    "1\\. Select Theaters (Ordered by Preference)", 
                    options=t_opts,
                    default=None,
                    format_func=lambda x: cluster_theaters.get(x),
                    key=t_theaters_key
                )

                target_days = st.multiselect(
                    "2\\. Select Dates", 
                    options=cached_dates,
                    format_func=lambda x: datetime.strptime(x, "%m-%d-%Y").strftime("%b %d"),
                    default=None,
                    key=t_days_key
                )
            
            with r1_c2:
                targets_key = f"target_movies_{t_item['theater_code']}"
                if targets_key not in st.session_state:
                    raw_targets_param = st.query_params.get("targets")
                    if raw_targets_param:
                        req_codes = [c.strip() for c in raw_targets_param.split(",") if c.strip()]
                        if req_codes:
                            st.session_state[targets_key] = req_codes

                selected_targets = st.session_state.get(targets_key, [])

                global_reactive_codes = set()
                for target_day_key in target_days:
                    day_flat = st.session_state.registry['schedule'].get(target_day_key, [])
                    for s in day_flat:
                        if s['TheaterCode'] in target_theaters:
                            global_reactive_codes.add(s['master_code'])
                
                # Ensure all selected target movies are in options so Streamlit multiselect calls format_func on them
                all_display_codes = set(global_reactive_codes) | set(selected_targets)
                sorted_codes = sorted(
                    list(all_display_codes), 
                    key=lambda x: (
                        get_movie_group_info(x)[0], 
                        st.session_state.global_movie_catalog.get(x, {}).get('title', '').lower()
                    )
                )

                target_movies = st.multiselect(
                    "3\\. Select Movies (Ordered by Preference)", 
                    options=sorted_codes,
                    default=None,
                    format_func=format_movie_label_grouped,
                    key=targets_key,
                    help = "🔴 New Release  🎬 Regular Release  🎫 Unlimited Excluded"
                )
                if target_movies:
                    sync_query_param("targets", ",".join(target_movies))
                else:
                    sync_query_param("targets", None)

                n_movies = len(target_movies)

                available_formats = sorted(list(set(
                    s['ScreenType'] for s in all_flat_data 
                    if s['master_code'] in target_movies and s['TheaterCode'] in target_theaters
                    ))) if target_movies else sorted(list(set(
                    s['ScreenType'] for s in all_flat_data 
                    if s['TheaterCode'] in target_theaters)))
                
                formats_key = f"target_formats_{t_item['theater_code']}"
                all_format_options = sorted(list(set(available_formats) | set(st.session_state.get(formats_key, []))))
                target_formats = st.multiselect(
                    "4\\. Preferred Formats", 
                    options=all_format_options, 
                    placeholder="All",
                    key=formats_key
                )
            
            time_opts = ["Any Time"] + get_time_options()
            c1, c2, c3 = st.columns(3)
            with c1: 
                k_start = f"sched_start_{primary_code}"
                if k_start not in st.session_state or st.session_state[k_start] not in time_opts:
                    st.session_state[k_start] = "Any Time"
                sel_start = st.selectbox("Earliest Start", options=time_opts, key=k_start)

                k_end = f"sched_end_{primary_code}"
                if k_end not in st.session_state or st.session_state[k_end] not in time_opts:
                    st.session_state[k_end] = "Any Time"
                sel_end = st.selectbox("Latest End", options=time_opts, key=k_end)

                t_start = dt_time(0, 0) if sel_start == "Any Time" else datetime.strptime(sel_start, "%H:%M").time()
                t_end = dt_time(23, 59) if sel_end == "Any Time" else datetime.strptime(sel_end, "%H:%M").time()

                break_opts = [None] + list(range(1, n_movies)) if n_movies > 1 else [None]
                k_bafter = f"sched_bafter_{primary_code}"
                if k_bafter not in st.session_state or st.session_state[k_bafter] not in break_opts:
                    st.session_state[k_bafter] = None
                b_after = st.selectbox("Long break after movie #", options=break_opts, key=k_bafter, help="Set a longer break than after a specific movie.")
            with c2: 
                k_buff = f"sched_buff_{primary_code}"
                if k_buff not in st.session_state:
                    st.session_state[k_buff] = 15
                buff = st.slider("Buffer (min)", 0, 60, key=k_buff, help="Set the minimum gap between two movies")

                k_gcap = f"sched_gcap_{primary_code}"
                if k_gcap not in st.session_state:
                    st.session_state[k_gcap] = 120
                g_cap = st.slider("Max Gap (min)", 30, 240, key=k_gcap, help="Set the maximum gap between two movies")

                k_bval = f"sched_bval_{primary_code}"
                if k_bval not in st.session_state:
                    st.session_state[k_bval] = 60
                b_val = st.slider("Break duration (min)", 30, 120, key=k_bval) if b_after else 0
            with c3: 
                k_unlimited = f"sched_unlimited_{primary_code}"
                if k_unlimited not in st.session_state:
                    st.session_state[k_unlimited] = True
                unlimited = st.checkbox("Regal Unlimited Rule (90-min gap)", key=k_unlimited, help="Apply a minimum gap of 90-min between showtimes.")

                k_fudge = f"sched_fudge_{primary_code}"
                if k_fudge not in st.session_state:
                    st.session_state[k_fudge] = False
                fudge = st.checkbox("Fudge Factor (5-min overlap)", key=k_fudge, help="Allow a 5 min overlap between showtimes if no better schedule possible.")

                max_option = n_movies if n_movies > 1 else 1
                if len(target_days) > 1:
                    k_maxday = f"sched_maxday_{primary_code}"
                    k_prev_n = f"sched_prev_n_{primary_code}"
                    prev_n = st.session_state.get(k_prev_n)

                    if (
                        k_maxday not in st.session_state 
                        or prev_n is None 
                        or st.session_state.get(k_maxday) == prev_n 
                        or st.session_state[k_maxday] > max_option 
                        or st.session_state[k_maxday] < 1
                    ):
                        st.session_state[k_maxday] = max_option
                    st.session_state[k_prev_n] = max_option
                    max_per_day = st.number_input("Max Movies per Day", min_value=1, max_value=max_option, key=k_maxday)

                    strat_opts = ["Minimize Days", "Maximize Compactness"]
                    k_strat = f"sched_strat_{primary_code}"
                    if k_strat not in st.session_state or st.session_state[k_strat] not in strat_opts:
                        st.session_state[k_strat] = "Minimize Days"
                    strategy = st.selectbox(
                        "Optimization Strategy", 
                        options=strat_opts, 
                        key=k_strat,
                        help = "Minimize Days will pack your selected movies into the fewest number of trips possible. Maximize Compactness prioritizes the most efficient schedules with the shortest gaps and minimal travel, even if spread across more days.") 
                else:
                    max_per_day = n_movies
                    strategy = "Minimize Days" 

        with st.container(border=True):
            enable_anchor = st.checkbox("📍 Include a Booked (Anchor) Show", value=False)
            anchor_show = None

            if enable_anchor:
                a_col1, a_col2, a_col3, a_col4  = st.columns(4)
                with a_col1:
                    a_theater = st.selectbox("Anchor Theater", options=target_theaters, format_func=lambda x: cluster_theaters.get(x))
                with a_col2:
                    a_day = st.selectbox("Anchor Day", options=target_days, format_func=lambda x: datetime.strptime(x, "%m-%d-%Y").strftime("%b %d"))
                with a_col3:
                    day_flat_anchor = st.session_state.registry['schedule'].get(a_day, [])
                    valid_anchor_codes = sorted(
                        [mc for mc in target_movies if any(s['master_code'] == mc and s['TheaterCode'] == a_theater for s in day_flat_anchor)],
                        key=lambda x: st.session_state.global_movie_catalog.get(x, {}).get('title', '')
                    )
                    
                    a_movie_code = st.selectbox("Anchor Movie", options=valid_anchor_codes, format_func=format_movie_label_grouped)
                with a_col4:
                    a_showtimes = []
                    if day_flat_anchor and a_movie_code:
                        a_day_obj = datetime.strptime(a_day, '%m-%d-%Y').date()
                        a_showtimes = [s for s in day_flat_anchor if 
                                    s['master_code'] == a_movie_code and 
                                    s['TheaterCode'] == a_theater and
                                    (current_local_time <= s['Showtime'] + timedelta(minutes=30) 
                                        if a_day_obj == current_local_time.date() else True)]
                    selected_anchor = st.selectbox("Anchor Showtime", options=a_showtimes, format_func=lambda x: f"{x['Showtime'].strftime('%I:%M %p')} ({x['ScreenType']})")
                    anchor_show = selected_anchor

        all_screenings = []
        for d_key in target_days:
            all_screenings.extend(st.session_state.registry['schedule'].get(d_key, []))
        
        filtered_pool = [s for s in all_screenings if s['TheaterCode'] in target_theaters]

        if st.button("🚀 Generate Itineraries"):
            if len(target_movies) < 2:
                st.error("Please select at least 2 movies.")
            else:
                sched_date_str = target_days[0] if target_days else d_str
                sched_date_obj = datetime.strptime(sched_date_str, "%m-%d-%Y").date()
                params = {
                    'start': t_start, 'end': t_end, 'buffer': buff, 'gap_cap': g_cap, 
                    'unlimited': unlimited, 'fudge': fudge, 'break_after': b_after, 
                    'long_buffer': b_val, 'formats': target_formats, 'theaters': target_theaters,
                    'primary_code': t_item['theater_code'],
                    'strategy': strategy if len(target_days) > 1 else "Minimize Days",
                    'max_per_day': max_per_day if len(target_days) > 1 else n_movies
                }
                
                if len(target_days) > 1:
                    with st.spinner(f"🧠 Optimizing schedules across {len(target_theaters)} theater(s) and {len(target_days)} date(s)... Please wait."):
                        multi_itinerary = find_multi_day_itineraries(target_movies, target_days, params, drive_map, anchor_show)
            
                    if not multi_itinerary:
                        st.error("Could not find a valid multi-day schedule for these movies. Consider expanding selections and broadening filters.")
                    else:
                        if enable_anchor and anchor_show:
                            t_name = cluster_theaters.get(anchor_show['TheaterCode'], "Selected Theater")
                            d_fmt = anchor_show['Showtime'].strftime('%b %d')
                            t_fmt = anchor_show['Showtime'].strftime('%I:%M %p')
                            
                            st.info(f"📍 **Anchored Search active**: Generated schedule is  guaranteed to include "
                                    f"**{anchor_show['Title']}** at **{t_fmt}** @**{t_name}** on **{d_fmt}**.")
                            
                        st.success(f"🗓️ Multi-Day Plan Generated: {len(multi_itinerary)} days used.")
                        
                        total_movies = sum(len(p) for p in multi_itinerary.values())
                        total_hops = sum(calculate_path_score(p, params['primary_code'])['hops'] for p in multi_itinerary.values())
                        sorted_plan_days = sorted(multi_itinerary.keys(), key=lambda x: datetime.strptime(x, '%m-%d-%Y'))
                        scheduled_codes = [s['master_code'] for p in multi_itinerary.values() for s in p]
                        unscheduled = [m for m in target_movies if m not in scheduled_codes]

                        st.markdown(f"### 🏆 Schedule Summary")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Total Movies", f"{total_movies} / {len(target_movies)}")
                        c2.metric("Total Days", len(multi_itinerary))
                        c3.metric("Total Hops", total_hops)

                        timeline_html = "<div style='display: flex; gap: 5px; margin-bottom: 20px;'>"
                        for d_str in sorted_plan_days:
                            color = "#2ecc71" if d_str in multi_itinerary else "#ecf0f1"
                            label = datetime.strptime(d_str, '%m-%d-%Y').strftime('%A, %b %d')
                            count = len(multi_itinerary[d_str]) if d_str in multi_itinerary else 0
                            timeline_html += f"""
                            <div style='background-color: {color}; padding: 10px; border-radius: 5px; text-align: center; flex: 1; border: 1px solid #bdc3c7; min-width: 80px;'>
                                <div style='font-size: 0.7rem; color: #7f8c8d;'>{label}</div>
                                <div style='font-weight: bold;'>{count} 🎬</div>
                            </div>"""
                        timeline_html += "</div>"
                        st.markdown(timeline_html, unsafe_allow_html=True)
                        
                        full_col1, full_col2 = st.columns([1, 1])
                        with full_col1:
                            st.download_button(
                                "📅 Download ICS for Full Schedule", 
                                generate_batch_ics(multi_itinerary, cluster_theaters), 
                                file_name=f"movies_multi_full.ics", 
                                mime="text/calendar", 
                                key=f"dl_multi_full",
                                use_container_width=True
                            )
                        with full_col2:
                            with st.popover("🔗 Share Full Marathon", use_container_width=True):
                                base_url = "https://regalpro.streamlit.app" if IS_CLOUD else "http://localhost:8501"
                                all_multi_shows = [serialize_showing(s, cluster_theaters) for d in sorted_plan_days for s in multi_itinerary[d]]
                                full_label = f"Full Marathon ({len(sorted_plan_days)} Days, {total_movies} Movies)"
                                full_date_range = f"{sorted_plan_days[0]} to {sorted_plan_days[-1]}"
                                full_plan_data = {
                                    "v": 1,
                                    "label": full_label,
                                    "date": full_date_range,
                                    "primary_code": params['primary_code'],
                                    "shows": all_multi_shows,
                                    "cfg": {
                                        "theaters": target_theaters,
                                        "days": target_days,
                                        "movies": target_movies,
                                        "start": sel_start if sel_start != "Any Time" else None,
                                        "end": sel_end if sel_end != "Any Time" else None,
                                        "buff": buff,
                                        "g_cap": g_cap,
                                        "unlimited": unlimited,
                                        "fudge": fudge,
                                        "b_after": b_after,
                                        "b_val": b_val,
                                        "fmts": target_formats,
                                        "strat": strategy,
                                        "max_day": max_per_day
                                    }
                                }
                                full_tok = encode_plan_token(full_plan_data)
                                full_p_url = build_plan_share_url(base_url, full_tok, params['primary_code'], sorted_plan_days[0])
                                full_p_txt = format_multi_day_summary(multi_itinerary, full_label, cluster_theaters)
                                st.markdown("##### 🔗 Full Marathon Share Link")
                                st.text_input("Link", value=full_p_url, key=f"inp_full_multi_url_{full_tok[:8]}", label_visibility="collapsed")
                                st.markdown("##### 💬 Text Summary (for SMS/Discord)")
                                st.text_area("Text", value=f"{full_p_txt}\n\n🔗 View & Add to Calendar:\n{full_p_url}", height=140, key=f"inp_full_multi_txt_{full_tok[:8]}", label_visibility="collapsed")

                        if not unscheduled:
                            st.success("✅ **Perfect Match!** All selected movies fit the schedule.")
                        else:
                            m_names = [st.session_state.global_movie_catalog.get(m, {}).get('title', m) for m in unscheduled]
                            st.warning(f"⚠️ **Incomplete Schedule:** {', '.join(m_names)} could not be fitted.")

                        for d_str in sorted_plan_days:
                            path = multi_itinerary[d_str]
                            d_display = datetime.strptime(d_str, '%m-%d-%Y').strftime('%A, %b %d')
                            stats = calculate_path_score(path, params['primary_code'])
                            
                            with st.container(border=True):
                                st.markdown(f"#### 📅 {d_display}")
                                st.markdown(f"🎬 **{len(path)} Movies** | 🚗 {stats['hops']} Hops | ⏱️ {stats['gap']}m Total Gap")
                                
                                for idx, s in enumerate(path):
                                    t_name = cluster_theaters.get(s['TheaterCode'], "Unknown")
                                    start_t = s['Showtime'].strftime('%I:%M %p')
                                    end_t = (s['Showtime'] + timedelta(minutes=s['Duration'])).strftime('%I:%M %p')
                                    
                                    st.write(f"🕒 **{start_t} - {end_t}**: {s['Title']} (**{s['ScreenType']}**) @{t_name}")
                                    
                                    if idx < len(path) - 1:
                                        next_s = path[idx + 1]
                                        gap = int((next_s['Showtime'] - (s['Showtime'] + timedelta(minutes=s['Duration']))).total_seconds() / 60)
                                        drive_info = ""
                                        if s['TheaterCode'] != next_s['TheaterCode']:
                                            nb_code = next_s['TheaterCode'] if next_s['TheaterCode'] != params['primary_code'] else s['TheaterCode']
                                            d_stats = drive_map.get(nb_code, {'time': 20, 'dist': 0})
                                            drive_info = f". Drive: {d_stats['time']} mins ({d_stats['dist']} mi)"
                                        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;<small style='color:grey'>Gap: {gap} mins{drive_info}</small>", unsafe_allow_html=True)
                                
                                st.divider()
                                m_c1, m_c2 = st.columns([1, 1])
                                with m_c1:
                                    st.download_button("📅 Download ICS for Day", 
                                                    generate_ics(path), 
                                                    file_name=f"movies_{d_str}.ics", 
                                                    mime="text/calendar", 
                                                    key=f"dl_multi_{d_str}")
                                with m_c2:
                                    with st.popover("🔗 Share Day Plan", use_container_width=True):
                                        base_url = "https://regalpro.streamlit.app" if IS_CLOUD else "http://localhost:8501"
                                        day_primary = path[0]['TheaterCode'] if path and path[0].get('TheaterCode') else params['primary_code']
                                        day_theaters = list(dict.fromkeys([s['TheaterCode'] for s in path if s.get('TheaterCode')]))
                                        if day_primary not in day_theaters:
                                            day_theaters.insert(0, day_primary)
                                        day_movies = [s['master_code'] for s in path]
                                        plan_data = {
                                            "v": 1,
                                            "label": f"Day Plan ({d_display})",
                                            "date": d_str,
                                            "primary_code": day_primary,
                                            "shows": [serialize_showing(s, cluster_theaters) for s in path],
                                            "cfg": {
                                                "theaters": day_theaters,
                                                "days": [d_str],
                                                "movies": day_movies,
                                                "start": sel_start if sel_start != "Any Time" else None,
                                                "end": sel_end if sel_end != "Any Time" else None,
                                                "buff": buff,
                                                "g_cap": g_cap,
                                                "unlimited": unlimited,
                                                "fudge": fudge,
                                                "b_after": b_after,
                                                "b_val": b_val,
                                                "fmts": target_formats,
                                                "strat": "Minimize Days",
                                                "max_day": len(path)
                                            }
                                        }
                                        tok = encode_plan_token(plan_data)
                                        p_url = build_plan_share_url(base_url, tok, day_primary, d_str)
                                        p_txt = format_single_day_summary(path, f"Day Plan ({d_display})", d_display, cluster_theaters)
                                        st.markdown("##### 🔗 Direct Share Link")
                                        st.text_input("Link", value=p_url, key=f"inp_murl_{d_str}_{tok[:8]}", label_visibility="collapsed")
                                        st.markdown("##### 💬 Text Summary (for SMS/Discord)")
                                        st.text_area("Text", value=f"{p_txt}\n\n🔗 View & Add to Calendar:\n{p_url}", height=140, key=f"inp_mtxt_{d_str}_{tok[:8]}", label_visibility="collapsed")

                        if unscheduled:
                            with st.expander("⚠️ Unscheduled Movies"):
                                for m in unscheduled:
                                    m_title = st.session_state.global_movie_catalog.get(m, {}).get('title', m)
                                    st.write(f"❌ **{m_title}**: Could not fit into the selected time windows or theater constraints.")
                
                else:
                    with st.spinner("🧠 Optimizing schedules across theaters and showtimes... Please wait."):
                        if enable_anchor and anchor_show:
                            paths = run_anchored_search(anchor_show, target_movies, sched_date_str, params, drive_map)
                        else:
                            paths = find_itineraries([], target_movies, filtered_pool, params, sched_date_obj, drive_map)
                
                    if not paths: 
                        st.error("No valid schedules found. Consider expanding selections and broadening filters.")
                    else:
                        if enable_anchor and anchor_show:
                            t_name = cluster_theaters.get(anchor_show['TheaterCode'], "Selected Theater")
                            d_fmt = anchor_show['Showtime'].strftime('%b %d')
                            t_fmt = anchor_show['Showtime'].strftime('%I:%M %p')
                            
                            st.info(f"📍 **Anchored Search active**: All paths below are guaranteed to include "
                                    f"**{anchor_show['Title']}** at **{t_fmt}** @**{t_name}** on **{d_fmt}**.")
                            
                        processed_paths = []
                        for p_raw in paths:
                            stats = calculate_path_score(p_raw, primary_code)
                            
                            p_id = "-".join([f"{s['master_code']}{s['Showtime'].timestamp()}" for s in p_raw])
                            processed_paths.append({
                                'path': p_raw, 
                                'count': stats['count'], 
                                'hops': stats['hops'], 
                                'miles': stats['miles'], 
                                'score': stats['score'], 
                                'total_gap': stats['gap'], 
                                'id': p_id
                            })

                        ranked_pool = sorted(processed_paths, key=lambda x: (-x['score'], x['total_gap']))

                        final_selections = []
                        seen_ids = set()

                        def add_selection(entry, label):
                            if entry and entry['id'] not in seen_ids:
                                final_selections.append((entry, label))
                                seen_ids.add(entry['id'])
                                return True
                            return False

                        add_selection(ranked_pool[0], "Smart Marathon (Best Efficiency)")

                        abs_mar = sorted(processed_paths, key=lambda x: (-x['count'], -x['score']))[0]
                        add_selection(abs_mar, "Absolute Marathon (Max Movies)")

                        st_p = sorted([pp for pp in processed_paths if pp['hops'] == 0], key=lambda x: (-x['score']))
                        if st_p: add_selection(st_p[0], "Single-Theater Max (Zero Hops)")

                        if len(target_movies) >= 2:
                            top_two = set(target_movies[:2])
                            p_mov = sorted([pp for pp in processed_paths if top_two.issubset(set(s['master_code'] for s in pp['path']))], key=lambda x: (-x['score']))
                            if p_mov: add_selection(p_mov[0], "Priority Movie Match (#1 & #2)")

                        for entry in ranked_pool:
                            if len(final_selections) >= 5: break
                            add_selection(entry, "Alternative Optimized Path")

                        for i, (entry, label) in enumerate(final_selections[:5]):
                            path, count, hops, miles = entry['path'], entry['count'], entry['hops'], entry['miles']
                            with st.container(border=True):
                                st.markdown(f"#### Option {i+1}: {count} Movies")
                                st.markdown(f"🏆 **{label}** | 🚗 {hops} Hops ({round(miles, 1)} mi travel)", unsafe_allow_html=True)
                                
                                for idx, s in enumerate(path):
                                    t_name = cluster_theaters.get(s['TheaterCode'], "Unknown")
                                    start_t, end_t = s['Showtime'], s['Showtime'] + timedelta(minutes=s['Duration'])
                                    st.write(f"🕒 **{start_t.strftime('%I:%M %p')} - {end_t.strftime('%I:%M %p')}**: {s['Title']} (**{s['ScreenType']}**) @{t_name}")
                                    
                                    if idx < len(path) - 1:
                                        next_s = path[idx + 1]
                                        gap = int((next_s['Showtime'] - end_t).total_seconds() / 60)
                                        drive_info = ""
                                        if s['TheaterCode'] != next_s['TheaterCode']:
                                            nb_code = next_s['TheaterCode'] if next_s['TheaterCode'] != primary_code else s['TheaterCode']
                                            d_stats = drive_map.get(nb_code, {'time': 20, 'dist': 0})
                                            drive_info = f". Drive: {d_stats['time']} mins ({d_stats['dist']} mi)"
                                        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;<small style='color:grey'>Gap: {gap} mins{drive_info}</small>", unsafe_allow_html=True)
                                
                                st.divider()
                                dl_col1, dl_col2 = st.columns([1, 1])
                                with dl_col1:
                                    st.download_button("📅 Download ICS", 
                                                    generate_ics(path), 
                                                    file_name=f"movies_{q_date}.ics", 
                                                    mime="text/calendar", 
                                                    key=f"dl_{i}_{entry['id']}")
                                with dl_col2:
                                    with st.popover("🔗 Share Plan", use_container_width=True):
                                        base_url = "https://regalpro.streamlit.app" if IS_CLOUD else "http://localhost:8501"
                                        opt_primary = path[0]['TheaterCode'] if path and path[0].get('TheaterCode') else primary_code
                                        opt_theaters = list(dict.fromkeys([s['TheaterCode'] for s in path if s.get('TheaterCode')]))
                                        if opt_primary not in opt_theaters:
                                            opt_theaters.insert(0, opt_primary)
                                        opt_movies = [s['master_code'] for s in path]
                                        plan_data = {
                                            "v": 1,
                                            "label": label,
                                            "date": q_date.strftime('%m-%d-%Y'),
                                            "primary_code": opt_primary,
                                            "shows": [serialize_showing(s, cluster_theaters) for s in path],
                                            "cfg": {
                                                "theaters": opt_theaters,
                                                "days": [q_date.strftime('%m-%d-%Y')],
                                                "movies": opt_movies,
                                                "start": sel_start if sel_start != "Any Time" else None,
                                                "end": sel_end if sel_end != "Any Time" else None,
                                                "buff": buff,
                                                "g_cap": g_cap,
                                                "unlimited": unlimited,
                                                "fudge": fudge,
                                                "b_after": b_after,
                                                "b_val": b_val,
                                                "fmts": target_formats,
                                                "strat": "Minimize Days",
                                                "max_day": len(path)
                                            }
                                        }
                                        tok = encode_plan_token(plan_data)
                                        p_url = build_plan_share_url(base_url, tok, opt_primary, q_date.strftime('%m-%d-%Y'))
                                        p_txt = format_single_day_summary(path, label, q_date.strftime('%A, %b %d'), cluster_theaters)
                                        st.markdown("##### 🔗 Direct Share Link")
                                        st.text_input("Link", value=p_url, key=f"inp_purl_{i}_{entry['id']}_{tok[:8]}", label_visibility="collapsed")
                                        st.markdown("##### 💬 Text Summary (for SMS/Discord)")
                                        st.text_area("Text", value=f"{p_txt}\n\n🔗 View & Add to Calendar:\n{p_url}", height=140, key=f"inp_ptxt_{i}_{entry['id']}_{tok[:8]}", label_visibility="collapsed")
                                
                                if count < len(target_movies):
                                    missing = [c for c in target_movies if c not in [s['master_code'] for s in path]]
                                    with st.expander("⚠️ Why were some movies left out?"):
                                        report_pool = st.session_state.registry['schedule'].get(sched_date_str, [])
                                        report = get_conflict_report(path, missing, report_pool, params, anchor_show)
                                        for line in report: st.write(line)
else:
    # --- Main-Canvas Search Hero (Mobile-First Landing & Switcher) ---
    st.markdown("### 🔍 Find Your Regal Theater")
    st.caption("Search across 400+ Regal locations nationwide by city, state, zip code, or theater name.")

    if st.session_state.get("change_theater_mode") and st.session_state.get("theater"):
        prev_t = st.session_state.theater
        if st.button(f"🔙 Cancel & Return to {prev_t['name']}", key="btn_cancel_change"):
            st.session_state.change_theater_mode = False
            st.rerun()

    st.markdown("###### 📍 Popular Locations")
    quick_picks = [
        ("🗽 New York", "1320"),
        ("🌴 Los Angeles", "1484"),
        ("🍑 Atlanta", "1346"),
        ("🏖️ Orlando", "1938"),
        ("🏎️ Indianapolis", "1299"),
        ("⛰️ Knoxville", "0690")
    ]
    qp_cols = st.columns(6)
    for idx, (qp_name, qp_code) in enumerate(quick_picks):
        with qp_cols[idx]:
            if st.button(qp_name, key=f"qp_{qp_code}", use_container_width=True):
                st.session_state.active_theater_code = qp_code
                st.session_state.change_theater_mode = False
                sync_query_param("theater", qp_code)
                if "temp_theater_select" in st.session_state:
                    del st.session_state.temp_theater_select
                st.rerun()

    st.divider()

    hero_query = st.text_input(
        "Search Theaters",
        placeholder="Type a city (e.g. Dallas), state (e.g. FL), zip code (e.g. 46201), or theater name...",
        key="hero_search_input"
    )

    hero_matches = []
    if hero_query:
        q_clean = hero_query.strip().lower()
        if q_clean.isdigit() and len(q_clean) == 5:
            nomi = pgeocode.Nominatim('us')
            z_data = nomi.query_postal_code(q_clean)
            if not math.isnan(z_data['latitude']):
                for t in theaters:
                    d = calculate_haversine_distance(z_data['latitude'], z_data['longitude'], t['latitude'], t['longitude'])
                    if d <= 50:
                        hero_matches.append((t, d))
                hero_matches.sort(key=lambda x: x[1])
        elif q_clean.isdigit() and len(q_clean) <= 4:
            padded = q_clean.zfill(4)
            hero_matches = [t for t in theaters if t['theater_code'] in (q_clean, padded)]
        else:
            for t in theaters:
                score = 0
                t_name = t['name'].lower()
                t_city = t.get('city', '').lower()
                t_state = t.get('state', '').lower()
                t_addr = str(t.get('address') or '').lower()

                if q_clean in t_name:
                    score += 10
                if q_clean in t_city:
                    score += 5
                if q_clean in t_addr:
                    score += 3
                if q_clean == t_state:
                    score += 5
                elif q_clean in STATE_NAMES.get(t.get('state', ''), '').lower():
                    score += 4

                if score > 0:
                    hero_matches.append((t, score))
            hero_matches.sort(key=lambda x: -x[1])
            hero_matches = [m[0] for m in hero_matches]

        if not hero_matches:
            st.warning(f"No theaters found matching '{hero_query}'. Try searching by city, state, or zip code.")
        else:
            st.markdown(f"**Found {len(hero_matches)} theaters:**")
            for item in hero_matches[:12]:
                t = item[0] if isinstance(item, tuple) else item
                dist_str = f" • *{round(item[1], 1)} miles away*" if isinstance(item, tuple) and not isinstance(item[1], int) else ""
                
                with st.container(border=True):
                    hc1, hc2 = st.columns([4, 1])
                    with hc1:
                        st.markdown(f"**{t['name']}**")
                        st.caption(f"📍 {t.get('address', '')}, {t.get('city', '')}, {t.get('state', '')} {dist_str}")
                    with hc2:
                        if st.button("Select", key=f"hero_btn_{t['theater_code']}", use_container_width=True):
                            st.session_state.active_theater_code = t['theater_code']
                            st.session_state.change_theater_mode = False
                            sync_query_param("theater", t['theater_code'])
                            if "temp_theater_select" in st.session_state:
                                del st.session_state.temp_theater_select
                            st.rerun()
    else:
        if location and not math.isnan(latitude) and not math.isnan(longitude):
            geo_matches = []
            for t in theaters:
                d = calculate_haversine_distance(latitude, longitude, t['latitude'], t['longitude'])
                if d <= 50:
                    geo_matches.append((t, d))
            geo_matches.sort(key=lambda x: x[1])
            if geo_matches:
                st.markdown(f"**📍 Theaters Near Your Current Location:**")
                for t, d in geo_matches[:6]:
                    with st.container(border=True):
                        hc1, hc2 = st.columns([4, 1])
                        with hc1:
                            st.markdown(f"**{t['name']}**")
                            st.caption(f"📍 {t.get('address', '')}, {t.get('city', '')}, {t.get('state', '')} • *{round(d, 1)} miles away*")
                        with hc2:
                            if st.button("Select", key=f"hero_geo_btn_{t['theater_code']}", use_container_width=True):
                                st.session_state.active_theater_code = t['theater_code']
                                st.session_state.change_theater_mode = False
                                sync_query_param("theater", t['theater_code'])
                                if "temp_theater_select" in st.session_state:
                                    del st.session_state.temp_theater_select
                                st.rerun()