import logging
import os
from aiohttp import web
import baseball_pipe.misc.utilities as u

logger = logging.getLogger(__name__)
PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
README_HTML = os.path.join(PACKAGE_ROOT, "html", "readme.html")

async def serve_readme(request):
    logger.info(f"returning readme page to {u.get_ip_from_request(request)}")
    with open(README_HTML) as f:
        html = f.read()
    return web.Response(text=html, content_type="text/html", headers={
        "Access-Control-Allow-Origin": "*"
    })
