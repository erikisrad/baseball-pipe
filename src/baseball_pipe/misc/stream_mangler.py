from urllib.parse import urljoin

def prefix_playlist_urls(playlist, base_url):
    lines = []
    for line in playlist.splitlines():
        if line.startswith("#"): #NOT A URL
            lines.append(line)
        elif line.strip() == "": #EMPTY LINE
            lines.append(line)
        elif line.startswith("http"): #ALREADY A COMPLETE URL
            lines.append(line)
        else:
            lines.append(urljoin(base_url, line.strip()))
    return "\n".join(lines)

