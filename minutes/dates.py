"""Meeting date helpers.

The meeting date comes from when the recording started (session.json),
or from --date when processing a file by hand.
"""

import datetime as dt
import re

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


def weekday_index(name):
    return WEEKDAYS.index(name.strip().lower())


def ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def long_date(d):
    """OCTOBER 15th, 2026"""
    return f"{MONTHS[d.month - 1].upper()} {ordinal(d.day)}, {d.year}"


def short_date(d):
    """October 29th"""
    return f"{MONTHS[d.month - 1].capitalize()} {ordinal(d.day)}"


def next_meeting(d, weekday_name):
    days = (weekday_index(weekday_name) - d.weekday()) % 7 or 7
    return d + dt.timedelta(days=days)


def scheduled_start(cfg, day):
    h, m = (int(x) for x in cfg["meeting"]["start"].split(":"))
    return dt.datetime.combine(day, dt.time(h, m))


def time_label(cfg, started_at):
    """The configured label for the regular meeting, else the real start
    time rounded to 15 minutes (for an extra meeting on another day)."""
    if started_at is None:
        return cfg["meeting"]["time_label"]
    sched = scheduled_start(cfg, started_at.date())
    same_day = started_at.weekday() == weekday_index(cfg["meeting"]["weekday"])
    if same_day and abs((started_at - sched).total_seconds()) <= 45 * 60:
        return cfg["meeting"]["time_label"]
    t = started_at + dt.timedelta(minutes=7)
    t = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
    hour = t.hour % 12 or 12
    ampm = "AM" if t.hour < 12 else "PM"
    return f"{hour} {ampm}" if t.minute == 0 else f"{hour}:{t.minute:02d} {ampm}"


def parse_date_arg(s):
    return dt.date.fromisoformat(s)


_LONG = re.compile(r"(january|february|march|april|may|june|july|august|september|october|november|december)"
                   r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I)


def find_long_date(text):
    """Find 'OCTOBER 15th, 2026' style dates in a minutes subtitle."""
    m = _LONG.search(text or "")
    if not m:
        return None
    return dt.date(int(m.group(3)), MONTHS.index(m.group(1).lower()) + 1, int(m.group(2)))


def file_stem(cfg, d):
    return cfg["output"]["filename"].format(MM=f"{d.month:02d}", DD=f"{d.day:02d}", YYYY=d.year)
