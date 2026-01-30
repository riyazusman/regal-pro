# v2.0 RC Hotfix
import streamlit as st
import json
import math
import pgeocode
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import time
import os
import sys
import random
from datetime import datetime, timedelta, timezone, time as dt_time
from curl_cffi import requests as c_requests
from streamlit_js_eval import get_geolocation, set_cookie, get_cookie

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
            flex-direction: row; 
            gap: 2rem; 
            background-color: rgba(151, 166, 195, 0.15);
            padding: 10px 20px; 
            border-radius: 10px; 
            margin-bottom: 20px;
            border: 1px solid rgba(151, 166, 195, 0.2);
    }
    .stRadio [data-testid="stMarkdownContainer"] p { 
            font-size: 1.1rem; 
            font-weight: 600; 
            color: inherit;
    }
    .stRadio > div[role="radiogroup"]:hover {
        background-color: rgba(151, 166, 195, 0.25);
        transition: background-color 0.3s ease;
    }
    </style>
""", unsafe_allow_html=True)

# --- Constants & Headers ---
THEATERS_FILE = get_resource_path("theater_list.json")
proxy_zip_code = None


AJAX_HEADERS = {
    "Host": "www.regmovies.com",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
}

# --- Utility Functions ---

@st.cache_data(ttl=300)
def get_proxy_health():
    if not IS_CLOUD:
        return "Local Bypass", "System IP"
    
    try:
        p_user = st.secrets["proxy"]["username"]
        p_pass = st.secrets["proxy"]["password"]
        p_addr = st.secrets["proxy"]["address"]
        port = st.session_state.get('current_proxy_port', 10000)
        
        auth_user = f"user-{p_user}-session-healthcheck"
        proxy_url = f"http://{auth_user}:{p_pass}@{p_addr}:{port}"
        proxies = {"http": proxy_url, "https": proxy_url}
        
        test_resp = c_requests.get(
            "https://httpbin.org/ip", 
            proxies=proxies, 
            impersonate="chrome124", 
            timeout=10
        )
        
        if test_resp.status_code == 200:
            return "Active", test_resp.json().get('origin')
        return "Connection Error", "None"
    except Exception:
        return "Offline / Config Error", "None"

@st.cache_data
def load_theaters():
    try:
        with open(THEATERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("theatre_list", [])
    except Exception as e:
        st.error(f"Error loading theater list: {e}"); return []

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

def fetch_data(api_url, path_name, status_context, max_retries=3):
    proxies = None
    if IS_CLOUD:
        if "current_proxy_port" not in st.session_state:
            st.session_state.current_proxy_port = 10001
            if "proxy_session_id" not in st.session_state:
                #st.session_state.proxy_session_id = os.urandom(4).hex()
                st.session_state.proxy_session_id = f"sess_{path_name}_{datetime.now().strftime('%H%M')}"
        try:
            p = st.secrets["proxy"]
        except KeyError:
            st.error("Proxy secrets not configured!")
            return None
    
    minimal_headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://www.regmovies.com/theatres/{path_name}",
            "X-Requested-With": "XMLHttpRequest" # Keep this ONLY if Regal requires it for API hits
        }

    for attempt in range(max_retries):
        if IS_CLOUD:
            #auth = f"user-{p['username']}-session-{st.session_state.proxy_session_id}"
            auth = f"user-{p['username']}-country-us-zip-{proxy_zip_code}-session-{st.session_state.proxy_session_id}"
            proxy_url = f"https://{auth}:{p['password']}@{p['address']}:{st.session_state.current_proxy_port}"
            proxies = {"http": proxy_url, "https": proxy_url}

        if "api_session" not in st.session_state:
            st.session_state.api_session = c_requests.Session()
        
        st.session_state.api_session.proxies = proxies
        api_headers = AJAX_HEADERS.copy()
        api_headers["Referer"] = f"https://www.regmovies.com/theatres/{path_name}"
        theater_url = f"https://www.regmovies.com/theatres/{path_name}"

        if debug_mode:
            with status_context:
                with st.expander("🛠️ Outgoing Request Log", expanded=False):
                    st.json({
                        "API_URL": api_url,
                        "API_Headers": api_headers,
                        "Proxy": proxies["https"] if proxies else "None"
                    })
    
        try:
            st.session_state.api_session.get(theater_url, impersonate="chrome124", proxies=proxies)
            response = st.session_state.api_session.get(
                api_url, 
                headers=minimal_headers, 
                impersonate="chrome124",
                proxies=proxies,
                timeout=30
            )
            if response.status_code == 200: 
                return response.json()
            if response.status_code == 403:
                if debug_mode:
                    with status_context:
                        with st.expander("🛠️ Response", expanded=False):
                            st.write(response)
                st.session_state.current_proxy_port = 10001 + (st.session_state.current_proxy_port - 10001 + 1) % 10
                st.session_state.proxy_session_id = os.urandom(4).hex()
                del st.session_state.api_session

                if attempt < max_retries - 1:
                    st.toast("Regal 403 detected. Rotating IP and retrying...")
                    time.sleep(random.uniform(7, 12))
                    continue
                else:
                    st.error("Access Denied (403). Regal is blocking the request. Please try refreshing the page after a few seconds.")
                    return None
            response.raise_for_status()
        except Exception as e:
            if debug_mode:
                st.error(f"Proxy Connection Error: {str(e)}")
            if attempt < max_retries - 1: time.sleep(1)
            continue
    return None

def flatten_data(data):
    flat_list = []
    
    if "global_movie_catalog" not in st.session_state:
        st.session_state.global_movie_catalog = {}

    for m in data.get('movies', []):
        m_code = m.get('MasterMovieCode')
        if m_code:
            st.session_state.global_movie_catalog[m_code] = {
                'title': m.get('Title', 'Unknown'),
                'rating': m.get('Rating', 'NR'), 
                'duration': int(m.get('Duration', '0')),
                'is_new': is_new_release(m.get('OpeningDate')),
                'opening_date': m.get('OpeningDate')
            }
    
    raw_attrs_list = data.get('attributes', [])
    attr_map = {a.get('Acronym', '').strip(): a.get('ShortName', '').strip() 
                for a in raw_attrs_list if a.get('Acronym')}

    shows = data.get("shows", [])
    active_movie_codes = set()
    
    for theater_show in shows:
        t_code = theater_show.get("TheatreCode")
        for movie in theater_show.get("Film", []):
            m_code = movie.get('MasterMovieCode')
            active_movie_codes.add(m_code)
            
            meta = st.session_state.global_movie_catalog.get(m_code)
            
            if meta:
                title_to_use = movie.get('Title') if len(movie.get('Title', '')) > len(meta['title']) else meta['title']
                rating_to_use = meta['rating']
                duration_to_use = meta['duration']
            else:
                title_to_use = movie.get('Title', 'Unknown')
                rating_to_use = 'NR'
                duration_to_use = 120
            
            for perf in movie.get("Performances", []):
                try:
                    show_dt = datetime.strptime(perf["CalendarShowTime"], "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    continue
                
                raw_codes = perf.get("PerformanceAttributes", [])
                expanded_names = sorted([attr_map.get(c.strip(), c) for c in raw_codes])
                
                flat_list.append({
                    "TheaterCode": t_code,
                    "Title": title_to_use,
                    "Rating": rating_to_use,
                    "Duration": duration_to_use,
                    "Showtime": show_dt,
                    "Auditorium": str(perf.get("Auditorium", "?")),
                    "ScreenType": perf.get("PerformanceGroup") or "2D",
                    "Attributes": ", ".join(expanded_names),
                    "raw_attrs": set(expanded_names),
                    "master_code": m_code
                })
                
    theater_future_map = {}
    shows_list = data.get('shows', [])
    primary_t = shows_list[0].get('TheatreCode') if shows_list else None
    
    if primary_t:
        theater_future_map[primary_t] = []
        for fs in data.get("futureShows", []):
            m_code = fs.get('hoCode')
            formatted_dates = []
            for d_entry in fs.get('dates', []):
                raw_date = d_entry.get('date')
                if raw_date:
                    try:
                        dt_obj = datetime.strptime(raw_date[:10], "%m-%d-%Y")
                        formatted_dates.append(dt_obj.strftime("%b %d"))
                    except ValueError: continue
            
            meta = st.session_state.global_movie_catalog.get(m_code, {})
            if meta:
                meta_copy = meta.copy()
                meta_copy['scheduled_dates'] = formatted_dates
                if meta_copy['scheduled_dates']:
                    theater_future_map[primary_t].append(meta_copy)

    return flat_list, st.session_state.global_movie_catalog, attr_map, theater_future_map

def check_metadata_gaps(flat_list):
    gaps = {}
    for s in flat_list:
        if s['Duration'] == 0:
            if s['master_code'] not in gaps:
                gaps[s['master_code']] = s['TheaterCode']
    return gaps

def is_new_release(opening_date_str):
    if not opening_date_str:
        return False
    try:
        open_dt = datetime.strptime(opening_date_str[:10], "%Y-%m-%d").date()

        today = datetime.now().date()
        days_since_thu = (today.weekday() - 3) % 7
        current_thu = today - timedelta(days=days_since_thu)
        next_wed = current_thu + timedelta(days=6)
        
        return current_thu <= open_dt <= next_wed
    except Exception:
        return False
    
def get_attr_diff(screening_attrs, common_attrs):
    s_set = set([a.strip() for a in screening_attrs.split(",") if a.strip()])
    diff = s_set - common_attrs
    if not diff: return ""
    diff_str = ", ".join(sorted(diff))
    return diff_str

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
        if p['formats']: 
            potential_shows = [s for s in potential_shows if s['ScreenType'] in p['formats']]
        
        for s in potential_shows:
            show_start = s['Showtime']
            show_end = show_start + timedelta(minutes=s['Duration'])
            if show_start < window_start or show_end > window_end: 
                continue
            
            if current_path:
                prev = current_path[-1]
                prev_end = prev['Showtime'] + timedelta(minutes=prev['Duration'])
                if p['fudge']: 
                    prev_end -= timedelta(minutes=5)
                
                drive_time = 0
                if s['TheaterCode'] != prev['TheaterCode']:
                    nb_code = s['TheaterCode'] if s['TheaterCode'] != p['primary_code'] else prev['TheaterCode']
                    drive_time = drive_map.get(nb_code, {}).get('time', 20)
                
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
    
    if not valid_paths and current_path:
        return [current_path]

    return valid_paths

def find_multi_day_itineraries(target_movies, target_days, params, drive_map, anchor_show=None):
    itinerary_by_day = {}
    remaining_movies = list(target_movies)
    max_per_day = params.get('max_per_day', len(target_movies))

    if anchor_show:
        a_day_str = anchor_show['Showtime'].strftime('%m-%d-%Y')
        
        anchor_day_options = run_anchored_search(anchor_show, target_movies, a_day_str, params, drive_map)
        
        if anchor_day_options:
            best_a_path = sorted(anchor_day_options, key=lambda x: -calculate_path_score(x, params['primary_code'], drive_map)['score'])[0]
            itinerary_by_day[a_day_str] = best_a_path
            
            for s in best_a_path:
                if s['Title'] in remaining_movies:
                    remaining_movies.remove(s['Title'])
    
    sorted_days = sorted([d for d in target_days if d != (anchor_show['Showtime'].strftime('%m-%d-%Y') if anchor_show else None)],
                        key=lambda x: datetime.strptime(x, '%m-%d-%Y'))
    
    if params.get('strategy') == "Minimize Days":
        for i, d_str in enumerate(sorted_days):
            if not remaining_movies: break
            
            d_obj = datetime.strptime(d_str, '%m-%d-%Y').date()
            day_data = st.session_state.multi_day_raw.get(d_str)
            if not day_data: continue
            day_flat, _, _, _ = flatten_data(day_data)

            # Find all valid paths for today
            all_paths = find_itineraries([], remaining_movies, day_flat, params, d_obj, drive_map)
            if not all_paths: continue

            # Limit candidates to the top 5 most diverse/high-scoring paths to manage performance
            candidates = sorted([p for p in all_paths if len(p) <= max_per_day], 
                                key=lambda x: (-len(x), -calculate_path_score(x, params['primary_code'], drive_map)['score']))[:5]

            best_path_for_today = None
            max_future_yield = -1

            # Simulation: If there are future days, see which candidate today yields the most movies overall
            if i < len(sorted_days) - 1:
                for cand in candidates:
                    cand_titles = [s['Title'] for s in cand]
                    mock_remaining = [m for m in remaining_movies if m not in cand_titles]
                    
                    # Mock the next day only for a fast "one-step look-ahead"
                    next_day_str = sorted_days[i+1]
                    nd_obj = datetime.strptime(next_day_str, '%m-%d-%Y').date()
                    nd_data = st.session_state.multi_day_raw.get(next_day_str)
                    if nd_data:
                        nd_flat, _, _, _ = flatten_data(nd_data)
                        next_day_paths = find_itineraries([], mock_remaining, nd_flat, params, nd_obj, drive_map)
                        next_day_yield = max([len(p) for p in next_day_paths if len(p) <= max_per_day]) if next_day_paths else 0
                    else:
                        next_day_yield = 0
                    
                    total_yield = len(cand) + next_day_yield
                    if total_yield > max_future_yield:
                        max_future_yield = total_yield
                        best_path_for_today = cand
            else:
                # Last day, no look-ahead needed
                best_path_for_today = candidates[0]

            if best_path_for_today:
                itinerary_by_day[d_str] = best_path_for_today
                for s in best_path_for_today:
                    remaining_movies.remove(s['Title'])
                    
                                        
    else: # Strategy: Maximize Compactness
        global_pool = []
        for d_str in sorted_days:
            d_obj = datetime.strptime(d_str, '%m-%d-%Y').date()
            day_data = st.session_state.multi_day_raw.get(d_str)
            day_flat, _, _, _ = flatten_data(day_data)

            paths = find_itineraries([], target_movies, day_flat, params, d_obj, drive_map)
            for p in paths:
                if len(p) <= max_per_day:
                    stats = calculate_path_score(p, params['primary_code'], drive_map)
                    global_pool.append({
                        'date': d_str, 
                        'path': p, 
                        'score': stats['score'], 
                        'count': len(p)
                    })
        
        global_pool.sort(key=lambda x: -x['score'])
        
        assigned_dates = set()
        for entry in global_pool:
            if not remaining_movies: break
            if entry['date'] in assigned_dates: continue
            
            needed_in_path = [s for s in entry['path'] if s['Title'] in remaining_movies]
            
            if len(needed_in_path) == len(entry['path']):
                itinerary_by_day[entry['date']] = entry['path']
                assigned_dates.add(entry['date'])
                for s in entry['path']:
                    remaining_movies.remove(s['Title'])

    return itinerary_by_day

def run_anchored_search(anchor_show, target_movies, day_str, params, drive_map):
    day_data = st.session_state.multi_day_raw.get(day_str)
    if not day_data:
        return []
    
    day_flat, _, _, _ = flatten_data(day_data)
    d_obj = datetime.strptime(day_str, '%m-%d-%Y').date()
    total_max = params.get('max_per_day', 99)

    wing_titles = [t for t in target_movies if t != anchor_show['Title']]
    
    after_paths = find_itineraries([anchor_show], wing_titles, day_flat, params, d_obj, drive_map)
    if not after_paths:
        after_paths = [[anchor_show]]

    before_params = params.copy()
    before_params['max_per_day'] = total_max - 1
    latest_cutoff = anchor_show['Showtime'] - timedelta(minutes=params['buffer'])
    before_params['end'] = latest_cutoff.time()
    
    raw_before = find_itineraries([], wing_titles, day_flat, before_params, d_obj, drive_map)
    
    valid_before = []

    if not raw_before:
        valid_before = [[]]
    else:
        for b_path in raw_before:
            if not b_path:
                valid_before.append([])
                continue
            
            last_m = b_path[-1]
            last_m_end = last_m['Showtime'] + timedelta(minutes=last_m['Duration'])
            
            travel_time = 0
            if last_m['TheaterCode'] != anchor_show['TheaterCode']:
                nb_code = last_m['TheaterCode'] if last_m['TheaterCode'] != params['primary_code'] else anchor_show['TheaterCode']
                travel_time = drive_map.get(nb_code, {}).get('time', 20)
            
            # Ensure the last movie in the morning wing ends before the anchor starts
            if last_m_end + timedelta(minutes=travel_time + params['buffer']) <= anchor_show['Showtime']:
                valid_before.append(b_path)
    
    # If the travel/buffer checks above filtered everything out, revert to [[]] 
    # so the anchor can still be scheduled alone or with future shows.
    if not valid_before:
        valid_before = [[]]
            
    combined_itineraries = []
    search_before = valid_before if valid_before else [[]]

    for b_path in search_before:
        for a_path in after_paths:
            full_path = b_path + a_path 
            if len(full_path) <= params['max_per_day']:
                titles = [s['Title'] for s in full_path]
                if len(titles) == len(set(titles)):
                    combined_itineraries.append(full_path)
    return combined_itineraries

    first_day = target_days[0]
    day_flat, _, _, _ = flatten_data(first_day)
    
    candidates = find_itineraries([], target_movies, day_flat, params, first_day, drive_map)
    
    best_overall_plan = {}
    max_total_movies = -1

    for path in candidates[:5]:
        current_plan = {first_day: path}
        remaining = [m for m in target_movies if m not in [s['Title'] for s in path]]
        
        rest_of_week = find_multi_day_itineraries(remaining, target_days[1:], params, drive_map)
        
        total_count = len(path) + sum(len(p) for p in rest_of_week.values())
        
        if total_count > max_total_movies:
            max_total_movies = total_count
            best_overall_plan = {**current_plan, **rest_of_week}
            
    return best_overall_plan

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
                nb_code = nxt['TheaterCode'] if nxt['TheaterCode'] != primary_code else s['TheaterCode']
                total_miles += drive_map.get(nb_code, {}).get('dist', 0)

    score = (movie_count * 250) - (hops * 40) - (total_miles * 2) - (total_gap * 0.1)
    return {
        'score': score, 'count': movie_count, 'hops': hops, 
        'miles': total_miles, 'gap': total_gap, 'duration': total_duration
    }

def get_conflict_report(path, missing_codes, all_screenings, p, anchor_show=None, drive_map={}):
    conflicts = []
    for m_code in missing_codes:
        meta = st.session_state.global_movie_catalog.get(m_code, {})
        m_title = meta.get('title', f"Unknown ({m_code})")
        m_shows = [s for s in all_screenings if s['master_code'] == m_code and s['TheaterCode'] in p['theaters']]
        
        if p['formats']: 
            m_shows = [s for s in m_shows if s['ScreenType'] in p['formats']]
        
        if not m_shows:
            conflicts.append(f"❌ **{m_title}**: No screenings match your formats/theaters.")
            continue

        any_valid = False
        failure_details = [] 

        for ms in m_shows:
            ms_start = ms['Showtime']
            ms_end = ms_start + timedelta(minutes=ms['Duration'])
            reasons = []

            # 2. Daily Limit Check
            if len(path) >= p.get('max_per_day', 99):
                reasons.append((10, f"Exceeds daily limit of {p['max_per_day']} movies."))

            # 3. Anchor Check (Highest Priority)
            if anchor_show:
                a_start, a_end = anchor_show['Showtime'], anchor_show['Showtime'] + timedelta(minutes=anchor_show['Duration'])
                if not (ms_end <= a_start or ms_start >= a_end):
                    reasons.append((1, f"Overlaps with your **Anchor Show** ({anchor_show['Title']})."))

            # 4. Detailed Path Linkage
            for ps in path:
                ps_start = ps['Showtime']
                ps_end = ps_start + timedelta(minutes=ps['Duration'])
                
                # Check Physical Overlap
                if not (ms_end <= ps_start or ms_start >= ps_end):
                    reasons.append((1, f"Overlaps with **{ps['Title']}** ({ps_start.strftime('%I:%M %p')})."))
                    break # Immediate exit for physical impossibility
                
                # Check Logical Constraints
                if ms_start >= ps_end:
                    gap = int((ms_start - ps_end).total_seconds() / 60)
                    travel = 0
                    if ms['TheaterCode'] != ps['TheaterCode']:
                        nb = ms['TheaterCode'] if ms['TheaterCode'] != p['primary_code'] else ps['TheaterCode']
                        travel = drive_map.get(nb, {}).get('time', 20)
                    
                    if gap < (travel + p['buffer']):
                        reasons.append((2, f"Buffer violation after **{ps['Title']}** (Gap is {gap}m, needs {travel + p['buffer']}m)."))
                    elif gap > p['gap_cap']:
                        reasons.append((5, f"Gap after **{ps['Title']}** ({gap}m) exceeds your Max Gap ({p['gap_cap']}m)."))

            if not reasons:
                any_valid = True
                break
            else:
                # Store the most "urgent" reason for this specific showtime (lowest rank number)
                reasons.sort() 
                failure_details.append(reasons[0])

        if not any_valid:
            # Pick the most logical reason across all showtimes (prioritizing Overlaps > Buffers > Gaps)
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

# --- Main App ---
if "global_movie_catalog" not in st.session_state:
    st.session_state.global_movie_catalog = {}

if "multi_day_raw" not in st.session_state:
        st.session_state.multi_day_raw = {}

if "theater_future_cache" not in st.session_state:
    st.session_state.theater_future_cache = {}

st.title("🎬 Regal Pro")
theaters = load_theaters()

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
        match = next((t for t in theaters if t['item']['theatre_code'] == code_in), None)
        if match:
            results = [match]
            if 'nearby_theaters' in match['item']:
                nearby_codes = [n['code'] for n in match['item']['nearby_theaters']]
                results.extend([t for t in theaters if t['item']['theatre_code'] in nearby_codes])
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
                d = calculate_haversine_distance(z_data['latitude'], z_data['longitude'], t['item']['latitude'], t['item']['longitude'])
                if d <= radius_in: results.append((t, d))
            results.sort(key=lambda x: x[1])
    elif location and not math.isnan(latitude):
        for t in theaters:
            d = calculate_haversine_distance(latitude, longitude, t['item']['latitude'], t['item']['longitude'])
            if d <= 50: results.append((t, d))
        results.sort(key=lambda x: x[1])
elif search_mode == "Theater Name":
    name_in = st.sidebar.text_input("Theater Name")
    if name_in: search_performed = True; results = [t for t in theaters if name_in.lower() in t['item']['name'].lower()]
elif search_mode == "Address/City":
    addr_in = st.sidebar.text_input("Address, City, or State")
    if addr_in: search_performed = True; results = [t for t in theaters if any(addr_in.lower() in t['item'].get(f, '').lower() for f in ['address', 'city', 'state'])]

if search_performed and not results: st.sidebar.warning("No theaters found matching your criteria.")

selected_theater = None

if results:
    opts = {f"{r[0]['item']['name'] if isinstance(r, tuple) else r['item']['name']} - {r[0]['item']['city'] if isinstance(r, tuple) else r['item']['city']}": (r[0] if isinstance(r, tuple) else r) for r in results}    
    
    if "active_theater_code" not in st.session_state:
        st.session_state.active_theater_code = st.query_params.get("theater")
    
    idx = 0
    for i, t in enumerate(opts.values()):
        if t['item']['theatre_code'] == st.session_state.active_theater_code: 
            idx = i
            break

    sel_label = st.sidebar.selectbox("Select Theater", options=list(opts.keys()), index=idx)
    selected_theater = opts[sel_label]
    new_code = selected_theater['item']['theatre_code']

    if new_code != st.session_state.active_theater_code:
        st.session_state.active_theater_code = new_code
        st.query_params["theater"] = new_code
        st.rerun()

if selected_theater:
    t_item = selected_theater['item']
    cluster_theaters = {t_item['theatre_code']: t_item['name']}
    master_name_map = {t['item']['theatre_code']: t['item']['name'] for t in theaters}
    drive_map = {t_item['theatre_code']: {'time': 0, 'dist': 0}}
    proxy_zip_code = t_item['zip'] or default_zip_code or "46201"

    if 'nearby_theaters' in t_item:
        for nt in t_item['nearby_theaters']:
            n_code = nt['code']
            n_name = master_name_map.get(n_code, nt.get('name', f"Theater {n_code}"))
            cluster_theaters[n_code] = n_name
            drive_map[n_code] = {
                    'time': nt.get('drive_min', 20),
                    'dist': nt.get('road_miles', 0)
                }

    tz_off = st.session_state.get('auto_tz_offset', -5)
    local_today = (datetime.now(timezone.utc) + timedelta(hours=tz_off)).date()

    q_date = st.sidebar.date_input("Select Date", value=local_today, min_value=local_today, format="MM/DD/YYYY")

    t_lon = t_item.get('longitude')
    t_state = t_item.get('state_code')
    if t_lon:
        new_offset = get_offset_from_lon(t_lon,
                                         t_state,
                                         target_date=datetime.combine(q_date, dt_time(0,0)))
        st.session_state.auto_tz_offset = new_offset

    f_date = q_date.strftime('%m-%d-%Y')

    needs_fetch = True
    date_range = [q_date + timedelta(days=i) for i in range(7)]
    target_codes = list(cluster_theaters.keys())

    if "multi_day_raw" not in st.session_state:
        st.session_state.multi_day_raw = {}
        
    days_to_fetch = [d.strftime('%m-%d-%Y') for d in date_range 
                    if d.strftime('%m-%d-%Y') not in st.session_state.multi_day_raw]

    status_context = st.status("🛠️ Debug: Detailed Sync Log", expanded=True) if debug_mode else None

    if days_to_fetch:
        msg = st.toast(f"🔍 Synchronizing 7-Day Data for {t_item['name']}...")
        for d_str in days_to_fetch:
            log_msg = f"🌐 Fetching {d_str}..."
            msg.toast(log_msg)
            if status_context: status_context.write(log_msg)
            api_url = f"https://www.regmovies.com/api/getShowtimes?theatres={','.join(target_codes)}&date={d_str}"
            data = fetch_data(api_url, t_item['path_name'],status_context)
            
            if data:
                all_flat_data, _, _, _ = flatten_data(data)
                gaps = check_metadata_gaps(all_flat_data)
                
                if gaps:
                    anchor_theaters = list(set(gaps.values()))
                    for anchor in anchor_theaters:
                        t_name = cluster_theaters.get(anchor, anchor)
                        sweep_msg = f"🩹 Metadata gap fill: {d_str} via {t_name}"
                        msg.toast(sweep_msg)
                        if status_context: status_context.write(sweep_msg)
                        rotated = [anchor] + [c for c in target_codes if c != anchor]
                        sweep_url = f"https://www.regmovies.com/api/getShowtimes?theatres={','.join(rotated)}&date={d_str}"
                        sweep_data = fetch_data(sweep_url, t_item['path_name'],status_context)
                        if sweep_data:
                            flatten_data(sweep_data)
                
                st.session_state.multi_day_raw[d_str] = data
            
            msg.toast("🎉 7-Day Sync Complete!")
            if status_context: status_context.update(label="Sync Log Finished", state="complete", expanded=False)

    current_day_data = st.session_state.multi_day_raw.get(f_date)

    if "theater_future_cache" not in st.session_state:
        st.session_state.theater_future_cache = {}
    
    current_t_code = selected_theater['item']['theatre_code']

    if current_t_code not in st.session_state.theater_future_cache:
        log_msg = f"📡 Fetching upcoming schedule for {selected_theater['item']['name']}..."
        st.toast(log_msg)
        if status_context: status_context.write(log_msg)

        api_url = f"https://www.regmovies.com/api/getShowtimes?theatres={current_t_code}&date={f_date}"
        future_data = fetch_data(api_url, selected_theater['item']['path_name'],status_context)
        
        if future_data:
            _, _, _, new_future_map = flatten_data(future_data)
            st.session_state.theater_future_cache.update(new_future_map)

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
        debug_mode = st.checkbox("🐞 Debug Mode", value=debug_mode, help="Show raw API responses for troubleshooting.")
        status_label, ext_ip = get_proxy_health()
        
        if status_label == "Active":
            st.success(f"🌐 **Proxy:** {status_label}")
            st.caption(f"Masked IP: `{ext_ip.split(',')[0]}`")
        elif status_label == "Local Bypass":
            st.info(f"🏠 **Mode:** {status_label}")
            st.caption("Direct Connection Active")
        else:
            st.error(f"⚠️ **Proxy:** {status_label}")
            st.caption("Check Streamlit Secrets or Decodo Balance")

st.sidebar.link_button("🐞 Report a Bug","https://docs.google.com/forms/d/e/1FAIpQLSce6X3DtCwDJZUjf_Cc4IbJLA7q0Nvk_Grw7lOgyqLtxYIYPQ/viewform?usp=dialog")
st.sidebar.link_button("☕ Buy Me a Coffee","https://buymeacoffee.com/riyazusman")
st.sidebar.link_button("📃 Source on Github","https://github.com/riyazusman/regal-pro")

if selected_theater and current_day_data:
    if debug_mode:
        with st.expander("🛠️ Raw API Debug Output", expanded=False):
            st.json(current_day_data)

    all_flat_data, movie_meta, attr_map, future_movies = flatten_data(current_day_data)        
    flat_data = [s for s in all_flat_data if s['TheaterCode'] == t_item['theatre_code']]
        
    st.session_state.update({
        "all_flat_data": all_flat_data,
        "flat_data": flat_data,
        "movie_meta": movie_meta,
        "attr_map": attr_map,
        "future_movies": future_movies
    })

    if 'flat_data' in st.session_state:
        flat_data = st.session_state.flat_data
        all_flat_data = st.session_state.all_flat_data
        movie_meta = st.session_state.movie_meta
        attr_map = st.session_state.attr_map
        future_movies = st.session_state.future_movies

    t_key = t_item['theatre_code']
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

        tab_now, tab_nearby, tab_upcoming = st.tabs(["🍿 Now Playing", "🚗 Playing Nearby", "📅 Upcoming"])
        
        with tab_now:
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
                (not f_avail or (s['Showtime'] > current_local_time if q_date == current_local_time.date() else True)) and
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
                        if s['Attributes']: st.markdown(f'<p style="color: grey; font-size: 0.85em; margin-top: -10px;">{s["Attributes"]}</p>', unsafe_allow_html=True)
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
                for title in list(dict.fromkeys([s['Title'] for s in filtered])):
                    m_shows = [s for s in filtered if s['Title'] == title]
                    
                    other_t = sorted([cluster_theaters.get(tc, f"Theater {tc}") 
                                    for tc in set(s['TheaterCode'] for s in all_flat_data if s['Title'] == title) 
                                    if tc != t_item['theatre_code']])
                    
                    scheduled_days = sorted([datetime.strptime(d_str, "%m-%d-%Y").strftime("%b %d") for d_str, d_data in st.session_state.multi_day_raw.items() 
                                       if any(m.get('Title') == title for m in d_data.get('movies', []))])
                    
                    meta = movie_meta.get(m_shows[0]['master_code'], {})
                    new_tag = "🔴 NEW" if meta.get('is_new') else ""

                    with st.expander(f"🍿 {title} ({m_shows[0]['Rating']}) — {m_shows[0]['Duration']} min {new_tag}", expanded=True):
                        for mt in sorted(list(set(s['ScreenType'] for s in m_shows))):
                            ts = [s for s in m_shows if s['ScreenType'] == mt]
                            t_common = set.intersection(*(s['raw_attrs'] for s in ts)) if ts else set()
                            common_attribs = sorted(t_common - {mt})
                            st.markdown(f'<div style="margin-bottom: 6px;"><span style="background-color: rgba(151, 166, 195, 0.15); padding: 4px 12px; border-radius: 4px; border-left: 4px solid #ff4b4b;"><span style="font-weight: bold;">{mt}</span> <span style="color: grey; font-size: 0.85em; font-weight: normal; margin-left: 10px;">({", ".join(sorted(common_attribs)) if common_attribs else ""})</span></span></div>', unsafe_allow_html=True)

                            row = []
                            for s in ts:
                                is_past = (q_date == current_local_time.date() and s['Showtime'] < current_local_time)
                                delta_attribs = get_attr_diff(s['Attributes'], t_common)
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
                            if scheduled_days:
                                st.markdown(f"<div style='font-size: 0.8rem; color: #e67e22; padding-top: 2px;'>🗓️ <b>Scheduled Dates:</b> {', '.join(scheduled_days)}</div>", unsafe_allow_html=True)
                            if other_t:
                                st.markdown(f"<div style='font-size: 0.8rem; color: #666; padding-top: 5px;'><b>Also Playing at:</b> {', '.join(other_t)}</div>", unsafe_allow_html=True)

                        with link_col:
                            if other_t or scheduled_days:
                                if st.button("📅 Full Schedule", key=f"link_{title}_{t_item['theatre_code']}", use_container_width=True):
                                    st.session_state.nav_redirect = "🎬 Movie Explorer"
                                    st.session_state.selected_movie = title
                                    st.rerun()

        with tab_nearby:
            primary_titles_week = set()
            all_titles_week = set()
            
            for d_str, d_data in st.session_state.multi_day_raw.items():
                for theater_show in d_data.get('shows', []):
                    t_code = theater_show.get('TheatreCode')
                    titles = [m.get('Title') for m in theater_show.get('Film', [])]
                    if t_code == t_item['theatre_code']:
                        primary_titles_week.update(titles)
                    all_titles_week.update(titles)
            
            nearby_only_titles = sorted(list(all_titles_week - primary_titles_week))

            if nearby_only_titles:
                st.subheader("🚗 Exclusive Nearby This Week")
                st.caption(f"These movies are NOT playing at {t_item['name']} any time this week.")
                
                nearby_cols = st.columns(3)
                for idx, title in enumerate(nearby_only_titles):
                    theater_dates = {}
                    master_code = None
                    
                    for d_str, d_data in st.session_state.multi_day_raw.items():
                        for theater_show in d_data.get('shows', []):
                            t_code = theater_show.get('TheatreCode')
                            for movie in theater_show.get('Film', []):
                                if movie.get('Title') == title:
                                    if not master_code: master_code = movie.get('MasterMovieCode')
                                    t_name = cluster_theaters.get(t_code, f"Theater {t_code}")
                                    date_label = datetime.strptime(d_str, "%m-%d-%Y").strftime("%b %d")
                                    if t_name not in theater_dates:
                                        theater_dates[t_name] = []
                                    if date_label not in theater_dates[t_name]:
                                        theater_dates[t_name].append(date_label)
                    
                    meta = st.session_state.global_movie_catalog.get(master_code, {'rating': 'NR', 'duration': 0, 'is_new':False})
                    
                    new_tag = "<small style='font-size: 0.8rem; color:red;'>🔴 NEW</small>" if meta.get('is_new') else ""

                    with nearby_cols[idx % 3]:
                        with st.container(border=True):
                            st.markdown(f"**{title}** ({meta['rating']}) {new_tag}", unsafe_allow_html=True)
                            
                            for t_name, dates in theater_dates.items():
                                date_str = ", ".join(sorted(dates))
                                st.markdown(f"<p style='font-size: 0.8rem; margin-bottom: 2px;'>📍 <b>{t_name}</b></p>", unsafe_allow_html=True)
                                st.markdown(f"<p style='font-size: 0.8rem; color: #e67e22; margin-top: -5px;'>🗓️ {date_str}</p>", unsafe_allow_html=True)
                            
                            st.caption(f"⏱️ {meta['duration']} min")
            else:
                st.info("No exclusive nearby movies found for the upcoming 7 days.")

        with tab_upcoming:
            current_t_code = t_item['theatre_code']

            if current_t_code not in st.session_state.theater_future_cache:
                with st.spinner(f"Loading upcoming schedule for {t_item['name']}..."):
                    api_url = f"https://www.regmovies.com/api/getShowtimes?theatres={current_t_code}&date={f_date}"
                    future_data = fetch_data(api_url, t_item['path_name'], None)
                    if future_data:
                        _, _, _, new_future_map = flatten_data(future_data)
                        st.session_state.theater_future_cache.update(new_future_map)

            scoped_future_movies = st.session_state.theater_future_cache.get(current_t_code, [])

            if scoped_future_movies:
                st.subheader("📅 Upcoming Movies")
                st.caption(f"These movies are scheduled to show at {t_item['name']}.")
                cols = st.columns(3)
                for i, f_movie in enumerate(scoped_future_movies):
                    with cols[i % 3]:
                        with st.container(border=True):
                            st.markdown(f"**{f_movie['title']}** ({f_movie['rating']})")
                            dates_str = ", ".join(f_movie['scheduled_dates'])
                            st.markdown(f"<small style='color:#e67e22;'>Scheduled: {dates_str}</small>", unsafe_allow_html=True)
                            st.caption(f"⏱️ {f_movie['duration']} min")
            else:
                st.info("No upcoming movies listed for this theater.")
                            
    elif nav_tab == "🎬 Movie Explorer":
        st.subheader("🎬 Movie Explorer")
        st.info(f"Movies: **{t_item['name']}** and nearby theaters on **{q_date.strftime('%A, %b %d')}**")

        
        master_name_map = {t['item']['theatre_code']: t['item']['name'] for t in theaters}
        theater_info = {t_item['theatre_code']: {"name": t_item['name'], "dist": 0, "time": 0}}
        if 'nearby_theaters' in t_item:
            for nt in t_item['nearby_theaters']:
                n_code = nt['code']
                theater_info[n_code] = {
                    "name": master_name_map.get(n_code, f"Theater {n_code}"), 
                    "dist": nt.get('road_miles', 0), 
                    "time": nt.get('drive_min', 0)
                }
        
        movie_list_data = []
        titles_processed = set()
        for s in all_flat_data:
            if s['Title'] not in titles_processed:
                rating = movie_meta.get(s['master_code'], {}).get('rating', 'NR')
                movie_list_data.append({"title": s['Title'], "label": f"{s['Title']} ({rating})"})
                titles_processed.add(s['Title'])
        movie_list_data.sort(key=lambda x: x['title'])

        st.markdown("###### 🍿 Select a Movie")
        st.markdown("""
            <style>
            div.stButton > button {
                width: 100% !important;
                height: 40px !important;
                border-radius: 6px !important;
                background-color: rgba(151, 166, 195, 0.1) !important;
                border: 1px solid rgba(151, 166, 195, 0.2) !important;
                transition: all 0.2s ease-in-out !important;
            }
            div.stButton > button:hover {
                background-color: rgba(151, 166, 195, 0.2) !important;
                border-color: #ff4b4b !important;
            }
            div.stButton > button div p {
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
                font-size: 0.75rem !important;
                font-weight: 600 !important;
                color: var(--text-color) !important;
            }
            </style>
        """, unsafe_allow_html=True)

        if "selected_movie" not in st.session_state:
            st.session_state.selected_movie = movie_list_data[0]['title'] if movie_list_data else None

        with st.container(height=130, border=True):
            cols_per_row = 5
            for i in range(0, len(movie_list_data), cols_per_row):
                row_cols = st.columns(cols_per_row)
                for j, m_entry in enumerate(movie_list_data[i : i + cols_per_row]):
                    title = m_entry['title']
                    is_selected = (title == st.session_state.selected_movie)
                    label = f"✅ {m_entry['label']}" if is_selected else m_entry['label']
                    if row_cols[j].button(label, key=f"grid_{title}", use_container_width=True):
                        st.session_state.selected_movie = title
                        st.rerun()

        sel_movie = st.session_state.selected_movie
        if sel_movie:
            m_data = [s for s in all_flat_data if s['Title'] == sel_movie]
            if m_data:
                meta = movie_meta.get(m_data[0]['master_code'], {})
                new_tag = " | 🔴 NEW RELEASE" if meta.get('is_new') else ""
                st.markdown(f"## {sel_movie}", unsafe_allow_html=True)
                st.markdown(f"#### <small style='color:grey'>({meta.get('rating', 'NR')} | {meta.get('duration', 0)} min {new_tag})</small>", unsafe_allow_html=True)
                
                with st.expander("🔍 Advanced Filters", expanded=False):
                    f_col1, f_col2, f_col3 = st.columns(3)
                    with f_col1:
                        m_formats = sorted(list(set(s['ScreenType'] for s in m_data)))
                        f_fmt = st.multiselect("Format", options=m_formats, placeholder="All")
                    with f_col2:
                        t_ranges = {"8AM-12N": (8, 12), "12N-4PM": (12, 16), "4PM-8PM": (16, 20), "8PM-12M": (20, 24)}
                        f_win = st.multiselect("Time Window", options=list(t_ranges.keys()))
                    with f_col3:
                        all_m_attrs = set(a for s in m_data for a in s['raw_attrs'])
                        f_extra = st.multiselect("Attributes", options=sorted(list(all_m_attrs - set(m_formats))))
                        f_hide = st.checkbox("Hide Past Shows", value=True)

                filtered_m = [s for s in m_data if 
                        (not f_fmt or s['ScreenType'] in f_fmt) and
                        (not f_win or any(t_ranges[w][0] <= s['Showtime'].hour < t_ranges[w][1] for w in f_win)) and
                        (not f_extra or set(f_extra).issubset(s['raw_attrs'])) and
                        (not f_hide or (s['Showtime'] > current_local_time if q_date == current_local_time.date() else True))]

                fmts_to_show = sorted(list(set(s['ScreenType'] for s in filtered_m)))
                
                for fmt in fmts_to_show:
                    fmt_shows = [s for s in filtered_m if s['ScreenType'] == fmt]
                    
                    with st.expander(f"✨ {fmt}", expanded=True):
                        t_codes = sorted(list(set(s['TheaterCode'] for s in fmt_shows)), 
                                        key=lambda x: theater_info.get(x, {}).get('time', 999))
                        
                        for tc in t_codes:
                            t_shows = sorted([s for s in fmt_shows if s['TheaterCode'] == tc], key=lambda x: x['Showtime'])
                            info = theater_info.get(tc, {"name": f"Theater {tc}", "dist": 0, "time": 0})
                            
                            is_primary = (tc == t_item['theatre_code'])
                            t_icon = "📍" if is_primary else "🚗"
                            dist_txt = "(Current)" if is_primary else f"({info['time']}m / {info['dist']}mi)"
                            
                            st.markdown(f"**{t_icon} {info['name']}** <small style='color:grey'>{dist_txt}</small>", unsafe_allow_html=True)
                            
                            playing_on_dates = []
                            for d_str, d_data in st.session_state.multi_day_raw.items():
                                for theater_show in d_data.get('shows', []):
                                    if theater_show.get('TheatreCode') == tc:
                                        for movie in theater_show.get('Film', []):
                                            if movie.get('Title') == sel_movie:
                                                day_fmts = set(p.get('PerformanceGroup') or "2D" for p in movie.get('Performances', []))
                                                if fmt in day_fmts:
                                                    playing_on_dates.append(datetime.strptime(d_str, "%m-%d-%Y").strftime("%b %d"))
                            
                            t_common = set.intersection(*(s['raw_attrs'] for s in t_shows)) if t_shows else set()
                            common_attribs = sorted(t_common - {fmt})
                            st.markdown(f"<p style='color:grey; font-size:0.8rem; margin-top:-10px; margin-bottom:5px;'>({', '.join(common_attribs) if common_attribs else ""})</p>", unsafe_allow_html=True)

                            row_items = []
                            for s in t_shows:
                                t_str = s['Showtime'].strftime('%I:%M %p')
                                delta_attribs = get_attr_diff(s['Attributes'], t_common)
                                
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
                                date_str = ", ".join(sorted(list(set(playing_on_dates))))
                                st.markdown(f"<p style='font-size: 0.8rem; color: #e67e22; margin-top: -5px;'>🗓️ <b>Scheduled Dates:</b> {date_str}</p>", unsafe_allow_html=True)

                            st.divider()
            else:
                st.warning(f"No showtimes found for **{sel_movie}** on {q_date.strftime('%b %d')}.")
                st.info("Try selecting a different date or theater in the sidebar.")
                        
    elif nav_tab == "🗓️ Smart Scheduler":
        st.subheader("🗓️ Smart Scheduler")
        st.info(f"Scheduling: **{t_item['name']}** and nearby theaters on **{q_date.strftime('%A, %b %d')}**")

        primary_code = t_item['theatre_code']

        with st.expander("⚙️ Parameters", expanded=True):
            cached_dates = sorted(list(st.session_state.multi_day_raw.keys()))
            t_opts = list(cluster_theaters.keys())
            r1_c1, r1_c2 = st.columns(2)
            
            with r1_c1:
                target_theaters = st.multiselect(
                    "1\\. Select Theaters (Ordered by Preference)", 
                    options=t_opts,
                    default=[t_item['theatre_code']],
                    format_func=lambda x: cluster_theaters.get(x),
                    key=f"target_theaters_{t_item['theatre_code']}"
                )

                target_days = st.multiselect(
                    "2\\. Select Dates", 
                    options=cached_dates,
                    format_func=lambda x: datetime.strptime(x, "%m-%d-%Y").strftime("%b %d"),
                    default=[f_date])
            
            with r1_c2:
                global_reactive_codes = set()
                for d_str in target_days:
                    day_data = st.session_state.multi_day_raw.get(d_str)
                    if day_data:
                        for theater_show in day_data.get('shows', []):
                            if theater_show.get('TheatreCode') in target_theaters:
                                for movie in theater_show.get('Film', []):
                                    global_reactive_codes.add(movie.get('MasterMovieCode'))

                # 2. Define a formatter that injects the Title and NEW tag
                def format_movie_label(m_code):
                    meta = st.session_state.global_movie_catalog.get(m_code, {})
                    title = meta.get('title', 'Unknown Title')
                    is_new = meta.get('is_new', False)
                    return f"{title} (🔴 NEW)" if is_new else title

                # 3. Sort options by title for better UX
                sorted_codes = sorted(list(global_reactive_codes), 
                                     key=lambda x: st.session_state.global_movie_catalog.get(x, {}).get('title', ''))

                # 4. Multiselect now returns CODES, not TITLES
                target_movies = st.multiselect(
                    "3\\. Select Movies (Ordered by Preference)", 
                    options=sorted_codes,
                    format_func=format_movie_label,
                    key=f"target_movies_{t_item['theatre_code']}"
                )

                n_movies = len(target_movies)

                available_formats = sorted(list(set(
                    s['ScreenType'] for s in all_flat_data 
                    if s['Title'] in target_movies and s['TheaterCode'] in target_theaters
                ))) if target_movies else sorted(list(set(
                    s['ScreenType'] for s in all_flat_data 
                    if s['TheaterCode'] in target_theaters
                )))
                
                target_formats = st.multiselect(
                    "4\\. Preferred Formats", 
                    options=available_formats, 
                    placeholder="All",
                    key=f"target_formats_{t_item['theatre_code']}"
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
                b_val = st.slider("Break duration (min)", 30, 120, 60)
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

        # --- Anchor Show Selection ---
        with st.container(border=True):
            enable_anchor = st.checkbox("📍 Include a Booked (Anchor) Show", value=False)
            anchor_show = None

            if enable_anchor:
                st.info("Lock a booked showtime into your plan. The scheduler will build your itinerary around this fixed point, which may limit other options.")
                a_col1, a_col2, a_col3, a_col4  = st.columns(4)
                with a_col1:
                    # Filter based on target_theaters
                    a_theater = st.selectbox("Anchor Theater", 
                                             options=target_theaters, 
                                             format_func=lambda x: cluster_theaters.get(x))
                with a_col2:
                    # Filter based on target_days
                    a_day = st.selectbox("Anchor Day", 
                                         options=target_days,
                                         format_func=lambda x: datetime.strptime(x, "%m-%d-%Y").strftime("%b %d"))
                with a_col3:
                    # Pull movies available for that theater and day
                    a_day_data = st.session_state.multi_day_raw.get(a_day)
                    valid_anchor_codes = []
                    if a_day_data:
                        for ts in a_day_data.get('shows', []):
                            if ts.get('TheatreCode') == a_theater:
                                # Collect codes playing at this specific theater
                                theater_codes = [m.get('MasterMovieCode') for m in ts.get('Film', [])]
                                # Intersection of target_movies (codes) and theater codes
                                valid_anchor_codes = [c for c in target_movies if c in theater_codes]
                    
                    # Selectbox now returns the MasterMovieCode
                    a_movie_code = st.selectbox(
                        "Anchor Movie", 
                        options=sorted(valid_anchor_codes, key=lambda x: st.session_state.global_movie_catalog.get(x, {}).get('title', '')),
                        format_func=format_movie_label # Reuses your formatter with the "NEW" tag
                    )

                # Final step: Select the exact showtime
                with a_col4:
                    a_showtimes = []
                    if a_day_data and a_movie_code:
                        day_flat_anchor, _, _, _ = flatten_data(a_day_data)
                        # Filter using master_code instead of Title
                        a_showtimes = [s for s in day_flat_anchor if s['master_code'] == a_movie_code and s['TheaterCode'] == a_theater]
                    
                    selected_anchor = st.selectbox(
                        "Anchor Showtime", 
                        options=a_showtimes, 
                        format_func=lambda x: f"{x['Showtime'].strftime('%I:%M %p')} ({x['ScreenType']})"
                    )
                    anchor_show = selected_anchor
                
        if st.button("🚀 Generate Itineraries"):
            if len(target_movies) < 2:
                st.error("Please select at least 2 movies.")
            else:
                params = {
                    'start': t_start, 'end': t_end, 'buffer': buff, 'gap_cap': g_cap, 
                    'unlimited': unlimited, 'fudge': fudge, 'break_after': b_after, 
                    'long_buffer': b_val, 'formats': target_formats, 'theaters': target_theaters,
                    'primary_code': t_item['theatre_code'],
                    'strategy': strategy if len(target_days) > 1 else "Minimize Days",
                    'max_per_day': max_per_day if len(target_days) > 1 else n_movies
                }
                
                if len(target_days) > 1:
                    multi_itinerary = find_multi_day_itineraries(target_movies, target_days, params, drive_map,anchor_show)
            
                    if not multi_itinerary:
                        st.error("Could not find a valid multi-day schedule for these movies. Consider expanding selections and broadening filters.")
                    else:
                        st.success(f"🗓️ Multi-Day Plan Generated: {len(multi_itinerary)} days used.")
                        
                        total_movies = sum(len(p) for p in multi_itinerary.values())
                        total_hops = sum(calculate_path_score(p, params['primary_code'], drive_map)['hops'] for p in multi_itinerary.values())
                        sorted_plan_days = sorted(multi_itinerary.keys(), key=lambda x: datetime.strptime(x, '%m-%d-%Y'))
                        scheduled_titles = [s['Title'] for p in multi_itinerary.values() for s in p]
                        unscheduled = [m for m in target_movies if m not in scheduled_titles]

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
                            st.warning(f"⚠️ **Incomplete Schedule:** {len(unscheduled)} movies could not be fitted.")

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
                                    st.write(f"❌ **{m}**: Could not fit into the selected time windows or theater constraints.")
                
                else:
                    sched_date_str = target_days[0]
                    sched_date_obj = datetime.strptime(sched_date_str, '%m-%d-%Y').date()
                    day_data_raw = st.session_state.multi_day_raw.get(sched_date_str)
                    if day_data_raw:
                        day_flat_sched, _, _, _ = flatten_data(day_data_raw)

                        if enable_anchor and anchor_show:
                            paths = run_anchored_search(anchor_show, target_movies, sched_date_str, params, drive_map)
                        else:
                            paths = find_itineraries([], target_movies, day_flat_sched, params, sched_date_obj, drive_map)
                    else:
                        paths = []
                
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

                        # 1. Smart Marathon (Best overall Score)
                        add_selection(ranked_pool[0], "Smart Marathon (Best Efficiency)")

                        # 2. Absolute Marathon (Max Movies - strictly by count)
                        abs_mar = sorted(processed_paths, key=lambda x: (-x['count'], -x['score']))[0]
                        add_selection(abs_mar, "Absolute Marathon (Max Movies)")

                        # 3. Single-Theater Max (Filtered by 0 Hops)
                        st_p = sorted([pp for pp in processed_paths if pp['hops'] == 0], key=lambda x: (-x['score']))
                        if st_p: add_selection(st_p[0], "Single-Theater Max (Zero Hops)")

                        # 4. Priority Movie Match (Matches the first two selected movies)
                        if len(target_movies) >= 2:
                            top_two = set(target_movies[:2])
                            p_mov = sorted([pp for pp in processed_paths if top_two.issubset(set(s['Title'] for s in pp['path']))], key=lambda x: (-x['score']))
                            if p_mov: add_selection(p_mov[0], "Priority Movie Match (#1 & #2)")

                        # 5. Fill remaining slots with the next best optimized paths
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
                                        report = get_conflict_report(path, missing, all_flat_data, params, anchor_show, drive_map)
                                        for line in report: st.write(line)
else: st.info("Search for a theater in the sidebar to begin.")