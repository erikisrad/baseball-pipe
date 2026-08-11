import re
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

import logging

from baseball_pipe.mlb.mlb_stats import UNKNOWN_TIME

logger = logging.getLogger(__name__)

URI_PATTERN = re.compile(r'URI="([^"]+)"')
PLAYLIST_TYPE_PATTERN = re.compile("#EXT-X-PLAYLIST-TYPE:([A-Z]+)")

def uri_search_and_replace(line, full_url):
    logger.debug(f"rewriting URL for line {line}")
    old = URI_PATTERN.search(line)
    assert old, f"failed to find URI in line: {line}"
    new = full_url + old.group(1)
    new_line = URI_PATTERN.sub(f'URI="{new}"', line)
    return new_line

def format_program_date_time(dt:datetime) -> str:
    ms = dt.microsecond // 1000
    ts_str = dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"
    return f"#EXT-X-PROGRAM-DATE-TIME:{ts_str}"

def prefix_playlist_urls(playlist, base_url):
    lines = []
    for line in playlist.splitlines():
        
        if line.strip() == "": #EMPTY LINE
            lines.append(line)
        elif line.startswith("http"): #ALREADY A COMPLETE URL
            lines.append(line)
        elif "URI=" in line: #URI
            lines.append(uri_search_and_replace(line, base_url))
        elif line.startswith("#"): #NOT A URL
            lines.append(line)
        else: #RELATIVE URL
            lines.append(urljoin(base_url, line.strip()))

    return "\n".join(lines)

def rewrite_media_playlist(playlist_content:str, full_url:str, ad_free=True, strip=True, end_time:datetime=None, start_time:datetime=None):
    lines = playlist_content.split('\n')
    max_lines_read = 10

    for i, line in enumerate(lines):

        if i == max_lines_read:
            raise Exception(f"media playlist doesnt have playlist type in first {max_lines_read} lines")
        
        if line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            if "VOD" in line:
                return rewrite_vod_playlist3(lines, full_url, ad_free=ad_free, strip=strip, end_time=end_time, start_time=start_time)
                
            else:
                return rewrite_vod_playlist3(lines, full_url, ad_free=False, strip=strip, end_time=end_time, start_time=start_time)
                                

def rewrite_vod_playlist(lines:list, full_url:str, ad_free=True, strip=True):
    start = time.perf_counter()
    rewritten = []
    cued_out = False

    for line in lines:

        if not line:
            continue

        elif line.startswith("#EXT-X-CUE-IN"):
            if not cued_out:
                logger.warning("received unexpected #EXT-X-CUE-IN")

            cued_out = False
            rewritten.append("#EXT-X-DISCONTINUITY") # throw one of these bad boys in there since we fucked with the timeline so much

        elif cued_out:
            if line.startswith("#EXT-X-CUE-OUT"):
                logger.warning("received unexpected #EXT-X-CUE-OUT")

        elif not line.startswith('#'):
            rewritten.append(full_url + line)

        elif "URI=" in line:
            rewritten.append(uri_search_and_replace(line, full_url))

        # elif line.startswith("#EXT-X-PLAYLIST-TYPE:"):
        #     res = re.search(PLAYLIST_TYPE_PATTERN, line)
        #     playlist_type = res.group(1)

        #     if playlist_type == "EVENT":
        #         rewritten.append("#EXT-X-PLAYLIST-TYPE:LIVE")
        #     else:
        #         rewritten.append(line)

        elif line.startswith("#EXT-X-CUE-OUT"):
            cued_out = True

        #anything we just want to reprint
        elif (line.startswith("#EXTM3U")
              or line.startswith("#EXTINF:")
              or line.startswith("#EXT-X-VERSION:")
              or line.startswith("#EXT-X-TARGETDURATION:")
              or line.startswith("#EXT-X-MEDIA-SEQUENCE:")
              or line.startswith("#EXT-X-PROGRAM-DATE-TIME")
              or line.startswith("#EXT-X-ENDLIST")
              or line.startswith("#EXT-X-PLAYLIST-TYPE:")):
            
            rewritten.append(line)

        #anything we want to throw away
        elif (line.startswith("#EXT-X-CUE-OUT-CONT:")
              or line.startswith("#EXT-OATCLS-SCTE35")):
                pass

        else:
            logger.warning(f"keeping unknown line: {line}")
            rewritten.append(line)

    elapsed = time.perf_counter() - start
    logger.info(f"rewrote vod playlist in {elapsed:.2f} seconds")
    return '\n'.join(rewritten)

