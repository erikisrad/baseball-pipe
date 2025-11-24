import logging, requests
from datetime import datetime, timedelta
import baseball_pipe.utilities as u


logger = logging.getLogger(__name__)

SCHEDULE_URL_PREFIX = "https://statsapi.mlb.com/api/v1/schedule?"
SCHEDULE_URL_SUFFIX = ",game(content(media(epg)),editorial(preview,recap)),linescore,team,probablePitcher(note)"

def search_for_last_gameday(days_ago=0):
    while days_ago < 365:
        games = get_games_on_date(days_ago=days_ago)
        if games:
            date = u.get_date(days_ago=days_ago)
            logger.info(f"Found last game on date {date}")
            return (date, games)
        days_ago += 1
    raise Exception("No games found in the last year? wtf")

def get_games_on_date(date=None, days_ago=None):
    if not date:
        date = u.get_date(days_ago=days_ago)

    if isinstance(date, datetime):
        date = date.strftime("%Y-%m-%d")

    assert isinstance(date, str), "date must be a string or datetime object"

    if date.isdigit() and len(date) == 8: # like 20251120
        date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"

    schedule_url_options = [
        "sportId=1",
        f"startDate={date}",
        f"endDate={date}",
        "hydrate=broadcasts(all)"
    ]

    schedule_url = SCHEDULE_URL_PREFIX + "&".join(schedule_url_options) + SCHEDULE_URL_SUFFIX

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Connection": "close"}

    r = requests.get(schedule_url, headers=headers, verify=False)
    if not r.ok:
        raise Exception(f"Failed to fetch schedule for {date}: {r.status_code} {r.reason}")

    try:
        games = r.json()["dates"][0]["games"]
        assert len(games) > 0
        logger.debug(f"found {len(games)} games on date {date}")

    except (IndexError, AssertionError) as err:
        logger.warning(f"No games found on {date}. Response: {r.text}\n{err}")
        return []
        
    return games