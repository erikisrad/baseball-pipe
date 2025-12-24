import logging
import re
from .mlbtv_token import Token

from . import utilities as u
import aiohttp
import m3u8

GRAPHQL_URL = "https://media-gateway.mlb.com/graphql"

logger = logging.getLogger(__name__)

#get games
#GAME_PK = "777218"
#MEDIA_ID = "408db4cb-41de-4805-80ea-62700421f33b"

def rewrite_playlist_urls(playlist_content, full_url):
    lines = playlist_content.split('\n')
    rewritten = []
    cued_out = False
    cue_expected_time = 0
    cue_elapsed_time = 0
    cue_measured_time = 0

    for line in lines:

        if line and not line.startswith('#'):
            if cued_out:
                logger.debug(f"skipping ad: {line}")
            else:
                logger.debug(f"rewriting URL for line {line}")
                rewritten.append(full_url + line)

        elif "#EXT-OATCLS-SCTE35:" in line:
            logger.debug("skipping splice")

        elif "#EXT-X-CUE-OUT-CONT:" in line:
            try:
                m = re.search(r"ElapsedTime=([0-9]*\.?[0-9]+)", line)
                cue_elapsed_time = float(m.group(1))
                logger.debug(f"cue elapsed time: {cue_elapsed_time}")
            except Exception as err:
                logger.warning(f"failed to parse #EXT-X-CUE-OUT-CONT: line {line}\n{err}")

        elif "#EXT-X-CUE-OUT:" in line:
            if cued_out:
                logger.warning("received unexpected #EXT-X-CUE-OUT while already cued out")

            cued_out = True
            try:
                m = re.search(r"#EXT-X-CUE-OUT:([0-9]*\.?[0-9]+)", line)
                cue_expected_time = float(m.group(1))
                logger.debug(f"now cued out for: {cue_expected_time} sec")
            except Exception as err:
                logger.warning(f"failed to parse #EXT-X-CUE-OUT: line {line}\n{err}")

        elif "#EXT-X-CUE-IN" in line:

            if not cued_out:
                logger.warning("received unexpected #EXT-X-CUE-IN without being cued out")

            cued_out = False
            cue_expected_time = 0
            cue_elapsed_time = 0
            cue_measured_time = 0
            rewritten.append("#EXT-X-DISCONTINUITY") # throw one of these bad boys in there since we fucked with the timeline so much
            logger.debug("cue ended")

        elif "#EXTINF:" in line and cued_out:
            try:
                m = re.search(r"#EXTINF:([0-9]*\.?[0-9]+)", line)
                cue_measured_time += float(m.group(1))
                logger.debug(f"cue measured time: {cue_measured_time}")
            except Exception as err:
                logger.warning(f"failed to parse cued out #EXTINF: line {line}\n{err}")

        # elif "#EXT-X-PROGRAM-DATE-TIME:" in line:
        #     logger.debug(f"cutting #EXT-X-PROGRAM-DATE-TIME: line {line}")

        elif cued_out:
            logger.debug(f"skipping line during ad: {line}")

        else:
            logger.debug(f"keeping line: {line}")
            rewritten.append(line)

    return '\n'.join(rewritten)

class Stream():

    def __init__(self, token: Token, game_pk: str, media_id: str):
        self.token = token
        self.game_pk = game_pk
        self.media_id = media_id
        self.url = "https://www.mlb.com/tv/g%s/v%s" % (self.game_pk, self.media_id)
        self.session = None

        self.reset()

    def reset(self):

        self.errors = None

        # via _gen_session()
        self._device_id = ""
        self._session_id = None

        # via _gen_master_playlist_url()
        self._master_playlist_url = None
        self._upstream_base_url = None

        # via _gen_master_playlist()
        self._etag = ""
        self._master_playlist = None

        # self._playlist_prefix = None
        # self._playback_session_id = None
        # self._media_playlists = None
        # self._milestones = None
        # self._commercial_breaks = None

    async def get_master_playlist(self, base_url):
        self.session = aiohttp.ClientSession()
        try:
            await self._gen_master_playlist(base_url)
        finally:
            await self.session.close()
        return self._master_playlist
    
    async def get_master_playlist_url(self):
        self.session = aiohttp.ClientSession()
        try:
            await self._gen_master_playlist_url()
        finally:
            await self.session.close()
        return self._master_playlist_url
    
    async def get_media_playlist(self, base_url, playlist):
        self.session = aiohttp.ClientSession()
        try:
            return await self._gen_media_playlist(base_url, playlist)
        finally:
            await self.session.close()

    def get_errors(self):
        return self.errors

    async def get_media_file(self, base_url, suffix):
        self.session = aiohttp.ClientSession()
        try:
            return await self._gen_media_file(base_url, suffix)
        finally:
            await self.session.close()

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
        async with self.session.post(GRAPHQL_URL, headers=headers, json=payload, ssl=False) as res:
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
        async with self.session.post(GRAPHQL_URL, headers=headers, json=payload, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to gen master playlist url: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        if "errors" in res_json:
            self.errors = res_json["errors"]
            raise Exception(f"Failed to gen master playlist url: {res_json['errors']}")
        
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
        async with self.session.get(self._master_playlist_url, headers=headers, ssl=False) as res:
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

        self._master_playlist = rewrite_playlist_urls(res_text, full_url)

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
        async with self.session.get(target, headers=headers, ssl=False) as res:
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

        return rewrite_playlist_urls(res_text, full_url)
    
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
        async with self.session.get(target, headers=headers, ssl=False) as res:
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