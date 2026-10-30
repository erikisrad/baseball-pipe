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
import baseball_pipe.mlbtv.media_playlist
from baseball_pipe.playlist import filler as gfs

logger = logging.getLogger(__name__)

URI_PATTERN = re.compile(r'URI="([^"]+)"')
IV_PATTERN = re.compile(r'IV=0[xX]([0-9a-fA-F]+)')
PLAYLIST_TYPE_PATTERN = re.compile("#EXT-X-PLAYLIST-TYPE:([A-Z]+)")
AUTOSELECT_PATTERN = re.compile(r'AUTOSELECT=YES')
DEFAULT_PATTERN = re.compile(r'DEFAULT=YES')
# CUE_OUT_CONT_PATTERN dropped -- defined in stream_mangler.py but never
# referenced anywhere in the codebase.

# stream.template caches one rendition's rewritten playlist and is shared
# across every rendition of the same stream (see Stream.template) -- segment
# paths embed the rendition's own id (e.g. "823657-HD_7500K"), so that id
# gets swapped for this placeholder before caching and swapped back in
# (with whichever rendition is actually asking) on every read
RENDITION_PLACEHOLDER = "{{RENDITION}}"
FILLER_PLACEHOLDER = "{{FILLER}}"

DEBUG_DUMP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "test_files")

# item #3: these two were copy-pasted verbatim into every rewrite function
# below (five times, byte-for-byte identical). Hoisted once.
MISC_HEADER_PREFIXES = (
    "#EXTM3U",
    "#EXTINF:",
    "#EXT-X-VERSION:",
    "#EXT-X-TARGETDURATION:",
    "#EXT-X-MEDIA-SEQUENCE:",
    "#EXT-X-PROGRAM-DATE-TIME",
    "#EXT-X-PLAYLIST-TYPE:",
)
GARBAGE_PREFIXES = (
    "#EXT-X-CUE-OUT-CONT:",
    "#EXT-OATCLS-SCTE35",
)


def is_misc_header_line(line):
    return line.startswith(MISC_HEADER_PREFIXES)


def is_garbage_line(line):
    return line.startswith(GARBAGE_PREFIXES)


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


def format_program_date_time(dt: datetime) -> str:
    ms = dt.microsecond // 1000
    ts_str = dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"
    return f"#EXT-X-PROGRAM-DATE-TIME:{ts_str}"


def prefix_master_urls(playlist, base_url):
    lines = []
    for line in playlist.splitlines():

        if line.strip() == "":  # EMPTY LINE
            lines.append(line)
        elif line.startswith("http"):  # ALREADY A COMPLETE URL
            lines.append(line)
        elif "URI=" in line:  # URI
            line = uri_search_and_replace(line, base_url)
            if line.startswith("#EXT-X-MEDIA:") and "TYPE=SUBTITLES" in line:
                line = force_autoselect_no(line)  # forced subtitles are annoying as fuck
            lines.append(line)
        elif line.startswith("#"):  # NOT A URL
            lines.append(line)
        else:  # RELATIVE URL
            lines.append(urljoin(base_url, line.strip()))

    return "\n".join(lines)


async def rewrite_media_playlist(stream: Stream, name: str, own_base: str):

    playlist: baseball_pipe.mlbtv.media_playlist.Playlist = await stream.get_variant(name)
    assert playlist, f"unknown playlist {name} for stream {stream}"

    if not stream.get_live_status():
        playlist_media = await playlist.get_media()
        lines = playlist_media.split('\n')
        stream.set_live_status(determine_live_status(lines))

    if playlist.type == baseball_pipe.mlbtv.media_playlist.VIDEO:
        if stream.get_live_status() == "vod":
            return await nuke_playlist_ads(stream, playlist, own_base)
        else:
            return await fill_playlist_ads2(stream, playlist, own_base)

    elif playlist.type == baseball_pipe.mlbtv.media_playlist.AUDIO:
        if stream.get_live_status() == "vod":
            return await nuke_playlist_ads(stream, playlist, own_base)
        else:
            return await fill_audio_playlist_ads(stream, playlist, own_base)

    elif playlist.type == baseball_pipe.mlbtv.media_playlist.SUBS:
        if stream.get_live_status() == "vod":
            return await nuke_playlist_ads(stream, playlist, own_base)
        else:
            return await fill_subtitle_playlist_ads(stream, playlist, own_base)

    else:
        return await nuke_playlist_ads(stream, playlist, own_base)


