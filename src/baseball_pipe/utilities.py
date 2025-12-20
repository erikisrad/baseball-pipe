from datetime import datetime, timedelta
import random
import string
import pytz

def format_bandwidth(bps):

    if not isinstance(bps, int):
        try:
            bps = int(bps)
        except ValueError as err:
            print("Bandwidth must be an integer value in bps")
            raise err

    if bps >= 1_000_000_000:
        return f"{bps / 1_000_000_000:.2f} Gbps"
    elif bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    elif bps >= 1_000:
        return f"{bps / 1_000:.2f} kbps"
    else:
        return f"{bps} bps"

def get_current_datetime():
    return datetime.now(tz=pytz.UTC)

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

def pretty_print_date(date):
    pretty_time = date.strftime("%A, %B %d %Y")
    return pretty_time

def machine_print_date(date):
    return date.strftime("%Y%m%d")

def pretty_print_time(date):
    pretty_time = date.strftime("%I:%M %p").lstrip("0")
    return pretty_time

def gen_random_string(n):
    """Generate a random string of uppercase letters and digits of length n."""
    return "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(n))