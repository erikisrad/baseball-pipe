import os
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

import logging

from baseball_pipe.mlbtv.stream import Stream
from baseball_pipe.mlbtv.media_playlist import Playlist
from baseball_pipe.playlist import generate_filler_segments as gfs

logger = logging.getLogger(__name__)

URI_PATTERN = re.compile(r'URI="([^"]+)"')
PLAYLIST_TYPE_PATTERN = re.compile("#EXT-X-PLAYLIST-TYPE:([A-Z]+)")
CUE_OUT_CONT_PATTERN = re.compile(r'ElapsedTime=([\d.]+),Duration=([\d.]+)')
AUTOSELECT_PATTERN = re.compile(r'AUTOSELECT=YES')
DEFAULT_PATTERN = re.compile(r'DEFAULT=YES')

# real MLB ad segments run at their own upstream cadence (1-6s each), but the
# filler library is a fixed 1-second-per-file countdown (see
# generate_filler_segments.py) -- this is that filler segment's own encoded
# duration, used so EXTINF stays accurate for the substituted content
FILLER_SEGMENT_DURATION = 1.001


def uri_search_and_replace(line, full_url):
    logger.debug(f"rewriting URL for line {line}")
    old = URI_PATTERN.search(line)
    assert old, f"failed to find URI in line: {line}"
    new = full_url + old.group(1)
    new_line = URI_PATTERN.sub(f'URI="{new}"', line)
    return new_line


def force_autoselect_no(line):
    # DEFAULT=YES is the stronger signal -- a player will auto-select a
    # rendition marked DEFAULT regardless of AUTOSELECT, so both need to be
    # flipped or subtitles keep coming on by themselves
    line = AUTOSELECT_PATTERN.sub("AUTOSELECT=NO", line)
    line = DEFAULT_PATTERN.sub("DEFAULT=NO", line)
    return line


def format_program_date_time(dt:datetime) -> str:
    ms = dt.microsecond // 1000
    ts_str = dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"
    return f"#EXT-X-PROGRAM-DATE-TIME:{ts_str}"


def prefix_master_urls(playlist, base_url):
    lines = []
    for line in playlist.splitlines():
        
        if line.strip() == "": #EMPTY LINE
            lines.append(line)
        elif line.startswith("http"): #ALREADY A COMPLETE URL
            lines.append(line)
        elif "URI=" in line: #URI
            line = uri_search_and_replace(line, base_url)
            if line.startswith("#EXT-X-MEDIA:") and "TYPE=SUBTITLES" in line:
                line = force_autoselect_no(line) # forced subtitles are annoying as fuck
            lines.append(line)
        elif line.startswith("#"): #NOT A URL
            lines.append(line)
        else: #RELATIVE URL
            lines.append(urljoin(base_url, line.strip()))

    return "\n".join(lines)


async def rewrite_media_playlist(stream:Stream, name:str, own_base:str):

    playlist:Playlist = await stream.get_variant(name)
    assert playlist, f"unknown playlist {name} for stream {stream}"

    playlist_media = await playlist.get_media()
    lines = playlist_media.split('\n')

    if not stream.get_playlist_type():
        stream.set_playlist_type(determine_playlist_type(lines))

    if stream.get_playlist_type() == "vod":
        #return await nuke_playlist_ads(stream, playlist, lines, own_base)
        return await fill_playlist_ads(stream, playlist, lines, own_base)

    else:
        return await fill_playlist_ads(stream, playlist, lines, own_base)

        
def determine_playlist_type(lines):
    max_lines_read = 10
    for i, line in enumerate(lines):
        if i == max_lines_read:
            raise Exception(f"media playlist doesnt have playlist type in first {max_lines_read} lines")
        
        if line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            if "VOD" in line:
                return "vod"
            else:
                return "live"

            
