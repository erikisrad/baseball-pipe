from datetime import datetime, timedelta
import logging
import aiohttp
import baseball_pipe.misc.utilities as u
import baseball_pipe.misc.header_handler as e

logger = logging.getLogger(__name__)

SCHEDULE_URL_PREFIX = "https://statsapi.mlb.com/api/v1/schedule?"
LIVE_FEED_URL_PREFIX = "https://statsapi.mlb.com/api/v1.1/game/"

UNKNOWN_TIME = object()

async def get_games_on_date(session:aiohttp.ClientSession, start_date, broadcasts:bool=False):

    if isinstance(start_date, datetime):
        start_date = start_date.strftime("%Y-%m-%d")
    elif start_date.isdigit() and len(start_date) == 8:
        start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    logger.info(f"fetching games on {start_date}")

    assert isinstance(start_date, str), "start_date must be a string or datetime"

    schedule_url_options = [
        "sportId=1",
        f"startDate={start_date}",
        f"endDate={start_date}"
    ]

    hydrations = [
        "linescore"
    ]

    if broadcasts:
        hydrations.append("broadcasts(all)")

    if schedule_url_options:
        schedule_url = SCHEDULE_URL_PREFIX + "&".join(schedule_url_options)
    if hydrations:
        schedule_url += "&hydrate=" + ",".join(hydrations)

    logger.info(f"sending request to {schedule_url}")
    async with session.get(schedule_url, headers=e.AGENT_HEADER, ssl=False) as res:
        if res.status != 200:
            raise Exception(f"Failed to fetch schedule for {start_date}: {res.status} {res.reason}")
        res_json = await res.json()

    for date_obj in reversed(res_json.get("dates", [])):
        date_str = date_obj.get("date", "unknown")
        games = date_obj.get("games", [])
        if games:
            logger.info(f"found {len(games)} games on {date_str}")
            return games

    return []

async def get_game_content(gamePK, session:aiohttp.ClientSession):

    content_url = SCHEDULE_URL_PREFIX + f"gamePk={gamePK}"

    hydrations = [
        "team",
        "broadcasts(all)",
        "game(content(media(all)editorial(all)))",
        "venue(timezone)"
    ]

    if hydrations:
        content_url += "&hydrate=" + ",".join(hydrations)

    logger.info(f"sending request to {content_url}")

    async with session.get(content_url, headers=e.AGENT_HEADER, ssl=False) as res:
        if res.status != 200:
            raise Exception(f"failed to fetch content game {gamePK}: {res.status} {res.reason}")
        res_json = await res.json()

    try:
        game = res_json["dates"][-1]["games"][-1]
    except (KeyError, IndexError) as err:
        logger.error(f"failed to parse game content for {gamePK}: {err}")
        game = {}

    return game

async def get_game_start_end_times(gamePK,
                                    session:aiohttp.ClientSession,
                                    start_buffer:float=0,
                                    end_buffer:float=2):

    start_buffer = timedelta(minutes=start_buffer)
    end_buffer = timedelta(minutes=end_buffer)
    live_feed_url = f"{LIVE_FEED_URL_PREFIX}{gamePK}/feed/live"

    logger.info(f"sending request to {live_feed_url}")
    async with session.get(live_feed_url, headers=e.AGENT_HEADER, ssl=False) as res:
        if res.status != 200:
            raise Exception(f"failed to fetch live feed for {gamePK}: {res.status} {res.reason}")
        res_json = await res.json()

    plays = u.safe_get(res_json, "liveData", "plays", "allPlays", default=[])
    if not plays:
        logger.warning(f"no plays found for game {gamePK}")
        return UNKNOWN_TIME, UNKNOWN_TIME

    game_over = u.safe_get(res_json, "gameData", "status", "abstractGameState", default=None) == "Final"

    first_start = u.safe_get(plays, 0, "about", "startTime", default=None)
    last_end = u.safe_get(plays, -1, "about", "endTime", default=None) if game_over else None

    first_start = datetime.fromisoformat(first_start.replace("Z", "+00:00")) - start_buffer if first_start else UNKNOWN_TIME
    last_end = datetime.fromisoformat(last_end.replace("Z", "+00:00")) + end_buffer if game_over and last_end else UNKNOWN_TIME

    return first_start, last_end

def get_game_datetime(game):

    if "rescheduleDate" in game:
        official_date = u.safe_get(game, "rescheduleDate", default=None)
    else:
        official_date = u.safe_get(game, "gameDate", default=None)

    if official_date:
        return u.get_date(start_date=official_date)
    
    return None