def determine_live_status(lines):
    max_lines_read = 10
    for i, line in enumerate(lines):
        if i == max_lines_read:
            raise Exception(f"media playlist doesnt have live status in first {max_lines_read} lines")

        if line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            if "VOD" in line:
                return "vod"
            else:
                return "live"


async def rewrite_playlist_only(stream: Stream, playlist: "baseball_pipe.mlbtv.media_playlist.Playlist", base_url: str):
    """Debug-only pass-through: rewrites segment/key URLs to point through
    our own proxy and otherwise copies the upstream playlist verbatim -- no
    ad-cue handling, no filler substitution, and critically no
    #EXT-X-DISCONTINUITY ever inserted (CUE-OUT/CUE-IN/SCTE35 lines, if
    present upstream, are passed through untouched rather than converted).

    Deliberately kept separate from _rewrite_playlist_core() below: unlike
    the other four rewrite functions, this one never inspects ad-cue state
    at all, so forcing it through the shared ad-aware skeleton would mean
    adding flags to turn *more* of that skeleton off than it would actually
    reuse. It's already the simplest function in this module; that's a
    feature, not duplication.
    """
    func_start = time.perf_counter()

    name = playlist.get_name()
    rendition_id = name.rsplit('.', 1)[0]  # "823657-HD_7500K.m3u8" -> "823657-HD_7500K"

    template = copy.deepcopy(playlist.get_template())
    rewritten = [line.replace(RENDITION_PLACEHOLDER, rendition_id) for line in template.playlist]

    fetch_start = time.perf_counter()
    playlist_media = await playlist.get_media()
    fetch_ms = (time.perf_counter() - fetch_start) * 1000

    lines = playlist_media.splitlines()
    new_lines = lines[template.line_count:]

    for line in new_lines:

        if not line:
            continue

        elif line.endswith(".ts") or line.endswith(".aac") or line.endswith(".vtt"):
            rewritten.append(base_url + line)

        elif "URI=" in line:
            rewritten.append(uri_search_and_replace(line, base_url))

        elif not line.startswith('#'):
            rewritten.append(base_url + line)
            logger.warning(f"unknown segment: {line} for {stream}/{name}")

        else:
            # headers, EXTINF, PROGRAM-DATE-TIME, ENDLIST, CUE-OUT/CUE-IN/
            # SCTE35, anything else -- copied through verbatim. No
            # discontinuities are ever inserted here.
            rewritten.append(line)

    template.playlist = [line.replace(rendition_id, RENDITION_PLACEHOLDER) for line in rewritten]
    template.line_count = len(lines)
    playlist.overwrite_template(template)

    elapsed_ms = (time.perf_counter() - func_start) * 1000
    logger.info(f"rewrote pass-through playlist in {elapsed_ms:.2f}ms (upstream fetch {fetch_ms:.2f}ms), {len(new_lines)} new lines processed, {len(rewritten)} total lines")
    return '\n'.join(rewritten)


# ---------------------------------------------------------------------------
# item #1: the shared skeleton every ad-aware rewrite function below used to
# reimplement from scratch. nuke_playlist_ads, fill_audio_playlist_ads,
# fill_subtitle_playlist_ads, and fill_playlist_ads2 were ~170 lines each,
# identical in structure and differing only in: whether #EXT-X-KEY lines get
# tracked, whether a second (filler-directory) placeholder needs swapping
# alongside the rendition id, whether a bad EXTINF/segment pairing gets
# dumped to disk for debugging before raising, and -- the only part that's
# actually *interesting* per mode -- what happens on CUE-IN, on CUE-OUT, and
# to EXTINF lines while an ad is playing.
#
# Those three behaviors are supplied as small closures (on_cue_in/on_cue_out/
# on_ad_extinf) defined inside each public function below, where they can
# capture stream/name/base_url directly instead of needing every hook
# signature padded out with params most of them don't use.
# ---------------------------------------------------------------------------

