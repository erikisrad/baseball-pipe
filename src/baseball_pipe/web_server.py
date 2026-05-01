import os
from aiohttp import web
import curl_cffi
from string import Template
from pathlib import Path
from urllib.parse import urljoin
import logging as logger

import aiohttp
import baseball_pipe.mlb_stats
import baseball_pipe.utilities as u
import baseball_pipe.mlbtv_account
import baseball_pipe.mlbtv_stream
import baseball_pipe.login
from baseball_pipe.mlbtv_stream import Stream

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(SCRIPT_DIR, "index.html")
LOCAL_PLAYLIST = os.path.join(os.path.dirname(__file__), "local.m3u8")
AT = " @ "
SPC = "&nbsp;"

def cors_headers(content_type=None):
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*"
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

@web.middleware
async def auth_middleware(request, handler):
    path = request.path

    if (path == "/login"
        or path.startswith("/static")
        or path.endswith(".m3u8")
        or path.endswith(".ts")
        or path.endswith(".key")
        or path.endswith(".vtt")
        or path.endswith(".aac")):

        return await handler(request)

    raw = request.cookies.get("auth")
    if not raw:
        raise web.HTTPFound("/login")

    if not baseball_pipe.login.verify_signed_cookie(raw):
        raise web.HTTPFound("/login")

    return await handler(request)

