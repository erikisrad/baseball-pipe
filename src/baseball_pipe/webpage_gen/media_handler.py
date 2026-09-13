import asyncio
import logging
import os
import subprocess
import time
from aiohttp import web

from baseball_pipe.mlbtv.stream import Stream
from baseball_pipe.misc.header_handler import cors_headers
from baseball_pipe.playlist.stream_mangler import prefix_master_urls, rewrite_media_playlist
from baseball_pipe.playlist import generate_filler_segments as gfs

logger = logging.getLogger(__name__)

SEGMENT_CONTENT_TYPES = {
    ".ts": "video/mp2t",
    ".aac": "audio/aac",
    ".key": "application/octet-stream",
    ".vtt": "text/vtt",
}

async def serve_master_playlist(request: web.Request, stream: Stream):
    gamePK = request.match_info.get("gamePK")
    mediaId = request.match_info.get("mediaId")

    playlist = await stream.get_master_playlist()

    own_base = f"{request.url.origin()}/{gamePK}/{mediaId}/"
    playlist = prefix_master_urls(playlist, own_base)

    return web.Response(text=playlist, headers=cors_headers("application/vnd.apple.mpegurl"))

async def serve_media_playlist(request: web.Request, stream: Stream, path: str):
    gamePK = request.match_info.get("gamePK")
    mediaId = request.match_info.get("mediaId")

    own_base = f"{request.url.origin()}/{gamePK}/{mediaId}/"
    playlist = await rewrite_media_playlist(stream, path, own_base)

    return web.Response(text=playlist, headers=cors_headers("application/vnd.apple.mpegurl"))

async def serve_segment(request: web.Request, stream: Stream, path: str):
    ext = os.path.splitext(path)[1].lower()
    content_type = SEGMENT_CONTENT_TYPES.get(ext, "application/octet-stream")

    data = await stream.get_segment(path)
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
    """
    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", file_path,
        "-c", "copy",
        "-muxdelay", "0", "-muxpreload", "0",
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
            func_start = time.perf_counter()
            data = await asyncio.to_thread(_rewrite_segment_timestamps, file_path, target_offset)
            elapsed_ms = (time.perf_counter() - func_start) * 1000
            logger.info(f"served filler segment {os.path.basename(file_path)} (tsoffset={target_offset:.6f}) in {elapsed_ms:.2f}ms")
            return web.Response(body=data, headers=cors_headers(content_type))
        except Exception as err:
            logger.warning(f"failed to rewrite timestamps for {file_path} (tsoffset={tsoffset}): {err}")
            # fall through and serve the file unmodified rather than fail
            # the whole request over a timestamp correction that didn't work

    return web.FileResponse(file_path, headers=cors_headers(content_type))
