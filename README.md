# baseball-pipe

A webserver that proxies MLB.tv game broadcasts, rewriting the HLS streams to strip/replace ads, and serving a simple web GUI (or raw stream URLs) for watching in a browser, Chromecast, or external player like VLC.

## Features

- Full modern MLB broadcast catalog, browsable by date, game, and broadcast.
- VODs skip ad breaks entirely; live games replace ads with a silent countdown until play resumes.
- Chromecast support via a custom receiver.
- Multiple audio tracks (including radio and Spanish commentary) and subtitle tracks.
- Adaptive quality by default, or force a resolution/frame rate/bandwidth via query string.
- Query strings can bypass the GUI entirely, e.g. to pipe a broadcast straight into VLC.

See [/readme](src/baseball_pipe/html/readme.html) once the server is running for the full query string reference.

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) available on `PATH` (used to remux filler segments)
- `pip install -r requirements.txt`

## Configuration

The server reads the following environment variables on startup:

| Variable | Purpose |
|---|---|
| `bbp_proxy_url` | Proxy URL used for MLB.tv/auth requests |
| `bbp_user` | MLB.tv account email/username |
| `bbp_pass` | MLB.tv account password |
| `bbp_secret` | Secret key used to sign session cookies |
| `bbp_auth` | Password required to log in to the site |

## Running

```
cd src
python -m baseball_pipe
```

The server listens on `127.0.0.1:8080` by default. Logs are written to `logs/baseball_pipe.log` at the project root.
