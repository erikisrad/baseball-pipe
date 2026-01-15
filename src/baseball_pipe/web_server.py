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
        if client_ip != "192.168.0.157" and client_ip != "192.168.0.206":
            logger.warning("!!!!!hit!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            
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

            if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) == 6 and len(parts[1]) == 36:
                logger.info(f"serving stream landing for gamePK {parts[0]} and mediaId {parts[1]}")
                return await self.serve_stream_landing(base_url, parts[0], parts[1])
            
            elif len(parts ) == 3 and parts[0].isdigit() and len(parts[0]) == 6 and len(parts[1]) == 36:

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

        elif rel_path and rel_path.isdigit() and len(rel_path) <= 8:
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
    
    async def serve_stream_landing(self, base_url, gamePK, mediaId):
        logger.info(f"processing stream landing for gamepk {gamePK}, mediaID {mediaId}")

        game = await baseball_pipe.mlb_stats.get_game_content(gamePK)
        broadcasts = game.get("broadcasts", [])

        selected_broadcast = None
        for broadcast in broadcasts:
            if broadcast.get("mediaId", None) == mediaId:
                selected_broadcast = broadcast
                break

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

        video_url = f"{base_url}{gamePK}/{mediaId}/master.m3u8"

        html = f"""\
<!doctype html>
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
            a[download],
            a[download]:link,
            a[download]:visited,
            a[download]:hover,
            a[download]:active,
            a[download]:focus {{
                white-space: pre;
                font-family: monospace;
                font-size: 18px;
                margin: 0;
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
                white-space: pre;
            }}
            th {{ background-color: #f2f2f2; }}
        </style>
    </head>
    <body>
        <p>{p_date}</p>
        <br>
        <p>{an}{AT}{hn}</p>
        <p>{day_night}time at {venue}</p>
        <p>{series_description}{series_string}</p>
        <br>
        <p>Broadcast via {selected_broadcast['name']}</p>
        <br>
"""
        
        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account()

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId)

        try:
            master_playlist_url = await self.streams[f"{gamePK}/{mediaId}"].get_master_playlist_url()
            assert "m3u8" in master_playlist_url
        except Exception as err:
            logger.error(f"error getting master playlist url for {gamePK}/{mediaId}: {err}")

        errors = self.streams[f"{gamePK}/{mediaId}"].get_errors()
        if errors:
            html += f'''\
        <p><strong>Stream Error: {errors[0]["message"]}</strong></p>'''
            
        elif selected_broadcast['type'] == "AM" or selected_broadcast['type'] == "FM":
            html += f'''\
        <audio src="{video_url}" controls autoplay></audio>'''
            
        else:
            html += f'''\
        <video src="{video_url}" width="400" controls autoplay></video>
        <br><br>
        <a href="{video_url}" download>download</a>'''

        html += f"""
        <br><br>
        <p><a href="{base_url}{gamePK}"><-- back</a></p>
    </body>
</html>
"""

        return web.Response(text=html, content_type="text/html", headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*"
        })
    
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
        broadcasts = game.get("broadcasts", [])

        home_name = game["teams"]["home"]["team"]["name"]
        away_name = game["teams"]["away"]["team"]["name"]

        short = {
            "home":game["teams"]["home"]["team"]["abbreviation"],
            "away":game["teams"]["away"]["team"]["abbreviation"],
            "N/A":"N/A"
        }



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

        html = f"""\
<!doctype html>
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
                white-space: pre;
            }}
            th {{ background-color: #f2f2f2; }}
        </style>
    </head>
    <body>
        <p>{p_date}</p>
        <br>
        <p>{away_name}{AT}{home_name}</p>
        <p>{day_night}time at {venue}</p>
        <p>{series_description}{series_string}</p>
        <br>
"""
        
        if broadcasts:
            html += f"""\
        <table>
            <tr>
                <th>Broadcast</th>
                <th>Type</th>
                <th>Side</th>
                <th>State</th>
                <th>Language</th>
                <th>Availability</th>
            </tr>
"""
            for broadcast in broadcasts:
                try:
                    media_state_id = broadcast.get('mediaState', {}).get('mediaStateId', 'N/A')
                    media_state_text = broadcast.get('mediaState', {}).get('mediaStateText', 'N/A')
                    if media_state_id != 1:
                        broadcast_str = f'<a href="{base_url}{gamePK}/{broadcast["mediaId"]}">{broadcast["name"]}</a>'
                    else:
                        broadcast_str = broadcast['name']

                    html += f"""\
            <tr>
                <td>{broadcast_str}</td>
                <td>{broadcast.get('type', 'N/A')}</td>
                <td>{short[broadcast.get('homeAway', 'N/A')]}</td>
                <td>{media_state_text}</td>
                <td>{u.get_language(broadcast.get('language', 'N/A'))}</td>
                <td>{broadcast['availability'].get('availabilityText', 'N/A')}</td>
            </tr>
"""                    

                except Exception as err:
                    logger.error(f"error processing broadcast {broadcast}\n{err}")

        else:
            html += '''\
            <p><strong>No broadcast info available for this game.</strong></p>
'''

        html += f"""\
        </table>
        <br>
        <p><a href="{base_url}{u.machine_print_date(date)}"><-- back</a></p>
    </body>
</html>"""

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

        yesterday_btn = (
            # f'<button style="{btn_style}" '
            f'<button onclick="window.location.href=&quot;{base_url + yesterday}&quot;;">&lt;</button>'
        )

        tomorrow_btn = (
            # f'<button style="{btn_style}" '
            f'<button onclick="window.location.href=&quot;{base_url + tomorrow}&quot;;">&gt;</button>'
        )
        
        p_date = u.pretty_print_date(date)

        html = f"""\
<!doctype html>
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
                white-space: pre;
            }}
            th {{ background-color: #f2f2f2; }}
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
            
            gamePK = game["gamePk"]
            game_date = game.get("gameDate", "Unknown")
            status = game.get("status", {}).get("detailedState", "Unknown")
            free = False

            for broadcast in game.get("broadcasts", []):
                if 'Padres' in broadcast['name']:
                    pass
                if broadcast.get("freeGame", False):
                    free = True
                    break

            left = f"({aw}-{al}) {an}"
            right = f"{hn} ({hw}-{hl})"

            left_width = max(left_width, len(left))
            right_width = max(right_width, len(right))

            pairs.append((left, right, gamePK, game_date, status, free))

        html += f"""
    <p>{yesterday_btn}  {p_date}  {tomorrow_btn}</p>
    <br>"""

        html += f"""
    <table>
        <tr>
            <th>Game</th>
            <th>{u.get_local_datetime()}</th>
            <th>State</th>
        </tr>"""

        for left, right, gamePK, game_date, status, free in pairs:
            padded_left = left.rjust(left_width)

            if free:
                link = f'<td style="background-color: HoneyDew;"><a href="{base_url}{gamePK}">{padded_left}{AT}{right}</a></td>'
            else:
                link = f'<td><a href="{base_url}{gamePK}">{padded_left}{AT}{right}</a></td>'

            html += f"""
        <tr>
            {link}
            <td>{u.pretty_print_time_locally(game_date)}</td>
            <td>{status}</td>
        </tr>"""
            
            #html += f"\n{INDENT}<p>{link}{padded_left}{AT}{right}</a></p>"

        if not pairs:
            html += f"""
                <tr>
                    <td colspan="999">No Games Scheduled.</td>
                </tr>"""

        html += """
        </table>
    </body>
</html>"""

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

    