from aiohttp import web
from string import Template
import logging
import os
import baseball_pipe.misc.utilities as u
import baseball_pipe.mlb.mlb_stats

logger = logging.getLogger(__name__)
PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
DATE_HTML = os.path.join(PACKAGE_ROOT, "html", "date.html")

async def serve_date(request):
    date_str = request.match_info.get("date")
    tz = request.cookies.get("tz", "UTC")
    session = request.app["master_session"]

    logger.info(f"fetching {date_str} date page for {u.get_ip_from_request(request)}")

    try:
        date = u.get_date(start_date=date_str)
    except ValueError:
        return serve_no_date(date_str)
    
    games = await baseball_pipe.mlb.mlb_stats.get_games_on_date(start_date=date, session=session, broadcasts=True)

    team_query = request.query.get("team")
    if team_query:
        index_query = request.query.get("index")
        try:
            index = int(index_query) if index_query else 1
        except ValueError:
            return serve_no_team_match(date_str, team_query, index_query)

        matches = match_games_by_team(games, team_query)
        if index < 1 or index > len(matches):
            return serve_no_team_match(date_str, team_query, index_query)

        gamePK = u.safe_get(matches[index - 1], "gamePk", default=None)
        redirect_url = f"/{gamePK}"
        extra = u.filter_query_string(request.query, {"index"})
        if extra:
            redirect_url += f"?{extra}"
        return web.HTTPFound(redirect_url)

    yesterday = (date - u.timedelta(days=1)).strftime("%Y%m%d")
    tomorrow = (date + u.timedelta(days=1)).strftime("%Y%m%d")
    yesterday_btn = f'<button onclick="window.location.href=&quot;/{yesterday}&quot;;">&lt;</button>'
    tomorrow_btn  = f'<button onclick="window.location.href=&quot;/{tomorrow}&quot;;">&gt;</button>'
        
    with open(DATE_HTML) as f:
        template = Template(f.read())
    html = template.substitute(p_date=u.pretty_print_date(date),
                                yesterday_btn=yesterday_btn,
                                tomorrow_btn=tomorrow_btn,
                                table=generate_games_table(games, tz))

    logger.info(f"returning {date_str} date page to {u.get_ip_from_request(request)}")

    return web.Response(text=html, content_type="text/html", headers={
            "Access-Control-Allow-Origin": "*"
    })

def generate_games_table(games, tz):
    table = ""
    records = reverse_final_scores(games)
    offset = u.get_tz_as_offset(tz)

    if games:
        table += f"""<table>
            <thead>
                <tr>
                    <th>Game</th>
                    <th>{offset}</th>
                    <th>State</th>
                </tr>
            </thead>
            <tbody>"""
    else:
        table += f"<p class='no-games'>No Games Scheduled.<p>"

    for game in games:

        hn = u.safe_get(game, "teams", "home", "team", "name", default="Unknown")
        hw = u.safe_get(records, hn, "wins", default=u.safe_get(game, "teams", "home", "leagueRecord", "wins", default="?"))
        hl = u.safe_get(records, hn, "losses", default=u.safe_get(game, "teams", "home", "leagueRecord", "losses", default="?"))

        an = u.safe_get(game, "teams", "away", "team", "name", default="Unknown")
        aw = u.safe_get(records, an, "wins", default=u.safe_get(game, "teams", "away", "leagueRecord", "wins", default="?"))
        al = u.safe_get(records, an, "losses", default=u.safe_get(game, "teams", "away", "leagueRecord", "losses", default="?"))

        gamePK = u.safe_get(game, "gamePk", default="Unknown")
        game_datetime = baseball_pipe.mlb.mlb_stats.get_game_datetime(game)

        status = u.safe_get(game, "status", "detailedState", default="Unknown")

        if "in progress" in status.lower():
            try:
                inning = u.safe_get(game, "linescore", "currentInningOrdinal", default="?")
                half = u.safe_get(game, "linescore", "inningHalf", default="?")[:3]
                status = f"{inning}, {half}"
            except Exception as e:
                logger.warning(f"failed to get linescore info for game {gamePK}: {e}")

        left = f"({aw}-{al}) {an}"
        right = f"{hn} ({hw}-{hl})"

        time_str = u.pretty_print_time_in_tz(game_datetime, tz)
        link = f'<a href="/{gamePK}"><span class="left">{left}</span><span class="at"> @ </span><span class="right">{right}</span></a>'

        table += f"""
                <tr>
                    <td data-label="Game">{link}</td>
                    <td data-label="{offset}">{time_str}</td>
                    <td data-label="State">{status}</td>
                </tr>"""
        
    if games: table += """
            </tbody>
        </table>"""

    return table

# attempts to not spoil games by reverting season records for finished games
# i could just query the previous day's standings but this is more efficient... probably
def reverse_final_scores(games):
    records = {}

    for game in games:
        team_data = game["teams"]
        for team in team_data.values():
            name = team["team"]["name"]

            if name not in records:
                records[name] = {
                    "wins": int(team["leagueRecord"]["wins"]),
                    "losses": int(team["leagueRecord"]["losses"])
                }

            if "isWinner" in team:
                if team["isWinner"]:
                    logger.debug(f"removing win from {name}")
                    records[name]["wins"] -= 1
                else:
                    logger.debug(f"removing loss from {name}")
                    records[name]["losses"] -= 1
            else:
                logger.debug(f"not adjusting record for {name}")

    return records

def serve_no_date(date_str):
    logger.warning(f"invalid date format: {date_str}")
    return web.Response(text=f"This isn't a valid YYYYMMDD date: {date_str}\nTry again", status=400)

def match_games_by_team(games, team_query):
    team_query = team_query.lower()
    matches = []
    for game in games:
        for side in ("home", "away"):
            name = u.safe_get(game, "teams", side, "team", "name", default="")
            if team_query in name.lower():
                matches.append(game)
                break
    return matches

def serve_no_team_match(date_str, team_query, index_query):
    logger.warning(f"no game match for {date_str}: team={team_query!r} index={index_query!r}")
    return web.Response(text=f"No game found on {date_str} matching team={team_query!r} index={index_query!r}\nTry again", status=400)
