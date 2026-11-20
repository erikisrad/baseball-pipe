import logging
import re
from abc import ABC, abstractmethod

import aiohttp

from baseball_pipe.misc import header_handler as e
from baseball_pipe.misc import utilities as u
from baseball_pipe.mlbtv import media_playlist

logger = logging.getLogger(__name__)


class BaseStream(ABC):
    session: aiohttp.ClientSession
    proxy: str

    def reset(self):
        self._live_status = None
        self._master_playlist = None
        self._variants = None
        self._upstream_base_url = None
        self._master_playlist_url = None
        self._master_playlist = None

    def get_live_status(self):
        return self._live_status

    def set_live_status(self, playlist_type):
        playlist_type = playlist_type.lower()
        if playlist_type not in ["vod", "live", "event"]:
            raise ValueError(f"invalid playlist type: {playlist_type}")
        self._live_status = playlist_type

    async def get_variant(self, name):
        if not self._variants:
            await self._gen_variants()

        return self._variants.get(name, None)

    async def get_upstream_base_url(self):
        if not self._upstream_base_url:
            await self._gen_upstream_base_url()
        return self._upstream_base_url

    async def get_segment(self, path):
        if not self._upstream_base_url:
            await self._gen_upstream_base_url()
        return await self._gen_segment(path)

    async def get_master_playlist_url(self):
        if not self._master_playlist_url:
            await self._gen_master_playlist_url()
        return self._master_playlist_url

    async def get_master_playlist(self):
        if not self._master_playlist_url:
            await self.inititialize()
        await self._gen_master_playlist()
        return self._master_playlist

    async def _gen_upstream_base_url(self):
        url = await self.get_master_playlist_url()
        self._upstream_base_url = url.rsplit('/', 1)[0] + '/'
        

    @abstractmethod
    async def inititialize(self):
        ...

    @abstractmethod
    async def get_start(self):
        ...

    @abstractmethod
    async def _gen_master_playlist(self):
        ...

    @abstractmethod
    async def _gen_master_playlist_url(self):
        ...

    @abstractmethod
    async def is_expired(self) -> bool:
        ...

    async def _gen_variants(self):

        if not self._master_playlist:
            await self._gen_master_playlist()

        video_templates = {}
        self._variants = {}

        try:
            lines = iter(self._master_playlist.splitlines())
            for line in lines:

                if line.startswith("#EXT-X-STREAM-INF:") and media_playlist.FRAME_RATE.upper() in line:
                    line = line.split(":", 1)[1]
                    pairs = re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]+)', line)
                    media_dict = {k.lower(): v.strip('"') for k, v in pairs}

                    next_line = next(lines)
                    name = next_line.rsplit('/', 1)[-1]
                    self._variants[name] = media_playlist.Playlist(self, name, media_dict, video_templates)

                elif (line.startswith("#EXT-X-MEDIA:") and "URI=" in line) or (line.startswith("#EXT-X-STREAM-INF:") and "mp4" in line):
                    line = line.split(":", 1)[1]
                    pairs = re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]+)', line)
                    media_dict = {k.lower(): v.strip('"') for k, v in pairs}

                    if "uri" in media_dict:
                        name = media_dict.pop("uri")
                    else:
                        next_line = next(lines)
                        name = next_line.rsplit('/', 1)[-1]

                    self._variants[name] = media_playlist.Playlist(self, name, media_dict, {})

        except Exception as err:
            logger.error(f"failed parsing variants for {self._master_playlist_url}: {err}")

    async def _gen_segment(self, path):

        target = self._upstream_base_url + path

        headers = {
            **e.MEDIA_HEADER,
            "Accept": "*/*",
            "Accept-Encoding": "identity;q=1, *;q=0",
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
        }

        async def _fetch():
            async with self.session.get(target, headers=headers, proxy=self.proxy, ssl=False) as res:
                logger.debug(f"got response headers for {target}: status={res.status}")
                if res.status != 200:
                    raise Exception(f"Failed segment request: {res.status} {res.reason}")
                data = await res.read()
                logger.debug(f"finished reading {len(data)} bytes for {target}")
                return data

        logger.debug(f"sending segment request to {target}")
        return await u.with_timeout(_fetch(), 45, f"segment request to {target}")
