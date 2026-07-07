async def serve_stream_landing2(self, base_url, gamePK, mediaId):
    logger.info(f"processing stream landing for gamepk {gamePK}, mediaID {mediaId}")
    html_file = Path(os.path.join(SCRIPT_DIR, "stream_landing.html"))

    game = await baseball_pipe.old.mlb_stats.get_game_content(gamePK)
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
        self.account = baseball_pipe.old.mlbtv_account.Account(self.chrome120_session, self.proxy_url)

    if not self.token:
        self.token = await self.account.get_token()
    
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