async def nuke_playlist_ads(stream:Stream, playlist:Playlist, lines:list, base_url:str):
    func_start = time.perf_counter()
    rewritten = [] # rewritten playlist
    cued_out = False # in ad break
    stream_time = None # moving playlist timestamp
    started_segments = False # have we started writing segments yet

    end_time = await stream.get_end()
    start_time = await stream.get_start()
    name = playlist.get_name()

    def can_write():
        return (not cued_out
                and (not start_time or stream_time >= start_time)
                and (not end_time or stream_time <= end_time))

    for line in lines:

        #EMPTY
        if not line:
            continue

        #ENDLIST
        elif line.startswith("#EXT-X-ENDLIST"):
                    rewritten.append(line)

        #DATE TIME
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            ts = line.split(":", 1)[1]
            stream_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))

            if can_write():
                rewritten.append(line)

        # EXTINF
        elif line.startswith("#EXTINF:"):

            try:
                duration = float(line[len("#EXTINF:"):].split(",")[0])
            except ValueError as err:
                logger.error(f"failed to parse EXTINF duration: {line} for {stream}/{name}\n{err}")
                raise
            segment_start_time = stream_time

            if stream_time is not None:
                stream_time = stream_time + timedelta(seconds=duration)

            if can_write():

                if not started_segments and segment_start_time is not None:
                    started_segments = True
                    last_line = rewritten[-1] if rewritten else None
                    if not (last_line and last_line.startswith("#EXT-X-PROGRAM-DATE-TIME:")):
                        rewritten.append(format_program_date_time(segment_start_time))

                rewritten.append(line)

        # TIME CHECK
        elif start_time and stream_time and stream_time < start_time:
            continue
        
        elif end_time and stream_time and stream_time > end_time:
            rewritten.append("#EXT-X-ENDLIST")
            break

        # AD CUES
        elif line.startswith("#EXT-X-CUE-IN"):
            if not cued_out:
                logger.warning(f"received unexpected #EXT-X-CUE-IN for {stream}/{name}")

            cued_out = False
            rewritten.append("#EXT-X-DISCONTINUITY") # throw one of these bad boys in there

        elif line.startswith("#EXT-X-CUE-OUT:"):
            if cued_out:
                logger.warning(f"received unexpected #EXT-X-CUE-OUT for {stream}/{name}")

            cued_out = True

        elif cued_out:
            continue

        # SEGMENTS
        elif line.endswith(".ts") or line.endswith(".aac") or line.endswith(".vtt"):
            rewritten.append(base_url + line)

        elif not line.startswith('#'):
            rewritten.append(base_url + line)
            logger.warning(f"unknown segment: {line} for {stream}/{name}")

        elif "URI=" in line:
            rewritten.append(uri_search_and_replace(line, base_url))

        # MISC

        elif (line.startswith("#EXTM3U")
                or line.startswith("#EXTINF:")
                or line.startswith("#EXT-X-VERSION:")
                or line.startswith("#EXT-X-TARGETDURATION:")
                or line.startswith("#EXT-X-MEDIA-SEQUENCE:")
                or line.startswith("#EXT-X-PROGRAM-DATE-TIME")
                or line.startswith("#EXT-X-PLAYLIST-TYPE:")):
            
            rewritten.append(line)

        # GARBAGE
        elif (line.startswith("#EXT-X-CUE-OUT-CONT:")
                or line.startswith("#EXT-OATCLS-SCTE35")):
            pass

        # CATCHALL
        else:
            logger.warning(f"keeping unknown line: {line} for {stream}/{name}")
            rewritten.append(line)

    elapsed_ms = (time.perf_counter() - func_start) * 1000
    logger.info(f"rewrote nuke playlist in {elapsed_ms:.2f}ms, {len(lines)} lines reduced to {len(rewritten)}")
    return '\n'.join(rewritten)


