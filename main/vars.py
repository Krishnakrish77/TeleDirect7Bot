import logging
import urllib.parse
from os import environ
from dotenv import load_dotenv
from pyrogram.types import LinkPreviewOptions

# kurigram 2.2.24+ removed the ``disable_web_page_preview`` kwarg from
# Message.reply/reply_text/edit_text; previews are now controlled via
# ``link_preview_options``. All bot replies that carry stream links use
# this shared sentinel.
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

load_dotenv()


def _require(name: str) -> str:
    value = environ.get(name)
    if value is None or value == "":
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. "
            f"See README for the list of mandatory vars."
        )
    return value


class Var(object):
    MULTI_CLIENT = False
    API_ID = int(_require("API_ID"))
    API_HASH = _require("API_HASH")
    BOT_TOKEN = _require("BOT_TOKEN")
    SLEEP_THRESHOLD = int(environ.get("SLEEP_THRESHOLD", "60"))
    WORKERS = int(environ.get("WORKERS", "6"))
    BIN_CHANNEL = int(_require("BIN_CHANNEL"))
    PORT = int(environ.get("PORT", 8080))
    BIND_ADDRESS = str(environ.get("WEB_SERVER_BIND_ADDRESS", "0.0.0.0"))
    PING_INTERVAL = int(environ.get("PING_INTERVAL", "1200"))
    # Keep-alive self-ping. Koyeb free instances sleep on idle, so the ping
    # is auto-enabled there (KOYEB_REGION present). Other platforms (Render,
    # Fly, …) opt in explicitly with KEEP_ALIVE=true; anything else disables.
    ON_KOYEB = "KOYEB_REGION" in environ
    KEEP_ALIVE = ON_KOYEB or environ.get("KEEP_ALIVE", "").strip().lower() in ("1", "true", "yes")
    HAS_SSL = str(environ.get("HAS_SSL", "")).lower() == "true"
    NO_PORT = str(environ.get("NO_PORT", "")).lower() == "true"
    _fqdn = str(environ.get("FQDN") or environ.get("KOYEB_PUBLIC_DOMAIN") or BIND_ADDRESS)
    # FQDN accepts BOTH forms: a bare hostname ("app.koyeb.app") or a full
    # URL ("https://app.koyeb.app:8443/base"). Normalize once here so every
    # downstream consumer (URL building, generated stream links, self-ping)
    # gets a canonical value. A bare hostname inherits scheme/port from
    # HAS_SSL/NO_PORT as before; a full URL carries its own scheme (and,
    # when present, its own explicit port overrides the PORT/HAS_SSL flags).
    _parsed = urllib.parse.urlparse(_fqdn if "://" in _fqdn else f"//{_fqdn}")
    _host = _parsed.hostname
    if not _host:
        raise RuntimeError(
            f"Invalid FQDN {_fqdn!r}: not a hostname or URL. "
            "Use e.g. 'app.koyeb.app' or 'https://app.koyeb.app'."
        )
    FQDN = _host
    if _parsed.scheme:
        URL = urllib.parse.urlunparse(
            (
                _parsed.scheme,
                _parsed.netloc,
                _parsed.path.rstrip("/") or "",
                "", "", "",
            )
        ).rstrip("/") + "/"
    elif ON_KOYEB:
        URL = f"https://{FQDN}/"
    else:
        URL = "http{}://{}{}/".format(
            "s" if HAS_SSL else "", FQDN, "" if NO_PORT else ":" + str(PORT)
        )
    if FQDN == BIND_ADDRESS and BIND_ADDRESS in ("0.0.0.0", "127.0.0.1", "localhost"):
        logging.warning(
            "FQDN is not set; generated stream links will use %s and only work "
            "from this machine. Set FQDN to your public hostname.", BIND_ADDRESS
        )

    UPDATES_CHANNEL = "TechZBots"
    OWNER_ID = int(environ.get("OWNER_ID", "777000"))
    # Optional TMDB API key for catalogue enrichment (posters, overviews,
    # IMDb ids). Free at themoviedb.org → Settings → API. Without it the
    # enrichment pipeline no-ops silently.
    # Auth — Telegram Login Widget + JWT sessions
    BOT_USERNAME = environ.get("BOT_USERNAME", "").strip()
    _jwt_raw = environ.get("JWT_SECRET", "").strip()
    if not _jwt_raw:
        import secrets as _secrets
        _jwt_raw = _secrets.token_hex(32)
        if environ.get("ROLE", "leader").strip().lower() == "replica":
            logging.warning(
                "ROLE=replica without JWT_SECRET — each deployment mints a "
                "different secret, so user sessions issued by the leader are "
                "rejected here (random logouts). Set the SAME JWT_SECRET on "
                "leader and replicas."
            )
        else:
            logging.warning(
                "JWT_SECRET not set — a random secret was generated. "
                "All user sessions will be lost on every restart. "
                "Set JWT_SECRET=<64-hex-char-random-string> in your environment "
                "to persist sessions across deploys."
            )
    elif len(_jwt_raw) < 32:
        logging.warning(
            "JWT_SECRET is only %d chars — use at least 32 random hex characters "
            "for adequate session security.", len(_jwt_raw)
        )
    JWT_SECRET = _jwt_raw

    TMDB_API_KEY = environ.get("TMDB_API_KEY", "").strip()
    # Optional Google Books key for admin book metadata lookup. Kept server-side
    # so it is never exposed in the browser bundle.
    GOOGLE_BOOKS_API_KEY = environ.get("GOOGLE_BOOKS_API_KEY", "").strip()
    # Optional Gemini API key for thumbnail-based metadata suggestions in admin.
    # Free tier at aistudio.google.com — no credit card required.
    GEMINI_API_KEY = environ.get("GEMINI_API_KEY", "").strip()
    # AI Picks uses function calling plus structured JSON. Keep its model
    # independent from the admin metadata-suggestion selector.
    GEMINI_AI_REC_MODEL = environ.get("GEMINI_AI_REC_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    # Movie Buddy chat model. Spoiler-guardrail adherence matters more than
    # cost here, so prefer a strong instruction-following model.
    GEMINI_BUDDY_MODEL = environ.get("GEMINI_BUDDY_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    # Per-user daily Movie Buddy message cap — keeps the opt-in chat from
    # doubling as a free general-purpose chatbot.
    BUDDY_DAILY_LIMIT = max(1, int(environ.get("BUDDY_DAILY_LIMIT", "100") or 100))
    # Optional Wyzie subtitle provider key.  This is intentionally consumed
    # by server-side routes only; never expose it to the browser bundle.
    WYZIE_API_KEY = environ.get("WYZIE_API_KEY", "").strip()
    # Per-user daily subtitle limits. Generous defaults: attaching a subtitle
    # must never be the thing that fails on a movie night. The provider's own
    # key budget is usually the real ceiling (see _GLOBAL_REQUEST_LIMIT).
    WYZIE_USER_SEARCH_LIMIT = max(10, int(environ.get("WYZIE_USER_SEARCH_LIMIT", "150") or 150))
    WYZIE_USER_ATTACH_LIMIT = max(5, int(environ.get("WYZIE_USER_ATTACH_LIMIT", "60") or 60))
    WYZIE_ITEM_ATTACH_LIMIT = max(2, int(environ.get("WYZIE_ITEM_ATTACH_LIMIT", "12") or 12))
    WYZIE_GLOBAL_REQUEST_LIMIT = max(100, int(environ.get("WYZIE_GLOBAL_REQUEST_LIMIT", "5000") or 5000))

    BANNED_CHANNELS = list({int(x) for x in str(environ.get("BANNED_CHANNELS", "")).split()})
    BANNED_USERS = list({int(x) for x in str(environ.get("BANNED_USERS", "")).split()})

    # ── Leader / replica topology ────────────────────────────────────
    # NOTE: stream concurrency limits (MAX_STREAMS_TOTAL / MAX_STREAMS_PER_IP)
    # and the client-cooldown are PER-DEPLOYMENT process-local counters.
    # With N replicas the effective limit multiplies by N; scale the env
    # values down accordingly if per-IP caps must hold fleet-wide.
    # TRUSTED_PROXY_CIDRS must be set on EVERY deployment (a replica missing
    # it lumps all clients behind one rate-limit bucket).
    # Every deployment runs the full process (Pyrogram client + aiohttp
    # server), but only the leader runs Telegram update handlers and
    # singleton background jobs. Replicas are stateless HTTP/stream
    # nodes: they resolve the same BIN_CHANNEL messages and share the
    # same Mongo cluster, so any link generated by the leader streams
    # from any replica.
    #   ROLE=leader                — handlers + background jobs (one deployment)
    #   ROLE=replica               — HTTP serving only; Telegram updates
    #                                are dropped at the handler level.
    ROLE = str(environ.get("ROLE", "leader")).strip().lower()
    IS_LEADER = ROLE == "leader"
    # Public base URLs of replica deployments, comma-separated. The leader
    # round-robins stream/watch links across them so bandwidth is spread
    # over every free-tier account. Example:
    #   REPLICA_URLS=https://a.koyeb.app,https://b.koyeb.app
    # The leader's own Var.URL must NOT be listed; it is always included
    # as the first pool entry. Replicas ignore this var.
    # Entries must be http(s) URLs — they are embedded into Telegram
    # buttons and HTML reply text, so anything else is rejected at boot
    # rather than interpolated verbatim.
    REPLICA_URLS = []
    for _u in environ.get("REPLICA_URLS", "").split(","):
        _u = _u.strip()
        if not _u:
            continue
        _parts = urllib.parse.urlsplit(_u)
        if _parts.scheme not in ("http", "https") or not _parts.netloc:
            raise RuntimeError(
                f"Invalid REPLICA_URLS entry {_u!r}: must be an absolute "
                "http(s) URL, e.g. https://replica.example.com"
            )
        REPLICA_URLS.append(_u.rstrip("/") + "/")

    # The leader's public base URL, as replicas see it. Required for
    # ROLE=replica deployments that should push catalogue edits (admin
    # record cleanups made while signed into the replica) back to the
    # leader's in-memory catalogue: the replica POSTs a signed nudge to
    # <LEADER_URL>internal/catalogue-refresh and the leader pulls the
    # changed rows from Mongo. Optional — without it, replica edits still
    # reach Mongo and propagate on the next sweep/restart.
    LEADER_URL = ""
    _leader_raw = environ.get("LEADER_URL", "").strip()
    if _leader_raw:
        _parts = urllib.parse.urlsplit(_leader_raw)
        if _parts.scheme not in ("http", "https") or not _parts.netloc:
            raise RuntimeError(
                f"Invalid LEADER_URL entry {_leader_raw!r}: must be an absolute "
                "http(s) URL, e.g. https://leader.example.com"
            )
        LEADER_URL = _leader_raw.rstrip("/") + "/"

    # Optional user-account session string for grabbing media from protected
    # (copy/forward-restricted) channels. Generate via /gensession command.
    # Use a SEPARATE api_id/api_hash from the bot to avoid Telegram flagging
    # the login — create one at my.telegram.org → App configuration.
    USER_SESSION = environ.get("USER_SESSION", "").strip()
    USER_API_ID = int(environ.get("USER_API_ID", "0") or "0")
    USER_API_HASH = environ.get("USER_API_HASH", "").strip()

    # ── Photos (TeleDirect Photos) ───────────────────────────────────
    # Personal photo-library feature: users back up photos/videos to their
    # own private Telegram channel (bot as admin), browsable in the SPA.
    PHOTOS_ENABLED = str(environ.get("PHOTOS_ENABLED", "true")).lower() in ("1", "true", "yes")
    # Feature is in beta until it soaks; the SPA shows a badge off this.
    PHOTOS_BETA = True
    # Runaway guardrail, not a product limit — generous for personal use.
    PHOTOS_PER_USER_CAP = max(100, int(environ.get("PHOTOS_PER_USER_CAP", "50000") or 50000))
    # Web-upload guardrails per POST /api/photos/upload request.
    PHOTOS_UPLOAD_MAX_FILES = max(1, int(environ.get("PHOTOS_UPLOAD_MAX_FILES", "50") or 50))
    PHOTOS_UPLOAD_MAX_TOTAL = max(
        1, int(environ.get("PHOTOS_UPLOAD_MAX_TOTAL", str(2 * 1024 * 1024 * 1024))) or 2 * 1024 * 1024 * 1024
    )
    # Per-file memory bound: each concurrent upload request buffers one
    # file at a time, so this caps peak RAM per in-flight request.
    PHOTOS_UPLOAD_MAX_FILE = max(
        1, int(environ.get("PHOTOS_UPLOAD_MAX_FILE", str(200 * 1024 * 1024))) or 200 * 1024 * 1024
    )
    # Thumbnail long edge (px) for the two generated webp sizes.
    PHOTO_THUMB_GRID = max(100, int(environ.get("PHOTO_THUMB_GRID", "400") or 400))
    PHOTO_THUMB_PREVIEW = max(400, int(environ.get("PHOTO_THUMB_PREVIEW", "1600") or 1600))
    # Concurrent decode/EXIF/ffmpeg jobs. Ingest runs one worker per bound
    # channel and the thumb route regenerates on demand, so without a cap
    # every call lands on the default executor and that many users can run
    # that many Pillow/ffmpeg jobs, each holding a decoded image in RAM.
    PHOTOS_PIPELINE_WORKERS = max(1, int(environ.get("PHOTOS_PIPELINE_WORKERS", "2") or 2))
    # Simultaneously-buffered originals. A whole photo/video is held in RAM
    # from download through thumbnail generation, and there is one ingest
    # worker per bound channel — this is the cross-channel memory bound.
    PHOTOS_FETCH_CONCURRENCY = max(1, int(environ.get("PHOTOS_FETCH_CONCURRENCY", "2") or 2))
