import asyncio
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
from baseball_pipe.playlist import generate_filler_segments as gfs

logger = logging.getLogger(__name__)

URI_PATTERN = re.compile(r'URI="([^"]+)"')
IV_PATTERN = re.compile(r'IV=0[xX]([0-9a-fA-F]+)')
PLAYLIST_TYPE_PATTERN = re.compile("#EXT-X-PLAYLIST-TYPE:([A-Z]+)")
CUE_OUT_CONT_PATTERN = re.compile(r'ElapsedTime=([\d.]+),Duration=([\d.]+)')
AUTOSELECT_PATTERN = re.compile(r'AUTOSELECT=YES')
DEFAULT_PATTERN = re.compile(r'DEFAULT=YES')

# real MLB ad segments run at their own upstream cadence (1-6s each), but the
# filler library is a fixed 1-second-per-file countdown (see
# generate_filler_segments.py) -- this is that filler segment's own encoded
# duration, used so EXTINF stays accurate for the substituted content
FILLER_SEGMENT_DURATION = 1.001

# a live playlist is a growing window, not a sliding one -- every CUE-OUT
# from earlier in the broadcast is still present and gets re-walked on every
# poll (roughly once per second), so without caching, every already-finished
# ad break's anchor gets re-fetched/decrypted/re-probed from scratch on every
# single request. A real segment's own embedded end-PTS never changes, so
# it's safe to compute once per (stream, segment path) and reuse forever.
_ad_break_anchor_cache = {}


def uri_search_and_replace(line, full_url):
    logger.debug(f"rewriting URL for line {line}")
    old = URI_PATTERN.search(line)
    assert old, f"failed to find URI in line: {line}"
    new = full_url + old.group(1)
    new_line = URI_PATTERN.sub(f'URI="{new}"', line)
    return new_line


