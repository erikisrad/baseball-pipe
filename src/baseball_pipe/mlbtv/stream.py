import logging
import re
import time
from datetime import datetime, timezone
from baseball_pipe.mlbtv.token import Token

from baseball_pipe.misc import utilities as u
import aiohttp
import m3u8

GRAPHQL_URL = "https://media-gateway.mlb.com/graphql"
URI_PATTERN = re.compile(r'URI="([^"]+)"')
PLAYLIST_TYPE_PATTERN = re.compile("#EXT-X-PLAYLIST-TYPE:([A-Z]+)")

logger = logging.getLogger(__name__)

class Stream():

    def __init__(self,
                 token:Token,
                 game_pk:str,
                 media_id:str,
                 session:aiohttp.ClientSession,
                 proxy:str):
        
        self.token = token
        self.game_pk = game_pk
        self.media_id = media_id
        self.url = "https://www.mlb.com/tv/g%s/v%s" % (self.game_pk, self.media_id)
        self.session = session
        self.proxy = proxy

        self.reset()

    def reset(self):

        #misc
        self._expiration = None

        # via _gen_session()
        self._device_id = ""
        self._session_id = None

        # via _gen_master_playlist_url()
        self._master_playlist_url = None
        self._upstream_base_url = None

        # via _gen_master_playlist()
        self._etag = ""
        self._master_playlist = None
        self._variant_playlists = None

    def __str__(self):
        return f"{self.game_pk}/{self.media_id}"
    
    def __repr__(self):
        return f"{self.game_pk}/{self.media_id}"

    def is_expired(self):
        if not self._expiration:
            return False

        expiration = self._expiration
        if isinstance(expiration, str):
            # truncate sub-microsecond digits (e.g. nanoseconds) so fromisoformat can parse it
            expiration = re.sub(r'(\.\d{6})\d*Z$', r'\1+00:00', expiration)
            expiration = expiration.replace('Z', '+00:00') if expiration.endswith('Z') else expiration
            expiration = datetime.fromisoformat(expiration)

        if isinstance(expiration, datetime):
            seconds_until_expired = round((expiration - datetime.now(timezone.utc)).total_seconds())
        else:
            seconds_until_expired = round(expiration - time.time())

        expired = seconds_until_expired <= 30
        logger.info(f"stream {self} expires in {seconds_until_expired} seconds")

        return expired

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
            self._expiration = res_json["data"]["initPlaybackSession"]["playback"]["expiration"]
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

        self._master_playlist = res_text
        
        variants = m3u8.loads(self._master_playlist).playlists
        self._variant_playlists = sorted(
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