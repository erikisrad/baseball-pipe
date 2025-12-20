import logging

logger = logging.getLogger(__name__)

def rewrite_playlist_urls(playlist_content, base_url, gamePK, mediaId):
    lines = playlist_content.split('\n')
    rewritten = []
    
    for line in lines:
        if line and not line.startswith('#'):
            # This is a URL line
            if line.startswith('http'):
                # Absolute URL - proxy it
                rewritten.append(f"{base_url}proxy/{gamePK}/{mediaId}/{line}")
            else:
                # Relative URL - proxy it
                rewritten.append(f"{base_url}proxy/{gamePK}/{mediaId}/{line}")
        else:
            rewritten.append(line)
    
    return '\n'.join(rewritten)

async def proxy_request(self, request: web.Request):
    """Proxy media playlists and segments from upstream"""
    gamePK = request.match_info['gamePK']
    mediaId = request.match_info['mediaId']
    url = request.match_info['url']
    
    scheme = request.scheme
    host = request.host
    base_url = f"{scheme}://{host}/"
    
    logger.info(f"Proxying: {url}")
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.error(f"Upstream error {resp.status} for {url}")
                return web.Response(status=resp.status, text="Upstream error")
            
            content_type = resp.headers.get('Content-Type', '')
            
            # Check if it's a playlist
            if 'mpegurl' in content_type or url.endswith('.m3u8'):
                content = await resp.text()
                # Rewrite URLs in the playlist
                rewritten = rewrite_playlist_urls(content, base_url, gamePK, mediaId)
                return web.Response(
                    text=rewritten,
                    content_type="application/vnd.apple.mpegurl",
                    headers=cors_headers("application/vnd.apple.mpegurl")
                )
            else:
                # It's a segment, just proxy the binary data
                data = await resp.read()
                return web.Response(
                    body=data,
                    headers=cors_headers(content_type or "video/mp2t")
                )