import asyncio
import logging
import os
import re
import subprocess
import time
from aiohttp import web

from baseball_pipe.mlbtv.mlbtv_stream import MlbtvStream
from baseball_pipe.misc.header_handler import cors_headers
from baseball_pipe.misc import utilities as u
from baseball_pipe.playlist.stream_mangler2 import prefix_master_urls, rewrite_media_playlist
from baseball_pipe.playlist import filler as gfs

logger = logging.getLogger(__name__)

SEGMENT_CONTENT_TYPES = {
    ".ts": "video/mp2t",
    ".aac": "audio/aac",
    ".key": "application/octet-stream",
    ".vtt": "text/vtt",
}

async def serve_master_playlist(request: web.Request, stream: MlbtvStream):
    serve_start = time.perf_counter()
    gamePK = request.match_info.get("gamePK")
    mediaId = request.match_info.get("mediaId")

    playlist = await stream.get_master_playlist()
    playlist = await filter_master_playlist(request, playlist)
    own_base = f"{request.url.origin()}/{gamePK}/{mediaId}/"

    ad_mode = request.query.get("ad")
    direct_mode = request.query.get("direct")
    extra_params = []
    if ad_mode:
        extra_params.append(f"ad={ad_mode}")
    if direct_mode:
        extra_params.append(f"direct={direct_mode}")
    extra_query = "&".join(extra_params) if extra_params else None
    playlist = prefix_master_urls(playlist, own_base, extra_query=extra_query)

    serve_ms = (time.perf_counter() - serve_start) * 1000
    logger.info(f"served rewritten master playlist in {serve_ms:.2f}ms, {len(playlist.splitlines())} total lines")

    return web.Response(text=playlist, headers=cors_headers("application/vnd.apple.mpegurl"))


async def filter_master_playlist(request: web.Request, playlist):
    query = {k.lower(): v.lower() for k, v in request.query.items()}
    bandwidth = query.get("bw")
    resolution = query.get("res")
    frame_rate = query.get("fps")

    if not (bandwidth or resolution or frame_rate):
        return playlist

    lines = playlist.splitlines()

    variants = []
    i = 0

    while i < len(lines):
        if lines[i].startswith("#EXT-X-STREAM-INF"):
            pairs = re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]+)', lines[i].split(":", 1)[1])
            attrs = {k.lower(): v.strip('"') for k, v in pairs}
            variants.append({"attrs": attrs, "inf": lines[i], "uri": lines[i + 1]})
            i += 1
        i += 1

    if not variants:
        return playlist

    matches = []
    if frame_rate:
        matches = [v for v in variants if v["attrs"].get("frame-rate") and round(float(v["attrs"]["frame-rate"])) == int(frame_rate)]
        if matches:
            variants = matches
        else:
            logger.warning(f"no variants matched frame-rate filter value: {frame_rate}")

    if resolution:
        matches = [v for v in variants if v["attrs"].get("resolution") == resolution]
        if matches:
            variants = matches
        else:
            logger.warning(f"no variants matched resolution filter value: {resolution}")

    if bandwidth:
        if bandwidth == "max":
            matches = max(variants, key=lambda v: int(v["attrs"].get("bandwidth", 0)))
        elif bandwidth == "min":
            matches = min(variants, key=lambda v: int(v["attrs"].get("bandwidth", 0)))
        else:
            logger.warning(f"invalid bandwidth filter value: {bandwidth}")

        if matches:
            variants = [matches]

    chosen = [v["inf"] for v in variants]
    out = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXT-X-STREAM-INF:"):
            if lines[i] in chosen:
                out.append(lines[i])
                out.append(lines[i + 1])
            i += 2
        else:
            out.append(lines[i])
            i += 1
    
    return "\n".join(out)

async def serve_media_playlist(request: web.Request, stream: MlbtvStream, path: str):
    serve_start = time.perf_counter()
    gamePK = request.match_info.get("gamePK")
    mediaId = request.match_info.get("mediaId")

    own_base = f"{request.url.origin()}/{gamePK}/{mediaId}/"
    direct = bool(request.query.get("direct"))
    playlist = await rewrite_media_playlist(stream, path, own_base, ad_mode=request.query.get("ad"), direct=direct)
    
    serve_ms = (time.perf_counter() - serve_start) * 1000
    logger.info(f"served rewritten media playlist {path} in {serve_ms:.2f}ms, {len(playlist.splitlines())} total lines") 
    return web.Response(text=playlist, headers=cors_headers("application/vnd.apple.mpegurl"))

async def serve_segment(request: web.Request, stream: MlbtvStream, path: str):
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
    return _run_ts_remux(file_path, target_offset)

async def serve_filler_segment(request: web.Request, path: str):
    serve_start = time.perf_counter()
    range_header = request.headers.get("Range")
    if range_header:
        logger.info(f"filler segment request for {path} from {request.headers.get('User-Agent')} included Range: {range_header}")

    relative_path = path[len("filler/"):]

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
