from datetime import datetime, timedelta
from baseball_pipe.utilities import get_current_datetime
import pytz
import logging

logger = logging.getLogger(__name__)

class Token:

    def __init__(self, token_json):
        self.token_type = token_json["token_type"]
        self.expires_secs = token_json["expires_in"]
        self.expires_datetime = datetime.now(tz=pytz.UTC) + timedelta(seconds=self.expires_secs)
        self.access_token = token_json["access_token"]
        self.scope = token_json["scope"]
        self.id_token = token_json["id_token"]

    def __str__(self):
        return self.access_token
    
    def __repr__(self):
        return self.access_token

    def secs_until_expired(self):
        return round((self.expires_datetime - get_current_datetime()).total_seconds(), 2)
    
    def is_expired(self):
        logger.info(f"token expires in {self.secs_until_expired()} seconds")
        return self.secs_until_expired() <= 30