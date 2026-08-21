import logging
import os
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
        raise web.HTTPNotFound()

    ext = os.path.splitext(file_path)[1].lower()
    content_type = SEGMENT_CONTENT_TYPES.get(ext, "application/octet-stream")

    return web.FileResponse(file_path, headers=cors_headers(content_type))
