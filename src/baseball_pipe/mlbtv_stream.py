import logging
import re
from .mlbtv_token import Token

from . import utilities as u
import aiohttp
import m3u8

GRAPHQL_URL = "https://media-gateway.mlb.com/graphql"
URI_PATTERN = re.compile(r'URI="([^"]+)"')
PLAYLIST_TYPE_PATTERN = re.compile("#EXT-X-PLAYLIST-TYPE:([A-Z]+)")

logger = logging.getLogger(__name__)

#get games
#GAME_PK = "777218"
#MEDIA_ID = "408db4cb-41de-4805-80ea-62700421f33b"


def uri_search_and_replace(line, full_url):
    logger.debug(f"rewriting URL for line {line}")
    old = URI_PATTERN.search(line)
    assert old, f"failed to find URI in line: {line}"
    new = full_url + old.group(1)
    new_line = URI_PATTERN.sub(f'URI="{new}"', line)
    return new_line

def rewrite_master_playlist_urls(playlist_content, full_url):
    lines = playlist_content.split('\n')

    streams:list[str,str] = []
    prefixes:list[str] = []
    audios:list[str] = []
    subtitles:list[str] = []

    for i, line in enumerate(lines):

        if not line:
            continue

        elif line.startswith("#EXT-X-STREAM-INF"):
            uri = lines[i+1]
            logger.debug(f"steam found: {line}, rewriting URL: {uri}")
            uri = full_url + uri
            streams.append((line, uri))

        elif line.endswith(".m3u8") and not line.startswith("#"):
            continue
           
        elif line.startswith("#EXT-X-MEDIA:TYPE=AUDIO"):
            logger.debug(f"audio found: {line}")
            try:
                new_line = uri_search_and_replace(line, full_url)
            except Exception:
                logger.warning
                new_line = line
            audios.append(new_line)

        elif line.startswith("#EXT-X-MEDIA:TYPE=SUBTITLES"):
            logger.debug(f"subtitle found: {line}")
            new_line = uri_search_and_replace(line, full_url)
            subtitles.append(new_line)

        elif line.startswith("#"):
            logger.debug(f"found prefix line: {line}")
            prefixes.append(line)

        else:
            raise Exception(f"unexpected line in master playlist: {line}")

    rewritten = []  
    rewritten.append('\n'.join(prefixes))
    rewritten.append('\n'.join(subtitles))
    rewritten.append('\n'.join(audios))
    for line, uri in streams:
        rewritten.append(line)
        rewritten.append(uri)

    return '\n'.join(rewritten)
    
def rewrite_playlist_urls_backwards(playlist_content, full_url):
    lines = playlist_content.split('\n')[::-1]
    rewritten = []
    cued_out = False
    ts_count = 0

    for line in lines:

        if not line:
            continue

        if line and not line.startswith('#'):
            if cued_out:
                logger.debug(f"skipping ad: {line}")
            else:
                logger.debug(f"rewriting URL for line {line}")
                rewritten.append(full_url + line)
                ts_count += 1

        elif "URI=" in line:
            if cued_out:
                logger.debug(f"skipping ad: {line}")
            else:
                rewritten.append(uri_search_and_replace(line, full_url))

        elif line.startswith("#EXT-OATCLS-SCTE35:"):
            logger.debug("skipping splice")

        elif line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            res = re.search(PLAYLIST_TYPE_PATTERN, line)
            playlist_type = res.group(1)
            logger.debug(f"playlist type: {playlist_type}")

            if playlist_type == "EVENT":
                rewritten.append("#EXT-X-PLAYLIST-TYPE:LIVE")
            else:
                rewritten.append(line)

        elif line.startswith("#EXT-X-CUE-OUT-CONT:"):
            logger.debug("skipping cue out continuation")

        elif line.startswith("#EXT-X-CUE-OUT:"):
            if not cued_out:
                logger.warning("received unexpected #EXT-X-CUE-OUT")

            cued_out = False
            logger.debug("cue ended")

        elif line.startswith("#EXT-X-CUE-IN"):
            if cued_out:
                logger.warning("received unexpected #EXT-X-CUE-IN")

            if ts_count > 100:
                cued_out = True
                rewritten.append("#EXT-X-DISCONTINUITY") # throw one of these bad boys in there since we fucked with the timeline so much
                logger.debug("cue start")
            else:
                logger.debug("too early to cue out")

        elif line.startswith("#EXTINF:"):
            if cued_out:
                logger.debug("skipping ad cue inf")
            else:
                logger.debug("writting cue inf")
                rewritten.append(line)

        elif (line.startswith("#EXTM3U")
              or line.startswith("#EXT-X-VERSION:")
              or line.startswith("#EXT-X-TARGETDURATION:")
              or line.startswith("#EXT-X-MEDIA-SEQUENCE:")
              or line.startswith("#EXT-X-PROGRAM-DATE-TIME")
              or line.startswith("#EXT-X-ENDLIST")) and not cued_out:
            
            logger.debug(f"keeping generic line {line}")
            rewritten.append(line)

        # elif "#EXT-X-PROGRAM-DATE-TIME:" in line:
        #     logger.debug(f"cutting #EXT-X-PROGRAM-DATE-TIME: line {line}")

        elif cued_out:
            logger.debug(f"skipping misc line during ad: {line}")

        else:
            logger.warning(f"keeping unknown line: {line}")
            rewritten.append(line)

    return '\n'.join(rewritten[::-1])

