# v2.6 RC 
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
from datetime import datetime, timedelta, timezone, time as dt_time
from curl_cffi import requests as c_requests
from streamlit_js_eval import get_geolocation, set_cookie, get_cookie
from supabase import create_client, Client

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
            flex-direction: row; gap: 2rem; 
            background-color: rgba(151, 166, 195, 0.15);
            padding: 10px 20px; border-radius: 10px; 
            margin-bottom: 20px; border: 1px solid rgba(151, 166, 195, 0.2);
    }
    .stRadio [data-testid="stMarkdownContainer"] p { 
            font-size: 1.1rem; font-weight: 600; 
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

# --- Utility Functions ---

def init_registry():
    if "registry" not in st.session_state:
        st.session_state.registry = {
            "theaters": {},  # tc -> { future: [] }
            "schedule": {},  # d_str -> [ screenings ]
            "movies": {},    # mc -> { metadata }
            "loaded_dates": set(),
            "active_ckey": None,
            "attr_map": {}
        }
    st.session_state.global_movie_catalog = st.session_state.registry['movies']

@st.cache_resource
def init_supabase() -> Client:
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])

def get_db_metadata(codes):
    if not codes: return {}
    try:
        res = db.table("movie_metadata").select("*").in_("master_code", list(codes)).execute()
        return {row['master_code']: row for row in res.data} if res.data else {}
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
        db_meta = get_db_metadata(missing_mc)
        translated_meta = {}
        placeholder_updates = {}
        
        for mc in missing_mc:
            if not mc in db_meta:
                gaps.add(mc)
                placeholder_updates[mc] = {
                    'title': f"New Release ({mc})",
                    'rating': "NR",
                    'duration': 120,
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
                        datetime.now().date() - timedelta(days=14) <= 
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
    except Exception:
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

def load_monthly_specials():
    try:
        if os.path.exists(SPECIALS_FILE):
            with open(SPECIALS_FILE, "r") as f:
                return json.load(f).get("series_list", [])
        return []
    except Exception: return []

def get_nationwide_active_movies():
    try:
        res = db.table("active_nationwide_movies_cache").select("master_code, theater_count").execute()
        return {row['master_code']: row['theater_count'] for row in res.data} if res.data else {}
    except Exception:
        return {}

def get_theaters_playing_movie(m_code, local_codes):
    try:
        res = db.rpc("get_theaters_by_movie", {"target_m_code": m_code}).execute()
        if not res.data: return set()
        all_found = {row['t_code'] for row in res.data}
        return all_found - set(local_codes)
    except Exception as e:
        st.write(e)
        return set()

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
        
        log_msg = "🚀 Fetching data using Scraping API..."
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
    flat_list = []
    catalog = st.session_state.registry['movies']

    for m in data.get('movies', []):
        m_code = m.get('MasterMovieCode')
        if m_code:
            catalog[m_code] = catalog.get(m_code, {'rating': 'NR', 'duration': 0})
            catalog[m_code].update({
                'title': m.get('Title', 'Unknown'),
                'is_new': (lambda d: d and datetime.now().date() - timedelta(days=14) <= datetime.strptime(d[:10], "%Y-%m-%d").date() <= datetime.now().date() + timedelta(days=14))(m.get('OpeningDate')),
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
                if not m_meta.get('title'):
                    st.write(t_code)
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
            dates = []
            for d in fs.get('dates', []):
                date_val = d.get('date') or d.get('calendarShowDate') # Fallback for different API versions
                if date_val:
                    try:
                        dates.append(datetime.strptime(date_val, "%m-%d-%Y").strftime("%b %d"))
                    except: continue
            if dates:
                if primary_t not in theater_future_map: theater_future_map[primary_t] = []
                theater_future_map[primary_t].append({
                    'master_code': m_code, 'title': fs.get('title'),
                    'scheduled_dates': sorted(list(set(dates))), 'rating': 'NR', 'duration': 0
                })

    return flat_list, attr_map, catalog, theater_future_map

def check_metadata_gaps(flat_list):
    gaps = {}
    for s in flat_list:
        if s['Duration'] == 0:
            if s['master_code'] not in gaps:
                gaps[s['master_code']] = s['TheaterCode']
    return gaps

def is_unlimited_excluded(m_code, attr_set):
    meta = st.session_state.global_movie_catalog.get(m_code, {})
    return meta.get('studio') == "Regal Cinemedia" or "No Pass/SS" in attr_set or "No Passes or Super Savers" in attr_set

def get_rating_badges(m_code):
    meta = st.session_state.global_movie_catalog.get(m_code, {})
    badges = []
    badges_type = []
    ratings_map = [
        ('imdb_score', '⭐', 'imdb_url', 'IMDB'),
        ('rt_critics', '🍅', 'rt_url','Tomatometer'),
        ('rt_users','🍿','rt_url','Popcornmeter'),
        ('metacritic', 'Ⓜ️', 'metacritic_url','Metacritic'),
        ('letterboxd', '🟢', 'letterboxd_url','Letterboxd')
    ]
    
    for score_key, icon, url_key,badge_type in ratings_map:
        score = meta.get(score_key)
        url = meta.get(url_key)
        if score:
            display_score = round(float(score), 1) if score_key == 'letterboxd' else score
            if url:
                badges.append(f"{icon} [{display_score}]({url})")
            else:
                badges.append(f"{icon} {display_score}")
            badges_type.append(f"{icon} {badge_type}")
        else:
            if url:
                badges.append(f"{icon} [N/A]({url})")
                badges_type.append(f"{icon} {badge_type}")
    return " | ".join(badges), " ".join(badges_type) if badges else ""

def get_movie_group_info(m_code):
    meta = st.session_state.global_movie_catalog.get(m_code, {})
    
    m_attrs = set()
    for s in all_flat_data:
        if s['master_code'] == m_code:
            m_attrs.update(s.get('raw_attrs', []))
    
    is_ineligible = is_unlimited_excluded(m_code, m_attrs)
    is_new = meta.get('is_new', False)
    
    if is_ineligible:
        return 2, "🎫 "
    elif is_new:
        return 0, "🔴 "
    return 1, "🎬 "
                
def format_movie_label_grouped(m_code):
    meta = st.session_state.global_movie_catalog.get(m_code, {})
    title = meta.get('title', f"Unknown ({m_code})")
    _, prefix = get_movie_group_info(m_code)
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

def generate_ics(path, theater_name):
    ics_lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Regal Pro//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for s in path:
        start_t = s['Showtime'].strftime("%Y%m%dT%H%M%S")
        end_t = (s['Showtime'] + timedelta(minutes=s['Duration'])).strftime("%Y%m%dT%H%M%S")
        ics_lines.extend(["BEGIN:VEVENT", f"DTSTART:{start_t}", f"DTEND:{end_t}", f"SUMMARY:{s['Title']} ({s['ScreenType']})", f"LOCATION:{theater_name} - Audi {s['Auditorium']}", "END:VEVENT"])
    ics_lines.append("END:VCALENDAR")
    return "\n".join(ics_lines)

def find_itineraries(current_path, remaining_codes, screenings, p, selected_date, drive_map):
    if len(current_path) >= p.get('max_per_day', 99):
        return []
    
    valid_paths = []
    window_start = datetime.combine(selected_date, p['start'])
    window_end = datetime.combine(selected_date, p['end'])
    
    if window_end <= window_start: 
        window_end += timedelta(days=1)
    elif p['end'] == dt_time(23, 59): 
        window_end += timedelta(hours=6)

    for m_code in remaining_codes:
        potential_shows = [s for s in screenings if s['master_code'] == m_code and s['TheaterCode'] in p['theaters']]
        
        if selected_date == current_local_time.date():
            cutoff = current_local_time - timedelta(minutes=30)
            potential_shows = [s for s in potential_shows if s['Showtime'] >= cutoff]
        
        if p.get('formats'): 
            potential_shows = [s for s in potential_shows if s['ScreenType'] in p['formats']]
        
        for s in potential_shows:
            show_start = s['Showtime']
            calc_dur = s['Duration'] if s['Duration'] > 0 else 120
            show_end = show_start + timedelta(minutes=calc_dur)
            if show_start < window_start or show_end > window_end: 
                continue
            
            if current_path:
                prev = current_path[-1]
                prev_end = prev['Showtime'] + timedelta(minutes=prev['Duration'])
                if p.get('fudge'): 
                    prev_end -= timedelta(minutes=5)
                
                drive_time = 0
                if s['TheaterCode'] != prev['TheaterCode']:
                    drive_time, _ = get_drive_stats(s['TheaterCode'], prev['TheaterCode'], theaters)
                
                req_buffer = p['long_buffer'] if p['break_after'] == len(current_path) else p['buffer']
                total_min_gap = drive_time + req_buffer
                
                if p['unlimited'] and show_start < prev['Showtime'] + timedelta(minutes=91): 
                    continue
                if show_start < prev_end + timedelta(minutes=total_min_gap): 
                    continue
                if (show_start - prev_end).total_seconds()/60 > p['gap_cap']: 
                    continue

            new_rem = [c for c in remaining_codes if c != m_code]
            sub = find_itineraries(current_path + [s], new_rem, screenings, p, selected_date, drive_map)
            if not sub: 
                valid_paths.append(current_path + [s])
            else: 
                valid_paths.extend(sub)
    
    return valid_paths if valid_paths else ([current_path] if current_path else [])

def find_multi_day_itineraries(target_codes, target_days, params, drive_map, anchor_show=None):
    itinerary_by_day = {}
    remaining_codes = list(target_codes)
    max_per_day = params.get('max_per_day', len(target_codes))

    if anchor_show:
        a_day_str = anchor_show['Showtime'].strftime('%m-%d-%Y')
        anchor_day_options = run_anchored_search(anchor_show, target_codes, a_day_str, params, drive_map)
        
        if anchor_day_options:
            best_a_path = sorted(anchor_day_options, key=lambda x: -calculate_path_score(x, params['primary_code'], drive_map)['score'])[0]
            itinerary_by_day[a_day_str] = best_a_path
            
            for s in best_a_path:
                if s['master_code'] in remaining_codes:
                    remaining_codes.remove(s['master_code'])
    
    sorted_days = sorted([d for d in target_days if d != (anchor_show['Showtime'].strftime('%m-%d-%Y') if anchor_show else None)],
                        key=lambda x: datetime.strptime(x, '%m-%d-%Y'))
    
    if params.get('strategy') == "Minimize Days":
        for i, d_str in enumerate(sorted_days):
            if not remaining_codes: break
            
            d_obj = datetime.strptime(d_str, '%m-%d-%Y').date()
            day_flat = st.session_state.registry['schedule'].get(d_str, [])

            if d_obj == current_local_time.date():
                cutoff = current_local_time - timedelta(minutes=30)
                day_flat = [s for s in day_flat if s['Showtime'] >= cutoff]

            all_paths = find_itineraries([], remaining_codes, day_flat, params, d_obj, drive_map)
            if not all_paths: continue

            candidates = sorted([p for p in all_paths if len(p) <= max_per_day], 
                                key=lambda x: (-len(x), -calculate_path_score(x, params['primary_code'], drive_map)['score']))[:5]

            best_path_for_today = None
            max_future_yield = -1

            if i < len(sorted_days) - 1:
                for cand in candidates:
                    cand_codes = [s['master_code'] for s in cand]
                    mock_remaining = [m for m in remaining_codes if m not in cand_codes]
                    
                    next_day_str = sorted_days[i+1]
                    nd_obj = datetime.strptime(next_day_str, '%m-%d-%Y').date()
                    next_day_flat = st.session_state.registry['schedule'].get(next_day_str, [])
                    if next_day_flat:
                        next_day_paths = find_itineraries([], mock_remaining, next_day_flat, params, nd_obj, drive_map)
                        next_day_yield = max([len(p) for p in next_day_paths if len(p) <= max_per_day]) if next_day_paths else 0
                    else:
                        next_day_yield = 0
                    
                    if (len(cand) + next_day_yield) > max_future_yield:
                        max_future_yield = len(cand) + next_day_yield
                        best_path_for_today = cand
            else:
                best_path_for_today = candidates[0]

            if best_path_for_today:
                itinerary_by_day[d_str] = best_path_for_today
                for s in best_path_for_today:
                    remaining_codes.remove(s['master_code'])
                    
    else: # Maximize Compactness
        global_pool = []
        for d_str in sorted_days:
            d_obj = datetime.strptime(d_str, '%m-%d-%Y').date()
            day_flat = st.session_state.registry['schedule'].get(d_str, [])
            paths = find_itineraries([], remaining_codes, day_flat, params, d_obj, drive_map)
            for p in paths:
                if len(p) <= max_per_day:
                    stats = calculate_path_score(p, params['primary_code'], drive_map)
                    global_pool.append({'date': d_str, 'path': p, 'score': stats['score']})
        
        global_pool.sort(key=lambda x: -x['score'])
        assigned_dates = set()
        for entry in global_pool:
            if not remaining_codes: break
            if entry['date'] in assigned_dates: continue
            
            needed_in_path = [s for s in entry['path'] if s['master_code'] in remaining_codes]
            
            if len(needed_in_path) == len(entry['path']):
                itinerary_by_day[entry['date']] = entry['path']
                assigned_dates.add(entry['date'])
                for s in entry['path']:
                    remaining_codes.remove(s['master_code'])

    return itinerary_by_day

def run_anchored_search(anchor_show, target_codes, day_str, params, drive_map):
    day_flat = st.session_state.registry['schedule'].get(day_str, [])
    d_obj = datetime.strptime(day_str, '%m-%d-%Y').date()

    if d_obj == current_local_time.date():
        cutoff = current_local_time - timedelta(minutes=30)
        day_flat = [s for s in day_flat if s['Showtime'] >= cutoff]
    
    wing_codes = [c for c in target_codes if c != anchor_show['master_code']]
    
    after_paths = find_itineraries([anchor_show], wing_codes, day_flat, params, d_obj, drive_map)
    if not after_paths: after_paths = [[anchor_show]]

    combined_itineraries = []
    for a_path in after_paths:
        before_params = params.copy()
        before_params['max_per_day'] = params['max_per_day'] - len(a_path)
        latest_cutoff = anchor_show['Showtime'] - timedelta(minutes=params['buffer'])
        before_params['end'] = latest_cutoff.time()
        
        raw_before = find_itineraries([], wing_codes, day_flat, before_params, d_obj, drive_map)
        
        valid_before = []
        if not raw_before:
            valid_before = [[]]
        else:
            for b_path in raw_before:
                last_m = b_path[-1]
                last_m_end = last_m['Showtime'] + timedelta(minutes=last_m['Duration'])
                travel = 0
                if last_m['TheaterCode'] != anchor_show['TheaterCode']:
                    nb = last_m['TheaterCode'] if last_m['TheaterCode'] != params['primary_code'] else anchor_show['TheaterCode']
                    travel = drive_map.get(nb, {}).get('time', 20)
                
                gap_to_anchor = int((anchor_show['Showtime'] - last_m_end).total_seconds() / 60)

                if (gap_to_anchor >= (travel + params['buffer'])) and (gap_to_anchor <= params['gap_cap']):
                    valid_before.append(b_path)
        
        if not valid_before: valid_before = [[]]
        
        for b_path in valid_before:
            b_codes = set(s['master_code'] for s in b_path)
            a_codes = set(s['master_code'] for s in a_path)
            if b_codes.intersection(a_codes): continue
            full_path = b_path + a_path
            if len(full_path) <= params['max_per_day']:
                combined_itineraries.append(full_path)
                
    return combined_itineraries

def calculate_path_score(path, primary_code, drive_map):
    movie_count = len(path)
    hops, total_miles, total_gap, total_duration = 0, 0, 0, 0
    
    for i in range(len(path)):
        s = path[i]
        total_duration += s['Duration']
        
        if i < len(path) - 1:
            nxt = path[i+1]
            curr_end = s['Showtime'] + timedelta(minutes=s['Duration'])
            total_gap += int((nxt['Showtime'] - curr_end).total_seconds() / 60)
            
            if s['TheaterCode'] != nxt['TheaterCode']:
                hops += 1
                t_time, t_miles = get_drive_stats(s['TheaterCode'], nxt['TheaterCode'], theaters)
                total_miles += t_miles

    score = (movie_count * 250) - (hops * 40) - (total_miles * 2) - (total_gap * 0.1)
    return {
        'score': score, 'count': movie_count, 'hops': hops, 
        'miles': total_miles, 'gap': total_gap, 'duration': total_duration
    }

def get_drive_stats(code_a, code_b, theaters_list):
    if code_a == code_b: return 0, 0
    
    t_a = next((t for t in theaters_list if t['theater_code'] == code_a), {})
    
    match = next((nt for nt in t_a.get('nearby_theaters', []) if nt['code'] == code_b), None)
    
    if match:
        return match.get('drive_min', 20), match.get('road_miles', 0)
    
    t_b = next((t for t in theaters_list if t['theater_code'] == code_b), {})
    match = next((nt for nt in t_b.get('nearby_theaters', []) if nt['code'] == code_a), None)
    
    if match:
        return match.get('drive_min', 20), match.get('road_miles', 0)
        
    return 20, 5

def get_conflict_report(path, missing_codes, all_screenings, p, anchor_show=None, drive_map={}):
    conflicts = []
    full_path = sorted(path + ([anchor_show] if anchor_show else []), key=lambda x: x['Showtime'])
    
    cutoff = current_local_time - timedelta(minutes=30)

    for m_code in missing_codes:
        meta = st.session_state.global_movie_catalog.get(m_code, {})
        m_title = meta.get('title', f"Unknown ({m_code})")
        m_shows = [s for s in all_screenings if s['master_code'] == m_code and 
                   s['TheaterCode'] in p['theaters'] and
                   (s['Showtime'] >= cutoff if s['Showtime'].date() == current_local_time.date() else True)]
        
        if p.get('formats'): 
            m_shows = [s for s in m_shows if s['ScreenType'] in p['formats']]
        
        if not m_shows:
            conflicts.append(f"❌ **{m_title}**: No screenings match your time/formats/theaters.")
            continue

        any_valid = False
        failure_details = [] 

        for ms in m_shows:
            ms_start = ms['Showtime']
            ms_end = ms_start + timedelta(minutes=ms['Duration'])
            reasons = []

            for ps in full_path:
                ps_start = ps['Showtime']
                ps_end = ps_start + timedelta(minutes=ps['Duration'])
                
                # 1. Overlap Check
                if not (ms_end <= ps_start or ms_start >= ps_end):
                    reasons.append((1, f"Overlaps with **{ps['Title']}** ({ps_start.strftime('%I:%M %p')})."))
                    break

                # 2. Dynamic Gap and Buffer Check
                gap = 0
                is_after = ms_start >= ps_end
                is_before = ms_end <= ps_start

                # Identify if this theater in the path is the immediate neighbor
                is_neighbor = False
                if is_after:
                    gap = int((ms_start - ps_end).total_seconds() / 60)
                    is_neighbor = not any(x['Showtime'] > ps_start and x['Showtime'] < ms_start for x in full_path)
                elif is_before:
                    gap = int((ps_start - ms_end).total_seconds() / 60)
                    is_neighbor = not any(x['Showtime'] > ms_start and x['Showtime'] < ps_start for x in full_path)

                if is_neighbor:
                    # UPDATED: Use Mesh Logic for Conflict Reporting
                    travel_time, _ = get_drive_stats(ms['TheaterCode'], ps['TheaterCode'], theaters)
                    
                    if gap < (travel_time + p['buffer']):
                        dir_str = "after" if is_after else "before"
                        reasons.append((2, f"Buffer violation {dir_str} **{ps['Title']}** (Gap {gap}m, needs {travel_time + p['buffer']}m drive+buffer)."))
                    elif gap > p['gap_cap']:
                        dir_str = "after" if is_after else "before"
                        reasons.append((5, f"Gap {dir_str} **{ps['Title']}** ({gap}m) exceeds Max Gap ({p['gap_cap']}m)."))

            if not reasons:
                any_valid = True
                break
            else:
                reasons.sort() 
                failure_details.append(reasons[0])

        if not any_valid:
            failure_details.sort()
            detail = failure_details[0][1]
            conflicts.append(f"❌ **{m_title}**: {detail}")
            
    return conflicts

def generate_batch_ics(multi_itinerary, theater_name_map):
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
            t_name = theater_name_map.get(s['TheaterCode'], "Regal Theater")
            
            ics_lines.extend([
                "BEGIN:VEVENT", 
                f"DTSTART:{start_t}", 
                f"DTEND:{end_t}", 
                f"SUMMARY:{s['Title']} ({s['ScreenType']})", 
                f"LOCATION:{t_name} - Audi {s['Auditorium']}", 
                "END:VEVENT"
            ])
            
    ics_lines.append("END:VCALENDAR")
    return "\n".join(ics_lines)

def clean_movie_title(title):
    if not title: return ""
    title = re.sub(r'^[A-Z0-9]{3,}:\s*', '', title)
    patterns = [
        r'[:\-\+]*\s*(\d+th\s+)?Anniversary.*',
        r'[:\-\+]*\s*(\d+th\s+)?Anniv\.*',
        r'[:\-\+]*\s*Early\s+Access.*',
        r'[:\-\+]*\s*(Livestream\s+)?Q&A.*',
        r'[:\-\+]*\s*Hong\s+Kong\s+Cinema\s+Classics.*',
        r'[:\-\+]*\s*70mm(\s+Film\s+Reissue)?.*'
    ]
    for p in patterns:
        title = re.sub(p, '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\([^)]*\)', '', title)
    return title.strip()

def sync_movie_metadata():
    all_codes = list(st.session_state.global_movie_catalog.keys())
    if not all_codes: return
    
    db_metadata = get_db_metadata(all_codes)
    for m_code, m_info in db_metadata.items():
        if m_code in st.session_state.global_movie_catalog:
            st.session_state.global_movie_catalog[m_code].update({
                'studio': m_info.get('studio'),
                'imdb_score': m_info.get('imdb_score'),
                'imdb_url': m_info.get('imdb_url'),
                'rt_critics': m_info.get('rt_critics_score'),
                'rt_users': m_info.get('rt_users_score'),
                'rt_url': m_info.get('rt_url'),
                'metacritic': m_info.get('metacritic_score'),
                'metacritic_url': m_info.get('metacritic_url'),
                'letterboxd': m_info.get('letterboxd_score'),
                'letterboxd_url': m_info.get('letterboxd_url')
            })

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
    val = st.session_state.get('initial_url_code', "")
    code_in = st.sidebar.text_input("Theater Code", value=val)
    
    if "initial_url_code" in st.session_state and code_in != st.session_state.initial_url_code:
        del st.session_state.initial_url_code

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

if results:
    opts = {f"{r[0]['name'] if isinstance(r, tuple) else r['name']} - {r[0]['city'] if isinstance(r, tuple) else r['city']}": (r[0] if isinstance(r, tuple) else r) for r in results}    
    
    if "active_theater_code" not in st.session_state:
        st.session_state.active_theater_code = st.query_params.get("theater")
    
    idx = 0
    for i, t in enumerate(opts.values()):
        if t['theater_code'] == st.session_state.active_theater_code: 
            idx = i
            break

    sel_label = st.sidebar.selectbox("Select Theater", options=list(opts.keys()), index=idx)
    selected_theater = opts[sel_label]
    new_code = selected_theater['theater_code']

    if new_code != st.session_state.active_theater_code:
        st.session_state.active_theater_code = new_code
        st.query_params["theater"] = new_code
        st.rerun()

if selected_theater:
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
    q_date = st.sidebar.date_input("Select Date", value=local_today, min_value=local_today, format="MM/DD/YYYY")
    d_str, today_cache_str = q_date.strftime('%m-%d-%Y'), local_today.strftime('%m-%d-%Y')

    t_lon = t_item.get('longitude')
    if t_lon:
        st.session_state.auto_tz_offset = get_offset_from_lon(
            t_lon, t_item.get('state_code'), 
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
            log_msg = f"⚡ Loading 7-Day cluster data from storage..."
            msg.toast(log_msg)
            if status_context: status_context.write(log_msg)
            registry_ingest(raw_payload)
            st.session_state.registry['loaded_dates'] = list(raw_payload.keys())
        else:
            log_msg = f"🌐 Cache miss. Synchronizing 7-Day Data from Regal API..."
            msg.toast(log_msg)  
            if status_context: status_context.write(log_msg)
            target_codes = [tc] + [nt['code'] for nt in t_item.get('nearby_theaters', [])]
            new_payload = {}
            for i in range(7):
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
        log_msg = "🎉 7-Day Sync Complete!"
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
            f_payload = fetch_data(api_url, t_item['name'], status_context if debug_mode else None, msg)
            if f_payload:
                save_future_to_storage("future-shows", f_file, f_payload)
                log_msg = f"✅ Successfully cached future data for {tc} to storage."
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
    st.session_state.update({
        "all_flat_data": all_flat_data,
        "flat_data": [s for s in all_flat_data if s['TheaterCode'] == tc],
        "movie_meta": st.session_state.registry['movies'],
        "future_movies": st.session_state.registry['theaters'].get(tc, {}).get('future', []),
        "drive_map": drive_map
    })


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
        
    st.session_state.update({
        "all_flat_data": all_flat_data,
        "flat_data": flat_data,
        "movie_meta": movie_meta,
        "attr_map": attr_map,
        "future_movies": future_movies
    })

    t_key = t_item['theater_code']
    nav_key = f"nav_tab_{t_key}"

    tabs_list = ["🔎 Theater Explorer", "🎬 Movie Explorer", "🗓️ Smart Scheduler"]
    default_idx = 0
    if "nav_redirect" in st.session_state:
        st.session_state[nav_key] = st.session_state.nav_redirect
        del st.session_state.nav_redirect

    nav_tab = st.radio(
            "Navigation", 
            ["🔎 Theater Explorer", "🎬 Movie Explorer", "🗓️ Smart Scheduler"], 
            horizontal=True, 
            label_visibility="collapsed",
            key=nav_key
        )

    if nav_tab == "🔎 Theater Explorer":
        if print_mode: st.markdown("<style>[data-testid='stSidebar'], [data-testid='stHeader'] {display: none;} .stExpander {border: none !important;}</style>", unsafe_allow_html=True)
        st.subheader("🔎 Theater Explorer")
        st.info(f"Viewing: **{t_item['name']}** on **{q_date.strftime('%A, %b %d')}**")

        sub_nav_key = f"sub_nav_{t_key}"
        if sub_nav_key not in st.session_state:
            st.session_state[sub_nav_key] = "🍿 Now Playing"

        sub_nav = st.radio("Sub Navigation", 
                           ["🍿 Now Playing", "🚗 Playing Nearby", "📅 Upcoming"], 
                           horizontal=True, 
                           label_visibility="collapsed",
                           key=sub_nav_key)
        
        if sub_nav == "🍿 Now Playing":
            st.subheader("🍿 Now Playing")
            st.caption(f"These movies are playing today at {t_item['name']}.")

            with st.expander("🔍 Filters & Sorting", expanded=not print_mode):
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    f_type = st.multiselect("Screen Type", 
                                            options=sorted(list(set(s['ScreenType'] for s in flat_data))), 
                                            placeholder="All",
                                            key=f"f_type_{t_key}")
                    f_rating = st.multiselect("Rating", 
                                              options=sorted(list(set(s['Rating'] for s in flat_data))), 
                                              placeholder="All",
                                              key=f"f_rating_{t_key}")
                with c2:
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
                with c3:
                    t_ranges = {"8AM - 12N": (8, 12), "12N - 4PM": (12, 16), "4PM - 8PM": (16, 20), "8PM - 12M": (20, 24)}
                    f_times = st.multiselect("Time Window", 
                                             options=list(t_ranges.keys()), 
                                             placeholder="All",
                                             key=f"f_times_{t_key}")
                    f_avail = st.checkbox("Hide past shows", value=True, key=f"f_avail_{t_key}")
                    f_new = st.checkbox("New Releases Only", value=False, key=f"f_new_{t_key}")
                with c4:
                    sort_by = st.selectbox("Sort By", 
                                           ["Movie Title", "Showtime", "Auditorium"],
                                           key=f"sort_by_{t_key}")
                    view_mode = st.selectbox("View Mode", 
                                             ["Group by Movie", "Group by Auditorium", "Full Schedule"],
                                             key=f"view_mode_{t_key}")
                    
            filtered = [s for s in flat_data if (
                not f_type or s['ScreenType'] in f_type) and 
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
                    
                    other_t = sorted([cluster_theaters[tc] 
                                    for tc in set(s['TheaterCode'] for s in all_flat_data if s['master_code'] == m_code) 
                                    if tc != t_item['theater_code'] and tc in cluster_theaters])
                    
                    valid_date_objs = [datetime.strptime(date_key, "%m-%d-%Y") 
                        for date_key, day_flat in st.session_state.registry['schedule'].items() 
                        if any(s['master_code'] == m_code for s in day_flat)]
                    
                    scheduled_days = [d.strftime("%b %d") for d in sorted(valid_date_objs)]
                    
                    meta = movie_meta.get(m_code, {})
                    new_tag = "| 🔴 New Release" if meta.get('is_new') else ""
                    unlimited_tag = "| 🎫 No Unlimited" if is_unlimited_excluded(m_code, m_shows[0]['raw_attrs']) else ""
                    badges, badges_type = get_rating_badges(m_code)

                    with st.expander(f"🍿 {title} ({m_shows[0]['Rating']}) | 🕒 {m_shows[0]['Duration']} min {new_tag} {unlimited_tag}", expanded=True):
                        st.markdown(f"<small>{badges}</small>", unsafe_allow_html=True, help=badges_type)
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
                            st.caption(f"🎥 {meta['studio']}")
                        footer_col, link_col = st.columns([4, 1])
                        with footer_col:
                            if scheduled_days:
                                st.markdown(f"<div style='font-size: 0.8rem; color: #e67e22; padding-top: 2px;'>🗓️ <b>Scheduled Dates:</b> {', '.join(scheduled_days)}</div>", unsafe_allow_html=True)
                            if other_t:
                                st.markdown(f"<div style='font-size: 0.8rem; color: #666; padding-top: 5px;'><b>Also Playing at:</b> {', '.join(other_t)}</div>", unsafe_allow_html=True)

                        with link_col:
                            if other_t or scheduled_days:
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
                nearby_cols = st.columns(3)
                for idx, m_code in enumerate(nearby_only_codes):
                    theater_dates = {}
                    for d_str, day_flat in st.session_state.registry['schedule'].items():
                        for s in day_flat:
                            if s['master_code'] == m_code and s['TheaterCode'] in cluster_theaters:
                                t_name = cluster_theaters.get(s['TheaterCode'])
                                if t_name not in theater_dates: theater_dates[t_name] = []
                                if d_str not in theater_dates[t_name]: theater_dates[t_name].append(d_str)
                    
                    m_attrs = set()
                    for s in all_flat_data:
                        if s['master_code'] == m_code:
                            m_attrs.update(s.get('raw_attrs', []))
                    
                    meta = st.session_state.global_movie_catalog.get(m_code, {'title': 'Unknown', 'rating': 'NR', 'duration': 0, 'is_new':False})
                    new_tag = " 🔴" if meta.get('is_new') else ""
                    unlimited_tag = " 🎫" if is_unlimited_excluded(m_code, m_attrs) else ""
                    badges, badges_type = get_rating_badges(m_code)

                    with nearby_cols[idx % 3]:
                        with st.container(border=True):
                            st.markdown(f"**{meta['title']}** ({meta['rating']}) {new_tag} {unlimited_tag}", unsafe_allow_html=True)
                            st.markdown(f"<small>{badges}</small>", unsafe_allow_html=True, help=badges_type)
                            for t_name, dates in theater_dates.items():
                                sorted_date_objs = sorted([datetime.strptime(d, "%m-%d-%Y") for d in dates])
                                date_str = ", ".join([d.strftime("%b %d") for d in sorted_date_objs])
                                st.markdown(f"<p style='font-size: 0.8rem; margin-bottom: 2px;'>📍 <b>{t_name}</b></p>", unsafe_allow_html=True)
                                st.markdown(f"<p style='font-size: 0.8rem; color: #e67e22; margin-top: -5px;'>🗓️ {date_str}</p>", unsafe_allow_html=True)
                            
                            st.caption(f"⏱️ {meta['duration']} min")
                            st.caption(f"🎥 {meta['studio']}")
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
                    st.subheader(subheader)
                    st.caption(f"These movies are scheduled to show at {t_item['name']}.")
                    st.caption("🎫 Unlimited Excluded  ⭐ IMDB  🍅 Tomatometer  🍿 Popcornmeter  Ⓜ️ Metacritic  🟢 Letterboxd")
                    cols = st.columns(3)
                    for i, f_movie in enumerate(display_movies):
                        m_code = f_movie['master_code']
                        meta = st.session_state.registry['movies'].get(m_code, {})
                        badges, badges_type = get_rating_badges(m_code)
                        display_title = meta.get('title')
                        if sel_prefix != "ALL" and ":" in display_title:
                            display_title = display_title.split(":", 1)[1].strip()

                        with cols[i % 3]:
                            with st.container(border=True): 
                                unlimited_tag = " 🎫 " if is_unlimited_excluded(m_code, set()) else ""
                                st.markdown(f"**{display_title}** ({meta.get('rating', 'NR')}) {unlimited_tag}")
                                st.markdown(f"<small>{badges}</small>", unsafe_allow_html=True, help=badges_type)
                                dates_str = ", ".join(f_movie['scheduled_dates'])
                                st.markdown(f"<small style='color:#e67e22;'>🗓️ {dates_str}</small>", unsafe_allow_html=True)
                                st.caption(f"⏱️ {meta.get('duration', 0)} min")
                                st.caption(f"🎥 {meta.get('studio', 'Unknown Studio')}")
            else:
                st.warning("No upcoming movies listed for this theater.")
                          
    elif nav_tab == "🎬 Movie Explorer":
        st.subheader("🎬 Movie Explorer")
        tab_gen, tab_else = st.tabs(["🎥 Playing Nearby", "🌍 Playing Elsewhere"])

        nearby_data = st.session_state.app_config.get('nearby_map', {}).get(tc, [])
        allowed_codes = {tc} | {nt['code'] for nt in nearby_data}
        
        with tab_gen:
            full_cluster_schedule = st.session_state.registry['schedule'].get(d_str, [])
            nearby_schedule = [s for s in full_cluster_schedule if s['TheaterCode'] in allowed_codes]
            movie_meta = st.session_state.registry['movies']

            theater_info = {tc: {"name": t_item['name'], "dist": 0, "time": 0}}
            
            for nt in nearby_data:
                n_code = nt['code']
                theater_info[n_code] = {
                    "name": master_name_map.get(n_code, f"Theater {n_code}"), 
                    "dist": nt.get('miles', 0), 
                    "time": nt.get('mins', 0)
                }

            if nearby_data:
                for nt in nearby_data:
                    n_code = nt['code'] # This matches the key in our new map
                    n_name = master_name_map.get(n_code, f"Theater {n_code}")
                    n_miles = nt.get('miles', 0)
                    n_mins = nt.get('mins', 20)
                    theater_info[n_code] = {
                        "name": n_name, 
                        "dist": n_miles, 
                        "time": n_mins
                    }

            movie_list_data = []
            seen_mc = set()
            for s in nearby_schedule:
                mc = s['master_code']
                if mc not in seen_mc:
                    meta = movie_meta.get(mc, {})
                    movie_list_data.append({
                        "code": mc, 
                        "title": meta.get('title', 'Unknown'), 
                        "label": f"{meta.get('title')} ({meta.get('rating')})"
                    })
                    seen_mc.add(mc)
            movie_list_data.sort(key=lambda x: x['title'])

            st.info(f"Showing {len(movie_list_data)} movies playing at **{t_item['name']}** and nearby theaters on **{q_date.strftime('%A, %b %d')}**")


            st.markdown("###### 🍿 Select a Movie")
            st.markdown("""
                <style>
                /* Target both standard buttons and buttons with tooltips */
                div.stButton > button, 
                div[data-testid="stTooltipHoverTarget"] > button {
                    width: 100% !important;
                    height: 40px !important;
                    border-radius: 6px !important;
                    background-color: rgba(151, 166, 195, 0.1) !important;
                    border: 1px solid rgba(151, 166, 195, 0.2) !important;
                    transition: all 0.2s ease-in-out !important;
                    display: flex !important;
                    align-items: center !important;
                    justify-content: center !important;
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
                    font-size: 0.75rem !important;
                    font-weight: 600 !important;
                    color: var(--text-color) !important;
                    margin: 0 !important;
                }
                </style>
            """, unsafe_allow_html=True)

            if "selected_movie_code" not in st.session_state:
                st.session_state.selected_movie_code = movie_list_data[0]['code'] if movie_list_data else None

            with st.container(height=130, border=True):
                cols_per_row = 5
                for i in range(0, len(movie_list_data), cols_per_row):
                    row_cols = st.columns(cols_per_row)
                    for j, m_entry in enumerate(movie_list_data[i : i + cols_per_row]):
                        m_code = m_entry['code']
                        is_selected = (m_code == st.session_state.selected_movie_code)
                        label = f"✅ {m_entry['label']}" if is_selected else m_entry['label']
                        if row_cols[j].button(label, key=f"grid_{m_code}", help=m_entry['label'], use_container_width=True):
                            st.session_state.selected_movie_code = m_code
                            st.rerun()

            sel_code = st.session_state.selected_movie_code
            if sel_code:
                m_today_data = [s for s in nearby_schedule if s['master_code'] == sel_code]
                if m_today_data:
                    meta = movie_meta.get(sel_code, {})
                    new_tag = " | 🔴 New Release" if meta.get('is_new') else ""
                    unlimited_tag = "| 🎫 No Unlimited" if is_unlimited_excluded(m_code, m_today_data[0]['raw_attrs']) else ""
                    badges, badges_type = get_rating_badges(sel_code)
                    st.markdown(f"## {meta.get('title')}", unsafe_allow_html=True)
                    st.markdown(f"#### <small style='color:grey'>({meta.get('rating', 'NR')} | {meta.get('duration', 0)} min {new_tag} {unlimited_tag}) | {badges} </small>", help=badges_type, unsafe_allow_html=True)
                    
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
                                for idx, tc in enumerate(t_codes):
                                    with cols[idx % 2]:
                                        t_shows = sorted([s for s in fmt_shows if s['TheaterCode'] == tc], key=lambda x: x['Showtime'])
                                        info = theater_info.get(tc, {"name": f"Theater {tc}", "dist": 0, "time": 0})
                                        
                                        is_primary = (tc == t_item['theater_code'])
                                        t_icon = "📍" if is_primary else "🚗"
                                        dist_txt = "(Current)" if is_primary else f"({info['time']}m / {info['dist']}mi)"
                                        
                                        st.markdown(f"**{t_icon} {info['name']}** <small style='color:grey'>{dist_txt}</small>", unsafe_allow_html=True)
                                        
                                        playing_on_dates = []
                                        for date_key, day_flat in st.session_state.registry['schedule'].items():
                                            if any(s['master_code'] == sel_code and s['TheaterCode'] == tc for s in day_flat):
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
                                            date_str = ", ".join([d.strftime("%b %d") for d in unique_dates])
                                            st.markdown(f"<p style='font-size: 0.8rem; color: #e67e22; margin-top: -5px;'>🗓️ <b>Scheduled Dates:</b> {date_str}</p>", unsafe_allow_html=True)

                                        st.divider()
                else:
                    meta = st.session_state.global_movie_catalog.get(sel_code, {})
                    sel_title = meta.get('title', f"Movie {sel_code}")
                    st.warning(f"No showtimes found for **{sel_title}** on {q_date.strftime('%b %d')}.")
                    st.info("Try selecting a different date or theater in the sidebar.")
        with tab_else:
            local_weekly_codes = set()
            for date_key in st.session_state.registry['schedule']:
                for s in st.session_state.registry['schedule'][date_key]:
                    if s['TheaterCode'] in allowed_codes:
                        local_weekly_codes.add(s['master_code'])
            nationwide_data = get_nationwide_active_movies()
            nationwide_codes = set(nationwide_data.keys())
            elsewhere_only_codes = nationwide_codes - local_weekly_codes

            if not nationwide_codes:
                st.success("No exclusive 'Elsewhere' movies found today.")
            else:
                missing_meta = [c for c in elsewhere_only_codes if c not in movie_meta]
                if missing_meta:
                    st.session_state.registry['movies'].update(get_db_metadata(missing_meta))

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
                
                st.info(f"Showing **{len(elsewhere_display_list)}** movies playing across the US. 📍 indicates availability at theaters within your nearby radius.")

                if "sel_elsewhere_code" not in st.session_state or st.session_state.sel_elsewhere_code not in nationwide_codes:
                    st.session_state.sel_elsewhere_code = elsewhere_display_list[0]['code']

                with st.container(height=150, border=True):
                    cols_per_row = 5
                    for i in range(0, len(elsewhere_display_list), cols_per_row):
                        row_cols = st.columns(cols_per_row)
                        for j, m_entry in enumerate(elsewhere_display_list[i : i + cols_per_row]):
                            m_code = m_entry['code']
                            label = f"{m_entry['title']} ({m_entry['count']})"
                            is_sel = (m_code == st.session_state.sel_elsewhere_code)
                            if row_cols[j].button(f"✅ {label}" if is_sel else f"{label}", 
                                                  key=f"else_btn_{m_code}", help=label, use_container_width=True):
                                st.session_state.sel_elsewhere_code = m_code
                                st.rerun()

                sel_code = st.session_state.sel_elsewhere_code
                if sel_code:
                    meta = st.session_state.global_movie_catalog.get(sel_code, {})
                    st.markdown(f"### 🌍 {meta.get('title')} ({meta.get('rating')})")
                    badges, badges_type = get_rating_badges(sel_code)
                    st.markdown(f"<small>{badges}</small>", help=badges_type, unsafe_allow_html=True)
                    
                    target_theaters = get_theaters_playing_movie(st.session_state.sel_elsewhere_code, set())
                    
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
                        s_cols = st.columns(3)
                        for idx, state in enumerate(sorted(sorted_states)):
                            with s_cols[idx % 3]:
                                with st.expander(f"📍 {state} ({len(state_map[state])})"):
                                    for t_str in sorted(state_map[state]):
                                        st.markdown(f"<small>{t_str}</small>", unsafe_allow_html=True)

    elif nav_tab == "🗓️ Smart Scheduler":
        st.subheader("🗓️ Smart Scheduler")
        st.info(f"Scheduling: **{t_item['name']}** and nearby theaters on **{q_date.strftime('%A, %b %d')}**")

        primary_code = t_item['theater_code']
        with st.expander("⚙️ Parameters", expanded=True):
            t_opts = list(cluster_theaters.keys())
            cached_dates = sorted(list(st.session_state.registry['loaded_dates']))

            r1_c1, r1_c2 = st.columns(2)
            
            with r1_c1:
                target_theaters = st.multiselect(
                    "1\\. Select Theaters (Ordered by Preference)", 
                    options=t_opts,
                    default=[t_item['theater_code']],
                    format_func=lambda x: cluster_theaters.get(x),
                    key=f"target_theaters_{t_item['theater_code']}"
                )

                target_days = st.multiselect(
                    "2\\. Select Dates", 
                    options=cached_dates,
                    format_func=lambda x: datetime.strptime(x, "%m-%d-%Y").strftime("%b %d"),
                    default=[d_str])
            
            with r1_c2:
                global_reactive_codes = set()
                for target_day_key in target_days:
                    day_flat = st.session_state.registry['schedule'].get(target_day_key, [])
                    for s in day_flat:
                        if s['TheaterCode'] in target_theaters:
                            global_reactive_codes.add(s['master_code'])
                
                sorted_codes = sorted(
                    list(global_reactive_codes), 
                    key=lambda x: (
                        get_movie_group_info(x)[0], 
                        st.session_state.global_movie_catalog.get(x, {}).get('title', '').lower()
                    )
                )

                def format_movie_label_grouped(m_code):
                    meta = st.session_state.global_movie_catalog.get(m_code, {})
                    title = meta.get('title', f"Unknown ({m_code})")
                    _, prefix = get_movie_group_info(m_code)
                    return f"{prefix} {title}"

                target_movies = st.multiselect(
                    "3\\. Select Movies (Ordered by Preference)", 
                    options=sorted_codes,
                    format_func=format_movie_label_grouped,
                    key=f"target_movies_{t_item['theater_code']}",
                    help = "🔴 New Release  🎬 Regular Release  🎫 Unlimited Excluded"
                )

                n_movies = len(target_movies)

                available_formats = sorted(list(set(
                    s['ScreenType'] for s in all_flat_data 
                    if s['master_code'] in target_movies and s['TheaterCode'] in target_theaters
                    ))) if target_movies else sorted(list(set(
                    s['ScreenType'] for s in all_flat_data 
                    if s['TheaterCode'] in target_theaters)))
                
                target_formats = st.multiselect(
                    "4\\. Preferred Formats", 
                    options=available_formats, 
                    placeholder="All",
                    key=f"target_formats_{t_item['theater_code']}"
                )
            
            time_opts = ["Any Time"] + get_time_options()
            c1, c2, c3 = st.columns(3)
            with c1: 
                sel_start = st.selectbox("Earliest Start", options=time_opts, index=0)
                sel_end = st.selectbox("Latest End", options=time_opts, index=0)
                t_start = dt_time(0, 0) if sel_start == "Any Time" else datetime.strptime(sel_start, "%H:%M").time()
                t_end = dt_time(23, 59) if sel_end == "Any Time" else datetime.strptime(sel_end, "%H:%M").time()

                break_opts = [None] + list(range(1, n_movies)) if n_movies > 1 else [None]
                b_after = st.selectbox("Long break after movie #", options=break_opts, help="Set a longer break than after a specific movie.")
            with c2: 
                buff = st.slider("Buffer (min)", 0, 60, 15, help="Set the minimum gap between two movies")
                g_cap = st.slider("Max Gap (min)", 30, 240, 120, help="Set the maximum gap between two movies")
                b_val = st.slider("Break duration (min)", 30, 120, 60) if b_after else 0
            with c3: 
                unlimited = st.checkbox("Regal Unlimited Rule (90-min gap)", value=True, help="Apply a minimum gap of 90-min between showtimes.")
                fudge = st.checkbox("Fudge Factor (5-min overlap)", help="Allow a 5 min overlap between showtimes if no better schedule possible.")
                max_option = n_movies if n_movies > 1 else 1
                if len(target_days) > 1:
                    max_per_day = st.number_input("Max Movies per Day", min_value=1, max_value=max_option, value=max_option)
                    strategy = st.selectbox(
                        "Optimization Strategy", 
                        options=["Minimize Days", "Maximize Compactness"],
                        help = "Minimize Days will pack your selected movies into the fewest number of trips possible. Maximize Compactness prioritizes the most efficient schedules with the shortest gaps and minimal travel, even if spread across more days.") 

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
                    multi_itinerary = find_multi_day_itineraries(target_movies, target_days, params, drive_map, anchor_show)
            
                    if not multi_itinerary:
                        st.error("Could not find a valid multi-day schedule for these movies. Consider expanding selections and broadening filters.")
                    else:
                        st.success(f"🗓️ Multi-Day Plan Generated: {len(multi_itinerary)} days used.")
                        
                        total_movies = sum(len(p) for p in multi_itinerary.values())
                        total_hops = sum(calculate_path_score(p, params['primary_code'], drive_map)['hops'] for p in multi_itinerary.values())
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
                        
                        st.download_button("📅 Download ICS for Full Schedule", 
                                                generate_batch_ics(multi_itinerary, cluster_theaters), 
                                                file_name=f"movies_multi_full.ics", 
                                                mime="text/calendar", 
                                                key=f"dl_multi_full")

                        if not unscheduled:
                            st.success("✅ **Perfect Match!** All selected movies fit the schedule.")
                        else:
                            m_names = [st.session_state.global_movie_catalog.get(m, {}).get('title', m) for m in unscheduled]
                            st.warning(f"⚠️ **Incomplete Schedule:** {', '.join(m_names)} could not be fitted.")

                        for d_str in sorted_plan_days:
                            path = multi_itinerary[d_str]
                            d_display = datetime.strptime(d_str, '%m-%d-%Y').strftime('%A, %b %d')
                            stats = calculate_path_score(path, params['primary_code'], drive_map)
                            
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
                                
                                st.download_button("📅 Download ICS for Day", 
                                                generate_ics(path, cluster_theaters[params['primary_code']]), 
                                                file_name=f"movies_{d_str}.ics", 
                                                mime="text/calendar", 
                                                key=f"dl_multi_{d_str}")

                        if unscheduled:
                            with st.expander("⚠️ Unscheduled Movies"):
                                for m in unscheduled:
                                    m_title = st.session_state.global_movie_catalog.get(m, {}).get('title', m)
                                    st.write(f"❌ **{m_title}**: Could not fit into the selected time windows or theater constraints.")
                
                else:
                    if enable_anchor and anchor_show:
                        paths = run_anchored_search(anchor_show, target_movies, sched_date_str, params, drive_map)
                    else:
                        paths = find_itineraries([], target_movies, filtered_pool, params, sched_date_obj, drive_map)
                
                    if not paths: 
                        st.error("No valid schedules found. Consider expanding selections and broadening filters.")
                    else:
                        processed_paths = []
                        for p_raw in paths:
                            stats = calculate_path_score(p_raw, primary_code, drive_map)
                            
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
                                st.download_button("📅 Download ICS", 
                                                generate_ics(path, t_item['name']), 
                                                file_name=f"movies_{q_date}.ics", 
                                                mime="text/calendar", 
                                                key=f"dl_{i}_{entry['id']}")
                                
                                if count < len(target_movies):
                                    missing = [c for c in target_movies if c not in [s['master_code'] for s in path]]
                                    with st.expander("⚠️ Why were some movies left out?"):
                                        report_pool = st.session_state.registry['schedule'].get(sched_date_str, [])
                                        report = get_conflict_report(path, missing, report_pool, params, anchor_show, drive_map)
                                        for line in report: st.write(line)
else: st.info("Search for a theater in the sidebar to begin.")