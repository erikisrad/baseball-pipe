import logging
import os
from aiohttp import web

from baseball_pipe.mlbtv.stream import Stream
from baseball_pipe.misc.header_handler import cors_headers
from baseball_pipe.misc.stream_mangler import prefix_playlist_urls, rewrite_media_playlist

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
    playlist = prefix_playlist_urls(playlist, own_base)

    return web.Response(text=playlist, headers=cors_headers("application/vnd.apple.mpegurl"))

async def serve_variant_playlist(request: web.Request, stream: Stream, path: str):
    gamePK = request.match_info.get("gamePK")
    mediaId = request.match_info.get("mediaId")

    playlist = await stream.get_media_playlist(path)

    own_base = f"{request.url.origin()}/{gamePK}/{mediaId}/"
    playlist = rewrite_media_playlist(playlist,
                                     own_base,
                                     ad_free=True,
                                     strip=True,
                                     end_time=await stream.get_end(),
                                     start_time=await stream.get_start())

    return web.Response(text=playlist, headers=cors_headers("application/vnd.apple.mpegurl"))

async def serve_segment(request: web.Request, stream: Stream, path: str):
    ext = os.path.splitext(path)[1].lower()
    content_type = SEGMENT_CONTENT_TYPES.get(ext, "application/octet-stream")

    data = await stream.get_segment(path)
    return web.Response(body=data, headers=cors_headers(content_type))
