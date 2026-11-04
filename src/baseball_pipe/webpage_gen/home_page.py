import logging
import os
from aiohttp import web
import baseball_pipe.misc.utilities as u

logger = logging.getLogger(__name__)
PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
HOME_HTML = os.path.join(PACKAGE_ROOT, "html", "home.html")

async def serve_home(request):
    logger.info(f"returning home page to {u.get_ip_from_request(request)}")
    with open(HOME_HTML) as f:
        html = f.read()
    return web.Response(text=html, content_type="text/html", headers={
        "Access-Control-Allow-Origin": "*"
    })