class WebServer:
    def __init__(self, host="127.0.0.1", port=8080,
                 proxy_username:str=os.environ["proxu"],
                 proxy_password:str=os.environ["proxp"],
                 proxy_host:str=os.environ["proxhost"],
                 proxy_port:str=os.environ["proxport"]):

        self.host = host
        self.port = port
        self.app = web.Application(middlewares=[auth_middleware])
        self.app.router.add_static("/static", "baseball_pipe/static")
        self.app.router.add_route("*", "/login", self.decide_serve)


        self.account = None
        self.token = None
        self.streams: dict[str, Stream] = {}
        self.proxy_url = f"http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"
        self.master_session = None
        self.chrome120_session = None

    async def on_startup(self, app):
        # self.master_session = aiohttp.ClientSession(
        #     cookie_jar=aiohttp.CookieJar(unsafe=True),
        #     connector=aiohttp.TCPConnector(
        #         family=socket.AF_INET,   # force IPv4 (Bright Data + Okta friendly)
        #         ssl=False                # matches your requests
        #     )
        # )

        self.master_session = aiohttp.ClientSession()
        self.chrome120_session = curl_cffi.Session(impersonate="chrome120")

    async def on_cleanup(self, app):
        if self.master_session:
            await self.master_session.close()

    def start(self):

        #self.app.router.add_get("/proxy/{gamePK}/{mediaId}/{url:.*}", self.proxy_request)
        #self.app.router.add_get("/{gamePK}/{mediaId}/master.m3u8", self.serve_master_playlist)
        self.app.router.add_get("/{arg:.*}", self.decide_serve)
        self.app.on_startup.append(self.on_startup)
        self.app.on_cleanup.append(self.on_cleanup)

        logger.info(f"Starting web server at http://{self.host}:{self.port}")
        web.run_app(self.app, host=self.host, port=self.port)

    async def decide_serve(self, request: web.Request):
        real_ip = request.headers.get("X-Real-IP")
        xff = request.headers.get("X-Forwarded-For")
        client_ip = real_ip or (xff.split(",")[0] if xff else request.remote)

        logger.info(f"real_ip: {real_ip}, xff: {xff}, remote: {request.remote}")
        logger.info(f"Received request from {client_ip}: {request.method} {request.path}")

        if request.method == "OPTIONS":
            return self.serve_options()

        if request.path == "/favicon.ico":
            logger.info(f"favicon.ico requested, returning 404 to {client_ip}")
            return web.Response(status=404)

        if request.path == "/today":
            logger.info(f"today requested, redirecting to current date for {client_ip}")
            return web.HTTPFound(location=f"/{u.machine_print_date(u.get_date())}")
        
        if request.path == "/yesterday":
            logger.info(f"yesterday requested, redirecting to yesterday's date for {client_ip}")
            return web.HTTPFound(location=f"/{u.machine_print_date(u.get_date(days_ago=1))}")
        
        if request.path == "/tomorrow":
            logger.info(f"tomorrow requested, redirecting to tomorrow's date for {client_ip}")
            return web.HTTPFound(location=f"/{u.machine_print_date(u.get_date(days_ago=-1))}")
        
        if request.path == "/login":
            if request.method == "POST":
                return await baseball_pipe.login.login(request, client_ip)
            else:
                logger.info(f"login requested for {client_ip}")
                return web.Response(text=open(os.path.join(SCRIPT_DIR, "login.html"), "r").read(), content_type="text/html")
        
        if request.path == "/robots.txt":
            logger.info(f"robots.txt requested for {client_ip}")
            robots_path = os.path.join(SCRIPT_DIR, "robots.txt")
            with open(robots_path, 'r') as f:
                robots_content = f.read()
            return web.Response(text=robots_content, content_type="text/plain")

        try:
            scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
        except Exception:
            logger.warning(f"could not get scheme from request, defaulting to https")
            scheme = "https"

        host = request.host
        base_url = f"{scheme}://{host}/"
        rel_path = request.match_info['arg']
        #logger.info(f"base_url: {base_url}, rel_path: {rel_path}")
        
        if rel_path == "baseball_pipe.css":
            css_path = os.path.join(SCRIPT_DIR, "baseball_pipe.css")
            if os.path.exists(css_path):
                with open(css_path, 'r') as f:
                    css_content = f.read()
                return web.Response(text=css_content, content_type="text/css", headers=cors_headers())
            else:
                return web.Response(status=404)
            
        if rel_path.endswith(".js"):
            js_path = os.path.join(SCRIPT_DIR, rel_path)
            if os.path.exists(js_path):
                with open(js_path, "r") as f:
                    js_content = f.read()
                return web.Response(
                    text=js_content,
                    content_type="application/javascript",
                    headers=cors_headers()
                )
            else:
                return web.Response(status=404)
            
        # Check for gamePK/mediaId format (e.g., 777654/88c67daa-25e5-4737-9189-6e2295e12661)
        try:
            if '/' in rel_path and len(rel_path.split('/')) >= 2:
                parts = rel_path.split('/')

                if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) == 6 and len(parts[1]) == 36:
                    logger.info(f"serving stream landing for gamePK {parts[0]} and mediaId {parts[1]}")
                    return await self.serve_stream_landing2(base_url, parts[0], parts[1])
                
                elif len(parts ) == 3 and parts[0].isdigit() and len(parts[0]) == 6 and len(parts[1]) == 36:

                    if parts[2] == "master.m3u8":
                        logger.info(f"serving master playlist for gamePK {parts[0]} and mediaId {parts[1]}")
                        return await self.serve_master_playlist(base_url, parts[0], parts[1])
                    
                    elif parts[2].endswith(".m3u8"):
                        logger.info(f"serving media playlist {parts[2]} for gamePK {parts[0]} and mediaId {parts[1]}")
                        return await self.serve_media_playlist(base_url, parts[0], parts[1], parts[2])

                elif len(parts) >= 3 and (".ts" in parts[-1]):
                    suffix = '/'.join(parts[2:])
                    logger.info(f"serving .ts file {suffix} for gamePK {parts[0]} and mediaId {parts[1]}")
                    return await self.serve_media_file(base_url, parts[0], parts[1], suffix)
                
                elif len(parts) >= 3 and ".vtt" in parts[-1]:
                    suffix = '/'.join(parts[2:])
                    logger.info(f"serving .vtt file {suffix} for gamePK {parts[0]} and mediaId {parts[1]}")
                    return await self.serve_vtt_file(base_url, parts[0], parts[1], suffix)
                
                elif len(parts) >= 3 and (".aac" in parts[-1]):
                    suffix = '/'.join(parts[2:])
                    logger.info(f"serving .aac file {suffix} for gamePK {parts[0]} and mediaId {parts[1]}")
                    return await self.serve_aac_file(base_url, parts[0], parts[1], suffix)

            if rel_path and rel_path.isdigit() and len(rel_path) == 8:
                logger.info(f"serving date for {rel_path}")
                return await self.serve_date3(base_url, rel_path)

            elif rel_path and rel_path.isdigit() and len(rel_path) <= 8:
                logger.info(f"serving gamePK for {rel_path}")
                return await self.serve_gamePK2(base_url, rel_path)
            
            elif len(parts) >= 3 and ".key" in parts[-1]:
                suffix = '/'.join(parts[2:])
                logger.info(f"serving .key file {suffix} for gamePK {parts[0]} and mediaId {parts[1]}")
                return await self.serve_key_file(base_url, parts[0], parts[1], suffix)

            else:
                logger.warning(f"defaulting to 404 {rel_path}")
                return web.Response(status=404)
            
        except Exception as err:
            logger.warning(f"hit exception when processing request {rel_path}\n{err}")
            return web.Response(status=404)

    async def serve_master_playlist(self, base_url, gamePK, mediaId):
        logger.info(f"processing master playlist for gamePK {gamePK}, mediaId: {mediaId}")

        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account(self.chrome120_session, self.proxy_url)

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId, self.master_session, self.proxy_url)

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
        </br>
        <p>{an}{AT}{hn}</p>
        <p>{day_night}time at {venue}</p>
        <p>{series_description}{series_string}</p>
        </br>
        <p>Broadcast via {selected_broadcast['name']}</p>
        </br>
