import aiohttp
import logging, requests
from datetime import datetime, timedelta
import baseball_pipe.utilities as u


logger = logging.getLogger(__name__)

SCHEDULE_URL_PREFIX = "https://statsapi.mlb.com/api/v1/schedule?"
#SCHEDULE_URL_SUFFIX = ",game(content(media(epg)),editorial(preview,recap)),linescore,team,probablePitcher(note)"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Connection": "close"
}

async def get_game_content(gamePK, session:aiohttp.ClientSession=None):

    content_url = SCHEDULE_URL_PREFIX + f"gamePk={gamePK}&hydrate=team,broadcasts(all),game(content(media(all)editorial(all)))"
    
    own_session = False
    if session is None:
        session = aiohttp.ClientSession()
        own_session = True

    try:
        logger.info(f"sending request to {content_url}")

        async with session.get(content_url, headers=HEADERS, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"failed to fetch content game {gamePK}: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        game = res_json["dates"][0]["games"][0]
        broadcasts = game.get("broadcasts", [])
        logger.info(f"found {len(broadcasts)} broadcasts for game {gamePK}")
        #assert len(broadcasts) > 0, f"no broadcasts found for game {gamePK}"
        return game
        
    finally:
        if own_session:
            await session.close()

async def get_games_on_date(start_date, end_date=None, session:aiohttp.ClientSession=None):

    if isinstance(start_date, datetime):
        start_date = start_date.strftime("%Y-%m-%d")
    elif start_date.isdigit() and len(start_date) == 8:
        start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"

    assert isinstance(start_date, str), "start_date must be a string or datetime"

    if end_date:

        if isinstance(end_date, datetime):
            end_date = end_date.strftime("%Y-%m-%d")
        elif end_date.isdigit() and len(end_date) == 8:
            end_date = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

        assert isinstance(end_date, str), "end_date must be a string or datetime"

    else:
        end_date = start_date

    schedule_url_options = [
        "sportId=1",
        f"startDate={end_date}",
        f"endDate={start_date}",
        "hydrate=broadcasts(all)"
    ]

    schedule_url = SCHEDULE_URL_PREFIX + "&".join(schedule_url_options)# + SCHEDULE_URL_SUFFIX

    own_session = False
    if session is None:
        session = aiohttp.ClientSession()
        own_session = True

    try:
        logger.info(f"sending request to {schedule_url}")
        async with session.get(schedule_url, headers=HEADERS, ssl=False) as res:
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