import logging
import os
from aiohttp import web
from zoneinfo import available_timezones
import hmac, hashlib, secrets

logger = logging.getLogger(__name__)

SECRET = os.environ["secret"]
PASSWORD = os.environ["auth"]
LOGIN_HTML = os.path.join(os.path.dirname(__file__), "html", "login.html")

def make_signed_cookie(value: str) -> str:
    sig = hmac.new(SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}:{sig}"

def verify_signed_cookie(raw: str) -> bool:
    try:
        value, sig = raw.split(":")
    except ValueError:
        return False

    expected = hmac.new(SECRET.encode(),value.encode(), hashlib.sha256).hexdigest()

    return hmac.compare_digest(sig, expected)

async def login(request):

    if request.method == "GET":
        return web.FileResponse(LOGIN_HTML)
    
    client_ip = request.headers.get("X-Real-IP") or request.remote
    data = await request.post()
    attempt = data.get("password", "")

    if attempt == PASSWORD:
        session_id = secrets.token_hex(16)
        cookie_value = make_signed_cookie(session_id)

        tz = data.get("timezone", "UTC")
        if tz not in available_timezones():
            tz = "UTC"

        response = web.HTTPFound("/today")
        response.set_cookie(
            "auth",
            cookie_value,
            httponly=True,
            secure=True,
            samesite="Strict",
            max_age=60*60*24*30
        )

        response.set_cookie(
            "tz",
            tz,
            httponly=False,
            secure=True,
            samesite="Strict",
            max_age=60*60*24*30
        )
        
        logger.info(f"User logged in successfully from {client_ip}")
        return response

    # Wrong password
    logger.warning(f"Login failed for {client_ip}")
    return web.Response(
        text="""
            <p style='color:red;text-align:center;'>Incorrect password</p>
            <a href="/login">Try again</a>
        """,
        content_type="text/html"
    )
