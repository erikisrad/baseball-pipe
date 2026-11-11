import logging
import aiohttp_jinja2
from aiohttp import web

from baseball_pipe.webpage_gen.game_page import serve_no_game, CONDENSED, RECAP
import baseball_pipe.mlb.mlb_stats
import baseball_pipe.misc.utilities as u
from baseball_pipe.misc.header_handler import cors_headers
from baseball_pipe.mlbtv.mlbtv_stream import MlbtvStream
from baseball_pipe.mlbtv.account2 import Account
from baseball_pipe.standalone.stream_bucket import StreamBucket

logger = logging.getLogger(__name__)

async def serve_broadcast(request):

    #META INFO
    gamePK = request.match_info.get("gamePK")
    mediaID = request.match_info.get("mediaId")
    local_tz = request.cookies.get("tz", "UTC")
    session = request.app["master_session"]
    bucket:StreamBucket = request.app["stream_bucket"]
    base_url = request.url.origin()

    #GAME
    logger.info(f"fetching {gamePK}/{mediaID} broadcast page for {u.get_ip_from_request(request)}")
    game = await baseball_pipe.mlb.mlb_stats.get_game_content(gamePK, session)

    if not game:
        return serve_no_game(gamePK)

    if mediaID.lower() == CONDENSED or mediaID.lower() == RECAP:

        if mediaID.lower() == CONDENSED:
            broadcast_name = "Condensed Game"
        elif mediaID.lower() == RECAP:
            broadcast_name = "Daily Recap"
        else:
            return serve_no_broadcast(gamePK, mediaID)
        cast_type = "video"

    else:
        #BROADCAST INFO
        broadcasts = game.get("broadcasts", [])
        selected_broadcast = None
        for broadcast in broadcasts:
            if broadcast.get("mediaId", None) == mediaID:
                selected_broadcast = broadcast
                break

        if not selected_broadcast:
            return serve_no_broadcast(gamePK, mediaID)

        broadcast_name = u.safe_get(selected_broadcast, 'name', default=None)
        broadcast_name = u.presenter_remover(broadcast_name)
        cast_type = u.safe_get(selected_broadcast, 'type', default="Unknown")

    #TEAM NAMES
    home_name = u.safe_get(game, "teams", "home", "team", "name", default="Unknown")
    away_name = u.safe_get(game, "teams", "away", "team", "name", default="Unknown")

    #SERIES LENGTH
    series_length = u.safe_get(game, "gamesInSeries", default=None)
    series_game_number = u.safe_get(game, "seriesGameNumber", default=None)
    if series_length and series_game_number:
        series_string = f", Game {series_game_number} of {series_length}"
    else:
        series_string = ""

    #SERIES TITLE
    series_description = u.safe_get(game, "seriesDescription", default="Unknown series")
    if series_string and not "series" in series_description.lower():
        series_description = f"{series_description} Series"

    #VENUE
    venue = u.safe_get(game, "venue", "name", default="Unknown Venue")
    venue_tz = u.safe_get(game, "venue", "timeZone", "id", default=None)

    #TIME
    date = baseball_pipe.mlb.mlb_stats.get_game_datetime(game)
    if date:
        local_date = u.localize(date, local_tz)
        local_time_str = u.pretty_print_time_in_tz(date, local_tz)
        local_offset = u.get_tz_as_offset(local_tz)
        if venue_tz:
            venue_time_str = u.pretty_print_time_in_tz(date, venue_tz)
            if venue_time_str == local_time_str:
                time_str = f"{venue_time_str} at {venue} ({local_offset})"
            else:
                time_str = f"{venue_time_str} at {venue} ({local_time_str} {local_offset})"
        else:
            logger.warning(f"missing venue timezone for game {gamePK}")
            time_str = f"{local_time_str} {local_offset}"
    else:
        local_date = None
        time_str = ""

    #STREAM
    video_url = f"{base_url}/{gamePK}/{mediaID}/master.m3u8"
    extra = u.filter_query_string(request.query, {"raw"})
    if extra:
        video_url += f"?{extra}"

    if request.query.get("raw"):
        return web.HTTPFound(video_url)

    error = None
    try:
        stream = await bucket.get_stream(gamePK, mediaID, request)
        await stream.inititialize()
    except Exception as err:
        logger.error(f"failed to get master playlist url for {gamePK}/{mediaID}: {err}")
        error = err

    response = aiohttp_jinja2.render_template("broadcast2.html", request, {
        "p_date": u.pretty_print_date(local_date) if local_date else "Unknown date",
        "away_name": away_name,
        "home_name": home_name,
        "broadcast_name": broadcast_name,
        "series_description": series_description,
        "series_string": series_string,
        "time_str": time_str,
        "error": error,
        "cast_type": cast_type,
        "video_url": video_url,
        "back_url": f"{base_url}/{gamePK}",
    })
    response.headers.update(cors_headers())

    logger.info(f"returning {gamePK}/{mediaID} broadcast page to {u.get_ip_from_request(request)}")

    return response

def serve_no_broadcast(gamePK, mediaId):
    logger.warning(f"no broadcast found for: {gamePK}/{mediaId}")
    return web.Response(text=f"This isn't a valid mediaId ({mediaId}) for this gamePK ({gamePK})\nTry again", status=400)