async def fill_playlist_ads(stream:Stream, playlist:Playlist, lines:list, base_url:str):
    func_start = time.perf_counter()
    rewritten = [] # rewritten playlist
    cued_out = False # in ad break
    stream_time = None # moving playlist timestamp
    started_segments = False # have we started writing segments yet
    ad_elapsed = 0.0 # measured by extinf printouts
    expected_ad_duration = 0.0 # measured by program date time printouts
    key_line = None # most recent real #EXT-X-KEY tag, so filler splices can disable/restore encryption around themselves

    end_time = await stream.get_end()
    start_time = await stream.get_start()
    name = playlist.get_name()

    def can_write():
        return (not cued_out
                and (not start_time or stream_time >= start_time)
                and (not end_time or stream_time <= end_time))

    for line in lines:

        #EMPTY
        if not line:
            continue

        #ENDLIST
        elif line.startswith("#EXT-X-ENDLIST"):
            rewritten.append(line)

        #KEY -- own branch, must come before the generic "URI=" branch below,
        # which would otherwise rewrite its URL but never track the tag
        elif line.startswith("#EXT-X-KEY:"):
            key_line = uri_search_and_replace(line, base_url)
            rewritten.append(key_line)

        #DATETIME
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):

            ts = line.split(":", 1)[1]
            stream_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))

            if (can_write()):
                rewritten.append(line)

        #EXTINF
        elif line.startswith("#EXTINF:"):
            try:
                duration = float(line[len("#EXTINF:"):].split(",")[0])
            except ValueError as err:
                logger.error(f"failed to parse EXTINF duration: {line}\n{err}")
                raise

            segment_start_time = stream_time
            if stream_time is not None:
                stream_time = stream_time + timedelta(seconds=duration)

            if cued_out:
                ad_elapsed += duration

            if can_write():

                if not started_segments and segment_start_time is not None:
                    started_segments = True
                    last_line = rewritten[-1] if rewritten else None
                    if not (last_line and last_line.startswith("#EXT-X-PROGRAM-DATE-TIME:")):
                        rewritten.append(format_program_date_time(segment_start_time))

                rewritten.append(line)

        #TIME
        elif start_time and stream_time and stream_time < start_time:
            continue

        elif end_time and stream_time and stream_time > end_time:
            rewritten.append("#EXT-X-ENDLIST")
            break


        #AD CUES
        elif line.startswith("#EXT-X-CUE-IN"):

            if not cued_out:
                logger.warning(f"received unexpected #EXT-X-CUE-IN for {stream}/{name}")

            cued_out = False

            logger.debug(f"received CUE-IN for stream {stream}/{name}\nexpected ad duration: {expected_ad_duration}\nad elapsed: {ad_elapsed}")

            if abs(ad_elapsed - expected_ad_duration) > 1:
                logger.warning(f"mismatch between expected ad duration ({expected_ad_duration}) and actual ad elapsed ({ad_elapsed}) for stream {stream}/{name}")

            if ad_elapsed > 1:
                rewritten.extend(all_filler_no_killer(base_url,
                                                      playlist.resolution,
                                                      playlist.frame_rate,
                                                      playlist.filler_duration,
                                                      ad_elapsed,
                                                      key_line))

            ad_elapsed = 0.0
            expected_ad_duration = 0.0

        elif line.startswith("#EXT-X-CUE-OUT:"):

            if cued_out:
                logger.warning(f"received unexpected #EXT-X-CUE-OUT for {stream}/{name}")

            cued_out = True

            ad_elapsed = 0.0
            try:
                expected_ad_duration = float(line.split(":", 1)[1])
            except ValueError as err:
                logger.error(f"failed to parse CUE-OUT duration for {stream}/{name}: {line}\n{err}")
                expected_ad_duration = 0.0

        #AD SKIP
        elif cued_out:
            continue

        #SEGMENT
        elif line.endswith(".ts") or line.endswith(".aac") or line.endswith(".vtt"):
            rewritten.append(base_url + line)

        #UNKNOWN SEGMENT
        elif not line.startswith('#'):
            rewritten.append(base_url + line)
            logger.warning(f"unknown segment for {stream}/{name}: " + line)

        #URI
        elif "URI=" in line:
            rewritten.append(uri_search_and_replace(line, base_url))

        #META
        elif (line.startswith("#EXTM3U")
                or line.startswith("#EXTINF:")
                or line.startswith("#EXT-X-VERSION:")
                or line.startswith("#EXT-X-TARGETDURATION:")
                or line.startswith("#EXT-X-MEDIA-SEQUENCE:")
                or line.startswith("#EXT-X-PROGRAM-DATE-TIME")
                or line.startswith("#EXT-X-PLAYLIST-TYPE:")):
            
            rewritten.append(line)


        #AD GARBAGE
        elif (line.startswith("#EXT-X-CUE-OUT-CONT:")
                or line.startswith("#EXT-OATCLS-SCTE35")):
                pass

        #FALL THROUGH
        else:
            logger.warning(f"keeping unknown line: {line}")
            rewritten.append(line)

    # PRINT OUT END AD
    if cued_out and ad_elapsed > 1:
        rewritten.extend(all_filler_no_killer(base_url,
                                                playlist.resolution,
                                                playlist.frame_rate,
                                                playlist.filler_duration,
                                                ad_elapsed,
                                                key_line))
        
    elapsed_ms = (time.perf_counter() - func_start) * 1000
    logger.info(f"rewrote fill playlist in {elapsed_ms:.2f}ms, {len(lines)} lines reduced to {len(rewritten)}")
    return '\n'.join(rewritten)