async def probe_encrypted_segment_end_pts(stream, segment_path, key_uri_path, iv_hex):
    """Real MLB segments are AES-128-CBC encrypted on the wire, so ffprobe
    can't read their embedded PTS without decrypting first. Fetches the
    segment ciphertext and its key straight from upstream (segment_path and
    key_uri_path are relative paths, exactly as they'd appear unrewritten in
    the playlist -- stream.get_segment() prepends the upstream base URL and
    handles auth itself), decrypts with the cryptography package, and hands
    the plaintext to probe_end_timestamp() via a throwaway temp file.
    """
    key_bytes, ciphertext = await asyncio.gather(
        stream.get_segment(key_uri_path),
        stream.get_segment(segment_path),
    )

    iv_bytes = bytes.fromhex(iv_hex)
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
    extinf_time = 0
    started_segments = False # have we started writing segments yet
    ad_elapsed = 0.0 # measured by extinf printouts
    expected_ad_duration = 0.0 # measured by program date time printouts
    key_line = None # most recent real #EXT-X-KEY tag, so filler splices can disable/restore encryption around themselves
    key_uri_path = None # current key's URI, relative to upstream base (pre-rewrite) -- for fetching key bytes to decrypt with
    key_iv_hex = None # current key's IV, hex digits only (no "0x") -- for decrypting real segments to probe their PTS

    # most recent REAL segment's URL -- the SEGMENT branch below is the only
    # place this gets set, and it's unreachable while cued_out (the "AD SKIP"
    # branch intercepts everything first), so this naturally stays frozen on
    # whichever real segment was last seen for the entire duration of an ad
    # break -- i.e. by the time CUE-OUT/CUE-IN fires, it's exactly "the real
    # segment that immediately preceded this ad break", with no extra
    # snapshotting needed. Will be used to anchor filler segments' timestamps
    # to wherever the real content's own embedded PTS actually left off.
    # last_real_segment_path is the raw relative path as it appears in the
    # upstream playlist (fetchable via stream.get_segment(), which handles
    # upstream auth) -- needed to pull the real, still-encrypted ciphertext
    # for decryption. last_real_segment_url is the same segment through our
    # own proxy, kept only as a fallback for the (untested against real MLB
    # content, which is always encrypted) unencrypted case.
    last_real_segment_path = None
    last_real_segment_url = None
    ad_break_anchor_pts = None # real content's end-PTS, probed once at CUE-OUT

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

        elif end_time and stream_time and stream_time > end_time:
            rewritten.append("#EXT-X-ENDLIST")
            break

        #KEY -- own branch, must come before the generic "URI=" branch below,
        # which would otherwise rewrite its URL but never track the tag
        elif line.startswith("#EXT-X-KEY:"):
            key_uri_match = URI_PATTERN.search(line)
            key_uri_path = key_uri_match.group(1) if key_uri_match else None
            key_iv_match = IV_PATTERN.search(line)
            key_iv_hex = key_iv_match.group(1) if key_iv_match else None

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
                extinf_time += duration
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


        #AD CUES
        elif line.startswith("#EXT-X-CUE-IN"):

            if not cued_out:
                logger.warning(f"received unexpected #EXT-X-CUE-IN for {stream}/{name}")

            cued_out = False

            logger.debug(f"received CUE-IN for stream {stream}/{name}\nexpected ad duration: {expected_ad_duration}\nad elapsed: {ad_elapsed}")

            if abs(ad_elapsed - expected_ad_duration) > 1:
                logger.warning(f"mismatch between expected ad duration ({expected_ad_duration}) and actual ad elapsed ({ad_elapsed}) for stream {stream}/{name}")

            if ad_elapsed > 0:
                rewritten.extend(all_filler_no_killer(base_url,
                                                      playlist,
                                                      stream_time,
                                                      expected_ad_duration,
                                                      ad_elapsed,
                                                      ad_break_anchor_pts,
                                                      key_line,
                                                      finished = True))

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
                raise

            # anchor point for filler timestamps: the real content's own
            # embedded end-PTS, read directly off the last real segment
            # rather than trusting EXTINF-summed stream_time, which we've
            # measured can be off by a couple ms per segment vs the real
            # encoder's PTS -- fine for one segment, but drifts over a long
            # broadcast if relied on as the sole source of truth.
            # real MLB segments are AES-128 encrypted on the wire, so
            # ffprobe can't read them directly -- decrypt first, then probe
            # the plaintext. probe_end_timestamp() and the decryption both
            # run via asyncio.to_thread()/are otherwise cheap, so this
            # doesn't stall the event loop (and every other in-flight
            # request) while it works.
            cache_key = (str(stream), last_real_segment_path or last_real_segment_url)

            if cache_key in _ad_break_anchor_cache:
                ad_break_anchor_pts = _ad_break_anchor_cache[cache_key]
                logger.debug(f"ad break anchor pts for {stream}/{name}: {ad_break_anchor_pts} (cached)")
            elif last_real_segment_path is not None and key_uri_path is not None and key_iv_hex is not None:
                try:
                    ad_break_anchor_pts = await probe_encrypted_segment_end_pts(stream, last_real_segment_path, key_uri_path, key_iv_hex)
                    _ad_break_anchor_cache[cache_key] = ad_break_anchor_pts
                    logger.info(f"ad break anchor pts for {stream}/{name}: {ad_break_anchor_pts} (from {last_real_segment_path}, decrypted)")
                    logger.info(f"current extinf time {extinf_time}")
                except Exception as err:
                    logger.warning(f"failed to decrypt/probe end timestamp for {last_real_segment_path}: {err}")
                    ad_break_anchor_pts = None
            elif last_real_segment_url is not None:
                # no active encryption key -- content is unencrypted, so the
                # segment can be probed directly through our own proxy
                try:
                    ad_break_anchor_pts = await asyncio.to_thread(gfs.probe_end_timestamp, last_real_segment_url)
                    _ad_break_anchor_cache[cache_key] = ad_break_anchor_pts
                    logger.debug(f"ad break anchor pts for {stream}/{name}: {ad_break_anchor_pts} (from {last_real_segment_url}, unencrypted)")
                except Exception as err:
                    logger.warning(f"failed to probe end timestamp for {last_real_segment_url}: {err}")
                    ad_break_anchor_pts = None
            else:
                logger.warning(f"CUE-OUT with no preceding real segment seen for {stream}/{name}")
                ad_break_anchor_pts = None

        #AD SKIP
        elif cued_out:
            continue

        #SEGMENT
        elif line.endswith(".ts") or line.endswith(".aac") or line.endswith(".vtt"):
            # only .ts carries both video and audio -- that's what we need
            # to anchor a filler segment's timestamps against later, so
            # .aac (audio-only rendition) and .vtt (subtitles) don't count
            if line.endswith(".ts"):
                last_real_segment_path = line
                last_real_segment_url = base_url + line
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
    if cued_out and ad_elapsed > 0:
        rewritten.extend(all_filler_no_killer(base_url,
                                                playlist,
                                                stream_time,
                                                expected_ad_duration,
                                                ad_elapsed,
                                                ad_break_anchor_pts,
                                                key_line,
                                                finished = False))
        
    elapsed_ms = (time.perf_counter() - func_start) * 1000
    logger.info(f"rewrote fill playlist in {elapsed_ms:.2f}ms, {len(lines)} lines reduced to {len(rewritten)}")
    return '\n'.join(rewritten)

        
