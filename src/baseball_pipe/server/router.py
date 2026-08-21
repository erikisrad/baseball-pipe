import os
from datetime import datetime, timedelta, timezone
from aiohttp import web
import baseball_pipe.misc.utilities as u
import baseball_pipe.webpage_gen.media_handler as media_handler
from baseball_pipe.mlbtv.account2 import Account
from baseball_pipe.mlbtv.stream import Stream

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


async def serve_today(request: web.Request):
    local_tz = request.cookies.get("tz", "UTC")
    date = u.localize(datetime.now(timezone.utc), local_tz)
    return web.HTTPFound(location=f"/{u.machine_print_date(date)}")

async def serve_yesterday(request: web.Request):
    local_tz = request.cookies.get("tz", "UTC")
    date = u.localize(datetime.now(timezone.utc), local_tz) - timedelta(days=1)
    return web.HTTPFound(location=f"/{u.machine_print_date(date)}")

async def serve_tomorrow(request: web.Request):
    local_tz = request.cookies.get("tz", "UTC")
    date = u.localize(datetime.now(timezone.utc), local_tz) + timedelta(days=1)
    return web.HTTPFound(location=f"/{u.machine_print_date(date)}")

async def route_media(request: web.Request):
    gamePK = request.match_info.get("gamePK")
    mediaId = request.match_info.get("mediaId")
    path = request.match_info.get("path")

    # filler segments are served straight from local disk, not proxied
    # through the upstream MLB stream, so this needs to short-circuit before
    # any of the stream/upstream-fetching logic below
    if path.startswith("filler/"):
        return await media_handler.serve_filler_segment(request, path)

    mlbtv_account: Account = request.app["mlbtv_account"]

    stream: Stream = await mlbtv_account.get_stream(gamePK, mediaId)

    if path == "master.m3u8":
        return await media_handler.serve_master_playlist(request, stream)

    if path.endswith(".m3u8"):
        return await media_handler.serve_media_playlist(request, stream, path)

    return await media_handler.serve_segment(request, stream, path)

async def serve_favicon(request: web.Request):
    return web.FileResponse(os.path.join(STATIC_DIR, "favicon.ico"))

async def serve_options(request: web.Request):
    return web.Response(
        status=204, # "No Content", standard for a preflight response
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Range, Authorization, X-Requested-With",
            "Access-Control-Max-Age": "86400",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range", # lets the player read these for seeking
        }
    )