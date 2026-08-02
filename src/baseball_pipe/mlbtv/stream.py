import logging
import re
from datetime import datetime, timezone
from baseball_pipe.mlbtv.token import Token

from baseball_pipe.misc import utilities as u
from baseball_pipe.misc import header_handler as e
import aiohttp
import m3u8

GRAPHQL_URL = "https://media-gateway.mlb.com/graphql"
logger = logging.getLogger(__name__)

class Stream():

    def __init__(self,
                 token:Token,
                 game_pk:str,
                 media_id:str,
                 session:aiohttp.ClientSession,
                 proxy:str = None):
        
        self.token = token
        self.game_pk = game_pk
        self.media_id = media_id
        self.url = "https://www.mlb.com/tv/g%s/v%s" % (self.game_pk, self.media_id)
        self.session = session
        self.proxy = proxy

        self.reset()

    async def inititialize(self):
        if not self._master_playlist_url:
            await self._gen_master_playlist_url()

    def reset(self):

        #misc
        self._expiration = None

        # via _gen_session()
        self._device_id = ""
        self._session_id = None

        # via _gen_master_playlist_url()
        self._master_playlist_url = None
        self._expiration = None
        self._upstream_base_url = None

        # via _gen_master_playlist()
        self._master_playlist = None
        self._variant_playlists = None

    def __str__(self):
        return f"{self.game_pk}/{self.media_id}"
    
    def __repr__(self):
        return f"{self.game_pk}/{self.media_id}"
    
    # GETS
    async def get_master_playlist_url(self):
        if not self._master_playlist_url:
            await self._gen_master_playlist_url()
        return self._master_playlist_url

    async def get_master_playlist(self):
        await self._gen_master_playlist()
        return self._master_playlist

    async def get_media_playlist(self, playlist):
        if not self._variant_playlists:
            await self._gen_master_playlist()

        await self._gen_media_playlist(playlist)
        return self._variant_playlists[playlist]

    async def get_variants(self):
        if not self._variant_playlists:
            await self._gen_master_playlist()

        return self._variant_playlists

    async def get_upstream_base_url(self):
        if not self._upstream_base_url:
            await self._gen_master_playlist_url()

        return self._upstream_base_url

    async def get_segment(self, path):
        if not self._upstream_base_url:
            await self._gen_master_playlist_url()

        return await self._gen_segment(path)

    @staticmethod
    def _parse_expiration(expiration: str) -> datetime:
        expiration = re.sub(r'(\.\d{6})\d*Z$', r'\1+00:00', expiration)
        expiration = expiration.replace('Z', '+00:00') if expiration.endswith('Z') else expiration
        return datetime.fromisoformat(expiration)

    def is_expired(self) -> bool:
        if not self._expiration:
            return False

        try:
            expiration = self._parse_expiration(self._expiration)
            seconds_until_expired = round((expiration - datetime.now(timezone.utc)).total_seconds())
            logger.debug(f"stream {self} expires in {seconds_until_expired} seconds")
            return seconds_until_expired <= 30
        except Exception as err:
            logger.warning(f"Unable to determine expiration for {self} stream: {err}")
            return True

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
            **e.GRAPHQL_HEADER,
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"{self.token.token_type} {self.token.access_token}",
            "Content-Type": "application/json",
            "Referer": "https://www.mlb.com/tv/g%s" % self.game_pk,
        }

        logger.info(f"sending session request to {GRAPHQL_URL}")
        async with self.session.post(GRAPHQL_URL, headers=headers, json=payload, proxy=self.proxy, ssl=False) as res:
            if res.status != 200:
                raise Exception(f"Failed session request: {res.status} {res.reason}")
            res_json = await res.json()

        try:
            self._device_id = res_json["data"]["initSession"]["deviceId"]
            self._session_id = res_json["data"]["initSession"]["sessionId"]
        except(KeyError, TypeError) as err:
            logger.error(f"Failed to parse session for {self} stream: {err}")
            raise err
        
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
            **e.GRAPHQL_HEADER,
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"{self.token.token_type} {self.token.access_token}",
            "Content-Type": "application/json",
            "Referer": self.url,
        }

        logger.info(f"sending master playlist URL request to {GRAPHQL_URL}")
        async with self.session.post(GRAPHQL_URL, headers=headers, proxy=self.proxy, json=payload, ssl=False) as res:
            if res.status != 200:
                raise Exception(f"Failed master playlist url request: {res.status} {res.reason}")
            res_json = await res.json()

        if "errors" in res_json:
            error_message = u.safe_get(res_json, "errors", 0, "message", default="Unknown error")
            logger.error(f"Errors in master playlist url response: {error_message}")
            raise Exception(error_message)

        try:
            self._master_playlist_url = res_json["data"]["initPlaybackSession"]["playback"]["url"]
            self._expiration = res_json["data"]["initPlaybackSession"]["playback"]["expiration"]
            self._upstream_base_url = self._master_playlist_url.rsplit('/', 1)[0] + '/'
        except(KeyError, TypeError) as err:
            logger.error(f"Failed to parse master playlist url for {self} stream: {err}")
            raise err

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

        logger.info(f"sending master playlist request to {self._master_playlist_url}")
        async with self.session.get(self._master_playlist_url, headers=headers, proxy=self.proxy, ssl=False) as res:
            if res.status != 200:
                raise Exception(f"Failed master playlist request: {res.status} {res.reason}")
            res_text = await res.text()

        self._master_playlist = res_text
        try:
            assert "#EXTM3U" in self._master_playlist
        except Exception as err:
            logger.error(f"Failed to parse master playlist for {self} stream\n{res_text}\n{err}")

        try:
            variants = m3u8.loads(self._master_playlist).playlists
            self._variant_playlists = dict.fromkeys(sorted(
                variants,
                key=lambda v: v.stream_info.bandwidth or 0,
                reverse=True
            ))
        except Exception as err:
            logger.warning(f"error generating variant playlists for {self} stream: {err}")
            self._variant_playlists = None

    async def _gen_media_playlist(self, playlist):

        if not self._upstream_base_url:
            await self._gen_master_playlist_url()

        target = self._upstream_base_url + playlist

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

        self._variant_playlists[playlist] = res_text

    async def _gen_segment(self, path):

        if not self._upstream_base_url:
            await self._gen_master_playlist_url()

        target = self._upstream_base_url + path

        headers = {
            **e.MEDIA_HEADER,
            "Accept": "*/*",
            "Accept-Encoding": "identity;q=1, *;q=0",
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
        }

        logger.info(f"sending segment request to {target}")
        async with self.session.get(target, headers=headers, proxy=self.proxy, ssl=False) as res:
            if res.status != 200:
                raise Exception(f"Failed segment request: {res.status} {res.reason}")
            return await res.read()