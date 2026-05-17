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
    if "DYNO" in environ:
        ON_HEROKU = True
        APP_NAME = _require("APP_NAME")
    else:
        ON_HEROKU = False
    FQDN = (
        str(environ.get("FQDN", BIND_ADDRESS))
        if not ON_HEROKU or environ.get("FQDN")
        else APP_NAME + ".herokuapp.com"
    )
    if ON_HEROKU:
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

    BANNED_CHANNELS = list({int(x) for x in str(environ.get("BANNED_CHANNELS", "")).split()})
    BANNED_USERS = list({int(x) for x in str(environ.get("BANNED_USERS", "")).split()})