async def _rewrite_playlist_core(
    stream: Stream,
    playlist: "baseball_pipe.mlbtv.media_playlist.Playlist",
    base_url: str,
    *,
    log_label: str,
    on_cue_in,
    on_cue_out,
    on_ad_extinf,
    track_key: bool = False,
    track_last_segment: bool = True,
    guard_extinf_before_segment: bool = True,
    extra_placeholder=None,  # (concrete_value, PLACEHOLDER_TOKEN) or None
):
    func_start = time.perf_counter()

    name = playlist.get_name()
    rendition_id = name.rsplit('.', 1)[0]  # "823657-HD_7500K.m3u8" -> "823657-HD_7500K"

    # deep copy, not just a reference -- stream.get_template() returns the
    # one StreamTemplate shared by every rendition of this stream, and this
    # function has several `await`s below before it writes back via
    # overwrite_template(). Without an isolated copy, a concurrent call
    # (e.g. a different rendition's poll interleaving through those awaits)
    # could mutate the same live object out from under this one -- or,
    # worse, a hook's own filler_lines.pop(0) would mutate the *shared*
    # list in place immediately, even before overwrite_template() ever runs.
    template = copy.deepcopy(playlist.get_template())
    rewritten = [line.replace(RENDITION_PLACEHOLDER, rendition_id) for line in template.playlist]
    if extra_placeholder:
        extra_value, extra_token = extra_placeholder
        rewritten = [line.replace(extra_token, extra_value) for line in rewritten]

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

        # EMPTY
        if not line:
            continue

        # ENDLIST
        elif line.startswith("#EXT-X-ENDLIST"):
            rewritten.append(line)

        # KEY
        elif track_key and line.startswith("#EXT-X-KEY:"):
            template.last_key = line
            rewritten.append(uri_search_and_replace(line, base_url))

        # DATE TIME
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

            if template.cued_out:
                # incrementing ad_elapsed is the hook's own job, not the
                # core's -- nuke_playlist_ads has no filler duration to
                # track and never touches this field at all in the
                # original, so making it unconditional here would be a
                # real (if currently inert) state difference from it.
                on_ad_extinf(template, duration, rewritten)

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
            on_cue_in(template, line, rewritten)

        elif line.startswith("#EXT-X-CUE-OUT:"):
            if template.cued_out:
                logger.warning(f"received unexpected #EXT-X-CUE-OUT for {stream}/{name}")
            template.cued_out = True
            await on_cue_out(template, line, rewritten)

        # AD SKIP
        elif template.cued_out:
            continue

        # SEGMENTS
        elif line.endswith(".ts") or line.endswith(".aac") or line.endswith(".vtt"):
            if track_last_segment:
                template.last_segment = line

            if guard_extinf_before_segment and not rewritten[-1].startswith("#EXTINF:"):
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
        elif is_misc_header_line(line):
            rewritten.append(line)

        # GARBAGE
        elif is_garbage_line(line):
            pass

        # CATCHALL
        else:
            logger.warning(f"keeping unknown line: {line} for {stream}/{name}")
            rewritten.append(line)

    # cache with this rendition's id (and, if given, the extra placeholder's
    # concrete value) swapped back out for its placeholder, so the next
    # rendition to ask (possibly a different one) can drop its own values
    # in rather than inheriting this one's segment paths
    cached = [line.replace(rendition_id, RENDITION_PLACEHOLDER) for line in rewritten]
    if extra_placeholder:
        extra_value, extra_token = extra_placeholder
        cached = [line.replace(extra_value, extra_token) for line in cached]
    template.playlist = cached
    template.line_count = len(lines)
    playlist.overwrite_template(template)

    elapsed_ms = (time.perf_counter() - func_start) * 1000
    logger.info(f"rewrote {log_label} playlist in {elapsed_ms:.2f}ms (upstream fetch {fetch_ms:.2f}ms), {len(new_lines)} new lines processed, {len(rewritten)} total lines")
    return '\n'.join(rewritten)


def _reset_ad_state(template):
    """Shared by every fill-with-filler mode's on_cue_in -- nuke_playlist_ads
    doesn't call this at all, since it never populates these fields in the
    first place (no filler substitution to size)."""
    template.ad_elapsed = 0.0
    template.expected_ad_duration = 0.0
    template.filler_elapsed = 0.0
    template.filler_lines = []