def rewrite_media_playlist(playlist_content, full_url):
    lines = playlist_content.split('\n')
    rewritten = []
    max_lines_read = 10

    for i, line in enumerate(lines):

        if i == max_lines_read:
            raise Exception(f"media playlist doesnt have playlist type in first {max_lines_read} lines")
        
        if line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            if "VOD" in line:
                return rewrite_vod_playlist(lines, full_url)
            else:
                return rewrite_live_playlist(lines, full_url)
            
def rewrite_live_playlist(lines, full_url):
    return None

def rewrite_vod_playlist(lines, full_url):
    rewritten = []
    cued_out = False

    for line in lines:

        if not line:
            continue

        if not line.startswith('#'):
            if cued_out:
                logger.debug(f"skipping ad: {line}")
            else:
                logger.debug(f"rewriting URL for line {line}")
                rewritten.append(full_url + line)

        elif "URI=" in line:
            if cued_out:
                logger.debug(f"skipping ad: {line}")
            else:
                rewritten.append(uri_search_and_replace(line, full_url))

        elif line.startswith("#EXT-OATCLS-SCTE35:"):
            logger.debug("skipping splice")

        elif line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            res = re.search(PLAYLIST_TYPE_PATTERN, line)
            playlist_type = res.group(1)
            logger.debug(f"playlist type: {playlist_type}")

            if playlist_type == "EVENT":
                rewritten.append("#EXT-X-PLAYLIST-TYPE:LIVE")
            else:
                rewritten.append(line)

        elif line.startswith("#EXT-X-CUE-OUT-CONT:"):
            logger.debug("skipping cue out continuation")

        elif line.startswith("#EXT-X-CUE-OUT:"):
            if cued_out:
                logger.warning("received unexpected #EXT-X-CUE-OUT")

            cued_out = True
            logger.debug("cued out")

        elif line.startswith("#EXT-X-CUE-IN"):
            if not cued_out:
                logger.warning("received unexpected #EXT-X-CUE-IN")

            cued_out = False
            rewritten.append("#EXT-X-DISCONTINUITY") # throw one of these bad boys in there since we fucked with the timeline so much
            logger.debug("cued in")

        elif line.startswith("#EXTINF:"):
            if cued_out:
                logger.debug("skipping ad cue inf")

            else:
                logger.debug("writting cue inf")
                rewritten.append(line)

        elif (line.startswith("#EXTM3U")
              or line.startswith("#EXT-X-VERSION:")
              or line.startswith("#EXT-X-TARGETDURATION:")
              or line.startswith("#EXT-X-MEDIA-SEQUENCE:")
              or line.startswith("#EXT-X-PROGRAM-DATE-TIME")
              or line.startswith("#EXT-X-ENDLIST")) and not cued_out:
            
            logger.debug(f"keeping generic line {line}")
            rewritten.append(line)

        # elif "#EXT-X-PROGRAM-DATE-TIME:" in line:
        #     logger.debug(f"cutting #EXT-X-PROGRAM-DATE-TIME: line {line}")

        elif cued_out:
            logger.debug(f"skipping misc line during ad: {line}")

        else:
            logger.warning(f"keeping unknown line: {line}")
            rewritten.append(line)

    return '\n'.join(rewritten)


