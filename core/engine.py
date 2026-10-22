"""Pure Python scheduling engine decoupled from Streamlit UI."""

from datetime import datetime, date, time as dt_time, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .constants import (
    UNLIMITED_GAP_MINS,
    FUDGE_FACTOR_MINS,
    DEFAULT_MOVIE_DURATION,
    DEFAULT_DRIVE_MINS,
    DEFAULT_DRIVE_MILES,
    PAST_SHOW_CUTOFF_MINS,
    SCORE_MOVIE_WEIGHT,
    SCORE_HOP_PENALTY,
    SCORE_MILE_PENALTY,
    SCORE_GAP_PENALTY,
)
from .models import PathScore, SchedulerConstraints


class SchedulerEngine:
    """Core domain search and optimization engine for movie itineraries."""

    def __init__(
        self,
        drive_stats_fn: Optional[Callable[[str, str], Tuple[int, float]]] = None,
        movie_meta: Optional[Dict[str, Dict[str, Any]]] = None,
        theater_name_map: Optional[Dict[str, str]] = None,
    ):
        """Initialize engine with optional lookup helpers.

        Args:
            drive_stats_fn: Callable taking (code_a, code_b) -> (drive_minutes, drive_miles).
            movie_meta: Dictionary mapping movie master_code -> metadata dict.
            theater_name_map: Dictionary mapping theater_code -> theater name.
        """
        self.drive_stats_fn = drive_stats_fn or self._default_drive_stats
        self.movie_meta = movie_meta or {}
        self.theater_name_map = theater_name_map or {}

    @staticmethod
    def _default_drive_stats(code_a: str, code_b: str) -> Tuple[int, float]:
        if code_a == code_b:
            return 0, 0.0
        return DEFAULT_DRIVE_MINS, float(DEFAULT_DRIVE_MILES)

    def calculate_path_score(
        self,
        path: List[Dict[str, Any]],
        primary_code: str = "",
        drive_stats_fn: Optional[Callable[[str, str], Tuple[int, float]]] = None,
    ) -> Dict[str, Any]:
        """Calculate composite efficiency and travel score for a daily movie path.

        Weights:
            +250 per movie
            -40 per theater hop
            -2 per driving mile
            -0.1 per minute of idle gap
        """
        drive_fn = drive_stats_fn or self.drive_stats_fn
        movie_count = len(path)
        hops = 0
        total_miles = 0.0
        total_gap = 0
        total_duration = 0

        for i in range(len(path)):
            s = path[i]
            total_duration += s.get('Duration', DEFAULT_MOVIE_DURATION)

            if i < len(path) - 1:
                nxt = path[i + 1]
                curr_end = s['Showtime'] + timedelta(minutes=s.get('Duration', DEFAULT_MOVIE_DURATION))
                total_gap += int((nxt['Showtime'] - curr_end).total_seconds() / 60)

                if s['TheaterCode'] != nxt['TheaterCode']:
                    hops += 1
                    _, t_miles = drive_fn(s['TheaterCode'], nxt['TheaterCode'])
                    total_miles += t_miles

        score = (
            (movie_count * SCORE_MOVIE_WEIGHT)
            - (hops * SCORE_HOP_PENALTY)
            - (total_miles * SCORE_MILE_PENALTY)
            - (total_gap * SCORE_GAP_PENALTY)
        )

        return PathScore(
            score=round(score, 2),
            count=movie_count,
            hops=hops,
            miles=round(total_miles, 2),
            gap=total_gap,
            duration=total_duration,
        ).to_dict()

    def find_itineraries(
        self,
        current_path: List[Dict[str, Any]],
        remaining_codes: List[str],
        screenings: List[Dict[str, Any]],
        params: Any,
        selected_date: date,
        drive_stats_fn: Optional[Callable[[str, str], Tuple[int, float]]] = None,
        current_time: Optional[datetime] = None,
    ) -> List[List[Dict[str, Any]]]:
        """Depth-first branch-and-bound search for valid daily movie itineraries."""
        drive_fn = drive_stats_fn or self.drive_stats_fn

        # Coerce params to SchedulerConstraints if needed
        c: SchedulerConstraints = (
            params if isinstance(params, SchedulerConstraints) else SchedulerConstraints.from_dict(params)
        )

        if len(current_path) >= c.max_per_day:
            return []

        valid_paths: List[List[Dict[str, Any]]] = []

        if c.extreme_mode:
            window_start = datetime.combine(selected_date, dt_time(0, 1))
            window_end = datetime.combine(selected_date, dt_time(23, 59)) + timedelta(hours=6)
        else:
            window_start = datetime.combine(selected_date, c.start)
            window_end = datetime.combine(selected_date, c.end)

        if window_end <= window_start:
            window_end += timedelta(days=1)
        elif c.end == dt_time(23, 59):
            window_end += timedelta(hours=6)

        for m_code in remaining_codes:
            potential_shows = [
                s for s in screenings
                if s['master_code'] == m_code and s['TheaterCode'] in c.theaters
            ]

            if current_time and selected_date == current_time.date():
                cutoff = current_time - timedelta(minutes=PAST_SHOW_CUTOFF_MINS)
                potential_shows = [s for s in potential_shows if s['Showtime'] >= cutoff]

            if c.formats:
                potential_shows = [s for s in potential_shows if s.get('ScreenType') in c.formats]

            for s in potential_shows:
                show_start = s['Showtime']
                if c.extreme_mode:
                    calc_dur = UNLIMITED_GAP_MINS
                else:
                    dur = s.get('Duration', 0)
                    calc_dur = dur if dur > 0 else DEFAULT_MOVIE_DURATION
                show_end = show_start + timedelta(minutes=calc_dur)

                if show_start < window_start or show_end > window_end:
                    continue

                if current_path:
                    prev = current_path[-1]
                    prev_dur = prev.get('Duration', 0)
                    prev_end = prev['Showtime'] + timedelta(
                        minutes=prev_dur if prev_dur > 0 else DEFAULT_MOVIE_DURATION
                    )
                    if c.fudge:
                        prev_end -= timedelta(minutes=FUDGE_FACTOR_MINS)

                    drive_time = 0
                    if s['TheaterCode'] != prev['TheaterCode']:
                        drive_time, _ = drive_fn(s['TheaterCode'], prev['TheaterCode'])

                    req_buffer = c.long_buffer if c.break_after == len(current_path) else c.buffer
                    total_min_gap = drive_time + req_buffer

                    if c.extreme_mode:
                        prev_end = prev['Showtime'] + timedelta(minutes=UNLIMITED_GAP_MINS)
                        total_min_gap = 0

                    # Unlimited 91-min start gap rule
                    if c.unlimited and show_start < prev['Showtime'] + timedelta(minutes=UNLIMITED_GAP_MINS):
                        continue

                    # Overlap / travel gap rule
                    if show_start < prev_end + timedelta(minutes=total_min_gap):
                        continue

                    # Max gap cap rule
                    if (show_start - prev_end).total_seconds() / 60 > c.gap_cap and not c.extreme_mode:
                        continue

                new_rem = [code for code in remaining_codes if code != m_code]
                sub = self.find_itineraries(
                    current_path + [s],
                    new_rem,
                    screenings,
                    c,
                    selected_date,
                    drive_stats_fn=drive_fn,
                    current_time=current_time,
                )
                if not sub:
                    valid_paths.append(current_path + [s])
                else:
                    valid_paths.extend(sub)

        return valid_paths if valid_paths else ([current_path] if current_path else [])

    def run_anchored_search(
        self,
        anchor_show: Dict[str, Any],
        target_codes: List[str],
        day_screenings: List[Dict[str, Any]],
        params: Any,
        selected_date: date,
        drive_stats_fn: Optional[Callable[[str, str], Tuple[int, float]]] = None,
        current_time: Optional[datetime] = None,
    ) -> List[List[Dict[str, Any]]]:
        """Perform search anchored to a specific chosen screening."""
        all_paths = self.find_itineraries(
            [],
            target_codes,
            day_screenings,
            params,
            selected_date,
            drive_stats_fn=drive_stats_fn,
            current_time=current_time,
        )

        anchored_results = []
        for path in all_paths:
            is_match = any(
                s.get('master_code') == anchor_show.get('master_code')
                and s.get('Showtime') == anchor_show.get('Showtime')
                and s.get('TheaterCode') == anchor_show.get('TheaterCode')
                for s in path
            )
            if is_match:
                anchored_results.append(path)

        if not anchored_results:
            return [[anchor_show]]

        return anchored_results

    def find_multi_day_itineraries(
        self,
        target_codes: List[str],
        target_days: List[str],
        params: Any,
        day_screenings_map: Dict[str, List[Dict[str, Any]]],
        anchor_show: Optional[Dict[str, Any]] = None,
        drive_stats_fn: Optional[Callable[[str, str], Tuple[int, float]]] = None,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Distribute target movies across multiple days."""
        drive_fn = drive_stats_fn or self.drive_stats_fn
        c: SchedulerConstraints = (
            params if isinstance(params, SchedulerConstraints) else SchedulerConstraints.from_dict(params)
        )

        itinerary_by_day: Dict[str, List[Dict[str, Any]]] = {}
        remaining_codes = list(target_codes)
        max_per_day = c.max_per_day if c.max_per_day < len(target_codes) else len(target_codes)

        if anchor_show:
            a_day_str = anchor_show['Showtime'].strftime('%m-%d-%Y')
            a_date_obj = anchor_show['Showtime'].date()
            anchor_day_screenings = day_screenings_map.get(a_day_str, [])
            anchor_day_options = self.run_anchored_search(
                anchor_show,
                target_codes,
                anchor_day_screenings,
                c,
                a_date_obj,
                drive_stats_fn=drive_fn,
                current_time=current_time,
            )

            if anchor_day_options:
                best_a_path = sorted(
                    anchor_day_options,
                    key=lambda x: -self.calculate_path_score(x, c.primary_code, drive_fn)['score'],
                )[0]
                itinerary_by_day[a_day_str] = best_a_path

                for s in best_a_path:
                    if s['master_code'] in remaining_codes:
                        remaining_codes.remove(s['master_code'])

        sorted_days = sorted(
            [d for d in target_days if d != (anchor_show['Showtime'].strftime('%m-%d-%Y') if anchor_show else None)],
            key=lambda x: datetime.strptime(x, '%m-%d-%Y'),
        )

        if c.strategy == "Minimize Days":
            for i, d_str in enumerate(sorted_days):
                if not remaining_codes:
                    break

                d_obj = datetime.strptime(d_str, '%m-%d-%Y').date()
                day_flat = day_screenings_map.get(d_str, [])

                all_paths = self.find_itineraries(
                    [],
                    remaining_codes,
                    day_flat,
                    c,
                    d_obj,
                    drive_stats_fn=drive_fn,
                    current_time=current_time,
                )
                if not all_paths:
                    continue

                candidates = sorted(
                    [p for p in all_paths if len(p) <= max_per_day],
                    key=lambda x: (-len(x), -self.calculate_path_score(x, c.primary_code, drive_fn)['score']),
                )[:5]

                best_path_for_today = None
                max_future_yield = -1

                if i < len(sorted_days) - 1:
                    for cand in candidates:
                        cand_codes = [s['master_code'] for s in cand]
                        mock_remaining = [m for m in remaining_codes if m not in cand_codes]

                        next_day_str = sorted_days[i + 1]
                        nd_obj = datetime.strptime(next_day_str, '%m-%d-%Y').date()
                        next_day_flat = day_screenings_map.get(next_day_str, [])
                        if next_day_flat:
                            next_day_paths = self.find_itineraries(
                                [],
                                mock_remaining,
                                next_day_flat,
                                c,
                                nd_obj,
                                drive_stats_fn=drive_fn,
                                current_time=current_time,
                            )
                            next_day_yield = (
                                max([len(p) for p in next_day_paths if len(p) <= max_per_day])
                                if next_day_paths
                                else 0
                            )
                        else:
                            next_day_yield = 0

                        if (len(cand) + next_day_yield) > max_future_yield:
                            max_future_yield = len(cand) + next_day_yield
                            best_path_for_today = cand
                else:
                    best_path_for_today = candidates[0] if candidates else None

                if best_path_for_today:
                    itinerary_by_day[d_str] = best_path_for_today
                    for s in best_path_for_today:
                        remaining_codes.remove(s['master_code'])

        else:  # Maximize Compactness
            global_pool = []
            for d_str in sorted_days:
                d_obj = datetime.strptime(d_str, '%m-%d-%Y').date()
                day_flat = day_screenings_map.get(d_str, [])
                paths = self.find_itineraries(
                    [],
                    remaining_codes,
                    day_flat,
                    c,
                    d_obj,
                    drive_stats_fn=drive_fn,
                    current_time=current_time,
                )
                for p in paths:
                    if len(p) <= max_per_day:
                        stats = self.calculate_path_score(p, c.primary_code, drive_fn)
                        global_pool.append({'date': d_str, 'path': p, 'score': stats['score']})

            global_pool.sort(key=lambda x: -x['score'])
            assigned_dates: Set[str] = set()
            for entry in global_pool:
                if not remaining_codes:
                    break
                if entry['date'] in assigned_dates:
                    continue

                needed_in_path = [s for s in entry['path'] if s['master_code'] in remaining_codes]

                if len(needed_in_path) == len(entry['path']):
                    itinerary_by_day[entry['date']] = entry['path']
                    assigned_dates.add(entry['date'])
                    for s in entry['path']:
                        remaining_codes.remove(s['master_code'])

        return itinerary_by_day

    def get_conflict_report(
        self,
        path: List[Dict[str, Any]],
        missing_codes: List[str],
        all_screenings: List[Dict[str, Any]],
        params: Any,
        anchor_show: Optional[Dict[str, Any]] = None,
        movie_meta: Optional[Dict[str, Dict[str, Any]]] = None,
        theater_name_map: Optional[Dict[str, str]] = None,
        drive_stats_fn: Optional[Callable[[str, str], Tuple[int, float]]] = None,
    ) -> List[str]:
        """Explain why specific missing target movies could not fit into an itinerary."""
        if not missing_codes:
            return ["✅ All your selected movies fit into this path!"]

        meta_lookup = movie_meta if movie_meta is not None else self.movie_meta
        name_lookup = theater_name_map if theater_name_map is not None else self.theater_name_map
        drive_fn = drive_stats_fn or self.drive_stats_fn

        c: SchedulerConstraints = (
            params if isinstance(params, SchedulerConstraints) else SchedulerConstraints.from_dict(params)
        )

        conflicts: List[str] = []
        path_theaters = {s['TheaterCode'] for s in path}
        if anchor_show:
            path_theaters.add(anchor_show['TheaterCode'])
        full_path = sorted(path + ([anchor_show] if anchor_show else []), key=lambda x: x['Showtime'])

        for m_code in missing_codes:
            meta = meta_lookup.get(m_code, {})
            m_title = meta.get('title', f"Unknown Movie ({m_code})")

            m_shows_in_pool = [
                s for s in all_screenings
                if s['master_code'] == m_code and s['TheaterCode'] in c.theaters
            ]

            if not m_shows_in_pool:
                conflicts.append(f"❌ **{m_title}**: Not playing at your selected theater(s) today.")
                continue

            m_shows_in_path_theater = [s for s in m_shows_in_pool if s['TheaterCode'] in path_theaters]

            if not m_shows_in_path_theater:
                other_theaters = {s['TheaterCode'] for s in m_shows_in_pool}
                t_names = [name_lookup.get(str(tc), f"Regal {tc}") for tc in other_theaters]
                conflicts.append(
                    f"🚗 **{m_title}**: Only playing at **{', '.join(t_names)}**. (Path restricted to current theater)."
                )
                continue

            any_valid = False
            failure_details = []

            for ms in m_shows_in_pool:
                ms_start = ms['Showtime']
                ms_dur = ms.get('Duration', DEFAULT_MOVIE_DURATION)
                ms_end = ms_start + timedelta(minutes=ms_dur)
                reasons = []

                for ps in full_path:
                    ps_start = ps['Showtime']
                    ps_dur = ps.get('Duration', DEFAULT_MOVIE_DURATION)
                    ps_end = ps_start + timedelta(minutes=ps_dur)

                    # Check 1: Overlap
                    if not (ms_end <= ps_start or ms_start >= ps_end):
                        reasons.append((1, f"Overlaps with **{ps['Title']}** ({ps_start.strftime('%I:%M %p')})."))
                        break

                    # Check 2: Travel & Gap
                    is_after = ms_start >= ps_end
                    is_before = ms_end <= ps_start

                    if is_after:
                        is_neighbor = not any(
                            x['Showtime'] >= ps_end and x['Showtime'] <= ms_start for x in full_path if x != ps
                        )
                    else:
                        x_dur = lambda item: item.get('Duration', DEFAULT_MOVIE_DURATION)
                        is_neighbor = not any(
                            (x['Showtime'] + timedelta(minutes=x_dur(x))) >= ms_end
                            and x['Showtime'] <= ps_start
                            for x in full_path
                            if x != ps
                        )

                    if is_neighbor:
                        gap = (
                            int(abs((ms_start - ps_end).total_seconds() / 60))
                            if is_after
                            else int(abs((ps_start - ms_end).total_seconds() / 60))
                        )
                        travel_time, _ = drive_fn(ms['TheaterCode'], ps['TheaterCode'])

                        if gap < (travel_time + c.buffer):
                            reasons.append((2, f"Insufficient travel time to/from **{ps['Title']}**."))
                        elif gap > c.gap_cap:
                            direction = "after" if is_after else "before"
                            reasons.append((5, f"Wait time {direction} **{ps['Title']}** ({gap}m) exceeds your Max Gap."))

                if not reasons:
                    any_valid = True
                    break
                else:
                    reasons.sort()
                    failure_details.append(reasons[0])

            if not any_valid:
                if failure_details:
                    failure_details.sort()
                    detail = failure_details[0][1]
                else:
                    detail = "Could not find a slot that respects your buffer/gap settings."

                conflicts.append(f"❌ **{m_title}**: {detail}")

        return conflicts
