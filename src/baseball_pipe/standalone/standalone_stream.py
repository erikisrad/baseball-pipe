import logging

import aiohttp

from baseball_pipe.misc import header_handler as e
from baseball_pipe.misc import utilities as u
from baseball_pipe.standalone.base_stream import BaseStream
from baseball_pipe.mlb.mlb_stats import get_supplemental_URL

logger = logging.getLogger(__name__)


class StandaloneStream(BaseStream):
    def __init__(self,
                 gamePK: str,
                 media_id: str,
                 session: aiohttp.ClientSession,
                 proxy: str = None,):

        self.gamePK = gamePK
        self.media_id = media_id
        self.session = session
        self.proxy = proxy

        self.reset()

    async def inititialize(self):
        if not self._master_playlist_url:
            await self._gen_master_playlist_url()

    def reset(self):
        super().reset()
        self._master_playlist_url = ""
        self._upstream_base_url = ""

    def __str__(self):
        return self.url

    def __repr__(self):
        return self.url

    async def get_start(self):
        return 0

    async def is_expired(self):
        return False

    async def _gen_master_playlist_url(self):
        self._master_playlist_url = await get_supplemental_URL(self.gamePK, self.session, target=self.media_id)

    async def _gen_master_playlist(self):

        if not self._master_playlist_url:
            await self._gen_master_playlist_url()

        headers = {
            **e.MEDIA_HEADER,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Encoding": e.ACCEPT_ENCODING,
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

        async def _fetch():
            async with self.session.get(self._master_playlist_url, headers=headers, proxy=self.proxy, ssl=False) as res:
                logger.debug(f"got response headers for {self._master_playlist_url}: status={res.status}")
                if res.status != 200:
                    raise Exception(f"Failed master playlist request: {res.status} {res.reason}")
                text = await res.text()
                logger.debug(f"finished reading {len(text)} chars for {self._master_playlist_url}")
                return text

        logger.info(f"sending master playlist request to {self._master_playlist_url}")
        res_text = await u.with_timeout(_fetch(), 20, f"master playlist request to {self._master_playlist_url}")

        self._master_playlist = res_text
        try:
            assert "#EXTM3U" in self._master_playlist
        except Exception as err:
            logger.error(f"Failed to parse master playlist for {self} stream\n{res_text}\n{err}")

        if not self._variants:
            await self._gen_variants()
