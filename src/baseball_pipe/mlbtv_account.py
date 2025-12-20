import base64
import hashlib
import baseball_pipe.utilities as u
from baseball_pipe.mlbtv_token import Token
import aiohttp
import logging

logger = logging.getLogger(__name__)


CLIENT_ID = "0oap7wa857jcvPlZ5355"

class Account():

    def __init__(self,
                 username:str="erik.rad@gmail.com",
                 password:str="As6?T.j$Lp3Ezz2"):
        
        self.username = username
        self.password = password
        self.session = None

        self.reset()

    def reset(self):
        self._interaction_handle = None
        self._introspect_state_handle = None
        self._code_verifier = None
        self._code_challenge = None
        self._identity_state_handle = None
        self._id_email = None
        self._id_password = None
        self._challenge_state_handle = None
        self._answer_state_handle =  None
        self._interaction_code = None
        self._token = None

    async def get_token(self) -> Token:
        if not self._token or self._token.is_expired():
            self.session = aiohttp.ClientSession()
            try:
                self.reset()
                await self._gen_token()
            finally:
                await self.session.close()
        return self._token

    async def _post_interact(self):

        def gen_challenge(code_verifier):
            code_challenge = code_verifier.encode('ascii')
            code_challenge = hashlib.sha256(code_challenge)
            code_challenge = code_challenge.digest()
            code_challenge = base64.urlsafe_b64encode(code_challenge)
            code_challenge = code_challenge.decode('ascii')
            code_challenge = code_challenge[:-1]
            return code_challenge

        interact_url = "https://ids.mlb.com/oauth2/aus1m088yK07noBfh356/v1/interact"

        state_param = u.gen_random_string(64)
        nonce_param = u.gen_random_string(64)

        self._code_verifier = u.gen_random_string(58)
        self._code_challenge = gen_challenge(self._code_verifier)

        payload = [f"client_id={CLIENT_ID}",
                "scope=openid%20email",
                "redirect_uri=https%3A%2F%2Fwww.mlb.com%2Flogin",
                f"code_challenge={self._code_challenge}",
                "code_challenge_method=S256",
                f"state={state_param}",
                f"nonce={nonce_param}"
        ]
        payload = '&'.join(payload)

        headers = { "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.mlb.com",
            "Priority": "u=1, i",
            "Referer": "https://www.mlb.com/login?redirectUri=/",
            "Sec-Ch-Ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {interact_url}")
        async with self.session.post(interact_url, headers=headers, data=payload, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to post interact: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        self._interaction_handle = res_json["interaction_handle"]
        logger.info(f"obtained interaction_handle: {self._interaction_handle[0:3]}...{self._interaction_handle[-3:]}")

    async def _post_introspect(self):

        if not self._interaction_handle:
            await self._post_interact()

        INTROSPECT_URL = "https://ids.mlb.com/idp/idx/introspect"

        payload = '{"interactionHandle":"%s"}' % self._interaction_handle

        headers = {
                "Accept": "application/ion+json; okta-version=1.0.0",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en",
                "Content-Type": "application/ion+json; okta-version=1.0.0",
                "Origin": "https://www.mlb.com",
                "Priority": "u=1, i",
                "Referer": "https://www.mlb.com/login?redirectUri=/",
                "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
                "Sec-Ch-Ua-Mobile": "?0",
                "sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {INTROSPECT_URL}")
        async with self.session.post(INTROSPECT_URL, headers=headers, data=payload, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to post introspect: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        self._introspect_state_handle =  res_json["stateHandle"]
        logger.info(f"obtained introspect stateHandle: {self._introspect_state_handle[0:3]}...{self._introspect_state_handle[-3:]}")

    async def _post_identity(self):

        if not self._introspect_state_handle:
            await self._post_introspect()

        IDENTITY_URL = "https://ids.mlb.com/idp/idx/identify"

        payload = '{"identifier":"%s","stateHandle":"%s"}' % (self.username, self._introspect_state_handle)

        headers = {
            "Accept": "application/json; okta-version=1.0.0",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en",
            "Content-Type": "application/json",
            "Origin": "https://www.mlb.com",
            "Priority": "u=1, i",
            "Referer": "https://www.mlb.com/login?redirectUri=/",
            "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            "Sec-Ch-Ua-Mobile": "?0",
            "sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {IDENTITY_URL}")
        async with self.session.post(IDENTITY_URL, headers=headers, data=payload, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to post identity: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        self._identity_state_handle = res_json["stateHandle"]
        authenticators = res_json["authenticators"]["value"]
        self._id_email = None
        self._id_password = None
        for value in authenticators:
            if value["type"] == "email":
                self._id_email = value["id"]
            elif value["type"] == "password":
                self._id_password = value["id"]
            if self._id_email and self._id_password:
                break

        if not self._id_email or not self._id_password:
            raise Exception(f"IDENTITY failed, email or password authenticator not found in IDENTITY response: {res.text}")
        
    async def _challenge(self):

        if not self._id_password or not self._identity_state_handle:
            await self._post_identity()

        CHALLENGE_URL = "https://ids.mlb.com/idp/idx/challenge"

        payload = '{"authenticator":{"id":"%s"},"stateHandle":"%s"}' % (self._id_password, self._identity_state_handle)

        headers = {
                "Accept": "application/json; okta-version=1.0.0",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en",
                "Content-Type": "application/json",
                "Origin": "https://www.mlb.com",
                "Priority": "u=1, i",
                "Referer": "https://www.mlb.com/login?redirectUri=/",
                "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
                "Sec-Ch-Ua-Mobile": "?0",
                "sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {CHALLENGE_URL}")
        async with self.session.post(CHALLENGE_URL, headers=headers, data=payload, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to post challenge: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        self._challenge_state_handle =  res_json["stateHandle"]

    async def _answer(self):

        if not self._challenge_state_handle:
            await self._challenge()

        ANSWER_URL = "https://ids.mlb.com/idp/idx/challenge/answer"

        payload = '{"credentials":{"passcode":"%s"},"stateHandle":"%s"}' % (self.password, self._challenge_state_handle)

        headers = {
                "Accept": "application/json; okta-version=1.0.0",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en",
                "Content-Type": "application/json",
                "Origin": "https://www.mlb.com",
                "Priority": "u=1, i",
                "Referer": "https://www.mlb.com/login?redirectUri=/",
                "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
                "Sec-Ch-Ua-Mobile": "?0",
                "sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {ANSWER_URL}")
        async with self.session.post(ANSWER_URL, headers=headers, data=payload, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to post answer: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        self._answer_state_handle =  res_json["stateHandle"]
        success_with_interaction_code = res_json["successWithInteractionCode"]["value"]
        self._interaction_code = None
        for value in success_with_interaction_code:
            if value["name"] == "interaction_code":
                self._interaction_code = value["value"]
                break

        if not self._interaction_code:
            raise Exception(f"ANSWER failed: interaction code not found in response: {res.text}")
        
    async def _gen_token(self):

        if not self._interaction_code:
            await self._answer()

        TOKEN_URL = "https://ids.mlb.com/oauth2/aus1m088yK07noBfh356/v1/token"

        if not self._code_verifier:
            raise ValueError("Code verifier is not set. Authentication is occuring out of order.")

        payload = [
            f"client_id={CLIENT_ID}",
            "redirect_uri=https://www.mlb.com/login",
            "grant_type=interaction_code",
            f"code_verifier={self._code_verifier}",
            f"interaction_code={self._interaction_code}"
        ]
        payload = '&'.join(payload)

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.mlb.com",
            "Priority": "u=1, i",
            "Referer": "https://www.mlb.com/login?redirectUri=/",
            "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            "Sec-Ch-Ua-Mobile": "?0",
            "sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        }

        logger.info(f"sending request to {TOKEN_URL}")
        async with self.session.post(TOKEN_URL, headers=headers, data=payload, ssl=False) as res:
            logger.info("awaiting response...")
            if res.status != 200:
                raise Exception(f"Failed to gen token: {res.status} {res.reason}")
            res_json = await res.json()
            logger.info(f"response received, status {res.status}")

        self._token = Token(res_json) 