async def rewrite_vod_playlist3(stream:Stream, playlist:str, lines:list, own_base:str, ad_free=True, strip=True, start_time:datetime=None, end_time:datetime=None):

    func_start = time.perf_counter()
    rewritten = []
    cued_out = False
    stream_time = None
    extinf_count = 0
    segment_count = 0
    started_segments = False


    for line in lines:

        if not line:
            continue

        elif line.startswith("#EXT-X-ENDLIST"):
            rewritten.append(line)

        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            if strip:
                ts = line.split(":", 1)[1]
                stream_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))

            if (not cued_out
                    and (not start_time or stream_time >= start_time)
                    and (not end_time or stream_time <= end_time)):
                rewritten.append(line)

        elif line.startswith("#EXTINF:"):
            segment_start_time = None
            if strip:
                duration = float(line[len("#EXTINF:"):].split(",")[0])
                segment_start_time = stream_time
                if stream_time is not None:
                    stream_time = stream_time + timedelta(seconds=duration)

            if (not cued_out
                    and (not start_time or stream_time >= start_time)
                    and (not end_time or stream_time <= end_time)):

                if not started_segments and segment_start_time is not None:
                    last_line = rewritten[-1] if rewritten else None
                    if not (last_line and last_line.startswith("#EXT-X-PROGRAM-DATE-TIME:")):
                        rewritten.append(format_program_date_time(segment_start_time))

                rewritten.append(line)
                extinf_count += 1
                started_segments = True

        elif start_time and stream_time and stream_time < start_time:
            continue

        elif end_time and stream_time and stream_time > end_time:
            rewritten.append("#EXT-X-ENDLIST")
            break

        elif line.startswith("#EXT-X-CUE-IN"):
            if ad_free:
                if not cued_out:
                    logger.warning("received unexpected #EXT-X-CUE-IN")

                cued_out = False
                rewritten.append("#EXT-X-DISCONTINUITY") # throw one of these bad boys in there

            else:
                rewritten.append(line)

        elif line.startswith("#EXT-X-CUE-OUT:"):
            if ad_free:
                if cued_out:
                    logger.warning("received unexpected #EXT-X-CUE-OUT")
                cued_out = True

            else:
                rewritten.append(line)

        elif cued_out:
            continue

        elif line.endswith(".ts") or line.endswith(".aac") or line.endswith(".vtt"):
            rewritten.append(own_base + line)
            segment_count += 1

        elif not line.startswith('#'):
            rewritten.append(own_base + line)
            segment_count += 1
            logger.warning("unknown segment: " + line)

        elif "URI=" in line:
            rewritten.append(uri_search_and_replace(line, own_base))

        # elif line.startswith("#EXT-X-PLAYLIST-TYPE:"):
        #     res = re.search(PLAYLIST_TYPE_PATTERN, line)
        #     playlist_type = res.group(1)

        #     if playlist_type == "EVENT":
        #         rewritten.append("#EXT-X-PLAYLIST-TYPE:LIVE")
        #     else:
        #         rewritten.append(line)

        #anything we just want to reprint
        elif (line.startswith("#EXTM3U")
              or line.startswith("#EXTINF:")
              or line.startswith("#EXT-X-VERSION:")
              or line.startswith("#EXT-X-TARGETDURATION:")
              or line.startswith("#EXT-X-MEDIA-SEQUENCE:")
              or line.startswith("#EXT-X-PROGRAM-DATE-TIME")
              or line.startswith("#EXT-X-PLAYLIST-TYPE:")):
            
            rewritten.append(line)

        #ad stuff
        elif (line.startswith("#EXT-X-CUE-OUT-CONT:")
              or line.startswith("#EXT-OATCLS-SCTE35")):
                if ad_free:
                    pass
                else:
                    rewritten.append(line)

        else:
            logger.warning(f"keeping unknown line: {line}")
            rewritten.append(line)

    elapsed_ms = (time.perf_counter() - func_start) * 1000
    logger.info(f"rewrote vod playlist in {elapsed_ms:.2f}ms. {segment_count} segments, {extinf_count} EXTINF lines, {len(rewritten)} total lines")
    return '\n'.join(rewritten)
        
