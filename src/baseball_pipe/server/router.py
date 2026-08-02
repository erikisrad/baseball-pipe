from aiohttp import web
import baseball_pipe.misc.utilities as u
import baseball_pipe.webpage_gen.media_handler as media_handler
from baseball_pipe.mlbtv.account import Account
from baseball_pipe.mlbtv.stream import Stream


async def serve_today(request: web.Request):
    return web.HTTPFound(location=f"/{u.machine_print_date(u.get_date())}")

async def serve_yesterday(request: web.Request):
    return web.HTTPFound(location=f"/{u.machine_print_date(u.get_date(days_ago=1))}")

async def serve_tomorrow(request: web.Request):
    return web.HTTPFound(location=f"/{u.machine_print_date(u.get_date(days_ago=-1))}")

async def route_media(request: web.Request):
    gamePK = request.match_info.get("gamePK")
    mediaId = request.match_info.get("mediaId")
    path = request.match_info.get("path")
    mlbtv_account: Account = request.app["mlbtv_account"]

    stream: Stream = await mlbtv_account.get_stream(gamePK, mediaId)

    if path == "master.m3u8":
        return await media_handler.serve_master_playlist(request, stream)

    if path.endswith(".m3u8"):
        return await media_handler.serve_variant_playlist(request, stream, path)

    return await media_handler.serve_segment(request, stream, path)

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