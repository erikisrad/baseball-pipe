import asyncio
import logging
import os
import subprocess
import time
from aiohttp import web

from baseball_pipe.mlbtv.stream import Stream
from baseball_pipe.misc.header_handler import cors_headers
from baseball_pipe.misc import utilities as u
from baseball_pipe.playlist.stream_mangler import prefix_master_urls, rewrite_media_playlist
from baseball_pipe.playlist import filler as gfs

logger = logging.getLogger(__name__)

SEGMENT_CONTENT_TYPES = {
    ".ts": "video/mp2t",
    ".aac": "audio/aac",
    ".key": "application/octet-stream",
    ".vtt": "text/vtt",
}

async def serve_master_playlist(request: web.Request, stream: Stream):
    serve_start = time.perf_counter()
    gamePK = request.match_info.get("gamePK")
    mediaId = request.match_info.get("mediaId")

    playlist = await stream.get_master_playlist()
    own_base = f"{request.url.origin()}/{gamePK}/{mediaId}/"
    playlist = prefix_master_urls(playlist, own_base)

    serve_ms = (time.perf_counter() - serve_start) * 1000
    logger.info(f"served rewritten master playlist in {serve_ms:.2f}ms, {len(playlist.splitlines())} total lines")

    return web.Response(text=playlist, headers=cors_headers("application/vnd.apple.mpegurl"))

async def serve_media_playlist(request: web.Request, stream: Stream, path: str):
    serve_start = time.perf_counter()
    gamePK = request.match_info.get("gamePK")
    mediaId = request.match_info.get("mediaId")

    own_base = f"{request.url.origin()}/{gamePK}/{mediaId}/"
    playlist = await rewrite_media_playlist(stream, path, own_base)
    
    serve_ms = (time.perf_counter() - serve_start) * 1000
    logger.info(f"served rewritten media playlist {path} in {serve_ms:.2f}ms, {len(playlist.splitlines())} total lines") 
    return web.Response(text=playlist, headers=cors_headers("application/vnd.apple.mpegurl"))

async def serve_segment(request: web.Request, stream: Stream, path: str):
    serve_start = time.perf_counter()
    ext = os.path.splitext(path)[1].lower()
    content_type = SEGMENT_CONTENT_TYPES.get(ext, "application/octet-stream")

    range_header = request.headers.get("Range")
    if range_header:
        logger.info(f"segment request for {path} from {request.headers.get('User-Agent')} included Range: {range_header}")

    data = await stream.get_segment(path)
    serve_ms = (time.perf_counter() - serve_start) * 1000
    logger.debug(f"served genuine segment {path} in {serve_ms:.2f}ms, {len(data)} bytes")
    return web.Response(body=data, headers=cors_headers(content_type))

def _run_ts_remux(file_path, output_ts_offset):
    """Blocking. Stream-copy remux (-c copy, no re-encode) with the given
    -output_ts_offset, piped straight to stdout -- no temp file at all.

    -muxdelay/-muxpreload 0 disable ffmpeg's default mpegts "broadcast
    preload" padding (see investigation history: without this, a requested
    offset of 0 actually comes out around 1.42s later than asked). With them
    off, a requested offset comes out within ~21ms of exact -- one AAC
    frame's worth of unavoidable sample-rate quantization, not artificial
    delay -- so the result can be trusted directly with no separate
    measure-and-correct step needed.

    -mpegts_pmt_start_pid/-streamid force the same PMT/video/audio PIDs
    filler.py's own encode already bakes in (gfs.PMT_PID/VIDEO_PID/AUDIO_PID,
    matched to real MLB segments) -- without them, this remux's own mpegts
    muxer silently reassigns its own default PIDs (PMT=4096, video=256,
    audio=257) on every single request, since `-c copy` only means "don't
    re-encode the codec data", not "keep the container's original PIDs".
    Confirmed directly: a source file with correct PIDs came out of this
    exact remux with the wrong ones before this was added, which is why the
    PID fix never actually reached anything served over HTTP.
    """
    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", file_path,
        "-c", "copy",
        "-muxdelay", "0", "-muxpreload", "0",
        "-mpegts_pmt_start_pid", str(gfs.PMT_PID),
        "-streamid", f"0:{gfs.VIDEO_PID}",
        "-streamid", f"1:{gfs.AUDIO_PID}",
        "-output_ts_offset", f"{output_ts_offset:.6f}",
        "-f", "mpegts", "pipe:1",
    ], capture_output=True, check=True)
    return result.stdout

