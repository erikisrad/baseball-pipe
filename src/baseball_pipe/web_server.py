import asyncio
import aiohttp
import os
from aiohttp import web
from urllib.parse import urljoin
import logging as logger
import baseball_pipe.mlb_stats
import baseball_pipe.utilities as u

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(SCRIPT_DIR, "index.html")
LOCAL_PLAYLIST = os.path.join(os.path.dirname(__file__), "local.m3u8")
INDENT = ' '*12
AT = " at "


def cors_headers(content_type):
    return {
        "Content-Type": content_type,
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*"
    }

async def serve_media(base_url, mediaId):
    logger.info(f"processing mediaId: {mediaId}")
    content_url = f"https://statsapi.mlb.com/api/v1/media/{mediaId}"
    
    async with aiohttp.ClientSession() as session:
        logger.info(f"sending request to {content_url}")

        async with session.get(content_url, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"failed to fetch media {mediaId}: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

    media = res_json.get("media", {})
    if not media:
        raise Exception(f"no media found for mediaId {mediaId}")

    media_url = media.get("playbacks", [])[0].get("url", None)
    if not media_url:
        raise Exception(f"no playback url found for mediaId {mediaId}")

    logger.info(f"redirecting to media url: {media_url}")
    raise web.HTTPFound(media_url)

async def serve_gamePK(base_url, gamePK):
    logger.info(f"processing gamePK: {gamePK}")
    game = await baseball_pipe.mlb_stats.get_game_content(gamePK)
    broadcasts = game["broadcasts"]

    hn = game["teams"]["home"]["team"]["name"]
    an = game["teams"]["away"]["team"]["name"]
    date = u.get_date(start_date=game["officialDate"])
    p_date = u.pretty_print_date(date)
    venue = game["venue"]["name"]
    series_length = game.get('gamesInSeries', None)
    series_game_number = game.get('seriesGameNumber', None)
    if series_length and series_game_number:
        series_string = f", Game {series_game_number} of {series_length}"
    else:
        series_string = ""
    series_description = f"{game['seriesDescription']}" if "series" in game['seriesDescription'].lower() or not series_string else f"{game['seriesDescription']} Series"
    game_description = game['ifNecessaryDescription']
    day_night = game['dayNight'].capitalize()

    html = f"""<!doctype html>
    <html>
        <head>
            <meta charset="utf-8" />
            <title>Baseball Pipe</title>
            <style>
                p {{
                    white-space: pre;
                    font-family: monospace;
                    font-size: 18px;
                    margin: 0;
                }}
                body a {{
                    text-decoration: none;
                    color: blue;
                }}
                body a:hover {{
                    text-decoration: none;
                    color: inherit;
                }}
                table {{
                    border-collapse: collapse;
                    font-family: monospace;
                    font-size: 18px;
                    display: grid;
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 8px;
                    text-align: left;
                }}
                th {{ background-color: #f2f2f2; }}
            </style>
        </head>
        <body>
            <p>{an}{AT}{hn}</p>
            <p>{p_date}</p>
            <p>{day_night}time at {venue}</p>
            <p>{series_description}{series_string}</p>
            <p>\n</p>
            <table>
                <tr>
                    <th>Broadcast</th>
                    <th>Type</th>
                    <th>Language</th>
                    <th>Availability</th>
                    <th>Side</th>
                </tr>"""
    
    for broadcast in broadcasts:
        html += f"""\n
                <tr>
                    <td><a href="{base_url}{broadcast['mediaId']}">{broadcast['name']}</a></td>
                    <td>{broadcast.get('type', 'N/A')}</td>
                    <td>{broadcast.get('language', 'N/A')}</td>
                    <td>{broadcast['availability'].get('availabilityText', 'N/A')}</td>
                    <td>{broadcast.get('homeAway', 'N/A')}</td>
                </tr>"""
    html += f"""
            </table>
            <p><a href="{base_url}{u.machine_print_date(date)}">\n<-- back</a></p>
        </body>
    </html>
    """

    return web.Response(text=html, content_type="text/html", headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*"
    })

