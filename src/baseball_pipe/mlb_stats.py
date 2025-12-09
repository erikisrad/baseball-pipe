import aiohttp
import logging, requests
from datetime import datetime, timedelta
import baseball_pipe.utilities as u


logger = logging.getLogger(__name__)

SCHEDULE_URL_PREFIX = "https://statsapi.mlb.com/api/v1/schedule?"
#SCHEDULE_URL_SUFFIX = ",game(content(media(epg)),editorial(preview,recap)),linescore,team,probablePitcher(note)"


async def get_games_on_date(start_date, end_date=None, session: aiohttp.ClientSession = None):
    """
    Fetch all games within a date range in a single API call.
    Returns a dict with dates as keys and game lists as values.
    """
    if isinstance(start_date, datetime):
        start_date = start_date.strftime("%Y-%m-%d")
    elif start_date.isdigit() and len(start_date) == 8:
        start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"

    assert isinstance(start_date, str), "start_date must be a string or datetime object"

    if end_date:

        if isinstance(end_date, datetime):
            end_date = end_date.strftime("%Y-%m-%d")
        elif end_date.isdigit() and len(end_date) == 8:
            end_date = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

        assert isinstance(end_date, str), "end_date must be a string or datetime object"

    else:
        end_date = start_date


    schedule_url_options = [
        "sportId=1",
        f"startDate={end_date}",
        f"endDate={start_date}",
        #"hydrate=broadcasts(all)"
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Connection": "close"
    }
    schedule_url = SCHEDULE_URL_PREFIX + "&".join(schedule_url_options)# + SCHEDULE_URL_SUFFIX

    own_session = False
    if session is None:
        session = aiohttp.ClientSession()
        own_session = True

    try:
        logger.info(f"sending request to {schedule_url}")
        async with session.get(schedule_url, headers=headers, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to fetch schedule for range {start_date} to {end_date}: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        for date_obj in reversed(res_json.get("dates", [])):
            date_str = date_obj.get("date", "unknown")
            games = date_obj.get("games", [])
            if games:
                logger.info(f"found {len(games)} games on date {date_str}")
                return (u.get_date(start_date=date_str), games)

    finally:
        if own_session:
            await session.close()
    return (u.get_date(start_date=start_date), [])

# async def get_games_on_date(date=None, days_ago=None):
#     if not date:
#         date = u.get_date(days_ago=days_ago)

#     if isinstance(date, datetime):
#         date = date.strftime("%Y-%m-%d")

#     assert isinstance(date, str), "date must be a string or datetime object"

#     if date.isdigit() and len(date) == 8: # like 20251120
#         date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"

#     schedule_url_options = [
#         "sportId=1",
#         f"startDate={date}",
#         f"endDate={date}",
#         "hydrate=broadcasts(all)"
#     ]

#     headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
#                "Connection": "close"}
#     schedule_url = SCHEDULE_URL_PREFIX + "&".join(schedule_url_options) + SCHEDULE_URL_SUFFIX
#     async with aiohttp.ClientSession() as session:
#         async with session.get(schedule_url, headers=headers) as res:
#             if res.status != 200:
#                 raise Exception(f"Failed to fetch schedule for {date}: {res.status} {res.reason}")
#             res_json = await res.json()

#     try:
#         games = res_json["dates"][0]["games"]
#         assert len(games) > 0
#         logger.debug(f"found {len(games)} games on date {date}")

#     except (IndexError, AssertionError) as err:
#         logger.warning(f"No games found on {date}. Response: {res.text}\n{err}")
#         return []
        
#     return games