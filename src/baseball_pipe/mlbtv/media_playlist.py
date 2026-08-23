import logging
from typing import TYPE_CHECKING

from baseball_pipe.misc import header_handler as e
from baseball_pipe.playlist import generate_filler_segments as gfs

if TYPE_CHECKING:
    from baseball_pipe.mlbtv.stream import Stream

#VIDEO KINDA KEYS
BANDWIDTH = "bandwidth"
AVERAGE_BANDWIDTH = "average-bandwidth"
CODECS = "codecs"
RESOLUTION = "resolution"
FRAME_RATE = "frame-rate"
AUDIO = "audio"
SUBTITLES = "subtitles"

# SUBTITLE / AUDIO KINDA KEYS
TYPE = "type"
GROUP_ID = "group-id"
LANGUAGE = "language"
NAME = "name"
AUTOSELECT = "autoselect"
DEFAULT = "default"
CHANNELS = "channels"
FORCED = "forced"

#OTHER
VIDEO = "VIDEO"

logger = logging.getLogger(__name__)

class Playlist():

    def __init__(self, stream: "Stream", name: str, media_dict:dict):
        self.parent_stream = stream
        self.name = name
        self.mdict = media_dict
        self._media = None

        self.type = None
        self.resolution = None
        self.frame_rate = None
        self.filler_duration = None

        if RESOLUTION in self.mdict and FRAME_RATE in self.mdict:
            try:
                self.type = VIDEO
                self.resolution = tuple(map(int, self.mdict[RESOLUTION].split("x")))
                self.frame_rate = gfs.ntsc_fraction_str(float(self.mdict[FRAME_RATE]))
                self.filler_duration = gfs.ensure_rendition(self.resolution, self.frame_rate)
            except Exception as err:
                logger.error(f"failed generating filler segments for {self.parent_stream._master_playlist_url} / {name}: {err}")
                raise

    def __str__(self):
        return f"{self.parent_stream}/{self.name}"

    def __repr__(self):
        return f"{self.parent_stream}/{self.name}"

    def get_name(self):
        return self.name

    def get_parent_stream(self):
        return self.parent_stream

    async def get_media(self):
        await self._gen_media_playlist()
        return self._media

    async def _gen_media_playlist(self):
    
        if not self.parent_stream._upstream_base_url:
            await self.parent_stream._gen_master_playlist_url()

        target = self.parent_stream._upstream_base_url + self.name

        headers = {
            **e.MEDIA_HEADER,
            "Accept": "*/*",
            "Accept-Encoding": "identity;q=1, *;q=0",
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
        }

        logger.info(f"sending media playlist request to {target}")
        async with self.parent_stream.session.get(target, headers=headers, proxy=self.parent_stream.proxy, ssl=False) as res:
            if res.status != 200:
                raise Exception(f"Failed media playlist request: {res.status} {res.reason}")
            res_text = await res.text()

        try:
            assert "#EXTM3U" in res_text
        except Exception as err:
            logger.error(f"Failed to parse media playlist {self.name} for {self.parent_stream} stream\nresult: {res_text}\n{err}")

        self._media = res_text
