import logging
from os import environ
from dotenv import load_dotenv

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
    HAS_SSL = str(environ.get("HAS_SSL", "")).lower() == "true"
    NO_PORT = str(environ.get("NO_PORT", "")).lower() == "true"
    ON_KOYEB = "KOYEB_REGION" in environ
    FQDN = str(environ.get("FQDN") or environ.get("KOYEB_PUBLIC_DOMAIN") or BIND_ADDRESS)
    if ON_KOYEB:
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
    TMDB_API_KEY = environ.get("TMDB_API_KEY", "").strip()
    # Optional Gemini API key for thumbnail-based metadata suggestions in admin.
    # Free tier at aistudio.google.com — no credit card required.
    GEMINI_API_KEY = environ.get("GEMINI_API_KEY", "").strip()

    BANNED_CHANNELS = list({int(x) for x in str(environ.get("BANNED_CHANNELS", "")).split()})
    BANNED_USERS = list({int(x) for x in str(environ.get("BANNED_USERS", "")).split()})

    # Optional user-account session string for grabbing media from protected
    # (copy/forward-restricted) channels. Generate with any Pyrogram string
    # session script and set USER_SESSION in .env.
    USER_SESSION = environ.get("USER_SESSION", "").strip()