def rewrite_vod_playlist2(lines:list, full_url:str, first_start:datetime, last_end:datetime=None):
    start = time.perf_counter()
    rewritten = []
    cued_out = False
    in_game_window = False
    keep_next_uri = False
    current_time = None
    pending_date_time = None

    for line in lines:

        if not line:
            continue

        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME"):
            ts = line.split(":", 1)[1]
            current_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            pending_date_time = line

        elif line.startswith("#EXT-X-CUE-IN"):
            if not cued_out:
                logger.warning("received unexpected #EXT-X-CUE-IN")

            cued_out = False
            if in_game_window: # no point marking a discontinuity in a region we're dropping entirely anyway
                rewritten.append("#EXT-X-DISCONTINUITY") # throw one of these bad boys in there since we fucked with the timeline so much

        elif cued_out:
            if line.startswith("#EXT-X-CUE-OUT"):
                logger.warning("received unexpected #EXT-X-CUE-OUT")

        elif line.startswith("#EXTINF:"):
            duration = float(line[len("#EXTINF:"):].split(",")[0])

            in_range = (current_time is not None
                        and current_time >= first_start
                        and (last_end is None or current_time <= last_end))

            if in_range:
                if not in_game_window:
                    rewritten.append("#EXT-X-DISCONTINUITY") # skipped pre/post game content, timeline jumped
                    in_game_window = True
                if pending_date_time:
                    rewritten.append(pending_date_time)
                rewritten.append(line)
                keep_next_uri = True
            else:
                keep_next_uri = False

            pending_date_time = None
            if current_time is not None:
                current_time = current_time + timedelta(seconds=duration)

        elif not line.startswith('#'):
            if keep_next_uri:
                rewritten.append(full_url + line)

        elif "URI=" in line:
            rewritten.append(uri_search_and_replace(line, full_url))

        elif line.startswith("#EXT-X-CUE-OUT"):
            cued_out = True

        #anything we just want to reprint
        elif (line.startswith("#EXTM3U")
              or line.startswith("#EXT-X-VERSION:")
              or line.startswith("#EXT-X-TARGETDURATION:")
              or line.startswith("#EXT-X-MEDIA-SEQUENCE:")
              or line.startswith("#EXT-X-ENDLIST")
              or line.startswith("#EXT-X-PLAYLIST-TYPE:")):

            rewritten.append(line)

        #anything we want to throw away
        elif (line.startswith("#EXT-X-CUE-OUT-CONT:")
              or line.startswith("#EXT-OATCLS-SCTE35")):
                pass

        else:
            logger.warning(f"keeping unknown line: {line}")
            rewritten.append(line)

    elapsed = time.perf_counter() - start
    logger.info(f"rewrote vod playlist (game window) in {elapsed:.2f} seconds")
    return '\n'.join(rewritten)

def rewrite_vod_playlist3(lines:list, full_url:str, ad_free=True, strip=True, end_time:datetime=None, start_time:datetime=None):
    func_start = time.perf_counter()
    rewritten = []
    cued_out = False
    stream_time = None
    extinf_count = 0
    segment_count = 0
    started_segments = False

    if strip:
        if start_time not in (None, UNKNOWN_TIME):
            verifying_start = True
        else:
            verifying_start = False

        if end_time not in (None, UNKNOWN_TIME):
            verifying_end = True
        else:
            verifying_end = False
    else:
        verifying_start = False
        verifying_end = False

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
                    and (not verifying_start or stream_time >= start_time)
                    and (not verifying_end or stream_time <= end_time)):
                rewritten.append(line)

        elif line.startswith("#EXTINF:"):
            segment_start_time = None
            if strip:
                duration = float(line[len("#EXTINF:"):].split(",")[0])
                segment_start_time = stream_time
                if stream_time is not None:
                    stream_time = stream_time + timedelta(seconds=duration)

            if (not cued_out
                    and (not verifying_start or stream_time >= start_time)
                    and (not verifying_end or stream_time <= end_time)):

                if not started_segments and segment_start_time is not None:
                    last_line = rewritten[-1] if rewritten else None
                    if not (last_line and last_line.startswith("#EXT-X-PROGRAM-DATE-TIME:")):
                        rewritten.append(format_program_date_time(segment_start_time))

                rewritten.append(line)
                extinf_count += 1
                started_segments = True

        elif verifying_start and stream_time and stream_time < start_time:
            continue

        elif verifying_end and stream_time and stream_time > end_time:
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
            rewritten.append(full_url + line)
            segment_count += 1

        elif not line.startswith('#'):
            rewritten.append(full_url + line)
            segment_count += 1
            logger.warning("unknown segment: " + line)

        elif "URI=" in line:
            rewritten.append(uri_search_and_replace(line, full_url))

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

    elapsed = time.perf_counter() - func_start
    logger.info(f"rewrote vod playlist in {elapsed:.2f} seconds. {segment_count} segments, {extinf_count} EXTINF lines, {len(rewritten)} total lines")
    return '\n'.join(rewritten)
        


def rewrite_live_playlist(lines, full_url):
    return lines