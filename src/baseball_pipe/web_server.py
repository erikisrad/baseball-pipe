import os
from aiohttp import web
import logging as logger

import aiohttp
import baseball_pipe.misc.utilities as u
import baseball_pipe.login
import baseball_pipe.router
from baseball_pipe.old.mlbtv_stream import Stream

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(SCRIPT_DIR, "index.html")
LOCAL_PLAYLIST = os.path.join(os.path.dirname(__file__), "local.m3u8")
AT = " @ "
SPC = "&nbsp;"

@web.middleware
async def auth_middleware(request, handler):
    path = request.path

    if (path == "/login"
        or path.startswith("/static")
        or path.endswith(".m3u8")):

        return await handler(request)

    raw = request.cookies.get("auth")
    if not raw:
        logger.info(f"sending redirect to login for {request.remote}")
        raise web.HTTPFound("/login")

    if not baseball_pipe.login.verify_signed_cookie(raw):
        logger.warning(f"sending bad cookie redirect to login for {request.remote}")
        raise web.HTTPFound("/login")

    return await handler(request)

class WebServer:
    def __init__(self,
                 host="127.0.0.1",
                 port=8080,
                 proxy_url:str=os.environ["bbp_proxy_url"]):

        self.host = host
        self.port = port
        self.proxy_url = proxy_url
        self.app = web.Application(middlewares=[auth_middleware])
        self.app.router.add_static("/static", "baseball_pipe/static")

    async def on_startup(self, app):
        self.master_session = aiohttp.ClientSession()

    async def on_cleanup(self, app):
        if self.master_session:
            await self.master_session.close()

    def start(self):

        self.app.on_startup.append(self.on_startup)
        self.app.on_cleanup.append(self.on_cleanup)

        # Named keyword routes
        self.app.router.add_get("/today", baseball_pipe.router.serve_today)
        self.app.router.add_get("/yesterday", baseball_pipe.router.serve_yesterday)
        self.app.router.add_get("/tomorrow", baseball_pipe.router.serve_tomorrow)
        self.app.router.add_route("*", "/login", baseball_pipe.login.login)

        # Regex-constrained path params (aiohttp supports this natively)
        # self.app.router.add_get(r"/{date:\d{8}}", self.serve_date3)
        # self.app.router.add_get(r"/{gamePK:\d{1,6}}", self.serve_gamePK2)
        # self.app.router.add_get("/{gamePK}/{mediaId}/master.m3u8", self.serve_master_playlist)
        # self.app.router.add_get(r"/{gamePK}/{mediaId}/{playlist:.+\.m3u8}", self.serve_media_playlist)
        # self.app.router.add_get("/{gamePK}/{mediaId}", self.serve_stream_landing2)

        logger.info(f"Starting web server at http://{self.host}:{self.port}")
        web.run_app(self.app, host=self.host, port=self.port)