def _pop_due_filler_lines(template, rewritten, stream, name):
    """Shared by every fill-with-filler mode's on_ad_extinf: pop filler
    lines off template.filler_lines until filler_elapsed has caught back up
    to ad_elapsed (template.ad_elapsed is assumed already incremented by
    the caller for this EXTINF's duration)."""
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
        segment_duration = float(extinf_line.split(":", 1)[1].split(",")[0])
        template.filler_elapsed += segment_duration


async def nuke_playlist_ads(stream: Stream, playlist: "baseball_pipe.mlbtv.media_playlist.Playlist", base_url: str):
    """Real content only -- ad breaks are silently skipped, no filler
    substitution. A single #EXT-X-DISCONTINUITY marks where the gap was;
    unlike the fill-* modes, the raw CUE-IN/CUE-OUT tags and any
    #EXT-X-KEY aren't re-asserted, since there's no filler encoding on
    either side of the cut that needs one."""
    name = playlist.get_name()

    def on_cue_in(template, line, rewritten):
        rewritten.append("#EXT-X-DISCONTINUITY")

    async def on_cue_out(template, line, rewritten):
        pass  # nothing appended, no duration parsed, no filler prepared

    def on_ad_extinf(template, duration, rewritten):
        pass  # no filler substitution at all

    return await _rewrite_playlist_core(
        stream, playlist, base_url,
        log_label="nuke",
        track_key=False,
        track_last_segment=False,
        guard_extinf_before_segment=False,
        on_cue_in=on_cue_in,
        on_cue_out=on_cue_out,
        on_ad_extinf=on_ad_extinf,
    )


async def fill_audio_playlist_ads(stream: Stream, playlist: "baseball_pipe.mlbtv.media_playlist.Playlist", base_url: str):
    name = playlist.get_name()

    def on_cue_in(template, line, rewritten):
        logger.debug(f"received CUE-IN for stream {stream}/{name}\nexpected ad duration: {template.expected_ad_duration}\nad elapsed: {template.ad_elapsed}")
        rewritten.append(line)
        rewritten.append("#EXT-X-DISCONTINUITY")
        if template.last_key:
            rewritten.append(template.last_key)
        _reset_ad_state(template)

    async def on_cue_out(template, line, rewritten):
        try:
            template.expected_ad_duration = float(line.split(":", 1)[1])
        except ValueError as err:
            logger.error(f"failed to parse CUE-OUT duration for {stream}/{name}: {line}\n{err}")
            template.expected_ad_duration = 0.0
            raise

        rewritten.append(line)
        rewritten.append("#EXT-X-DISCONTINUITY")  # throw one of these bad boys in there
        rewritten.append("#EXT-X-KEY:METHOD=NONE")  # disable encryption for filler segments

        ad_pts = await probe_last_segment_end_pts(stream, template.last_segment, template.last_key, probe=gfs.probe_audio_end_timestamp)
        template.filler_lines = all_filler_audio(base_url, template.expected_ad_duration, ad_pts)

    def on_ad_extinf(template, duration, rewritten):
        template.ad_elapsed += duration
        _pop_due_filler_lines(template, rewritten, stream, name)

    return await _rewrite_playlist_core(
        stream, playlist, base_url,
        log_label="audio fill",
        track_key=True,
        guard_extinf_before_segment=True,
        on_cue_in=on_cue_in,
        on_cue_out=on_cue_out,
        on_ad_extinf=on_ad_extinf,
    )