"""
        
        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account(self.chrome120_session, self.proxy_url)

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId, self.master_session, self.proxy_url)

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
        </br></br>
        <a href="{video_url}" download>download</a>'''

        html += f"""
        </br></br>
        <p><a href="{base_url}{gamePK}"><-- back</a></p>
    </body>
</html>
"""

        return web.Response(text=html, content_type="text/html", headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*"
        })

    async def serve_stream_landing2(self, base_url, gamePK, mediaId):
        logger.info(f"processing stream landing for gamepk {gamePK}, mediaID {mediaId}")
        html_file = Path(os.path.join(SCRIPT_DIR, "stream_landing.html"))

        game = await baseball_pipe.mlb_stats.get_game_content(gamePK)
        broadcasts = game.get("broadcasts", [])

        selected_broadcast = None
        for broadcast in broadcasts:
            if broadcast.get("mediaId", None) == mediaId:
                selected_broadcast = broadcast
                break

        home_name = game["teams"]["home"]["team"]["name"]
        away_name = game["teams"]["away"]["team"]["name"]
        date = u.get_date(start_date=game["officialDate"])
        venue = game["venue"]["name"]
        series_length = game.get('gamesInSeries', None)
        series_game_number = game.get('seriesGameNumber', None)

        if series_length and series_game_number:
            series_string = f", Game {series_game_number} of {series_length}"
        else:
            series_string = ""
        series_description = game['seriesDescription'] if "series" in game['seriesDescription'].lower() or not series_string else f"{game['seriesDescription']} Series"
        game_description = game['ifNecessaryDescription']
        day_night = game['dayNight'].capitalize()

        video_url = f"{base_url}{gamePK}/{mediaId}/master.m3u8"
        #video_url = "https://devstreaming-cdn.apple.com/videos/streaming/examples/bipbop_16x9/bipbop_16x9_variant.m3u8" # master debug
        #video_url = "https://devstreaming-cdn.apple.com/videos/streaming/examples/bipbop_16x9/gear5/prog_index.m3u8" # best
        #video_url = "https://dai.google.com/linear/hls/pa/event/k-VHR5unRdusBDqoXAuB0Q/stream/d337505d-c921-4b35-bdd2-8b22646e8522:MRN2/master.m3u8" # debug

        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account(self.chrome120_session, self.proxy_url)

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId, self.master_session, self.proxy_url)

        try:
            master_playlist_url = await self.streams[f"{gamePK}/{mediaId}"].get_master_playlist_url()
            assert "m3u8" in master_playlist_url
            await self.streams[f"{gamePK}/{mediaId}"].get_master_playlist(base_url)
            error = None
        except Exception as err:
            error = err
            logger.error(f"error getting master playlist url for {gamePK}/{mediaId}: {err}")

        if error:
            broadcast_html = f'<p><strong>Stream Error:</strong> {error}</p>'
            downloads = ""

        else:
            if selected_broadcast['type'] == "AM" or selected_broadcast['type'] == "FM":
                broadcast_html = f'<audio src="{video_url}" controls autoplay></audio>'
            
            else:
                #broadcast_html = f'<video src="{video_url}" controls autoplay></video>'
                broadcast_html = f'''<video id="hls-cast-player" class="video-js vjs-default-skin vjs-big-play-centered" controls preload="auto" crossorigin="anonymous">
                <source src="{video_url}" type="application/x-mpegURL" />
            </video>

            <script src="https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1"></script>
            <script src="https://vjs.zencdn.net/7.20.3/video.min.js"></script>
            <script src="https://unpkg.com/@silvermine/videojs-chromecast@1.5.0/dist/silvermine-videojs-chromecast.min.js"></script>
            <script src="/player.js"></script>'''

            downloads = f'''<div class="variant-columns">
                <div class="vc">
                    <p class="dl">Ad-Free Playlists</p>
                    <a class="dl" href="{video_url}" download>Master Playlist</a>'''
            
            if self.streams[f"{gamePK}/{mediaId}"].variant_playlists:

                
                for variant in self.streams[f"{gamePK}/{mediaId}"].variant_playlists:
                    xres, yres = variant.stream_info.resolution or ("?", "?")
                    fps = variant.stream_info.frame_rate or "?"
                    bps = variant.stream_info.bandwidth or 0

                    mbps = bps / 1_000_000

                    # Build padded fields INCLUDING commas
                    col_res = f"{xres}x{yres},".ljust(11)
                    col_fps = f"{fps} fps,".ljust(11)
                    col_mbps = f"{mbps:.2f} Mbps".ljust(9)

                    variant_url = urljoin(video_url, variant.uri)

                    downloads += f'''
            <a class="dl" href="{variant_url}" download>{col_res}{col_fps}{col_mbps}</a>'''
                    
                downloads += f'''
                </div>'''   

            downloads += f'''<div class="vc">
            <p class="dl">Raw MLB.TV Playlists</p>
            <a class="dl" href="{await self.streams[f"{gamePK}/{mediaId}"].get_master_playlist_url()}" download>Master Playlist</a>'''
            
            if self.streams[f"{gamePK}/{mediaId}"].mlbtv_variant_playlists:

                for variant in self.streams[f"{gamePK}/{mediaId}"].mlbtv_variant_playlists:
                    xres, yres = variant.stream_info.resolution or ("?", "?")
                    fps = variant.stream_info.frame_rate or "?"
                    bps = variant.stream_info.bandwidth or 0

                    mbps = bps / 1_000_000

                    # Build padded fields INCLUDING commas
                    col_res = f"{xres}x{yres},".ljust(11)
                    col_fps = f"{fps} fps,".ljust(11)
                    col_mbps = f"{mbps:.2f} Mbps".ljust(9)

                    variant_url = urljoin(self.streams[f"{gamePK}/{mediaId}"]._upstream_base_url, variant.uri)

                    downloads += f'''
            <a class="dl" href="{variant_url}" download>{col_res}{col_fps}{col_mbps}</a>'''
                    
                downloads += f'''
                </div>
                </div>''' 

        template = Template(html_file.read_text())
        html = template.substitute(p_date=u.pretty_print_date(date),
                                   away_name=away_name,
                                   AT=AT,
                                   home_name=home_name,
                                   day_night=day_night,
                                   venue=venue,
                                   series_description=series_description,
                                   series_string=series_string,
                                   broadcast_name=selected_broadcast['name'],
                                   broadcast_html=broadcast_html,
                                   downloads=downloads,
                                   back_url=f"{base_url}{gamePK}"
                                   )

        return web.Response(text=html, content_type="text/html", headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*"
        })

    async def serve_media_playlist(self, base_url, gamePK, mediaId, playlist):
        logger.info(f"processing {playlist} playlist for gamePK {gamePK}, mediaId: {mediaId}")

        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account(self.chrome120_session, self.proxy_url)

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId, self.master_session, self.proxy_url)

        #stream = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId)
        playlist = await self.streams[f"{gamePK}/{mediaId}"].get_media_playlist(base_url, playlist)
        
        return web.Response(
            body=playlist.encode("utf-8"),
            content_type="application/vnd.apple.mpegurl",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*"
            }
        )

    
    async def serve_media_file(self, base_url, gamePK, mediaId, suffix):
        logger.info(f"processing {suffix} file for gamePK {gamePK}, mediaId: {mediaId}")

        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account(self.chrome120_session, self.proxy_url)

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId, self.master_session, self.proxy_url)

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
    
    async def serve_vtt_file(self, base_url, gamePK, mediaId, suffix):
        logger.info(f"processing {suffix} file for gamePK {gamePK}, mediaId: {mediaId}")

        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account(self.chrome120_session, self.proxy_url)

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId, self.master_session, self.proxy_url)

        file = await self.streams[f"{gamePK}/{mediaId}"].get_vtt_file(base_url, suffix)
        
        return web.Response(
            body=file, 
            content_type="text/vtt",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                # "Content-Length": str(len(file)),
                # "Accept-Ranges": "bytes"
            }
        )
    
    async def serve_aac_file(self, base_url, gamePK, mediaId, suffix):
        logger.info(f"processing {suffix} file for gamePK {gamePK}, mediaId: {mediaId}")

        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account(self.chrome120_session, self.proxy_url)

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId, self.master_session, self.proxy_url)

        #stream = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId)
        file = await self.streams[f"{gamePK}/{mediaId}"].get_aac_file(base_url, suffix)
        
        return web.Response(
            body=file, 
            content_type="audio/aac",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                # "Content-Length": str(len(file)),
                # "Accept-Ranges": "bytes"
            }
        )
    
    async def serve_key_file(self, base_url, gamePK, mediaId, suffix):
        logger.info(f"processing {suffix} key for gamePK {gamePK}, mediaId: {mediaId}")

        if not self.account:
            self.account = baseball_pipe.mlbtv_account.Account(self.chrome120_session, self.proxy_url)

        if not self.token:
            self.token = await self.account.get_token()
        
        if f"{gamePK}/{mediaId}" not in self.streams:
            self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId, self.master_session, self.proxy_url)

        #stream = baseball_pipe.mlbtv_stream.Stream(self.token, gamePK, mediaId)
        file = await self.streams[f"{gamePK}/{mediaId}"].get_key_file(base_url, suffix)
        logger.info(f"key file content length: {len(file)} bytes")
        logger.info(f"HEX KEY: {file.hex()}")
        
        return web.Response(
            body=file, 
            content_type="application/octet-stream",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Content-Length": str(len(file)),
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
        </br>
        <p>{away_name}{AT}{home_name}</p>
        <p>{day_night}time at {venue}</p>
        <p>{series_description}{series_string}</p>
        </br>
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
        </br>
        <p><a href="{base_url}{u.machine_print_date(date)}"><-- back</a></p>
    </body>
</html>"""

        return web.Response(text=html, content_type="text/html", headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*"
        })

    async def serve_gamePK2(self, base_url, gamePK):
        logger.info(f"processing gamePK: {gamePK}")
        html_file = Path(os.path.join(SCRIPT_DIR, "gamePK.html"))

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

        if broadcasts:
            broadcast_html = self.construct_broadcasts(broadcasts, base_url, gamePK, short)
        else:
            broadcast_html += "<p><strong>No broadcast info available for this game.</strong></p>"

        template = Template(html_file.read_text())
        html = template.substitute(p_date=u.pretty_print_date(date),
                                   away_name=away_name,
                                   home_name=home_name,
                                   AT=AT,
                                   day_night=day_night,
                                   venue=venue,
                                   series_description=series_description,
                                   series_string=series_string,
                                   broadcast_html=broadcast_html,
                                   back_url=f"{base_url}{u.machine_print_date(date)}"
                                   )
        
        return web.Response(text=html, content_type="text/html", headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*"
        })
    
    def construct_broadcasts(self, broadcasts, base_url, gamePK, short, ):
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
            media_state_id = broadcast.get('mediaState', {}).get('mediaStateId', 'N/A')
            media_state_text = broadcast.get('mediaState', {}).get('mediaStateText', 'N/A')
            if media_state_id != 1:
                broadcast_str = f'<a href="{base_url}{gamePK}/{broadcast["mediaId"]}">{broadcast["name"]}</a>'
            else:
                broadcast_str = broadcast['name']

            broadcast_html += f"""
                    <tr>
                        <td data-label="Broadcast">{broadcast_str}</td>
                        <td data-label="Type">{broadcast.get('type', 'N/A')}</td>
                        <td data-label="Side">{short[broadcast.get('homeAway', 'N/A')]}</td>
                        <td data-label="State">{media_state_text}</td>
                        <td data-label="Language">{u.get_language(broadcast.get('language', 'N/A'))}</td>
                        <td data-label="Availability">{broadcast.get('availability', {}).get('availabilityText', 'N/A')}</td>
                    </tr>""" 
        broadcast_html += '''
                </tbody>
            </table>'''
        return broadcast_html

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
    </br>"""

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
    
    async def serve_date2(self, base_url, date_str=None):
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
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Baseball Pipe</title>
        <style>
            * {{
                box-sizing: border-box;
            }}
            body {{
                font-family: monospace;
                font-size: 16px;
                margin: 0;
                padding: 12px;
                background-color: #f9f9f9;
            }}
            .container {{
                max-width: 900px;
                margin: 0 auto;
            }}
            .header {{
                text-align: center;
                margin-bottom: 20px;
            }}
            .date-nav {{
                display: flex;
                justify-content: center;
                align-items: center;
                gap: 15px;
                margin-bottom: 20px;
                flex-wrap: nowrap;
            }}
            .date-nav button {{
                padding: 8px 12px;
                font-size: 16px;
                cursor: pointer;
                background-color: #007bff;
                color: white;
                border: none;
                border-radius: 4px;
                transition: background-color 0.3s;
                flex-shrink: 0;
            }}
            .date-nav button:hover {{
                background-color: #0056b3;
            }}
            .date-nav button:active {{
                background-color: #004085;
            }}
            .date-display {{
                font-size: 18px;
                font-weight: bold;
                min-width: 150px;
                text-align: center;
                flex-shrink: 1;
                min-width: 120px;
            }}
            p {{
                margin: 8px 0;
                line-height: 1.5;
            }}
            a {{
                text-decoration: none;
                color: #007bff;
            }}
            a:hover {{
                text-decoration: underline;
                color: #0056b3;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background-color: white;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
                border-radius: 4px;
                overflow: hidden;
            }}
            th {{
                background-color: #f2f2f2;
                padding: 12px;
                text-align: left;
                font-weight: 600;
                border-bottom: 2px solid #ddd;
                font-size: 14px;
            }}
            td {{
                padding: 12px;
                border-bottom: 1px solid #ddd;
            }}
            tr:last-child td {{
                border-bottom: none;
            }}
            td a {{
                display: block;
                padding: 4px 0;
            }}
            .free-game {{
                background-color: #d4edda;
            }}
            .no-games {{
                text-align: center;
                padding: 20px;
                color: #666;
                font-style: italic;
            }}

            /* Mobile Responsive */
            @media (max-width: 768px) {{
                body {{
                    padding: 8px;
                    font-size: 14px;
                }}
                .date-nav {{
                    gap: 10px;
                }}
                .date-nav button {{
                    padding: 6px 10px;
                    font-size: 14px;
                }}
                .date-display {{
                    font-size: 16px;
                    min-width: auto;
                }}
                th, td {{
                    padding: 8px;
                    font-size: 13px;
                }}
                th {{
                    font-size: 12px;
                }}
                table {{
                    font-size: 13px;
                }}
            }}

            @media (max-width: 480px) {{
                body {{
                    padding: 6px;
                    font-size: 12px;
                }}
                .date-nav {{
                    gap: 3px;
                    flex-wrap: nowrap;
                }}
                .date-nav button {{
                    padding: 4px 6px;
                    font-size: 11px;
                    min-width: 30px;
                    flex-shrink: 0;
                }}
                .date-display {{
                    font-size: 13px;
                    min-width: 80px;
                    flex-shrink: 1;
                }}
                .header p {{
                    font-size: 14px;
                    margin: 4px 0;
                }}
                th, td {{
                    padding: 6px;
                    font-size: 11px;
                }}
                th {{
                    font-size: 10px;
                }}
                table {{
                    font-size: 11px;
                }}
                /* Stack columns on very small screens */
                table, thead, tbody, th, td, tr {{
                    display: block;
                }}
                tr {{
                    margin-bottom: 12px;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    overflow: hidden;
                }}
                th {{
                    display: none;
                }}
                td {{
                    display: grid;
                    grid-template-columns: 100px 1fr;
                    align-items: start;
                    padding: 8px;
                    border: none;
                    border-bottom: 1px solid #eee;
                }}
                td:first-child {{
                    grid-column: 1 / -1;
                    padding: 8px;
                    background-color: #f9f9f9;
                    border-bottom: 1px solid #ddd;
                }}
                td:before {{
                    content: attr(data-label);
                    font-weight: 600;
                    color: #666;
                    font-size: 11px;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="date-nav">
                    {yesterday_btn}
                    <div class="date-display">{p_date}</div>
                    {tomorrow_btn}
                </div>
            </div>"""

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
            <table>
                <thead>
                    <tr>
                        <th>Game</th>
                        <th>{u.get_local_datetime()}</th>
                        <th>State</th>
                    </tr>
                </thead>
                <tbody>
