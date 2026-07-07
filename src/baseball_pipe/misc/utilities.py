from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)


def safe_get(data, *keys, default=None):
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            logger.warning(f"missing key {'.'.join(map(str, keys))} (failed at {key!r}), using default {default!r}")
            return default
        data = data[key]
    return data


def get_date(days_ago=None, start_date=None):
    if type(start_date) == str:
        for fmt in ["%Y%m%d", "%Y-%m-%d", "%m-%d-%Y", "%Y-%m-%dT%H:%M:%SZ"]:
            try:
                date = datetime.strptime(start_date, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Unable to parse date: {start_date}")
    elif type(start_date) == datetime:
        date = start_date
    else:
        date = datetime.now()

    if days_ago:
        date = (date - timedelta(days=days_ago))

    return date

def get_local_tz_offset(tz: str = None):
    zone = ZoneInfo(tz) if tz else None
    now_local = datetime.now(zone).astimezone(zone) if zone else datetime.now().astimezone()
    offset = now_local.utcoffset()
    hours = offset.total_seconds() / 3600
    return f"UTC+{hours:g}" if hours >= 0 else f"UTC{hours:g}"

def pretty_print_date(date):
    pretty_time = date.strftime("%A, %B %d %Y")
    return pretty_time

def machine_print_date(date):
    return date.strftime("%Y%m%d")

def pretty_print_time_in_tz(utc_str, tz):
    dt_utc = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    dt_local = dt_utc.astimezone(ZoneInfo(tz))
    pretty_time = dt_local.strftime("%I:%M%p").lstrip("0")
    pretty_time = pretty_time[:-1] if pretty_time.endswith("M") else pretty_time
    return pretty_time.lower()