def all_filler_no_killer(own_base:str, playlist:Playlist, stream_time, expected_seconds:float, elapsed_seconds:float, starting_timestamp=None, key:str=None, finished:bool=False):
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

    starting_timestamp is the real content's actual end-PTS (seconds),
    probed off the last real segment right as this ad break started (see
    ad_break_anchor_pts in fill_playlist_ads) -- each filler segment's own
    media-clock position gets embedded in its URL as a query parameter so
    serve_filler_segment can rewrite that specific segment's timestamps to
    continue from it, rather than the segment's own pre-generated-library-
    relative PTS (which is unrelated to where it's actually being spliced
    into a real broadcast). None means no real segment was seen yet (or the
    probe failed) -- filler still gets served, just without the correction,
    same as before this existed.
    """
    # gfs.rendition_dir() returns an OS filesystem path (backslashes on
    # Windows) -- URLs always need forward slashes, so re-derive the
    # relative "<resolution>/<framerate>" URL fragment from it rather than
    # hardcoding the naming scheme a second time here

    resolution = playlist.resolution
    frame_rate = playlist.frame_rate
    filler_duration = playlist.filler_duration

    rel_dir = os.path.relpath(gfs.rendition_dir(resolution, frame_rate), gfs.OUTPUT_DIR).replace(os.sep, "/")

    lines = []
    lines.append(f"#EXT-X-CUE-OUT:{expected_seconds:.3f}")
    lines.append("#EXT-X-DISCONTINUITY")

    if key is not None:
        # filler segments were never encrypted with the real content's key --
        # disable decryption for the duration of the splice
        lines.append("#EXT-X-KEY:METHOD=NONE")

    # The countdown number on each frame is anchored to expected_seconds (the
    # break's full declared length, known immediately from CUE-OUT and fixed
    # for the whole break), not elapsed_seconds -- only how MANY segments get
    # emitted this call is driven by elapsed_seconds (how much of the break
    # is actually known/available right now). That split matters for an
    # in-progress break at the live edge: with expected=120 and elapsed=5,
    # this must emit segments 120, 119, 118, 117, 116 -- not 5, 4, 3, 2, 1,
    # which is what starting the countdown from elapsed produced before.
    # It also keeps every playlist refresh naturally consistent without
    # revising already-served segments: since expected_seconds doesn't
    # change mid-break, segment 120 always means the same real instant no
    # matter which refresh generated it, so later refreshes (larger
    # elapsed_seconds) just extend the countdown rather than renumbering it.
    total_segments = math.ceil(expected_seconds/filler_duration)
    segments_needed = math.ceil(elapsed_seconds/filler_duration)
    segments_generated = 0
    while segments_generated < segments_needed:
        segment_current = total_segments - segments_generated
        segment_url = f"{own_base}filler/{rel_dir}/filler_{segment_current:03d}.ts"

        # segments_generated == 0 always corresponds to the same segment
        # number (total_segments) regardless of which call/refresh produced
        # it, since it's anchored off the fixed total_segments rather than
        # however many segments happen to exist yet -- same reasoning that
        # keeps the countdown numbers themselves stable across refreshes, so
        # this stays consistent too rather than re-deriving a different
        # value for the same segment on a later refresh of the same break
        if starting_timestamp is not None:
            segment_ts = starting_timestamp + segments_generated * filler_duration
            segment_url += f"?tsoffset={segment_ts:.6f}"

        lines.append(f"#EXTINF:{filler_duration:.6f},")
        lines.append(segment_url)
        segments_generated += 1

    # leaving the filler segments' fabricated timeline -- CUE-IN forwarding
    # is intentional (see earlier discussion), paired with the discontinuity
    # back to whatever real timeline resumes after this
    if finished:
        lines.append("#EXT-X-CUE-IN")
        lines.append("#EXT-X-DISCONTINUITY")
        if key is not None:
            # restore real-content decryption before real segments resume
            lines.append(key)

    return lines