"""

        for left, right, gamePK, game_date, status, free in pairs:
            time_str = u.pretty_print_time_locally(game_date)
            
            if free:
                row_class = 'class="free-game"'
                link = f'<a href="{base_url}{gamePK}">{left}{AT}{right}</a>'
            else:
                row_class = ''
                link = f'<a href="{base_url}{gamePK}">{left}{AT}{right}</a>'

            html += f"""
                    <tr {row_class}>
                        <td data-label="Game">{link}</td>
                        <td data-label="{u.get_local_datetime()}">{time_str}</td>
                        <td data-label="State">{status}</td>
                    </tr>
"""
            
        if not pairs:
            html += f"""
                    <tr>
                        <td colspan="999" class="no-games">No Games Scheduled.</td>
                    </tr>
"""

        html += """
                </tbody>
            </table>
        </div>
    </body>
</html>"""

        return web.Response(text=html, content_type="text/html", headers={
                "Access-Control-Allow-Origin": "*"
        })

    async def serve_date3(self, base_url, date_str=None):
        logger.info(f"processing date_str: {date_str}")
        html_file = Path(os.path.join(SCRIPT_DIR, "date.html"))

        if date_str:
            date = u.get_date(start_date=date_str)
            result = await baseball_pipe.mlb_stats.get_games_on_date(start_date=date)
            date, games = result
            
        yesterday = (date - u.timedelta(days=1)).strftime("%Y%m%d")
        tomorrow = (date + u.timedelta(days=1)).strftime("%Y%m%d")
        yesterday_btn = f'<button onclick="window.location.href=&quot;{base_url + yesterday}&quot;;">&lt;</button>'
        tomorrow_btn = f'<button onclick="window.location.href=&quot;{base_url + tomorrow}&quot;;">&gt;</button>'
        

        pairs = []
        left_width = 0
        right_width = 0
        table = ""
        if games:
            table += f"""<table>
                <thead>
                    <tr>
                        <th>Game</th>
                        <th>Free</th>
                        <th>{u.get_local_datetime()}</th>
                        <th>State</th>
                    </tr>
                </thead>
                <tbody>"""
        else:
            table += f"<p class='no-games'>No Games Scheduled.<p>"

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

        for left, right, gamePK, game_date, status, free in pairs:
            time_str = u.pretty_print_time_locally(game_date)
            
            if free:
                row_class = 'class="free-game"'
                link = f'<a href="{base_url}{gamePK}"><span class="left">{left}</span><span class="at">{AT}</span><span class="right">{right}</span></a>'
                free = "★"
            else:
                row_class = ''
                link = f'<a href="{base_url}{gamePK}"><span class="left">{left}</span><span class="at">{AT}</span><span class="right">{right}</span></a>'
                free = ""

            table += f"""
                    <tr {row_class}>
                        <td data-label="Game">{link}</td>
                        <td data-label="Free">{free}</td>
                        <td data-label="{u.get_local_datetime()}">{time_str}</td>
                        <td data-label="State">{status}</td>
                    </tr>"""
        if games: table += """
                </tbody>
            </table>"""
            
        template = Template(html_file.read_text())
        html = template.substitute(p_date=u.pretty_print_date(date),
                                   yesterday_btn=yesterday_btn,
                                   tomorrow_btn=tomorrow_btn,
                                   table=table)

        return web.Response(text=html, content_type="text/html", headers={
                "Access-Control-Allow-Origin": "*"
        })

    def serve_options(self):
        return web.Response(
            status=204, # Standard "No Content" for preflight
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Range, Authorization, X-Requested-With", # Explicit is safer than *
                "Access-Control-Max-Age": "86400",
                "Access-Control-Expose-Headers": "Content-Length, Content-Range", # Helps the player seek
            }
        )

    