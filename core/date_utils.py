"""Date parsing and chronological sorting utilities for Regal Pro."""

from datetime import datetime
from typing import Any, List, Optional, Tuple


def parse_and_sort_dates(date_list: List[Any], reference_date: Optional[datetime] = None) -> Tuple[List[str], datetime]:
    """Parse a list of heterogeneous date strings or objects and return them chronologically sorted.

    Handles "%b %d" (e.g. "Nov 01", "Oct 31"), "%m-%d-%Y", "%Y-%m-%d", and datetime objects.
    Intelligently handles year boundaries (e.g. current month in Q4, show date in Q1 -> next year).

    Returns:
        Tuple of (sorted_formatted_strings: List[str], earliest_datetime: datetime).
    """
    if not date_list:
        return [], datetime.max

    ref = reference_date or datetime.now()
    curr_year = ref.year
    curr_month = ref.month

    parsed = []
    for d in date_list:
        if isinstance(d, datetime):
            parsed.append((d, d.strftime("%b %d")))
        elif isinstance(d, str):
            clean_d = d.strip()
            if not clean_d:
                continue

            # 1. Try "%b %d" (e.g. "Oct 31")
            try:
                # Determine provisional year based on month name prefix
                test_month = datetime.strptime(clean_d.split()[0], "%b").month
                year = curr_year
                if curr_month >= 9 and test_month <= 3:
                    year += 1
                elif curr_month <= 3 and test_month >= 9:
                    year -= 1
                dt = datetime.strptime(f"{year} {clean_d}", "%Y %b %d")
                parsed.append((dt, dt.strftime("%b %d")))
                continue
            except Exception:
                pass

            # 2. Try "%m-%d-%Y"
            try:
                dt = datetime.strptime(clean_d, "%m-%d-%Y")
                parsed.append((dt, dt.strftime("%b %d")))
                continue
            except Exception:
                pass

            # 3. Try "%Y-%m-%d"
            try:
                dt = datetime.strptime(clean_d, "%Y-%m-%d")
                parsed.append((dt, dt.strftime("%b %d")))
                continue
            except Exception:
                pass

            # Fallback for unparseable strings
            parsed.append((datetime.max, clean_d))

    if not parsed:
        return [str(d) for d in date_list], datetime.max

    # Sort strictly chronologically
    parsed.sort(key=lambda x: x[0])

    # Deduplicate while preserving chronological order
    seen = set()
    result_strings = []
    for dt, s in parsed:
        if s not in seen:
            seen.add(s)
            result_strings.append(s)

    return result_strings, parsed[0][0]
