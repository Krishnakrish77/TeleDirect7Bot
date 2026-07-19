"""Self-check for media_index.radio_tracks scoring/capping. Run: python test_radio_tracks.py"""
import os
from types import SimpleNamespace

# media_index import pulls in main.vars, which requires the bot's mandatory env.
os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "x")
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("BIN_CHANNEL", "-100")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import media_index as mi


def _item(mid, artist, genres=(), tags=(), hidden=False, kind="audio"):
    return SimpleNamespace(
        message_id=mid, artist=artist, tmdb_genres=list(genres),
        tags=list(tags), hidden=hidden, media_kind=kind,
        album_title="", track_number=0, title=f"t{mid}",
    )


def _run():
    seed = _item(1, "A", ["rock"], ["90s"])
    catalogue = {
        1: seed,
        2: _item(2, "A", ["rock"]),          # same artist  -> 4
        3: _item(3, "A", ["rock"]),          # same artist  -> 4
        4: _item(4, "A"),                    # same artist  -> 4
        5: _item(5, "A"),                    # same artist  -> 4
        6: _item(6, "B", ["rock"], ["90s"]),  # genre+tag    -> 3
        7: _item(7, "C", ["rock"]),          # genre        -> 2
        8: _item(8, "D", ["pop"]),           # no overlap   -> filler
        9: _item(9, "E", ["jazz"]),          # no overlap   -> filler
        10: _item(10, "F", ["metal"], hidden=True),  # hidden -> skipped
        11: _item(11, "G", ["rock"], kind="video"),  # not audio -> skipped
    }
    orig = mi._items
    mi._items = catalogue
    try:
        # Track radio, limit 5, exclude the seed itself.
        out = mi.radio_tracks(seed, None, {1}, limit=5)
        ids = [it.message_id for it in out]
        assert len(out) == 5, ids
        assert 1 not in ids, "seed must be excluded"
        assert sum(1 for it in out if it.artist == "A") == 3, ("artist cap", ids)
        assert 6 in ids and 7 in ids, "genre/tag matches must rank in"
        assert 10 not in ids and 11 not in ids, "hidden/non-audio skipped"

        # Explicit exclude is honored.
        out2 = mi.radio_tracks(seed, None, {1, 2, 3}, limit=5)
        ids2 = [it.message_id for it in out2]
        assert 2 not in ids2 and 3 not in ids2, ids2

        # Thin pool: pads with overflow + filler so the station doesn't die.
        out3 = mi.radio_tracks(seed, None, {1}, limit=8)
        ids3 = {it.message_id for it in out3}
        assert len(out3) == 8, ids3
        assert 8 in ids3 or 9 in ids3, "filler should top up a short station"

        # Artist radio derives the vibe from the artist's own tracks.
        out4 = mi.radio_tracks(None, "a", set(), limit=5)
        assert out4, "artist radio should return tracks"

        print("radio_tracks self-check OK")
    finally:
        mi._items = orig


if __name__ == "__main__":
    _run()
