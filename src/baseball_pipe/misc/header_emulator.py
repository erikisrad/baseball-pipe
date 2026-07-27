#CONSTANT
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
SEC_CH_UA = '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"'
SEC_CH_UA_MOBILE = "?0"
SEC_CH_UA_PLATFORM = '"Windows"'
ORIGIN = "https://www.mlb.com"
ACCEPT_LANGUAGE = "en-US,en;q=0.9"
ACCEPT_ENCODING = "gzip, deflate, br, zstd"

#VARIABLE
PRIORITY = "u=1, i"
REFERER = "https://www.mlb.com/login?redirectUri=/"
SEC_FETCH_DEST = "empty"
SEC_FETCH_MODE = "cors"
SEC_FETCH_SITE = "same-site"

#ACCOUNT REQUESTS
# ACCEPT = "application/json; okta-version=1.0.0"
# CONTENT_TYPE = "application/json"

AGENT_HEADER = {
    "User-Agent": USER_AGENT
}

FINGERPRINT_HEADER = {
    "Accept-Language": ACCEPT_LANGUAGE,
    "Sec-Ch-Ua": SEC_CH_UA,
    "Sec-Ch-Ua-Mobile": SEC_CH_UA_MOBILE,
    "Sec-Ch-Ua-Platform": SEC_CH_UA_PLATFORM,
    "User-Agent": USER_AGENT
}

GRAPHQL_HEADER = {
    **FINGERPRINT_HEADER,
    "Accept-Encoding": ACCEPT_ENCODING,
    "Origin": ORIGIN,
    "Priority": PRIORITY,
    "Sec-Fetch-Dest": SEC_FETCH_DEST,
    "Sec-Fetch-Mode": SEC_FETCH_MODE,
    "Sec-Fetch-Site": SEC_FETCH_SITE,
}

ACCOUNT_HEADER = {
    **GRAPHQL_HEADER,
    "Referer": REFERER,
}

MEDIA_HEADER = {
    **FINGERPRINT_HEADER,
    "Accept-Encoding": ACCEPT_ENCODING,
    "Priority": PRIORITY
}

