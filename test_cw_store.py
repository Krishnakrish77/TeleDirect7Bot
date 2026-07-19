"""Self-check for cw_store._accept_write (cross-device rewind guard).
Run: python test_cw_store.py"""
import os

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "x")
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("BIN_CHANNEL", "-100")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import cw_store


def _run():
    accept = cw_store._accept_write

    # first write always wins
    assert accept(None, {"t": 100, "pos": 10, "started_at": 100}) is True

    # older-or-equal timestamp loses (recency wins)
    cur = {"t": 200, "pos": 100, "started_at": 150}
    assert accept(cur, {"t": 150, "pos": 120, "started_at": 150}) is False
    assert accept(cur, {"t": 200, "pos": 120, "started_at": 150}) is False

    # forward progress from a newer write is accepted
    assert accept(cur, {"t": 300, "pos": 120, "started_at": 150}) is True

    # STALE rewind: newer t (clock skew) but lower pos from an OLDER session -> reject
    assert accept(cur, {"t": 300, "pos": 40, "started_at": 120}) is False

    # intentional rewind on the SAME session (same started_at) -> accepted
    assert accept(cur, {"t": 300, "pos": 40, "started_at": 150}) is True

    # intentional rewind on a NEWER session (you're actively on this device) -> accepted
    assert accept(cur, {"t": 300, "pos": 40, "started_at": 260}) is True

    # small backward jitter within grace -> accepted even from an older session
    assert accept(cur, {"t": 300, "pos": 97, "started_at": 120}) is True

    print("cw_store._accept_write self-check OK")


if __name__ == "__main__":
    _run()
