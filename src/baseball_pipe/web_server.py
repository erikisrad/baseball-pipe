import asyncio
import aiohttp
import os
from aiohttp import web
from urllib.parse import urljoin
import logging as logger
import baseball_pipe.mlb_stats
import baseball_pipe.utilities as u
import baseball_pipe.mlbtv_account
import baseball_pipe.mlbtv_stream

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(SCRIPT_DIR, "index.html")
LOCAL_PLAYLIST = os.path.join(os.path.dirname(__file__), "local.m3u8")
INDENT = ' '*12
AT = " at "


def cors_headers(content_type=None):
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*"
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

class WebServer:
    def __init__(self, host="0.0.0.0", port=80):

        self.host = host
        self.port = port
        self.app = web.Application()

        self.account = None
        self.token = None
        self.streams = {}

    def start(self):

        #self.app.router.add_get("/proxy/{gamePK}/{mediaId}/{url:.*}", self.proxy_request)
        #self.app.router.add_get("/{gamePK}/{mediaId}/master.m3u8", self.serve_master_playlist)
        self.app.router.add_get("/{arg:.*}", self.decide_serve)

        logger.info(f"Starting web server at http://{self.host}:{self.port}")
        web.run_app(self.app, host=self.host, port=self.port)

    async def decide_serve(self, request: web.Request):
        client_ip = request.remote or "unknown"
        logger.info(f"Received request from {client_ip}: {request.method} {request.path}")

        if request.method == "OPTIONS":
            return self.serve_options()

        if request.path == "/favicon.ico":
            logger.info(f"favicon.ico requested, returning 404 to {request.host}")
            return web.Response(status=404)
        
        scheme = request.scheme
        host = request.host
        base_url = f"{scheme}://{host}/"
        rel_path = request.match_info['arg']

        # Check for gamePK/mediaId format (e.g., 777654/88c67daa-25e5-4737-9189-6e2295e12661)
        if '/' in rel_path:
            parts = rel_path.split('/')
            
            if len(parts ) == 3 and parts[0].isdigit() and len(parts[0]) == 6 and len(parts[1]) == 36:

                if parts[2] == "master.m3u8":
                    logger.info(f"serving master playlist for gamePK {parts[0]} and mediaId {parts[1]}")
                    return await self.serve_master_playlist(base_url, parts[0], parts[1])
                
                elif parts[2].endswith(".m3u8"):
                    logger.info(f"serving media playlist {parts[2]} for gamePK {parts[0]} and mediaId {parts[1]}")
                    return await self.serve_media_playlist(base_url, parts[0], parts[1], parts[2])

            elif len(parts) >= 3 and "." in parts[-1]:
                suffix = '/'.join(parts[2:])
                logger.info(f"serving .ts file {suffix} for gamePK {parts[0]} and mediaId {parts[1]}")
                return await self.serve_media_file(base_url, parts[0], parts[1], suffix)

        if rel_path and rel_path.isdigit() and len(rel_path) == 8:
            logger.info(f"serving date for {rel_path}")
            return await self.serve_date(base_url, rel_path)

        elif rel_path and rel_path.isdigit() and len(rel_path) == 6:
            logger.info(f"serving gamePK for {rel_path}")
            return await self.serve_gamePK(base_url, rel_path)
        
        else:
            logger.warning(f"defaulting to current date for arg {rel_path}")
            return await self.serve_date(base_url)

    async def serve_master_playlist(self, base_url, gamePK, mediaId):
        logger.info(f"processing master playlist for gamePK {gamePK}, mediaId: {mediaId}")

        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account()

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId)

        #stream = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId)
        playlist = await self.streams[f"{gamePK}/{mediaId}"].get_master_playlist(base_url)
        
        return web.Response(
            text=playlist, 
            content_type="application/vnd.apple.mpegurl",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*"
            }
        )
    
    async def serve_media_playlist(self, base_url, gamePK, mediaId, playlist):
        logger.info(f"processing {playlist} playlist for gamePK {gamePK}, mediaId: {mediaId}")

        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account()

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId)

        #stream = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId)
        playlist = await self.streams[f"{gamePK}/{mediaId}"].get_media_playlist(base_url, playlist)
        
        return web.Response(
            text=playlist, 
            content_type="application/vnd.apple.mpegurl",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*"
            }
        )
    
    async def serve_media_file(self, base_url, gamePK, mediaId, suffix):
        logger.info(f"processing {suffix} file for gamePK {gamePK}, mediaId: {mediaId}")

        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account()

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId)

        #stream = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId)
        file = await self.streams[f"{gamePK}/{mediaId}"].get_media_file(base_url, suffix)
        
        return web.Response(
            body=file, 
            content_type="video/mp2t",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                # "Content-Length": str(len(file)),
                # "Accept-Ranges": "bytes"
            }
        )

    async def serve_gamePK(self, base_url, gamePK):
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
                        <td><a href="{base_url}{gamePK}/{broadcast['mediaId']}/master.m3u8">{broadcast['name']}</a></td>
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

    async def serve_date(self, base_url, date_str=None):
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
    
    def serve_options(self):
        return web.Response(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Max-Age": "86400"
            }
        )

    