import asyncio
import aiohttp
import os
from aiohttp import web
from urllib.parse import urljoin

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(SCRIPT_DIR, "index.html")
LOCAL_PLAYLIST = os.path.join(os.path.dirname(__file__), "local.m3u8")

import logging as logger

def cors_headers(content_type):
    return {
        "Content-Type": content_type,
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*"
    }

def serve_homepage(request):
    logger.info("serving homepage")
    html = """
    <!doctype html>
    <html>
        <head>
            <meta charset="utf-8" />
            <title>Baseball Pipe</title>
        </head>
        <body>
            <h1>Baseball Pipe</h1>
            <p>This server proxies HLS playlists for local casting.</p>
        </body>
    </html>
    """
    return web.Response(text=html, content_type="text/html", headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*"
    })

async def serve_playlist(request):
    logger.info("Incoming request:", request.method, request.path)
    return web.FileResponse(LOCAL_PLAYLIST, headers=cors_headers("application/vnd.apple.mpegurl"))

async def serve_segment(self, request):
    rel_path = request.match_info['filename']
    upstream_url = urljoin(request.path, rel_path)
    logger.info("proxying segment:", rel_path)

    async with aiohttp.ClientSession() as session:
            async with session.get(upstream_url) as resp:
                    if resp.status != 200:
                            return web.Response(status=resp.status, text="Upstream error")
                    data = await resp.read()
                    return web.Response(body=data, headers=cors_headers("video/mp2t"))

class WebServer:
    def __init__(self, host="0.0.0.0", port=80):

        self.host = host
        self.port = port
        self.app = web.Application()

    def start(self):
        self.app.router.add_get("/", serve_homepage)
        self.app.router.add_get("/segments/{filename:.*}", serve_segment)
        self.app.router.add_get("/local.m3u8", serve_playlist)

        logger.info(f"Starting web server at http://{self.host}:{self.port}")
        web.run_app(self.app, host=self.host, port=self.port)