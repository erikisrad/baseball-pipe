from datetime import datetime
import logging
import aiohttp
import baseball_pipe.misc.utilities as u

logger = logging.getLogger(__name__)

SCHEDULE_URL_PREFIX = "https://statsapi.mlb.com/api/v1/schedule?"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Connection": "close"
}

async def get_games_on_date(session:aiohttp.ClientSession, start_date, broadcasts:bool=False):

    logger.info(f"fetching games on {start_date}")
    if isinstance(start_date, datetime):
        start_date = start_date.strftime("%Y-%m-%d")
    elif start_date.isdigit() and len(start_date) == 8:
        start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"

    assert isinstance(start_date, str), "start_date must be a string or datetime"

    schedule_url_options = [
        "sportId=1",
        f"startDate={start_date}",
        f"endDate={start_date}"
    ]

    if broadcasts:
        schedule_url_options.append("hydrate=broadcasts(all)")

    schedule_url = SCHEDULE_URL_PREFIX + "&".join(schedule_url_options)

    logger.info(f"sending request to {schedule_url}")
    async with session.get(schedule_url, headers=HEADERS, ssl=False) as res:
        logger.debug("awaiting response...")
        if res.status != 200:
            raise Exception(f"Failed to fetch schedule for {start_date}: {res.status} {res.reason}")
        res_json = await res.json()
        logger.debug(f"response received, status {res.status}")

    for date_obj in reversed(res_json.get("dates", [])):
        date_str = date_obj.get("date", "unknown")
        games = date_obj.get("games", [])
        if games:
            logger.info(f"found {len(games)} games on {date_str}")
            return games

    return []