def _rewrite_segment_timestamps(file_path, target_offset):
    """Blocking. Returns file_path's bytes with its embedded timestamps
    shifted so its real start PTS equals target_offset (within ~21ms).
    """
    return _run_ts_remux(file_path, target_offset)

async def serve_filler_segment(request: web.Request, path: str):
    serve_start = time.perf_counter()
    range_header = request.headers.get("Range")
    if range_header:
        logger.info(f"filler segment request for {path} from {request.headers.get('User-Agent')} included Range: {range_header}")

    # path is "filler/<resolution>/<framerate>/filler_NNN.ts" -- strip the
    # leading "filler/" so what's left is relative to gfs.OUTPUT_DIR itself
    relative_path = path[len("filler/"):]

    # path comes straight from the URL, so guard against traversal
    # (e.g. "filler/../../../../etc/passwd") by resolving it and confirming
    # it's still actually inside gfs.OUTPUT_DIR before touching the disk
    file_path = os.path.normpath(os.path.join(gfs.OUTPUT_DIR, relative_path))
    output_dir = os.path.normpath(gfs.OUTPUT_DIR)
    if os.path.commonpath([file_path, output_dir]) != output_dir:
        raise web.HTTPForbidden()

    if not os.path.isfile(file_path):
        return web.HTTPNotFound()

    ext = os.path.splitext(file_path)[1].lower()
    content_type = SEGMENT_CONTENT_TYPES.get(ext, "application/octet-stream")

    tsoffset = request.query.get("tsoffset")
    if tsoffset is not None:
        try:
            target_offset = float(tsoffset)

            data = await u.with_timeout(
                asyncio.to_thread(_rewrite_segment_timestamps, file_path, target_offset),
                10,
                f"filler timestamp rewrite for {file_path}"
            )
            serve_ms = (time.perf_counter() - serve_start) * 1000
            logger.debug(f"served filler segment {os.path.basename(file_path)} (tsoffset={target_offset:.6f}) in {serve_ms:.2f}ms, {len(data)} bytes")
            return web.Response(body=data, headers=cors_headers(content_type))
        except Exception as err:
            logger.warning(f"failed to rewrite timestamps for {file_path} (tsoffset={tsoffset}): {err}")
            # fall through and serve the file unmodified rather than fail
            # the whole request over a timestamp correction that didn't work

    return web.FileResponse(file_path, headers=cors_headers(content_type))

def _synchsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])

def _id3_timestamp_tag(tsoffset):
    ticks = round(tsoffset * 90000) % (1 << 33)
    body = gfs.ID3_TIMESTAMP_OWNER + ticks.to_bytes(8, "big")
    frame = b"PRIV" + _synchsafe(len(body)) + b"\x00\x00" + body
    return b"ID3\x04\x00\x00" + _synchsafe(len(frame)) + frame

async def serve_filler_audio(request: web.Request, path: str):
    relative_path = path[len("filler/audio/"):]
    file_path = os.path.normpath(os.path.join(gfs.OUTPUT_DIR, relative_path))
    output_dir = os.path.normpath(gfs.OUTPUT_DIR)
    if os.path.commonpath([file_path, output_dir]) != output_dir:
        raise web.HTTPForbidden()
    if not os.path.isfile(file_path):
        return web.HTTPNotFound()

    with open(file_path, "rb") as f:
        body = f.read()

    tsoffset = request.query.get("tsoffset")
    if tsoffset is not None:
        body = _id3_timestamp_tag(float(tsoffset)) + body
    return web.Response(body=body, headers=cors_headers("audio/aac"))

async def serve_filler_subtitle(request: web.Request, path: str):
    mpegts = request.query.get("tsoffset", "0")
    body = f"WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:{mpegts}\n\n"
    return web.Response(text=body, headers=cors_headers("text/vtt"))
