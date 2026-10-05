import asyncio
import copy
import math
import os
import re
import tempfile
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

import logging

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding

from baseball_pipe.mlbtv.stream import Stream
from baseball_pipe.mlbtv.media_playlist import Playlist
from baseball_pipe.playlist import filler as gfs

logger = logging.getLogger(__name__)

URI_PATTERN = re.compile(r'URI="([^"]+)"')
IV_PATTERN = re.compile(r'IV=0[xX]([0-9a-fA-F]+)')
PLAYLIST_TYPE_PATTERN = re.compile("#EXT-X-PLAYLIST-TYPE:([A-Z]+)")
CUE_OUT_CONT_PATTERN = re.compile(r'ElapsedTime=([\d.]+),Duration=([\d.]+)')
AUTOSELECT_PATTERN = re.compile(r'AUTOSELECT=YES')
DEFAULT_PATTERN = re.compile(r'DEFAULT=YES')

# stream.template caches one rendition's rewritten playlist and is shared
# across every rendition of the same stream (see Stream.template) -- segment
# paths embed the rendition's own id (e.g. "823657-HD_7500K"), so that id
# gets swapped for this placeholder before caching and swapped back in
# (with whichever rendition is actually asking) on every read
RENDITION_PLACEHOLDER = "{{RENDITION}}"
FILLER_PLACEHOLDER = "{{FILLER}}"

DEBUG_DUMP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "test_files")


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

    if not stream.get_playlist_type():
        playlist_media = await playlist.get_media()
        lines = playlist_media.split('\n')
        stream.set_playlist_type(determine_playlist_type(lines))

    if stream.get_playlist_type() == "vod":
        #return await nuke_playlist_ads(stream, playlist, own_base)
        return await fill_playlist_ads2(stream, playlist, own_base)

    else:
        return await fill_playlist_ads2(stream, playlist, own_base)

        
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

            
async def nuke_playlist_ads(stream:Stream, playlist:Playlist, base_url:str):
    func_start = time.perf_counter()

    name = playlist.get_name()
    rendition_id = name.rsplit('.', 1)[0] # "823657-HD_7500K.m3u8" -> "823657-HD_7500K"

    # deep copy, not just a reference -- see fill_playlist_ads2 for why:
    # stream.get_template() returns the one StreamTemplate shared by every
    # rendition of this stream, and this function has several `await`s
    # below before it writes back via set_template()
    template = copy.deepcopy(playlist.get_template())
    rewritten = [line.replace(RENDITION_PLACEHOLDER, rendition_id) for line in template.playlist]

    start_time = await stream.get_start()

    fetch_start = time.perf_counter()
    playlist_media = await playlist.get_media()
    fetch_ms = (time.perf_counter() - fetch_start) * 1000

    lines = playlist_media.splitlines()
    new_lines = lines[template.line_count:]

    def can_write():
        return (not template.cued_out
                and (not start_time or template.stream_time >= start_time))

    for line in new_lines:

        #EMPTY
        if not line:
            continue

        #ENDLIST
        elif line.startswith("#EXT-X-ENDLIST"):
                    rewritten.append(line)

        #DATE TIME
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            ts = line.split(":", 1)[1]
            template.stream_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))

            if can_write():
                rewritten.append(line)

        # EXTINF
        elif line.startswith("#EXTINF:"):

            try:
                duration = float(line[len("#EXTINF:"):].split(",")[0])
            except ValueError as err:
                logger.error(f"failed to parse EXTINF duration: {line} for {stream}/{name}\n{err}")
                raise
            segment_start_time = template.stream_time

            if template.stream_time is not None:
                template.stream_time = template.stream_time + timedelta(seconds=duration)

            if can_write():

                if not template.started_segments and segment_start_time is not None:
                    template.started_segments = True
                    last_line = rewritten[-1] if rewritten else None
                    if not (last_line and last_line.startswith("#EXT-X-PROGRAM-DATE-TIME:")):
                        rewritten.append(format_program_date_time(segment_start_time))

                rewritten.append(line)

        # TIME CHECK
        elif start_time and template.stream_time and template.stream_time < start_time:
            continue

        # AD CUES
        elif line.startswith("#EXT-X-CUE-IN"):
            if not template.cued_out:
                logger.warning(f"received unexpected #EXT-X-CUE-IN for {stream}/{name}")

            template.cued_out = False
            rewritten.append("#EXT-X-DISCONTINUITY") # throw one of these bad boys in there

        elif line.startswith("#EXT-X-CUE-OUT:"):
            if template.cued_out:
                logger.warning(f"received unexpected #EXT-X-CUE-OUT for {stream}/{name}")

            template.cued_out = True

        elif template.cued_out:
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

    # cache with this rendition's id swapped back out for the placeholder,
    # so the next rendition to ask (possibly a different one) can drop its
    # own id in rather than inheriting this one's segment paths
    template.playlist = [line.replace(rendition_id, RENDITION_PLACEHOLDER) for line in rewritten]
    template.line_count = len(lines)
    playlist.overwrite_template(template)

    elapsed_ms = (time.perf_counter() - func_start) * 1000
    logger.info(f"rewrote nuke playlist in {elapsed_ms:.2f}ms (upstream fetch {fetch_ms:.2f}ms), {len(new_lines)} new lines processed, {len(rewritten)} total lines")
    return '\n'.join(rewritten)


