import logging
import os
from string import Template
from aiohttp import web

from baseball_pipe.webpage_gen.game_page import serve_no_game
import baseball_pipe.mlb.mlb_stats
import baseball_pipe.misc.utilities as u

logger = logging.getLogger(__name__)
PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
BROADCAST_HTML = os.path.join(PACKAGE_ROOT, "html", "broadcast.html")

async def serve_broadcast(request):
    gamePK = request.match_info.get("gamePK")
    mediaId = request.match_info.get("mediaId")
    local_tz = request.cookies.get("tz", "UTC")
    session = request.app["master_session"]
    mlbtv_account = request.app["mlbtv_account"]

    logger.info(f"serving {gamePK}/{mediaId} broadcast page to {u.get_ip_from_request(request)}")
    game = await baseball_pipe.mlb.mlb_stats.get_game_content(gamePK, session)
    
    if not game:
        return serve_no_game(gamePK)
    
    broadcasts = game.get("broadcasts", [])
    selected_broadcast = None
    for broadcast in broadcasts:
        if broadcast.get("mediaId", None) == mediaId:
            selected_broadcast = broadcast
            break

    if not selected_broadcast:
        return serve_no_broadcast(gamePK, mediaId)

    #TEAM NAMES
    home_name = u.safe_get(game, "teams", "home", "team", "name", default="Unknown")
    away_name = u.safe_get(game, "teams", "away", "team", "name", default="Unknown")

    date = baseball_pipe.mlb.mlb_stats.get_game_datetime(game)

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

    with open(BROADCAST_HTML) as f:
        template = Template(f.read())

    video_url = f"/{gamePK}/{mediaId}/master.m3u8"
    #video_url = "https://devstreaming-cdn.apple.com/videos/streaming/examples/bipbop_16x9/bipbop_16x9_variant.m3u8" # master debug
    #video_url = "https://devstreaming-cdn.apple.com/videos/streaming/examples/bipbop_16x9/gear5/prog_index.m3u8" # best
    #video_url = "https://dai.google.com/linear/hls/pa/event/k-VHR5unRdusBDqoXAuB0Q/stream/d337505d-c921-4b35-bdd2-8b22646e8522:MRN2/master.m3u8" # debug

    if f"{gamePK}/{mediaId}" not in self.streams:
        self.streams[f"{gamePK}/{mediaId}"] = baseball_pipe.old.mlbtv_stream.Stream(self.token, gamePK, mediaId, self.master_session, self.proxy_url)

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

def serve_no_broadcast(gamePK, mediaId):
    logger.warning(f"no broadcast found for: {gamePK}/{mediaId}")
    return web.Response(text=f"This isn't a valid mediaId ({mediaId}) for this gamePK ({gamePK})\nTry again", status=400)
