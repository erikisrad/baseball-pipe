from aiohttp import web
from string import Template
import logging
import os
import baseball_pipe.misc.utilities as u
import baseball_pipe.mlb_stats

logger = logging.getLogger(__name__)
DATE_HTML = os.path.join(os.path.dirname(__file__), "html", "date.html")

async def serve_date(request):
    date_str = request.match_info.get("date")
    tz = request.cookies.get("tz", "UTC")
    session = request.app["master_session"]

    logger.info(f"serving date page for {date_str}")

    date = u.get_date(start_date=date_str)
    games = await baseball_pipe.mlb_stats.get_games_on_date(start_date=date, session=session)
        
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

    return web.Response(text=html, content_type="text/html", headers={
            "Access-Control-Allow-Origin": "*"
    })

def generate_games_table(games, tz):
    table = ""
    records = reverse_final_scores(games)

    if games:
        table += f"""<table>
            <thead>
                <tr>
                    <th>Game</th>
                    <th>Free</th>
                    <th>{u.get_local_tz_offset(tz)}</th>
                    <th>State</th>
                </tr>
            </thead>
            <tbody>"""
    else:
        table += f"<p class='no-games'>No Games Scheduled.<p>"

    for game in games:
        hn = game["teams"]["home"]["team"]["name"]
        hw = records.get(hn, {}).get("wins", game["teams"]["home"]["leagueRecord"]["wins"])
        hl = records.get(hn, {}).get("losses", game["teams"]["home"]["leagueRecord"]["losses"])
        an = game["teams"]["away"]["team"]["name"]
        aw = records.get(an, {}).get("wins", game["teams"]["away"]["leagueRecord"]["wins"])
        al = records.get(an, {}).get("losses", game["teams"]["away"]["leagueRecord"]["losses"])

        gamePK = game["gamePk"]
        game_date = game.get("gameDate", "Unknown")
        status = game.get("status", {}).get("detailedState", "Unknown")
        free = False

        for broadcast in game.get("broadcasts", []):
            if broadcast.get("freeGame", False):
                free = True
                break

        left = f"({aw}-{al}) {an}"
        right = f"{hn} ({hw}-{hl})"

        time_str = u.pretty_print_time_locally(game_date, tz)
        row_class = 'class="free-game"' if free else ''
        free_marker = "★" if free else ""
        link = f'<a href="/{gamePK}"><span class="left">{left}</span><span class="at"> @ </span><span class="right">{right}</span></a>'

        table += f"""
                <tr {row_class}>
                    <td data-label="Game">{link}</td>
                    <td data-label="Free">{free_marker}</td>
                    <td data-label="{u.get_local_tz_offset()}">{time_str}</td>
                    <td data-label="State">{status}</td>
                </tr>"""
        
    if games: table += """
            </tbody>
        </table>"""

    return table


# attempts to not spoil games by reverting season records for finished games
# i could just query the previous day's standings but im a masochist
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

    