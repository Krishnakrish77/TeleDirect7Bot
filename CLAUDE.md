# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```sh
virtualenv -p /usr/bin/python3 venv
. ./venv/bin/activate
pip install -r requirements.txt
python3 -m main          # entry point is main/__main__.py
```

Runtime is pinned to **Python 3.9.20** (`runtime.txt`). The Procfile launches the same command on Heroku/Koyeb (`web: python -m main`).

### Tests

Python: lightweight `unittest` self-checks live in `tests/` (no pytest config; they set dummy mandatory env vars, then import and exercise pure helpers — no Mongo/network). Run all with `python -m unittest discover -s tests`, or one with `python -m unittest tests.test_<name>`. Put new backend self-checks here, not at the repo root.

Frontend: `cd frontend && npm run build` (runs `tsc` then Vite; build output under `main/server/static/app` is gitignored and rebuilt on deploy) and `npx vitest run` for the component/unit suite.

Beyond that, changes are validated by running the bot against real Telegram credentials configured via `.env` (see README for the full var list; `API_ID`, `API_HASH`, `BOT_TOKEN`, `BIN_CHANNEL`, `OWNER_ID` are mandatory).

## Architecture

This is a Telegram → HTTP bridge: the bot stores every uploaded file as a message in a private "bin" channel, then serves the bytes over HTTP by streaming chunks back from Telegram's MTProto servers on demand. Files are never persisted to disk.

### Two processes in one event loop (`main/__main__.py`)

`start_services()` runs both the Pyrogram bot and an aiohttp web server in the same asyncio loop:
1. `StreamBot` (Pyrogram `Client`) connects and registers plugins from `main/bot/plugins/`.
2. `initialize_clients()` spins up extra Pyrogram clients from `MULTI_TOKEN1..N` env vars to parallelize streaming load.
3. `web_server()` (aiohttp) binds `Var.PORT` and serves `main/server/stream_routes.py`.
4. If `ON_HEROKU`, a `ping_server()` keep-alive task is launched.

`pyrogram.idle()` keeps the bot alive; `cleanup()` shuts both down on exit.

### Multi-client load balancing (`main/bot/clients.py`)

- `multi_clients: dict[int, Client]` — index 0 is `StreamBot`, additional indexes are extra workers.
- `work_loads: dict[int, int]` — per-client active stream counter.
- The HTTP request handler picks the least-loaded client per request (`min(work_loads, key=work_loads.get)`).
- A monkey-patch on `pyrogram.utils.get_peer_type` (in `main/bot/__init__.py`) fixes a known Pyrogram bug with peer ID parsing — keep this if touching client init.

### Request flow

Two routes in `main/server/stream_routes.py`, both extracting `(secure_hash, message_id)` from the URL path `/{hash}{message_id}`:
- `GET /watch/...` → renders an HTML page (`render_page` in `main/utils/render_template.py`) using `main/template/req.html` (video/audio player) or `dl.html` (download page).
- `GET /...` → `media_streamer()` returns the actual file bytes with `Range` / `Content-Range` headers.

`secure_hash` is the first 6 chars of the file's Telegram `unique_id`. Mismatch raises `InvalidHash` → 403. This is the only auth on stream URLs.

### Byte streaming (`main/utils/custom_dl.py`)

`ByteStreamer` is the core of the stream pipeline:
- Caches `FileId` objects per `message_id` (`cached_file_ids`).
- `generate_media_session()` builds/reuses a Pyrogram `Session` for the file's DC, including cross-DC auth export via `auth.ExportAuthorization` / `auth.ImportAuthorization`.
- `yield_file()` returns an async generator that pulls `raw.functions.upload.GetFile` chunks aligned to Telegram's chunk size constraints (`chunk_size()` rounds to a power-of-two between 4 KB and 1 MB; `offset_fix()` aligns request offsets to chunk boundaries).
- `class_cache` in `stream_routes.py` keeps one `ByteStreamer` per client to preserve the session and file-ID caches across requests.

### Plugins (`main/bot/plugins/`)

Pyrogram auto-loads these via `plugins={"root": "main/bot/plugins"}`:
- `start.py` — `/start` and onboarding.
- `stream.py` — forwards incoming media to `BIN_CHANNEL` and replies with the generated URL. Handles private chats, channels (also edits the source message to add a Download button), and groups.
- `callback.py` — inline button handlers.

When adding a media handler, mirror `stream.py`: forward to `BIN_CHANNEL` first, then call `gen_link()` (`main/utils/file_properties.py`) so the URL hash stays consistent with what the streaming routes expect.

### Config (`main/vars.py`)

All env vars funnel through a single `Var` class. `URL` is computed at import time from `FQDN`/`PORT`/`HAS_SSL`/`NO_PORT`/`ON_HEROKU` — any new env-driven behavior should land here rather than reading `os.environ` directly elsewhere.