async def serve_date(base_url, date_str=None):
    logger.info(f"processing date_str: {date_str}")

    if date_str:
        date = u.get_date(start_date=date_str)
        result = await baseball_pipe.mlb_stats.get_games_on_date(start_date=date)
        if isinstance(result, dict) and not result:
            games = []
        else:
            date, games = result

    else:
        days_ago = 60
        start_date = u.get_date()
        end_date = u.get_date(start_date=start_date, days_ago=days_ago)
        result = await baseball_pipe.mlb_stats.get_games_on_date(start_date=start_date, 
                                                                      end_date=end_date)
        if isinstance(result, dict) and not result:
            games = []
            date = start_date 
        else:
            date, games = result
        
    yesterday = (date - u.timedelta(days=1)).strftime("%Y%m%d")
    tomorrow = (date + u.timedelta(days=1)).strftime("%Y%m%d")

    btn_width = 4

    # btn_style = (
    #     f'width:{btn_width}ch;'
    #     "padding:0.35ch;"
    #     "font-family:monospace;"
    #     "font-size:inherit;"
    #     "text-align:center;"
    #     "box-sizing:border-box;"
    #     "display:inline-flex;"
    #     "align-items:center;"
    #     "justify-content:center;"
    #     "height:1.6em;"
    # )

    yesterday_btn = (
        # f'<button style="{btn_style}" '
        f'<button onclick="window.location.href=&quot;{base_url + yesterday}&quot;;">&lt;</button>'
    )

    tomorrow_btn = (
        # f'<button style="{btn_style}" '
        f'<button onclick="window.location.href=&quot;{base_url + tomorrow}&quot;;">&gt;</button>'
    )
    
    p_date = u.pretty_print_date(date)

    html = f"""<!doctype html>
    <html>
        <head>
            <meta charset="utf-8" />
            <title>Baseball Pipe</title>
            <style>
                p {{
                    white-space: pre;
                    font-family: monospace;
                    font-size: 18px;
                    line-height: 2;
                    margin: 0;
                }}
                body a {{
                    text-decoration: none;
                    color: blue;
                }}
                body a:hover {{
                    text-decoration: none;
                    color: inherit;
                }}
                button {{
                    width:{btn_width}ch;
                    padding:0.35ch;
                    font-family:monospace;
                    font-size:inherit;
                    text-align:center;
                    box-sizing:border-box;
                    display:inline-flex;
                    align-items:center;
                    justify-content:center;
                    height:1.6em;
                }}
            </style>
        </head>
        <body>"""

    pairs = []
    left_width = 0
    right_width = 0
    for game in games:
        hn = game["teams"]["home"]["team"]["name"]
        hw = game["teams"]["home"]["leagueRecord"]["wins"]
        hl = game["teams"]["home"]["leagueRecord"]["losses"]
        an = game["teams"]["away"]["team"]["name"]
        aw = game["teams"]["away"]["leagueRecord"]["wins"]
        al = game["teams"]["away"]["leagueRecord"]["losses"]
        pk = game["gamePk"]

        left = f"({aw}-{al}) {an}"
        right = f"{hn} ({hw}-{hl})"
        left_width = max(left_width, len(left))
        right_width = max(right_width, len(right))
        pairs.append((left, right, pk))

    if games:
        # account for the two buttons which occupy btn_width characters each
        total_width = left_width + len(AT) + right_width - (btn_width * 2)

        html = html + (
            f"\n{INDENT}<p><strong>"
            f"{yesterday_btn}"
            f"{p_date:^{total_width}}"
            f"{tomorrow_btn}</strong></p>"
        )

        for left, right, pk in pairs:
            padded_left = left.rjust(left_width)
            link = f'<a href="{base_url}{pk}">'
            html = html + f"\n{INDENT}<p>{link}{padded_left}{AT}{right}</a></p>"

    else:
        total_width = len(p_date) + 4
        no_games = "No games scheduled."
        html = html + (
            f"\n{INDENT}<p><strong>"
            f"{yesterday_btn}"
            f"{p_date:^{total_width}}"
            f"{tomorrow_btn}</strong></p>"
            f"\n{INDENT}<p>{no_games:^{total_width+(btn_width * 2)}}</p>"
        )

    html = html + """
        </body>
    </html>
    """

    return web.Response(text=html, content_type="text/html", headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*"
    })

async def decide_serve(request: web.Request):
    if request.path == "/favicon.ico":
        logger.info(f"favicon.ico requested, returning 404 to {request.host}")
        return web.Response(status=404)
    
    scheme = request.scheme
    host = request.host
    base_url = f"{scheme}://{host}/"
    rel_path = request.match_info['arg']

    if rel_path and rel_path.isdigit() and len(rel_path) == 8:
        logger.info(f"serving date for {rel_path}")
        return await serve_date(base_url, rel_path)

    elif rel_path and rel_path.isdigit() and len(rel_path) == 6:
        logger.info(f"serving gamePK for {rel_path}")
        return await serve_gamePK(base_url, rel_path)
    
    elif rel_path and len(rel_path) == 36:
        logger.info(f"serving mediaId for {rel_path}")
        return await serve_media(base_url, rel_path)

    else:
        logger.info(f"serving current date for arg ({rel_path})")
        return await serve_date(base_url)

async def serve_segment(request):
    rel_path = request.match_info['filename']
    upstream_url = urljoin(request.path, rel_path)
    logger.info("proxying segment:", rel_path)

    async with aiohttp.ClientSession() as session:
            async with session.get(upstream_url) as resp:
                    if resp.status != 200:
                            return web.Response(status=resp.status, text="Upstream error")
                    data = await resp.read()
                    return web.Response(body=data, headers=cors_headers("video/mp2t"))

class WebServer:
    def __init__(self, host="0.0.0.0", port=80):

        self.host = host
        self.port = port
        self.app = web.Application()

    def start(self):
        self.app.router.add_get("/{arg:.*}", decide_serve)
        self.app.router.add_get("/segments/{filename:.*}", serve_segment)

        logger.info(f"Starting web server at http://{self.host}:{self.port}")
        web.run_app(self.app, host=self.host, port=self.port)