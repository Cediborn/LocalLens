"""
LocalLens - Opening hours parser for OpenStreetMap data.

Parses OSM opening_hours strings and determines whether a place is open
at a given datetime. Handles common patterns including:
  - Simple ranges: "Mo-Fr 09:00-17:00"
  - Day lists: "Mo,We,Fr 10:00-18:00"
  - Multiple rules: "Mo-Fr 09:00-17:00; Sa 10:00-14:00"
  - "24/7" for always open
  - "off" for always closed
  - Overnight ranges: "22:00-02:00"
  - Seasonal/closed periods
  - PH (public holidays) handling (simplified)
"""

import re
from datetime import datetime, timedelta
from typing import Optional

DAY_MAP = {
    "mo": 0, "tu": 1, "we": 2, "th": 3,
    "fr": 4, "sa": 5, "su": 6,
}

DAY_NAMES = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

DAY_ABBR = {
    "monday": "mo", "tuesday": "tu", "wednesday": "we",
    "thursday": "th", "friday": "fr", "saturday": "sa", "sunday": "su",
    "mon": "mo", "tue": "tu", "wed": "we", "thu": "th",
    "fri": "fr", "sat": "sa", "sun": "su",
}


def _parse_time(t: str) -> Optional[int]:
    """Parse HH:MM to minutes from midnight. Returns None on failure."""
    t = t.strip()
    m = re.match(r'^(\d{1,2}):(\d{2})$', t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 48 or mi > 59:
        return None
    return h * 60 + mi


def _parse_day_range(part: str) -> list[int]:
    """Parse a day or day range like 'Mo' or 'Mo-Fr' into weekday indices."""
    part = part.strip().lower()
    if '-' in part:
        parts = part.split('-', 1)
        start_abbr = parts[0].strip()
        end_abbr = parts[1].strip()
        start = DAY_MAP.get(start_abbr)
        if start is None:
            start = DAY_MAP.get(DAY_ABBR.get(start_abbr, ''))
        end = DAY_MAP.get(end_abbr)
        if end is None:
            end = DAY_MAP.get(DAY_ABBR.get(end_abbr, ''))
        if start is None or end is None:
            return []
        days = []
        d = start
        while True:
            days.append(d)
            if d == end:
                break
            d = (d + 1) % 7
            if len(days) > 7:
                break
        return days
    d = DAY_MAP.get(part)
    if d is None:
        d = DAY_MAP.get(DAY_ABBR.get(part, ''))
    if d is not None:
        return [d]
    return []


def _parse_rule_time_ranges(time_part: str) -> list[tuple[int, int]]:
    """Parse time ranges like '09:00-17:00' or '09:00-12:00,14:00-18:00'."""
    ranges = []
    # Split on comma for multiple time ranges
    for segment in time_part.split(','):
        segment = segment.strip()
        if not segment:
            continue
        m = re.match(r'^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$', segment)
        if m:
            start = _parse_time(m.group(1))
            end = _parse_time(m.group(2))
            if start is not None and end is not None:
                ranges.append((start, end))
    return ranges


def _parse_single_rule(rule: str) -> list[tuple[list[int], list[tuple[int, int]]]]:
    """Parse a single rule like 'Mo-Fr 09:00-17:00' into (days, time_ranges)."""
    rule = rule.strip()
    if not rule or rule.lower() in ('off', 'closed'):
        return []

    # Check for 24/7
    if '24/7' in rule:
        return [([0, 1, 2, 3, 4, 5, 6], [(0, 1440)])]

    # Remove "PH" markers (public holidays - we can't determine these)
    rule = re.sub(r'\bPH\b', '', rule).strip()
    # Remove "SH" markers (school holidays)
    rule = re.sub(r'\bSH\b', '', rule).strip()
    # RemoveComments
    rule = re.sub(r'//.*$', '', rule).strip()

    if not rule:
        return []

    # Split into day part and time part
    # Days are alpha, times start with digits
    time_match = re.search(r'\d{1,2}:\d{2}', rule)
    if time_match:
        day_part = rule[:time_match.start()].strip().rstrip(',').strip()
        time_part = rule[time_match.start():].strip()
    else:
        day_part = rule.strip()
        time_part = ""

    # Parse days
    days = []
    if day_part:
        for day_group in day_part.split(','):
            day_group = day_group.strip()
            if day_group:
                days.extend(_parse_day_range(day_group))
    else:
        # If no days specified, applies to every day
        days = [0, 1, 2, 3, 4, 5, 6]

    # Parse time ranges
    time_ranges = _parse_rule_time_ranges(time_part) if time_part else [(0, 1440)]

    if days and time_ranges:
        return [(days, time_ranges)]
    return []


def _parse_opening_hours_string(oh: str) -> list[tuple[list[int], list[tuple[int, int]]]]:
    """Parse a full opening_hours string into rules."""
    if not oh:
        return []

    oh = oh.strip()

    # Remove wrapper quotes if present
    if oh.startswith('"') and oh.endswith('"'):
        oh = oh[1:-1].strip()

    # Split on semicolons for multiple rules
    rules = []
    for part in oh.split(';'):
        parsed = _parse_single_rule(part)
        rules.extend(parsed)

    return rules


def _minutes_in_range(start_min: int, end_min: int, check_min: int) -> bool:
    """Check if check_min falls within the range. Handles overnight ranges."""
    if start_min <= end_min:
        return start_min <= check_min < end_min
    else:
        # Overnight: e.g., 22:00-02:00
        return check_min >= start_min or check_min < end_min


def _find_next_opening(rules: list[tuple[list[int], list[tuple[int, int]]]],
                       check_min: int, check_weekday: int) -> Optional[tuple[int, int]]:
    """Find the next time a place opens, within 7 days. Returns (day_offset, minutes) or None."""
    best = None

    for day_offset in range(8):  # Check up to 7 days ahead
        check_day = (check_weekday + day_offset) % 7
        check_minute_of_day = check_min if day_offset == 0 else 0

        for days, time_ranges in rules:
            if check_day not in days:
                continue
            for start_min, end_min in time_ranges:
                if day_offset == 0 and start_min <= check_minute_of_day:
                    if end_min > check_minute_of_day:
                        # Already open
                        return None
                    # Check if this day's remaining time ranges could open later
                    # Already past end, check next range or next day
                    continue

                candidate = (day_offset, start_min)
                if best is None or candidate < best:
                    best = candidate

    return best


class OpenStatus:
    """Represents the open/closed status of a place."""
    def __init__(self, is_open: bool, status_text: str, hours_text: str = "",
                 next_open_text: str = "", has_hours_data: bool = False):
        self.is_open = is_open
        self.status_text = status_text  # "Open now", "Closed", "Unknown"
        self.hours_text = hours_text  # Human-readable hours string
        self.next_open_text = next_open_text  # "Opens at 09:00"
        self.has_hours_data = has_hours_data


def get_open_status(opening_hours_str: Optional[str],
                    check_time: Optional[datetime] = None) -> OpenStatus:
    """
    Determine if a place is open at the given time.

    Args:
        opening_hours_str: Raw OSM opening_hours value
        check_time: datetime to check (UTC). Uses current time if None.

    Returns:
        OpenStatus with is_open, status_text, and other details.
    """
    if not opening_hours_str or opening_hours_str.strip() in ('', 'Unknown', 'unknown'):
        return OpenStatus(
            is_open=False,
            status_text="Unknown",
            has_hours_data=False,
        )

    oh = opening_hours_str.strip()

    # Handle "24/7" directly
    if oh.lower() == '24/7':
        return OpenStatus(
            is_open=True,
            status_text="Open now",
            hours_text="Open 24/7",
            has_hours_data=True,
        )

    if check_time is None:
        check_time = datetime.utcnow()

    rules = _parse_opening_hours_string(oh)
    if not rules:
        return OpenStatus(
            is_open=False,
            status_text="Unknown",
            has_hours_data=False,
        )

    check_min = check_time.hour * 60 + check_time.minute
    check_weekday = check_time.weekday()  # Monday=0, Sunday=6

    # Check if currently open
    for days, time_ranges in rules:
        if check_weekday in days:
            for start_min, end_min in time_ranges:
                if _minutes_in_range(start_min, end_min, check_min):
                    # Format the hours for display
                    hours_text = _format_rules(rules)
                    return OpenStatus(
                        is_open=True,
                        status_text="Open now",
                        hours_text=hours_text,
                        has_hours_data=True,
                    )

    # Not currently open - find next opening
    next_open = _find_next_opening(rules, check_min, check_weekday)
    hours_text = _format_rules(rules)

    if next_open:
        day_offset, open_min = next_open
        open_h, open_m = divmod(open_min, 60)
        if day_offset == 0:
            next_text = f"Opens at {open_h:02d}:{open_m:02d}"
        elif day_offset == 1:
            next_text = f"Opens tomorrow at {open_h:02d}:{open_m:02d}"
        else:
            next_day = DAY_NAMES[(check_weekday + day_offset) % 7]
            next_text = f"Opens {next_day} at {open_h:02d}:{open_m:02d}"
    else:
        next_text = ""

    return OpenStatus(
        is_open=False,
        status_text="Closed" if next_text else "Unknown",
        hours_text=hours_text,
        next_open_text=next_text,
        has_hours_data=True,
    )


def _format_rules(rules: list[tuple[list[int], list[tuple[int, int]]]]) -> str:
    """Format parsed rules back into a human-readable string."""
    parts = []
    for days, time_ranges in rules:
        day_strs = []
        # Try to compress day ranges
        sorted_days = sorted(days)
        i = 0
        while i < len(sorted_days):
            start = sorted_days[i]
            end = start
            while i + 1 < len(sorted_days) and sorted_days[i + 1] == end + 1:
                i += 1
                end = sorted_days[i]
            if start == end:
                day_strs.append(DAY_NAMES[start])
            else:
                day_strs.append(f"{DAY_NAMES[start]}-{DAY_NAMES[end]}")
            i += 1

        time_strs = []
        for s, e in time_ranges:
            sh, sm = divmod(s, 60)
            eh, em = divmod(e, 60)
            time_strs.append(f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}")

        if day_strs and time_strs:
            parts.append(f"{', '.join(day_strs)} {', '.join(time_strs)}")
        elif day_strs:
            parts.append(', '.join(day_strs))

    return '; '.join(parts) if parts else ""


def is_open_at(opening_hours_str: Optional[str],
               check_time: Optional[datetime] = None) -> bool:
    """Simple check: is the place open? Returns False for unknown."""
    status = get_open_status(opening_hours_str, check_time)
    return status.is_open


def compute_open_score(opening_hours_str: Optional[str],
                       check_time: Optional[datetime] = None) -> float:
    """
    Compute a 0-1 score for time-awareness:
    - 1.0 = open now
    - 0.6 = has hours data, currently closed (will open later)
    - 0.3 = unknown hours (we can't tell)
    """
    status = get_open_status(opening_hours_str, check_time)
    if status.is_open:
        return 1.0
    elif status.has_hours_data:
        return 0.6
    else:
        return 0.3
