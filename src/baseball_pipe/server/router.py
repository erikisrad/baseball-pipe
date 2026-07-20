from aiohttp import web
import baseball_pipe.misc.utilities as u


async def serve_today(request: web.Request):
    return web.HTTPFound(location=f"/{u.machine_print_date(u.get_date())}")

async def serve_yesterday(request: web.Request):
    return web.HTTPFound(location=f"/{u.machine_print_date(u.get_date(days_ago=1))}")

async def serve_tomorrow(request: web.Request):
    return web.HTTPFound(location=f"/{u.machine_print_date(u.get_date(days_ago=-1))}")