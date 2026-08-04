import os
from aiohttp import web
import logging as logger

import aiohttp
import aiohttp_jinja2
import jinja2
from aiohttp_remotes import setup as setup_remotes, XForwardedRelaxed
import baseball_pipe.webpage_gen.login_page
import baseball_pipe.webpage_gen.date_page
import baseball_pipe.webpage_gen.game_page
import baseball_pipe.server.router
import baseball_pipe.webpage_gen.broadcast_page2
import baseball_pipe.mlbtv.account

AT = " @ "
SPC = "&nbsp;"

PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
HTML_DIR = os.path.join(PACKAGE_ROOT, "html")

@web.middleware
async def auth_middleware(request, handler):
    path = request.path

    if (request.method == "OPTIONS"
        or path == "/login"
        or path.startswith("/static")
        or path.endswith((".m3u8", ".ts", ".aac", ".key", ".vtt"))):

        return await handler(request)

    raw = request.cookies.get("auth")
    if not raw:
        logger.info(f"sending redirect to login for {request.remote}")
        raise web.HTTPFound("/login")

    if not baseball_pipe.webpage_gen.login_page.verify_signed_cookie(raw):
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
        self.app = web.Application()
        self.app.router.add_static("/static", "baseball_pipe/static")
        aiohttp_jinja2.setup(self.app, loader=jinja2.FileSystemLoader(HTML_DIR))

    async def on_startup(self, app):
        await setup_remotes(app, XForwardedRelaxed())
        app.middlewares.append(auth_middleware)

        self.master_session = aiohttp.ClientSession()

        self.mlbtv_account = baseball_pipe.mlbtv.account.Account(self.master_session, proxy=self.proxy_url)
        await self.mlbtv_account._gen_token()

        app["master_session"] = self.master_session
        app["mlbtv_account"] = self.mlbtv_account
        app["proxy_url"] = self.proxy_url

    async def on_cleanup(self, app):
        if self.master_session:
            await self.master_session.close()

    def start(self):
        logger.getLogger("aiohttp.access").setLevel(logger.WARNING)

        self.app.on_startup.append(self.on_startup)
        self.app.on_cleanup.append(self.on_cleanup)

        # CORS preflight, catch-all
        self.app.router.add_route("OPTIONS", "/{tail:.*}", baseball_pipe.server.router.serve_options)

        self.app.router.add_get("/favicon.ico", baseball_pipe.server.router.serve_favicon)

        # Named keyword routes
        self.app.router.add_get("/today", baseball_pipe.server.router.serve_today)
        self.app.router.add_get("/yesterday", baseball_pipe.server.router.serve_yesterday)
        self.app.router.add_get("/tomorrow", baseball_pipe.server.router.serve_tomorrow)
        self.app.router.add_route("*", "/login", baseball_pipe.webpage_gen.login_page.login)

        # Regex-constrained path params (aiohttp supports this natively)
        self.app.router.add_get(r"/{date:\d{8}}", baseball_pipe.webpage_gen.date_page.serve_date)
        self.app.router.add_get(r"/{gamePK:\d{1,6}}", baseball_pipe.webpage_gen.game_page.serve_game)
        self.app.router.add_get("/{gamePK}/{mediaId}", baseball_pipe.webpage_gen.broadcast_page2.serve_broadcast)
        self.app.router.add_get(r"/{gamePK}/{mediaId}/{path:.+}", baseball_pipe.server.router.route_media)

        logger.info(f"Starting web server at http://{self.host}:{self.port}")
        web.run_app(self.app, host=self.host, port=self.port)