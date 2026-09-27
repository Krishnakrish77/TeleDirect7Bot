"""Local photos test harness — runs the real web server + a FAKE Telegram.

Let's you exercise the whole Photos stack (connect, upload, ingest, timeline,
thumbs, resync) in a browser at localhost:8080 without Telegram credentials
or a Koyeb deploy.

    . .venv/bin/activate
    python scripts/photos_dev_server.py

What's faked: the Pyrogram client (send_document/get_messages return
in-memory stub messages). What's real: aiohttp routes, photo_store,
photo_pipeline (EXIF/sha256/thumbs run for real), Mongo (needs
STORE_BACKEND=mongo + MONGO_URI, or set MONGO_DB_NAME to a scratch db).

Auth: /api/photos/* requires a td_session JWT — the harness prints a
ready-made login URL that mintes one via /auth/telegram with a valid
signed payload (uses the real BOT_TOKEN hash, so set BOT_TOKEN to
anything like "1:testdev").
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:devharness")
os.environ.setdefault("BIN_CHANNEL", "-1000000000001")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("STORE_BACKEND", "mongo")
os.environ.setdefault("MONGO_DB", "teledirect_photos_dev")
os.environ.setdefault("JWT_SECRET", "dev" * 22)
os.environ.setdefault("PHOTOS_ENABLED", "true")

from aiohttp import web

FAKE_CHANNEL_ID = -1001234567890
_FAKE_FILE_SEQ = 0


class FakeMedia:
    def __init__(self, data: bytes, name: str, mime: str):
        self.file_id = f"fake-{name}-{id(data)}"
        self.file_unique_id = f"uniq-{name}-{_FAKE_FILE_SEQ}"
        self.file_size = len(data)
        self.mime_type = mime
        self.file_name = name
        self._data = data


class FakeMessage:
    """Duck-types enough of pyrogram Message for the photos paths."""

    def __init__(self, msg_id: int, data: bytes, name: str, mime: str, chat_id: int):
        global _FAKE_FILE_SEQ
        _FAKE_FILE_SEQ += 1
        self.id = msg_id
        self.chat = SimpleNamespace(id=chat_id)
        media = FakeMedia(data, name, mime)
        if mime.startswith("video/"):
            self.video = media
            self.document = None
        else:
            self.document = media
            self.video = None
        self.photo = None
        self.empty = False

    async def download(self, in_memory=True):
        media = self.document or self.video
        return io.BytesIO(media._data)


class FakeBot:
    """Stands in for StreamBot / multi_clients[0]."""

    def __init__(self):
        self.me = SimpleNamespace(id=777000, username="TeleDirect7Bot")
        self._next_id = 10
        self._messages: dict[int, FakeMessage] = {}
        self._members: dict[int, str] = {}

    async def get_me(self):
        return self.me

    async def get_chat(self, ref):
        # The harness pre-binds FAKE_CHANNEL_ID; any other ref resolves to it.
        return SimpleNamespace(
            id=FAKE_CHANNEL_ID,
            type=SimpleNamespace(value="channel", name="CHANNEL"),
            title="Dev Vault",
            username=None,  # private channel
        )

    async def get_chat_member(self, channel_id, user_id):
        status = self._members.get(user_id, "administrator")
        return SimpleNamespace(
            status=status,
            privileges=SimpleNamespace(can_post_messages=True),
        )

    async def send_document(self, chat_id, document):
        payload = document.read()
        name = getattr(document, "name", "upload.bin")
        mime = "image/jpeg" if name.lower().endswith((".jpg", ".jpeg")) else "application/octet-stream"
        msg = FakeMessage(self._next_id, payload, name, mime, chat_id)
        self._next_id += 1
        self._messages[msg.id] = msg
        return msg

    async def get_messages(self, chat_id, ids):
        if isinstance(ids, list):
            return [self._messages.get(i) for i in ids]
        return self._messages.get(ids)


def install_fake_bot():
    """Swap the real Pyrogram clients for the fake across the app."""
    import main.bot as bot_mod
    import main.server.stream_routes as stream_routes
    import main.server.photo_routes as photo_routes

    fake = FakeBot()
    # Mark the user as channel creator so connect verification passes.
    fake._members[1] = "creator"

    bot_mod.multi_clients[0] = fake
    bot_mod.work_loads[0] = 0
    photo_routes.StreamBot = fake
    # class_cache is a WeakKeyDictionary keyed by client — pre-seed not needed;
    # ByteStreamer is only used for original-byte streaming (skipped in harness:
    # thumbs come from the pipeline, originals can't stream without Telegram).

    # The catalogue store isn't running — photo_store reads media_index._store.
    # Harness uses its own MongoStore instance:
    from main.utils import photo_store as ps, store as store_mod

    async def _connect():
        uri = os.environ.get("MONGO_URI") or "mongodb://localhost:27017"
        store = store_mod.MongoStore(uri, os.environ["MONGO_DB"], "items", "meta")
        await store.init()
        ps.__dict__["_get_db_original"] = None
        # Inject: _get_db returns store._client[db]
        import main.utils.media_index as mi
        mi._store = store  # photo_store._get_db reads this
        print(f"[harness] Mongo connected: {os.environ['MONGO_DB']}")
        return store

    return fake, _connect


def _auth_payload(user_id: int = 1) -> dict:
    """Build a Telegram-login payload whose hash verifies against BOT_TOKEN."""
    import hashlib
    import hmac
    import time

    token = os.environ["BOT_TOKEN"]
    data = {
        "id": str(user_id),
        "first_name": "Dev",
        "username": "devuser",
        "auth_date": str(int(time.time())),
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hashlib.sha256(token.encode()).digest()
    data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return data


async def make_seed_login(app: web.Application):
    """Mint a session for user 1 via the real /auth/telegram endpoint."""
    from aiohttp import ClientSession

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{app._port}/auth/telegram", json=_auth_payload(1)
        ) as res:
            body = await res.json()
    return body.get("token")


def photos_routes_stub_streamer():
    """Original-byte streaming hits Telegram for real — not available in the
    harness. Thumb regeneration (generate_thumbs_for) gracefully returns None;
    the UI falls back to the empty-state. Nothing to do: ByteStreamer is only
    constructed on demand."""


async def main() -> None:
    fake_bot, connect_mongo = install_fake_bot()

    from main.server import web_server
    from main.__main__ import _photos_channel_reverify_loop  # noqa: F401

    app = web_app = web.Application(client_max_size=2_000_000_000)
    # Reuse the production web_server() but drop the SPA catch-all conflict:
    from main.server import __init__ as server_init  # noqa: F401

    app = web_server()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8081)
    await site.start()
    app._port = 8081

    await connect_mongo()

    # Pre-bind the fake channel to user 1 so connect flow is skippable —
    # or leave it unbound to exercise the wizard. Keep it bound for speed:
    from main.utils import photo_store as ps

    err = await ps.bind_channel(FAKE_CHANNEL_ID, 1, 1)
    print(f"[harness] pre-bound channel {FAKE_CHANNEL_ID}: {err or 'ok'}")

    token = await make_seed_login(app)
    print()
    print("=" * 60)
    print("TeleDirect Photos dev harness")
    print(f"  Open:  http://127.0.0.1:8081/photos")
    print(f"  Login: paste this in the browser console to set the session:")
    print(f"         document.cookie='td_session={token}'; location.reload()")
    print("  Upload via the UI; Mongo db:", os.environ["MONGO_DB"])
    print("=" * 60)
    print()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