async def fill_subtitle_playlist_ads(stream: Stream, playlist: "baseball_pipe.mlbtv.media_playlist.Playlist", base_url: str):
    name = playlist.get_name()

    def on_cue_in(template, line, rewritten):
        logger.debug(f"received CUE-IN for stream {stream}/{name}\nexpected ad duration: {template.expected_ad_duration}\nad elapsed: {template.ad_elapsed}")
        rewritten.append(line)
        rewritten.append("#EXT-X-DISCONTINUITY")
        if template.last_key:
            rewritten.append(template.last_key)
        _reset_ad_state(template)

    async def on_cue_out(template, line, rewritten):
        try:
            template.expected_ad_duration = float(line.split(":", 1)[1])
        except ValueError as err:
            logger.error(f"failed to parse CUE-OUT duration for {stream}/{name}: {line}\n{err}")
            template.expected_ad_duration = 0.0
            raise

        rewritten.append(line)
        rewritten.append("#EXT-X-DISCONTINUITY")  # throw one of these bad boys in there
        rewritten.append("#EXT-X-KEY:METHOD=NONE")  # disable encryption for filler segments

        if not template.header:
            template.header = await probe_last_subtitle_header(stream, template.last_segment)

        template.filler_lines = all_filler_subtitle(base_url, len(rewritten), template.header)

    def on_ad_extinf(template, duration, rewritten):
        template.ad_elapsed += duration
        _pop_due_filler_lines(template, rewritten, stream, name)

    return await _rewrite_playlist_core(
        stream, playlist, base_url,
        log_label="subtitle fill",
        track_key=True,
        guard_extinf_before_segment=True,
        on_cue_in=on_cue_in,
        on_cue_out=on_cue_out,
        on_ad_extinf=on_ad_extinf,
    )


async def fill_playlist_ads2(stream: Stream, playlist: "baseball_pipe.mlbtv.media_playlist.Playlist", base_url: str):
    name = playlist.get_name()
    rel_dir = gfs.rendition_str(playlist.resolution, playlist.frame_rate)

    def on_cue_in(template, line, rewritten):
        logger.debug(f"received CUE-IN for stream {stream}/{name}\nexpected ad duration: {template.expected_ad_duration}\nad elapsed: {template.ad_elapsed}")
        rewritten.append(line)
        rewritten.append("#EXT-X-DISCONTINUITY")
        if template.last_key:
            rewritten.append(template.last_key)
        _reset_ad_state(template)

    async def on_cue_out(template, line, rewritten):
        try:
            template.expected_ad_duration = float(line.split(":", 1)[1])
        except ValueError as err:
            logger.error(f"failed to parse CUE-OUT duration for {stream}/{name}: {line}\n{err}")
            template.expected_ad_duration = 0.0
            raise

        rewritten.append(line)
        rewritten.append("#EXT-X-DISCONTINUITY")  # throw one of these bad boys in there
        rewritten.append("#EXT-X-KEY:METHOD=NONE")  # disable encryption for filler segments

        fetch_start = time.perf_counter()
        ad_pts = await probe_last_segment_end_pts(stream, template.last_segment, template.last_key)
        fetch_ms = (time.perf_counter() - fetch_start) * 1000
        logger.info(f"fetched and decrypted {template.last_segment} in {fetch_ms:.2f}ms")

        template.filler_lines = all_filler_no_killer2(base_url, playlist, template.expected_ad_duration, ad_pts)

    def on_ad_extinf(template, duration, rewritten):
        template.ad_elapsed += duration
        _pop_due_filler_lines(template, rewritten, stream, name)

    return await _rewrite_playlist_core(
        stream, playlist, base_url,
        log_label="fill",
        track_key=True,
        guard_extinf_before_segment=True,
        on_cue_in=on_cue_in,
        on_cue_out=on_cue_out,
        on_ad_extinf=on_ad_extinf,
        extra_placeholder=(rel_dir, FILLER_PLACEHOLDER),
    )


async def probe_last_segment_end_pts(stream, last_segment, last_key=None, probe=gfs.probe_end_timestamp):
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
    if last_segment is None:
        logger.warning(f"probe_last_segment_end_pts called with no preceding real segment seen for {stream}")
        return None

    encrypted = last_key is not None and "METHOD=NONE" not in last_key
    if encrypted:
        key_uri_match = URI_PATTERN.search(last_key)
        iv_match = IV_PATTERN.search(last_key)
        if not key_uri_match or not iv_match:
            logger.warning(f"couldn't find key URI/IV in {last_key!r}, cannot probe end timestamp in {stream}")
            return None

    try:
        if encrypted:
            key_bytes, ciphertext = await asyncio.gather(
                stream.get_segment(key_uri_match.group(1)),
                stream.get_segment(last_segment),
            )

            decryptor = Cipher(algorithms.AES(key_bytes), modes.CBC(bytes.fromhex(iv_match.group(1)))).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = sym_padding.PKCS7(128).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
        else:
            plaintext = await stream.get_segment(last_segment)

        fd, tmp_path = tempfile.mkstemp(suffix=".ts")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(plaintext)
            return await asyncio.to_thread(probe, tmp_path)
        finally:
            os.remove(tmp_path)
    except Exception as err:
        logger.warning(f"failed to decrypt/probe end timestamp for {last_segment} in {stream}: {err}")
        return None


