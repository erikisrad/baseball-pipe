import os
from aiohttp import web
import curl_cffi
from string import Template
from pathlib import Path
from urllib.parse import urljoin
import logging as logger

import aiohttp
import baseball_pipe.old.mlb_stats
import baseball_pipe.old.utilities as u
import baseball_pipe.old.mlbtv_account
import baseball_pipe.old.mlbtv_stream
import baseball_pipe.old.login
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
        self.app = web.Application(middlewares=[auth_middleware])
        self.app.router.add_static("/static", "baseball_pipe/static")
        self.app.router.add_route("*", "/login", self.decide_serve)


        self.account = None
        self.token = None
        self.streams: dict[str, Stream] = {}
        self.proxy_url = f"http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"
        self.master_session = None
        self.chrome120_session = None

    async def on_startup(self, app):
        # self.master_session = aiohttp.ClientSession(
        #     cookie_jar=aiohttp.CookieJar(unsafe=True),
        #     connector=aiohttp.TCPConnector(
        #         family=socket.AF_INET,   # force IPv4 (Bright Data + Okta friendly)
        #         ssl=False                # matches your requests
        #     )
        # )

        self.master_session = aiohttp.ClientSession()
        self.chrome120_session = curl_cffi.Session(impersonate="chrome120")

    async def on_cleanup(self, app):
        if self.master_session:
            await self.master_session.close()