async def rewrite_live_playlist2(stream:Stream, playlist:str, lines:list, own_base:str, ad_free=True, strip=True, start_time:datetime=None, end_time:datetime=None):
    func_start = time.perf_counter()
    rewritten = []
    cued_out = False
    stream_time = None
    extinf_count = 0
    segment_count = 0
    started_segments = False
    ad_elapsed = 0.0
    expected_ad_duration = 0.0

    resolution = await stream.get_variant_split_resolution(playlist)
    frame_rate = await stream.get_variant_ntsc_frame_rate(playlist)
    filler_duration = await stream.get_variant_filler_duration(playlist)

    video_playlist = bool(resolution and frame_rate)

    for line in lines:

        if not line:
            continue

        elif line.startswith("#EXT-X-ENDLIST"):
            rewritten.append(line)

        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            if strip:
                ts = line.split(":", 1)[1]
                stream_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))

            if (not cued_out
                    and (not start_time or stream_time >= start_time)
                    and (not end_time or stream_time <= end_time)):
                rewritten.append(line)

        elif line.startswith("#EXTINF:"):
            segment_start_time = None

            try:
                duration = float(line[len("#EXTINF:"):].split(",")[0])
            except ValueError as err:
                logger.error(f"failed to parse EXTINF duration: {line}\n{err}")
                raise

            if strip:   
                segment_start_time = stream_time
                if stream_time is not None:
                    stream_time = stream_time + timedelta(seconds=duration)

            if cued_out:
                ad_elapsed += duration

            if (not cued_out
                    and (not start_time or stream_time >= start_time)
                    and (not end_time or stream_time <= end_time)):

                if not started_segments and segment_start_time is not None:
                    last_line = rewritten[-1] if rewritten else None
                    if not (last_line and last_line.startswith("#EXT-X-PROGRAM-DATE-TIME:")):
                        rewritten.append(format_program_date_time(segment_start_time))

                rewritten.append(line)
                extinf_count += 1
                started_segments = True

        elif start_time and stream_time and stream_time < start_time:
            continue

        elif end_time and stream_time and stream_time > end_time:
            rewritten.append("#EXT-X-ENDLIST")
            break

        elif line.startswith("#EXT-X-CUE-IN"):

            if ad_free:

                if not cued_out:
                    logger.warning("received unexpected #EXT-X-CUE-IN")

                cued_out = False

                logger.debug(f"received CUE-IN\nexpected ad duration: {expected_ad_duration}\nad elapsed: {ad_elapsed}")

                if abs(ad_elapsed - expected_ad_duration) > 1:
                    logger.warning(f"mismatch between expected ad duration ({expected_ad_duration}) and actual ad elapsed ({ad_elapsed})")

                if ad_elapsed > 1:
                    rewritten.extend(all_filler_no_killer(own_base,
                                                          await stream.get_variant_split_resolution(playlist),
                                                          await stream.get_variant_ntsc_frame_rate(playlist),
                                                          ad_elapsed,
                                                          await stream.get_variant_filler_duration(playlist)))

                ad_elapsed = 0.0
                expected_ad_duration = 0.0

            else:
                rewritten.append(line)

        elif line.startswith("#EXT-X-CUE-OUT:"):

            if ad_free:

                if cued_out:
                    logger.warning("received unexpected #EXT-X-CUE-OUT")

                cued_out = True

                if video_playlist:
                    ad_elapsed = 0.0
                    try:
                        expected_ad_duration = float(line.split(":", 1)[1])
                    except ValueError as err:
                        logger.error(f"failed to parse CUE-OUT duration: {line}\n{err}")
                        expected_ad_duration = 0.0

            else:
                rewritten.append(line)

        elif cued_out:
            continue

        elif line.endswith(".ts") or line.endswith(".aac") or line.endswith(".vtt"):
            rewritten.append(own_base + line)
            segment_count += 1

        elif not line.startswith('#'):
            rewritten.append(own_base + line)
            segment_count += 1
            logger.warning("unknown segment: " + line)

        elif "URI=" in line:
            rewritten.append(uri_search_and_replace(line, own_base))

        # elif line.startswith("#EXT-X-PLAYLIST-TYPE:"):
        #     res = re.search(PLAYLIST_TYPE_PATTERN, line)
        #     playlist_type = res.group(1)

        #     if playlist_type == "EVENT":
        #         rewritten.append("#EXT-X-PLAYLIST-TYPE:LIVE")
        #     else:
        #         rewritten.append(line)

        #anything we just want to reprint
        elif (line.startswith("#EXTM3U")
              or line.startswith("#EXTINF:")
              or line.startswith("#EXT-X-VERSION:")
              or line.startswith("#EXT-X-TARGETDURATION:")
              or line.startswith("#EXT-X-MEDIA-SEQUENCE:")
              or line.startswith("#EXT-X-PROGRAM-DATE-TIME")
              or line.startswith("#EXT-X-PLAYLIST-TYPE:")):
            
            rewritten.append(line)

        #ad stuff
        elif (line.startswith("#EXT-X-CUE-OUT-CONT:")
              or line.startswith("#EXT-OATCLS-SCTE35")):
                if ad_free:
                    pass
                else:
                    rewritten.append(line)

        else:
            logger.warning(f"keeping unknown line: {line}")
            rewritten.append(line)

    if cued_out and ad_elapsed > 1:
        rewritten.append(all_filler_no_killer(own_base,
                                              await stream.get_variant_split_resolution(playlist),
                                              await stream.get_variant_ntsc_frame_rate(playlist),
                                              ad_elapsed,
                                              await stream.get_variant_filler_duration(playlist)))

    elapsed_ms = (time.perf_counter() - func_start) * 1000
    logger.info(f"rewrote vod playlist in {elapsed_ms:.2f}ms. {segment_count} segments, {extinf_count} EXTINF lines, {len(rewritten)} total lines")
    return '\n'.join(rewritten)



