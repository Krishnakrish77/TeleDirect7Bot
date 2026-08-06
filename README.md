<div align="center">

<img src="https://socialify.git.ci/Krishnakrish77/TeleDirect7Bot/image?description=1&amp;font=Source%20Code%20Pro&amp;forks=1&amp;issues=1&amp;pattern=Charlie%20Brown&amp;pulls=1&amp;stargazers=1&amp;theme=Dark" alt="TeleDirect7Bot project overview" width="640" height="320" />

[Quick start](#quick-start) · [Configuration](#configuration) · [Docker deployment](#docker) · [Daily use](#daily-use)

</div>

---

## A Telegram-first media library

TeleDirect7Bot keeps media in a Telegram channel while providing a web app for the library around it.

| | Capability |
| --- | --- |
| 🎬 | Browse movies and series with TMDB-enriched posters, details, cast, genres, and search. |
| ▶️ | Stream or download files directly from Telegram, including HLS transcoding when needed. |
| 📚 | Track watch progress, likes, watchlists, requests, subtitles, and playback statistics. |
| ✨ | Get library-grounded AI Picks for movies and series, plus music mixes as a separate experience. |
| 🛠️ | Operate the catalogue from the admin console: enrich metadata, fix titles, inspect health, and remove stale entries. |

<table>
  <tr>
    <td width="33%"><strong>Keep control</strong><br />Files remain in your Telegram channel; the web app is the private library layer around them.</td>
    <td width="33%"><strong>Find something good</strong><br />Metadata, search, discovery shelves, and AI Picks make a growing catalogue useful.</td>
    <td width="33%"><strong>Run it confidently</strong><br />A focused admin console exposes enrichment, catalogue health, and maintenance workflows.</td>
  </tr>
</table>

The service is intended for a private media library. You are responsible for ensuring that the media you store and stream is lawful to use.

## Architecture at a glance

```text
Telegram bot / BIN channel
           │
           ├── media files + durable catalogue snapshots
           │
           ▼
Python service ──► React web app ──► private library, player & admin tools
           │
           ├── optional MongoDB durable catalogue
           ├── optional TMDB enrichment
           └── optional Gemini AI Picks / metadata assistance
```

## Quick start

### 1. Create the Telegram pieces

1. Create a Telegram API application at [my.telegram.org](https://my.telegram.org).
2. Create a bot with [@BotFather](https://t.me/BotFather).
3. Create a private channel to act as the `BIN_CHANNEL`.
4. Add the bot as an administrator in that channel.
5. Get your Telegram numeric user ID and set it as `OWNER_ID`.

To find the channel ID, forward a post from the channel to a Telegram ID helper bot and use the reported ID. It usually starts with `-100`.

### 2. Configure the service

Create a `.env` file in the repository root. It is ignored by Git—keep it private.

```dotenv
# Required Telegram configuration
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
BOT_TOKEN=your_bot_token_from_botfather
BIN_CHANNEL=-1001234567890
OWNER_ID=your_numeric_telegram_user_id

# Public address used in generated stream links
FQDN=media.example.com
HAS_SSL=true
NO_PORT=true

# Required for persistent web sessions. Generate with:
# python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET=replace_with_a_long_random_value

# Required only when enabling Telegram Login for the web app
BOT_USERNAME=your_bot_username_without_at

# Optional integrations
TMDB_API_KEY=your_tmdb_api_key
GEMINI_API_KEY=your_gemini_api_key
WYZIE_API_KEY=your_wyzie_api_key
```

Never commit `.env`, bot tokens, API keys, session strings, or database URLs. Use your deployment provider's secret/environment-variable settings in production.

### 3. Run it

The local workflow needs Python 3.12+ and Node 22+ for the React web app.

```sh
git clone https://github.com/Krishnakrish77/TeleDirect7Bot.git
cd TeleDirect7Bot

python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cd frontend
npm ci
npm run build
cd ..

python -m main
```

Open `http://localhost:8080` for a local run. Set `FQDN` to your public hostname before sharing generated links outside your machine.

## Docker

Docker is the recommended production path. The image builds the React app and includes `ffmpeg`/`ffprobe` for media playback support.

```sh
docker build -t teledirect7bot .
docker run --detach \
  --name teledirect7bot \
  --env-file .env \
  --publish 8080:8080 \
  --restart unless-stopped \
  teledirect7bot
```

Put a TLS-enabled reverse proxy in front of the container and set `FQDN`, `HAS_SSL=true`, and `NO_PORT=true`. The application listens on port `8080` by default; change it with `PORT` if needed.

## Configuration

The essentials are in the Quick start `.env` example. The complete reference is collapsed here so new operators can get running without scrolling past a wall of settings.

<details>
<summary><strong>Open the full configuration reference</strong></summary>

<br />

### Required

| Variable | Purpose |
| --- | --- |
| `API_ID` | Telegram application ID from [my.telegram.org](https://my.telegram.org). |
| `API_HASH` | Telegram application hash from the same application. |
| `BOT_TOKEN` | Token issued by [@BotFather](https://t.me/BotFather). |
| `BIN_CHANNEL` | Numeric ID of the private Telegram channel that stores the media. |
| `OWNER_ID` | Your numeric Telegram user ID; controls owner-only actions. |

### Web and deployment

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8080` | HTTP port the service listens on. |
| `WEB_SERVER_BIND_ADDRESS` | `0.0.0.0` | Bind address for the web server. |
| `FQDN` | bind address | Public hostname used for generated links. |
| `HAS_SSL` | `false` | Generate HTTPS links when `true`. |
| `NO_PORT` | `false` | Omit the port from generated links when a proxy serves standard HTTP(S) ports. |
| `JWT_SECRET` | generated per boot | A persistent, random secret for web sessions. Set this in production. |
| `BOT_USERNAME` | — | Bot username for Telegram Login; omit `@`. |
| `ADMIN_TOKEN_TTL_MIN` | `15` | Validity of one-time admin DM links. |
| `ADMIN_SESSION_TTL_MIN` | `60` | Validity of an admin browser session. |

### Catalogue and metadata

| Variable | Default | Purpose |
| --- | --- | --- |
| `TMDB_API_KEY` | — | Enables TMDB movie/series metadata, artwork, trailers, and discovery. |
| `GEMINI_API_KEY` | — | Enables Gemini-backed, library-grounded AI Picks and admin metadata assistance. |
| `WYZIE_API_KEY` | — | Enables server-side subtitle search. Never expose this key to the browser. |
| `STORE_BACKEND` | — | Set to `mongo` to use MongoDB for the durable catalogue. |
| `MONGO_URI` | — | Required when `STORE_BACKEND=mongo`. |
| `MONGO_DB` | `teledirect` | MongoDB database name. |
| `MONGO_COLLECTION` | `items` | MongoDB catalogue collection. |
| `MONGO_META_COLLECTION` | `meta` | MongoDB metadata collection. |
| `MEDIA_INDEX_SEED_OVERLAP` | `32` | Recent channel messages rechecked at normal startup. |
| `MEDIA_INDEX_SEED_DEPTH` | `800` | History window for recovery or explicit reconciliation. |

### Streaming and throughput

| Variable | Default | Purpose |
| --- | --- | --- |
| `WORKERS` | `6` | Maximum concurrent update workers. |
| `SLEEP_THRESHOLD` | `60` | Retry FloodWait exceptions at or below this many seconds. |
| `INDEX_CONCURRENCY` | `3` | Parallel catalogue indexing/enrichment work. |
| `CODEC_PROBE_CONCURRENCY` | `3` | Parallel `ffprobe` jobs. Keep modest on small hosts. |
| `MAX_STREAMS_TOTAL` | `25` | Total concurrent streams allowed. |
| `MAX_STREAMS_PER_IP` | `8` | Concurrent streams per client IP. |
| `TRUSTED_PROXY_CIDRS` | — | Space-separated reverse-proxy/LB CIDRs allowed to supply `X-Forwarded-For`. Leave unset when the origin is directly reachable. |
| `HLS_MAX_CONCURRENT` | `2` | Parallel HLS segment work. |
| `HLS_SESSION_MAX` | `2` | Active HLS sessions. |
| `HLS_TRANSCODE_MAX` | `1` | Concurrent transcoding HLS sessions. |
| `HLS_TRANSCODE_ACQUIRE_TIMEOUT` | `5` | Seconds an HLS request may wait for a transcode slot before receiving a retryable 503. |
| `HLS_SESSION_DISK_BUDGET` | available disk | Optional byte cap for total source-size reservations across active HLS sessions; `0` derives the cap from currently free disk space. |
| `HLS_SESSION_MIN_FREE_BYTES` | `128 MiB` | Reject a new HLS session when the work disk is below this free-space floor. |

### Optional bot capabilities

| Variable | Purpose |
| --- | --- |
| `MULTI_TOKEN1`, `MULTI_TOKEN2`, … | Extra bot tokens/session strings for multi-client streaming capacity. |
| `USER_SESSION` | Telegram user session string for protected-channel grabs. |
| `USER_API_ID`, `USER_API_HASH` | Separate Telegram API application used with `USER_SESSION`. |
| `BANNED_CHANNELS`, `BANNED_USERS` | Space-separated numeric IDs to block. |
| `GRAB_URL_MAX_BYTES` | Maximum remote file size accepted by `/grab`; default 1.5 GiB. |

</details>

## MongoDB migration

The default catalogue is backed by Telegram snapshots. For a large or long-lived library, MongoDB is recommended.

1. Set `MONGO_URI` but leave `STORE_BACKEND` unset.
2. Start the service and open `/admin`.
3. Select **Migrate → Mongo** and verify the document count in your database.
4. Set `STORE_BACKEND=mongo` and restart the service.

Mongo mode starts strictly: an invalid or unreachable URI stops the application instead of silently falling back to a temporary local catalogue.

## Daily use

1. Send or forward supported media to the bot. It stores the media in `BIN_CHANNEL` and replies with a stream/download link.
2. Open the web app to browse the library, continue playback, manage your watchlist, or request a missing title.
3. Visit `/admin` through the owner flow to enrich metadata, repair catalogue entries, inspect operational health, and run maintenance tasks.

If you add multiple bot clients, add every one of them as an administrator in `BIN_CHANNEL`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Generated links work only locally | Set `FQDN` to the public hostname and configure `HAS_SSL` / `NO_PORT` to match your proxy. |
| The bot cannot access stored files | Confirm the bot is an administrator of `BIN_CHANNEL` and the channel ID is correct. |
| Users are logged out after deploys | Set a stable, high-entropy `JWT_SECRET`. |
| No posters or metadata | Add a valid `TMDB_API_KEY`, then run enrichment from `/admin`. |
| AI Picks fall back to library shelves | Check `GEMINI_API_KEY` and server logs; the deterministic library fallback remains available. |
| Mongo mode will not start | Verify `MONGO_URI`, connectivity, and that `STORE_BACKEND=mongo` is intentional. |

## Credits and license

The original streaming implementation was based on [TG-Direct-Link-Generator](https://github.com/TechShreyash/TG-Direct-Link-Generator). See [LICENSE](LICENSE) for licensing details.

For project contact, reach [@Krishnakrish77](https://t.me/Krishnakrish77) on Telegram.
