from baseball_pipe.webpage_gen.game_page import CONDENSED, RECAP
from baseball_pipe.standalone.standalone_stream import StandaloneStream
from baseball_pipe.mlbtv.mlbtv_stream import MlbtvStream


class StreamBucket():

    def __init__(self):
        self.bucket = {}

    async def get_stream(self, gamePK, mediaID, request):
        id = f"{gamePK}/{mediaID}"
        session = request.app["master_session"]
        proxy = request.app["proxy_url"]

        if id in self.bucket.keys() and not await self.bucket[id].is_expired():
            pass

        elif mediaID in [CONDENSED, RECAP]:
            self.bucket[id] = StandaloneStream(gamePK, mediaID, session=session, proxy=proxy)

        else:
            account = request.app["mlbtv_account"]
            self.bucket[id] = MlbtvStream(account, gamePK, mediaID, session=session, proxy=proxy)

        return self.bucket[id]