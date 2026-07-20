from aiohttp import web
from string import Template
import logging
import os
import baseball_pipe.misc.utilities as u
import baseball_pipe.mlb.mlb_stats

logger = logging.getLogger(__name__)
PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
GAME_HTML = os.path.join(PACKAGE_ROOT, "html", "game.html")
NO_GAME_HTML = os.path.join(PACKAGE_ROOT, "html", "no_game.html")

async def serve_game(request):
    gamePK = request.match_info.get("gamePK")
    local_tz = request.cookies.get("tz", "UTC")
    session = request.app["master_session"]

    logger.info(f"serving {gamePK} game page to {u.get_ip_from_request(request)}")
    game = await baseball_pipe.mlb.mlb_stats.get_game_content(gamePK, session)
    
    if not game:
        return serve_no_game(gamePK)

    #TEAM NAMES
    home_name = u.safe_get(game, "teams", "home", "team", "name", default="Unknown")
    away_name = u.safe_get(game, "teams", "away", "team", "name", default="Unknown")

    short = {

        "home": u.safe_get(game, "teams", "home", "team", "abbreviation", default="N/A"),
        "away": u.safe_get(game, "teams", "away", "team", "abbreviation", default="N/A")
    }

    date = baseball_pipe.mlb.mlb_stats.get_game_datetime(game)
    if date:
        date_str = u.pretty_print_date(date)
        back_str = u.machine_print_date(date)
    else:
        date_str = "Unknown date"
        back_str = ""

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

    #MISC
    # game_description = u.safe_get(game, "ifNecessaryDescription", default="Unknown")
    # day_night = u.safe_get(game, "dayNight", default="Unknown")

    #TIME
    venue = u.safe_get(game, "venue", "name", default="Unknown Venue")
    venue_tz = u.safe_get(game, "venue", "timeZone", "id", default=None)
    if date:
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
        time_str = ""

    #BROADCASTS
    broadcasts = game.get("broadcasts", [])
    if broadcasts:
        broadcast_html = construct_broadcasts(broadcasts, gamePK, short)
    else:
        broadcast_html = '<p class="no-games">No broadcast info available for this game.</p>'

    with open(GAME_HTML) as f:
        template = Template(f.read())

    html = template.substitute(p_date=date_str,
                                away_name=away_name,
                                home_name=home_name,
                                time_str=time_str,
                                series_description=series_description,
                                series_string=series_string,
                                broadcast_html=broadcast_html,
                                back_url=f"/{back_str}"
                                )
    
    return web.Response(text=html, content_type="text/html", headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*"
    })

def serve_no_game(gamePK):

    logger.warning(f"no game found for: {gamePK}")
    return web.Response(text=f"This isn't a valid gamePK: {gamePK}\nTry again", status=400)

    # logger.warning(f"returning no game data for {gamePK}")
    # with open(NO_GAME_HTML) as f:
    #     template = Template(f.read())
    # html = template.substitute(gamePK=gamePK)
    # return web.Response(text=html, content_type="text/html", headers={
    #     "Access-Control-Allow-Origin": "*"
    # })

def construct_broadcasts(broadcasts, gamePK, short):

    broadcast_html = '''<table>
            <thead>
                <tr>
                    <th>Broadcast</th>
                    <th>Type</th>
                    <th>Side</th>
                    <th>State</th>
                    <th>Language</th>
                    <th>Availability</th>
                </tr>
            </thead>
            <tbody>'''
    
    for broadcast in broadcasts:
        media_state_id = u.safe_get(broadcast, 'mediaState', 'mediaStateId', default=1)
        media_state_text = u.safe_get(broadcast, 'mediaState', 'mediaStateText', default='N/A')

        media_id = u.safe_get(broadcast, 'mediaId', default=None)
        name = u.safe_get(broadcast, 'name', default=None)
        if name:
            presented_idx = name.lower().find('presented')
            if presented_idx != -1:
                name = name[:presented_idx].strip()
        type = u.safe_get(broadcast, 'type', default='N/A')

        side = u.safe_get(broadcast, 'homeAway', default="N/A")
        side = short.get(side, side)

        language = get_language(u.safe_get(broadcast, 'language', default='N/A'))
        availability = u.safe_get(broadcast, 'availability', 'availabilityText', default='N/A')

        if media_state_id != 1 and media_id and name:
            broadcast_str = f'<a href="/{gamePK}/{media_id}">{name}</a>'
        elif name:
            broadcast_str = name
        else:
            logger.warning(f"broadcast missing name for game {gamePK}: {broadcast}")
            broadcast_str = "Unknown"

        broadcast_html += f"""
                <tr>
                    <td data-label="Broadcast">{broadcast_str}</td>
                    <td data-label="Type">{type}</td>
                    <td data-label="Side">{side}</td>
                    <td data-label="State">{media_state_text}</td>
                    <td data-label="Language">{language}</td>
                    <td data-label="Availability">{availability}</td>
                </tr>""" 
    broadcast_html += '''
            </tbody>
        </table>'''
    return broadcast_html

def get_language(language):
    languages = {
        "en":"English",
        "es":"Spanish",
        "fr":"French",
        "de":"German",
        "it":"Italian",
        "ja":"Japanese",
        "ko":"Korean",
        "zh":"Chinese",
        "pt":"Portuguese",
        "ru":"Russian"
    }
    return languages.get(language, language)

def pretty_print_tz_city(tz: str) -> str:
    try:
        return tz.rsplit("/", 1)[-1].replace("_", " ")
    except IndexError:
        logger.warning(f"failed to parse city from timezone {tz}")
        return tz