class Stream():

    def __init__(self, token:Token, game_pk:str, media_id:str, session:aiohttp.ClientSession, proxy:str):
        self.token = token
        self.game_pk = game_pk
        self.media_id = media_id
        self.url = "https://www.mlb.com/tv/g%s/v%s" % (self.game_pk, self.media_id)
        self.session = session

        self.proxy = proxy
        #self.proxy = None

        self.reset()

    def reset(self):

        # via _gen_session()
        self._device_id = ""
        self._session_id = None

        # via _gen_master_playlist_url()
        self._master_playlist_url = None
        self._upstream_base_url = None

        # via _gen_master_playlist()
        self._etag = ""
        self._master_playlist = None
        self.mlbtv_variant_playlists = None
        self.variant_playlists = None

        # self._playlist_prefix = None
        # self._playback_session_id = None
        # self._media_playlists = None
        # self._milestones = None
        # self._commercial_breaks = None

    async def get_master_playlist(self, base_url):
        await self._gen_master_playlist(base_url)
        return self._master_playlist
    
    async def get_master_playlist_url(self):
        await self._gen_master_playlist_url()
        return self._master_playlist_url
    
    async def get_media_playlist(self, base_url, playlist):
        return await self._gen_media_playlist(base_url, playlist)

    async def get_media_file(self, base_url, suffix):
        return await self._gen_media_file(base_url, suffix)

    async def get_key_file(self, base_url, suffix):
        return await self._gen_key_file(base_url, suffix)
    
    async def get_vtt_file(self, base_url, suffix):
        return await self._gen_vtt_file(base_url, suffix)
    
    async def get_aac_file(self, base_url, suffix):
        return await self._gen_aac_file(base_url, suffix)

    async def _gen_session(self):

        payload = {
            "operationName": "initSession",
            "query": '''mutation initSession($device: InitSessionInput!, $clientType: ClientType!) {
                initSession(device: $device, clientType: $clientType) {
                    deviceId
                    sessionId
                    entitlements {
                        code
                    }
                    location {
                        countryCode
                        regionName
                        zipCode
                        latitude
                        longitude
                    }
                    clientExperience
                    features
                }
            }''',
            "variables": {
                "clientType": "WEB",
                "device": {
                    "appVersion": "8.1.0",
                    "deviceFamily": "desktop",
                    "knownDeviceId": self._device_id,
                    "languagePreference": "ENGLISH",
                    "manufacturer": "Google Inc.",
                    "model": "",
                    "os": "windows",
                    "osVersion": "10"
                }
            }
        }

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9",
            "Authorization": f"{self.token.token_type} {self.token.access_token}",
            "Content-Type": "application/json",
            "Origin": "https://www.mlb.com",
            "Priority": "u=1, i",
            "Referer": "https://www.mlb.com/tv/g%s" % self.game_pk,
            "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            "Sec-Ch-Ua-Mobile": "?0",
            "sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {GRAPHQL_URL}")
        async with self.session.post(GRAPHQL_URL, headers=headers, json=payload, proxy=self.proxy, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to gen session: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        self._device_id = res_json["data"]["initSession"]["deviceId"]
        self._session_id = res_json["data"]["initSession"]["sessionId"]

    async def _gen_master_playlist_url(self):

        if not self._session_id:
            await self._gen_session()

        payload = {
            "operationName":"initPlaybackSession",
            "query":'''mutation initPlaybackSession(
                $adCapabilities: [AdExperienceType]
                $mediaId: String!
                $deviceId: String!
                $sessionId: String!
                $quality: PlaybackQuality
                $playbackCapabilities: PlaybackCapabilities
            ) {
                initPlaybackSession(
                    adCapabilities: $adCapabilities
                    mediaId: $mediaId
                    deviceId: $deviceId
                    sessionId: $sessionId
                    quality: $quality
                    playbackCapabilities: $playbackCapabilities
                ) {
                    playbackSessionId
                    playback {
                        url
                        token
                        expiration
                        cdn
                    }
                    adScenarios {
                        adParamsObj
                        adScenarioType
                        adExperienceType
                    }
                    adExperience {
                        adExperienceTypes
                        adEngineIdentifiers {
                            name
                            value
                        }
                        adsEnabled
                    }
                    heartbeatInfo {
                        url
                        interval
                    }
                    trackingObj
                }
            }''',
            "variables":{
                "adCapabilities":["GOOGLE_STANDALONE_AD_PODS"],
                "deviceId":"%s" % self._device_id,
                "mediaId":"%s" % self.media_id,
                "playbackCapabilities":{},
                "quality":"PLACEHOLDER",
                "sessionId":"%s" % self._session_id}
            }

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9",
            "Authorization": f"{self.token.token_type} {self.token.access_token}",
            "Content-Type": "application/json",
            "Origin": "https://www.mlb.com",
            "Priority": "u=1, i",
            "Referer": self.url,
            "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            "Sec-Ch-Ua-Mobile": "?0",
            "sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {GRAPHQL_URL}")
        async with self.session.post(GRAPHQL_URL, headers=headers, proxy=self.proxy, json=payload, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to gen master playlist url: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        if "errors" in res_json:
            raise Exception(res_json['errors'][0]['message'])
        
        else:
            self._master_playlist_url = res_json["data"]["initPlaybackSession"]["playback"]["url"]
            self._upstream_base_url = self._master_playlist_url.rsplit('/', 1)[0] + '/'


    async def _gen_master_playlist(self, base_url):

        if not self._master_playlist_url:
            await self._gen_master_playlist_url()

        full_url = f"{base_url}{self.game_pk}/{self.media_id}/"

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "max-age=0",
            #"If-Modified-Since": {last_get},
            #"If-None-Match": {self._etag},
            "Priority": "u=0, i",
            #"Range": "bytes=0-638",
            #"Referer": "fst.mlb.com/1766168221_MDB1OGRxaDZlZXBlRXlEUmQzNTY_YWxsb3dlZE1lZGlhVHlwZXM9VklERU8sQVVESU8_14681a4b82809b3fc8c860b2f8a9677e609b911cf43d15712fa0ce8a57776b6e/20250808/776825-HD.m3u8"
            "Sec-Ch-Ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {self._master_playlist_url}")
        async with self.session.get(self._master_playlist_url, headers=headers, proxy=self.proxy, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to gen master playlist: {res.status} {res.reason}")
            res_text = await res.text()
            logger.info(f"response received, status {res.status}")
        try:
            self._etag = res.headers['ETag']
        except Exception:
            logger.warning("Failed to get ETag from response headers")
            self._etag = ''

        self._master_playlist = rewrite_master_playlist_urls(res_text, full_url)

        variants = m3u8.loads(res_text).playlists
        self.mlbtv_variant_playlists = sorted(
            variants,
            key=lambda v: v.stream_info.bandwidth or 0,
            reverse=True
        )
        
        variants = m3u8.loads(self._master_playlist).playlists
        self.variant_playlists = sorted(
            variants,
            key=lambda v: v.stream_info.bandwidth or 0,
            reverse=True
        )

    async def _gen_media_playlist(self, base_url, playlist):

        if not self._upstream_base_url:
            await self._gen_master_playlist_url()

        full_url = f"{base_url}{self.game_pk}/{self.media_id}/"
        target = self._upstream_base_url + playlist

        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "identity;q=1, *;q=0",
            "Accept-Language": "en-US,en;q=0.9",
            #"If-Modified-Since": {last_get},
            #"If-None-Match": {self._etag},
            "Priority": "i",
            #"Range": "bytes=0-638",
            #"Referer": "fst.mlb.com/1766168221_MDB1OGRxaDZlZXBlRXlEUmQzNTY_YWxsb3dlZE1lZGlhVHlwZXM9VklERU8sQVVESU8_14681a4b82809b3fc8c860b2f8a9677e609b911cf43d15712fa0ce8a57776b6e/20250808/776825-HD.m3u8"
            "Sec-Ch-Ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {target}")
        async with self.session.get(target, headers=headers, proxy=self.proxy, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to gen {playlist} playlist: {res.status} {res.reason}")
            res_text = await res.text()
            logger.info(f"response received, status {res.status}")
        try:
            self._etag = res.headers['ETag']
        except Exception:
            logger.warning("Failed to get ETag from response headers")
            self._etag = ''

        return rewrite_media_playlist(res_text, full_url)
    
    async def _gen_media_file(self, base_url, suffix):
        
        if not self._upstream_base_url:
            await self._gen_master_playlist_url()

        full_url = f"{base_url}{self.game_pk}/{self.media_id}/"
        target = self._upstream_base_url + suffix

        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "identity;q=1, *;q=0",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            #"If-Modified-Since": {last_get},
            #"If-None-Match": {self._etag},
            "Pragma": "no-cache",
            "Priority": "i",
            #"Range": "bytes=0-638",
            #"Referer": "fst.mlb.com/1766168221_MDB1OGRxaDZlZXBlRXlEUmQzNTY_YWxsb3dlZE1lZGlhVHlwZXM9VklERU8sQVVESU8_14681a4b82809b3fc8c860b2f8a9677e609b911cf43d15712fa0ce8a57776b6e/20250808/776825-HD.m3u8"
            "Sec-Ch-Ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {target}")
        async with self.session.get(target, headers=headers, proxy=self.proxy, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to gen {target} file: {res.status} {res.reason}")
            res_data = await res.read()
            logger.info(f"response received, status {res.status}")
        try:
            self._etag = res.headers['ETag']
        except Exception:
            logger.warning("Failed to get ETag from response headers")
            self._etag = ''
        
        return res_data
    
    async def _gen_key_file(self, base_url, suffix):
        
        if not self._upstream_base_url:
            await self._gen_master_playlist_url()

        full_url = f"{base_url}{self.game_pk}/{self.media_id}/"
        target = self._upstream_base_url + suffix

        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "identity;q=1, *;q=0",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            #"If-Modified-Since": {last_get},
            #"If-None-Match": {self._etag},
            "Pragma": "no-cache",
            "Priority": "i",
            #"Range": "bytes=0-",
            #"Referer": "fst.mlb.com/1766168221_MDB1OGRxaDZlZXBlRXlEUmQzNTY_YWxsb3dlZE1lZGlhVHlwZXM9VklERU8sQVVESU8_14681a4b82809b3fc8c860b2f8a9677e609b911cf43d15712fa0ce8a57776b6e/20250808/776825-HD.m3u8"
            "Sec-Ch-Ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {target}")
        async with self.session.get(target, headers=headers, proxy=self.proxy, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status not in [200]:
                raise Exception(f"Failed to gen {target} file: {res.status} {res.reason}")
            
            if res.content_length != 16:
                raise Exception(f"Unexpected key file size for {target}: {res.content_length} bytes")

            res_data = await res.read()
            
            logger.info(f"response received, status {res.status}")
        try:
            self._etag = res.headers['ETag']
        except Exception:
            logger.warning("Failed to get ETag from response headers")
            self._etag = ''
        
        return res_data
    
    async def _gen_vtt_file(self, base_url, suffix):
        
        if not self._upstream_base_url:
            await self._gen_master_playlist_url()

        full_url = f"{base_url}{self.game_pk}/{self.media_id}/"
        target = self._upstream_base_url + suffix

        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            #"If-Modified-Since": {last_get},
            #"If-None-Match": {self._etag},
            "Pragma": "no-cache",
            "Priority": "i",
            #"Referer": "fst.mlb.com/1766168221_MDB1OGRxaDZlZXBlRXlEUmQzNTY_YWxsb3dlZE1lZGlhVHlwZXM9VklERU8sQVVESU8_14681a4b82809b3fc8c860b2f8a9677e609b911cf43d15712fa0ce8a57776b6e/20250808/776825-HD.m3u8"
            # "Sec-Ch-Ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            # "Sec-Ch-Ua-Mobile": "?0",
            # "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {target}")
        async with self.session.get(target, headers=headers, proxy=self.proxy, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status not in [200]:
                raise Exception(f"Failed to gen {target} file: {res.status} {res.reason}")
            res_text = await res.text()
            logger.info(f"response received, status {res.status}")
        try:
            self._etag = res.headers['ETag']
        except Exception:
            logger.warning("Failed to get ETag from response headers")
            self._etag = ''
        
        return res_text
    
    async def _gen_aac_file(self, base_url, suffix):
        
        if not self._upstream_base_url:
            await self._gen_master_playlist_url()

        full_url = f"{base_url}{self.game_pk}/{self.media_id}/"
        target = self._upstream_base_url + suffix

        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            #"If-Modified-Since": {last_get},
            #"If-None-Match": {self._etag},
            "Pragma": "no-cache",
            "Priority": "u=1, i",
            #"Range": "bytes=0-638",
            #"Referer": "fst.mlb.com/1766168221_MDB1OGRxaDZlZXBlRXlEUmQzNTY_YWxsb3dlZE1lZGlhVHlwZXM9VklERU8sQVVESU8_14681a4b82809b3fc8c860b2f8a9677e609b911cf43d15712fa0ce8a57776b6e/20250808/776825-HD.m3u8"
            "Sec-Ch-Ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {target}")
        async with self.session.get(target, headers=headers,  proxy=self.proxy, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to gen {target} file: {res.status} {res.reason}")
            res_data = await res.read()
            logger.info(f"response received, status {res.status}")
        try:
            self._etag = res.headers['ETag']
        except Exception:
            logger.warning("Failed to get ETag from response headers")
            self._etag = ''
        
        return res_data