async def fill_playlist_ads2(stream:Stream, playlist:Playlist, base_url:str):
    func_start = time.perf_counter()

    name = playlist.get_name()
    rendition_id = name.rsplit('.', 1)[0] # "823657-HD_7500K.m3u8" -> "823657-HD_7500K"
    rel_dir = gfs.rendition_str(playlist.resolution, playlist.frame_rate)

    filler_duration = playlist.filler_duration

    # deep copy, not just a reference -- stream.get_template() returns the
    # one StreamTemplate shared by every rendition of this stream, and this
    # function has several `await`s below before it writes back via
    # set_template(). Without an isolated copy, a concurrent call (e.g. a
    # different rendition's poll interleaving through those awaits) could
    # mutate the same live object out from under this one -- or, worse, this
    # function's own filler_lines.pop(0) below would mutate the *shared*
    # list in place immediately, even before set_template() ever runs.
    template = copy.deepcopy(playlist.get_template())
    rewritten = [
        line.replace(RENDITION_PLACEHOLDER, rendition_id).replace(FILLER_PLACEHOLDER, rel_dir)
        for line in template.playlist
    ]

    start_time = await stream.get_start()

    fetch_start = time.perf_counter()
    playlist_media = await playlist.get_media()
    fetch_ms = (time.perf_counter() - fetch_start) * 1000

    lines = playlist_media.splitlines()

    new_lines = lines[template.line_count:]

    def can_write():
        return (not template.cued_out
                and (not start_time or template.stream_time >= start_time))

    for line in new_lines:

        #EMPTY
        if not line:
            continue

        #ENDLIST
        elif line.startswith("#EXT-X-ENDLIST"):
                    rewritten.append(line)

        #KEY
        elif line.startswith("#EXT-X-KEY:"):
            template.last_key = line
            rewritten.append(uri_search_and_replace(line, base_url))

        #DATE TIME
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            ts = line.split(":", 1)[1]
            template.stream_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))

            if can_write():
                rewritten.append(line)

        #EXTINF
        elif line.startswith("#EXTINF:"):

            try:
                duration = float(line[len("#EXTINF:"):].split(",")[0])
            except ValueError as err:
                logger.error(f"failed to parse EXTINF duration: {line}\n{err}")
                raise

            segment_start_time = template.stream_time

            if template.stream_time is not None:
                template.stream_time = template.stream_time + timedelta(seconds=duration)

            if template.cued_out:
                template.ad_elapsed += duration
                while template.filler_elapsed < template.ad_elapsed:
                    try:
                        extinf_line = template.filler_lines.pop(0)
                        segment_line = template.filler_lines.pop(0)
                        assert extinf_line.startswith("#EXTINF:"), f"expected EXTINF line, got: {extinf_line} in stream {stream}/{name}"
                        assert not segment_line.startswith("#"), f"expected segment line, got: {segment_line} in stream {stream}/{name}"
                        rewritten.extend([extinf_line, segment_line])
                    except IndexError:
                        logger.warning(f"not enough filler lines for {stream}/{name}")
                        raise
                    template.filler_elapsed += filler_duration

            if can_write():

                if not template.started_segments and segment_start_time is not None:
                    template.started_segments = True
                    last_line = rewritten[-1] if rewritten else None
                    if not (last_line and last_line.startswith("#EXT-X-PROGRAM-DATE-TIME:")):
                        rewritten.append(format_program_date_time(segment_start_time))

                rewritten.append(line)

        # GAME NOT STARTED
        elif start_time and template.stream_time and template.stream_time < start_time:
            continue

        # AD CUES
        elif line.startswith("#EXT-X-CUE-IN"):

            if not template.cued_out:
                logger.warning(f"received unexpected #EXT-X-CUE-IN for {stream}/{name}")

            template.cued_out = False
            logger.debug(f"received CUE-IN for stream {stream}/{name}\nexpected ad duration: {template.expected_ad_duration}\nad elapsed: {template.ad_elapsed}")

            if abs(template.ad_elapsed - template.expected_ad_duration) > 1:
                logger.warning(f"mismatch between expected ad duration ({template.expected_ad_duration}) and actual ad elapsed ({template.ad_elapsed}) for stream {stream}/{name}")

            template.ad_elapsed = 0.0
            template.expected_ad_duration = 0.0
            template.filler_elapsed = 0.0
            template.filler_lines = []

            rewritten.append(line)
            rewritten.append("#EXT-X-DISCONTINUITY")
            if template.last_key:
                rewritten.append(template.last_key)

        elif line.startswith("#EXT-X-CUE-OUT:"):
            if template.cued_out:
                logger.warning(f"received unexpected #EXT-X-CUE-OUT for {stream}/{name}")

            template.cued_out = True

            template.ad_elapsed = 0.0
            try:
                template.expected_ad_duration = float(line.split(":", 1)[1])
            except ValueError as err:
                logger.error(f"failed to parse CUE-OUT duration for {stream}/{name}: {line}\n{err}")
                template.expected_ad_duration = 0.0
                raise

            rewritten.append(line)
            rewritten.append("#EXT-X-DISCONTINUITY") # throw one of these bad boys in there
            rewritten.append("#EXT-X-KEY:METHOD=NONE") # disable encryption for filler segments
            ad_pts = await probe_last_segment_end_pts(stream, template.last_segment, template.last_key)
            template.filler_lines = all_filler_no_killer2(base_url, playlist, template.expected_ad_duration, ad_pts)

        # AD SKIP
        elif template.cued_out:
            continue

        # SEGMENTS
        elif line.endswith(".ts") or line.endswith(".aac") or line.endswith(".vtt"):
            template.last_segment = line

            if not rewritten[-1].startswith("#EXTINF:"):
                with open(os.path.join(DEBUG_DUMP_DIR, f"template_playlist_{rendition_id}.txt"), "w", encoding="utf-8") as f:
                    f.write('\n'.join(template.playlist))
                with open(os.path.join(DEBUG_DUMP_DIR, f"lines_{rendition_id}.txt"), "w", encoding="utf-8") as f:
                    f.write('\n'.join(lines))
                raise Exception(f"expected EXTINF before segment {line} for {stream}/{name}")

            rewritten.append(base_url + line)

        elif not line.startswith('#'):
            rewritten.append(base_url + line)
            logger.warning(f"unknown segment: {line} for {stream}/{name}")

        elif "URI=" in line:
            rewritten.append(uri_search_and_replace(line, base_url))
            logger.warning(f"unknown URI segment: {line} for {stream}/{name}")

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


    # cache with this rendition's id swapped back out for the placeholder,
    # so the next rendition to ask (possibly a different one) can drop its
    # own id in rather than inheriting this one's segment paths
    template.playlist = [
        line.replace(rendition_id, RENDITION_PLACEHOLDER).replace(rel_dir, FILLER_PLACEHOLDER)
        for line in rewritten
    ]
    template.line_count = len(lines)
    playlist.overwrite_template(template)

    elapsed_ms = (time.perf_counter() - func_start) * 1000
    logger.info(f"rewrote fill playlist in {elapsed_ms:.2f}ms (upstream fetch {fetch_ms:.2f}ms), {len(new_lines)} new lines processed, {len(rewritten)} total lines")
    return '\n'.join(rewritten)


