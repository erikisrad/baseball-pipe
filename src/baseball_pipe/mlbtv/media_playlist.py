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

#DERIVED
SPLIT_RES = "split_resolution"
NTSC_FPS = "ntsc_frame-rate"
FILLER_DURATION = "filler_duration"

#OTHER
VIDEO = "VIDEO"

logger = logging.getLogger(__name__)

class Playlist():

    def __init__(self, stream: "Stream", name: str, media_dict:dict):
        self.parent_stream = stream
        self.name = name
        self.mdict = media_dict
        self.media = None

        if RESOLUTION in media_dict and FRAME_RATE in media_dict:
            try:
                media_dict[TYPE] = VIDEO
                size = tuple(map(int, media_dict[RESOLUTION].split("x")))
                fps = gfs.ntsc_fraction_str(float(media_dict[FRAME_RATE]))
                media_dict[SPLIT_RES] = size
                media_dict[NTSC_FPS] = fps
                media_dict[FILLER_DURATION] = gfs.ensure_rendition(size, fps)
            except Exception as err:
                logger.error(f"failed generating filler segments for {self._master_playlist_url} / {name}: {err}")
                raise

    def __str__(self):
        return f"{self.parent_stream}/{self.name}"

    def __repr__(self):
        return f"{self.parent_stream}/{self.name}"

    async def get_media(self):
        await self._gen_media()
        return self._media

    async def _gen_media_playlist(self, playlist):
    
        if not self.parent_stream._upstream_base_url:
            await self.parent_stream._gen_master_playlist_url()

        target = self.parent_stream._upstream_base_url + playlist

        headers = {
            **e.MEDIA_HEADER,
            "Accept": "*/*",
            "Accept-Encoding": "identity;q=1, *;q=0",
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
        }

        logger.info(f"sending media playlist request to {target}")
        async with self.session.get(target, headers=headers, proxy=self.proxy, ssl=False) as res:
            if res.status != 200:
                raise Exception(f"Failed media playlist request: {res.status} {res.reason}")
            res_text = await res.text()

        try:
            assert "#EXTM3U" in res_text
        except Exception as err:
            logger.error(f"Failed to parse media playlist {playlist} for {self} stream\nresult: {res_text}\n{err}")

        self._variants[playlist]['media'] = res_text