def all_filler_no_killer(own_base, resolution, frame_rate, filler_duration, seconds, key_line=None):
    """Build a complete, self-contained filler ad break of the given duration.

    Unlike rewrite_live_playlist2 (which swaps filler in for specific real ad
    segments as they arrive), this generates a whole break from scratch --
    for when there's no real upstream ad-break structure to match against,
    just a target duration. Segment URIs are absolute, prefixed with
    own_base, matching how the rewrite_* functions above serve segments
    through this proxy rather than pointing directly at upstream.

    key_line is the real content's current (rewritten) #EXT-X-KEY tag, if
    any. HLS applies a #EXT-X-KEY to every segment that follows it until a
    new one appears, so without disabling it here the player keeps trying to
    AES-decrypt our unencrypted filler segments -- producing garbage bytes a
    demuxer reports as "neither audio nor video found in segment."
    """
    # gfs.rendition_dir() returns an OS filesystem path (backslashes on
    # Windows) -- URLs always need forward slashes, so re-derive the
    # relative "<resolution>/<framerate>" URL fragment from it rather than
    # hardcoding the naming scheme a second time here
    rel_dir = os.path.relpath(gfs.rendition_dir(resolution, frame_rate), gfs.OUTPUT_DIR).replace(os.sep, "/")

    lines = []
    lines.append(f"#EXT-X-CUE-OUT:{seconds:.3f}")
    lines.append("#EXT-X-DISCONTINUITY")

    if key_line is not None:
        # filler segments were never encrypted with the real content's key --
        # disable decryption for the duration of the splice
        lines.append("#EXT-X-KEY:METHOD=NONE")

    # count down from the full break duration to 0, one filler segment at a
    # time, so the countdown baked into each frame lines up with how much of
    # the break is actually left
    elapsed = 0.0
    while elapsed < seconds:
        seconds_remaining = seconds - elapsed
        idx = max(0, min(gfs.MAX_SECONDS, round(seconds_remaining)))

        lines.append(f"#EXTINF:{filler_duration:.6f},")
        lines.append(f"{own_base}filler/{rel_dir}/filler_{idx:03d}.ts")

        elapsed += filler_duration

    if key_line is not None:
        # restore real-content decryption before real segments resume
        lines.append(key_line)

    # leaving the filler segments' fabricated timeline -- CUE-IN forwarding
    # is intentional (see earlier discussion), paired with the discontinuity
    # back to whatever real timeline resumes after this
    lines.append("#EXT-X-CUE-IN")
    lines.append("#EXT-X-DISCONTINUITY")

    return lines