async def probe_last_subtitle_header(stream, last_segment):
    """Fetches the most recent real subtitle segment and returns its
    X-TIMESTAMP-MAP MPEGTS value (an int, 90 kHz ticks) -- this asset's fixed
    anchor, so filler .vtt segments can reuse it instead of guessing one.
    Subtitle segments aren't encrypted, so there's no key/IV step here,
    unlike the video and audio probes. Returns None if there's nothing to
    probe yet, or if the fetch or parse fails, so one bad segment doesn't
    take down the whole playlist rewrite.
    """
    if last_segment is None:
        logger.warning(f"probe_last_subtitle_header called with no preceding real segment seen for {stream}")
        return None

    try:
        data = await stream.get_segment(last_segment)
        match = re.search(r"MPEGTS:(\d+)", data.decode("utf-8", errors="replace"))
        if not match:
            logger.warning(f"no MPEGTS timestamp found in subtitle header for {last_segment} in {stream}")
            return None
        return int(match.group(1))
    except Exception as err:
        logger.warning(f"failed to read subtitle header for {last_segment} in {stream}: {err}")
        return None


def all_filler_no_killer2(own_base: str,
                           playlist: "baseball_pipe.mlbtv.media_playlist.Playlist",
                           expected_seconds: float,
                           starting_timestamp=None):

    resolution = playlist.resolution
    frame_rate = playlist.frame_rate
    filler_duration = playlist.filler_duration

    rel_dir = gfs.rendition_str(resolution, frame_rate)

    lines = []

    sec = gfs.SEC_DURATION
    total_segments = math.ceil(expected_seconds / sec)
    first_countdown = total_segments * sec
    overflow_segments = math.ceil(gfs.OVERFLOW_SECONDS / sec)
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


def all_filler_subtitle(own_base: str,
                         guid: float,
                         header: str):

    sec = gfs.SEC_DURATION
    duration = float(gfs.video_segment_seconds(gfs.AUDIO_REFERENCE_FPS, sec))
    total_segments = math.ceil(gfs.MAX_SECONDS / sec)
    first_countdown = total_segments * sec
    overflow_segments = math.ceil(gfs.OVERFLOW_SECONDS / sec)

    lines = []
    for i in range(total_segments + overflow_segments):
        countdown = first_countdown - i * sec
        lines.append(f"#EXTINF:{duration:.6f}")
        lines.append(f"{own_base}filler/subtitle/{guid}-{countdown}.vtt?tsoffset={header}")
    return lines


def all_filler_audio(own_base: str,
                      expected_seconds: float,
                      starting_timestamp: float):
    sec = gfs.SEC_DURATION
    total_segments = math.ceil(expected_seconds / sec)
    first_countdown = total_segments * sec
    overflow_segments = math.ceil(gfs.OVERFLOW_SECONDS / sec)

    lines = []
    elapsed = 0.0
    for i in range(total_segments + overflow_segments):
        countdown = first_countdown - i * sec
        packets = gfs.audio_packet_count(countdown, gfs.AUDIO_REFERENCE_FPS, sec)
        duration = packets * gfs.AUDIO_PACKET_SAMPLES / gfs.AUDIO_SAMPLE_RATE
        lines.append(f"#EXTINF:{duration:.6f}")
        segment_url = f"{own_base}filler/audio/silent_audio_{packets}pk.aac"
        # starting_timestamp is None when probing the last real segment's
        # end-PTS failed (see probe_last_segment_end_pts) -- there's nothing
        # to anchor these filler segments' tsoffset to, so omit it rather
        # than crash formatting None with :.6f.
        if starting_timestamp is not None:
            segment_url += f"?tsoffset={starting_timestamp + elapsed:.6f}"
        lines.append(segment_url)
        elapsed += duration
    return lines