async def probe_last_segment_end_pts(stream, last_segment, last_key=None):
    """Decrypts the most recent real segment and returns its true embedded
    end-PTS -- used to anchor filler segments' timestamps to wherever real
    content actually left off, rather than trusting EXTINF-summed time
    (which we've measured drifts from the real encoder's PTS over a long
    broadcast). last_segment/last_key are the raw, upstream-relative forms
    fill_playlist_ads already tracks -- exactly the form stream.get_segment()
    needs to reach MLB's own CDN. Returns None if there's nothing to probe
    yet, or if decrypting/probing fails, so one bad segment doesn't take
    down the whole playlist rewrite.
    """
    if last_segment is None or last_key is None:
        logger.warning(f"probe_last_segment_end_pts called with no preceding real segment/key seen for {stream}")
        return None

    key_uri_match = URI_PATTERN.search(last_key)
    iv_match = IV_PATTERN.search(last_key)
    if not key_uri_match or not iv_match:
        logger.warning(f"couldn't find key URI/IV in {last_key!r}, cannot probe end timestamp in {stream}")
        return None

    key_uri_path = key_uri_match.group(1)
    iv_bytes = bytes.fromhex(iv_match.group(1))

    try:
        key_bytes, ciphertext = await asyncio.gather(
            stream.get_segment(key_uri_path),
            stream.get_segment(last_segment),
        )

        decryptor = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = sym_padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()

        fd, tmp_path = tempfile.mkstemp(suffix=".ts")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(plaintext)
            return await asyncio.to_thread(gfs.probe_end_timestamp, tmp_path)
        finally:
            os.remove(tmp_path)
    except Exception as err:
        logger.warning(f"failed to decrypt/probe end timestamp for {last_segment} in {stream}: {err}")
        return None


def all_filler_no_killer2(own_base:str,
                         playlist:Playlist,
                         expected_seconds:float,
                         starting_timestamp=None):

    resolution = playlist.resolution
    frame_rate = playlist.frame_rate
    filler_duration = playlist.filler_duration

    rel_dir = gfs.rendition_str(resolution, frame_rate)

    lines = []

    sec = gfs.SEC_DURATION
    total_segments = math.ceil(expected_seconds/sec)
    first_countdown = total_segments * sec
    overflow_segments = math.ceil(gfs.OVERFLOW_SECONDS/sec)
    segments_generated = 0
    while segments_generated < total_segments + overflow_segments:
        segment_current = first_countdown - segments_generated * sec
        segment_url = f"{own_base}filler/{rel_dir}/filler_{segment_current:03d}.ts"

        if starting_timestamp is not None:
            segment_ts = starting_timestamp + segments_generated * filler_duration
            segment_url += f"?tsoffset={segment_ts:.6f}"

        lines.append(f"#EXTINF:{filler_duration:.6f}")
        lines.append(segment_url)
        segments_generated += 1

    return lines
