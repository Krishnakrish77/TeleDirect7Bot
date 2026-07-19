"""Self-check for ai_rec pure helpers (grounding/dedup/cold-start).
Run: python test_ai_rec.py"""
import os

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "x")
os.environ.setdefault("BOT_TOKEN", "x")
os.environ.setdefault("BIN_CHANNEL", "-100")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import ai_rec


def _card(href, watch_key="", title="T", eyebrow="Movie"):
    return {"href": href, "watchKey": watch_key, "title": title, "eyebrow": eyebrow, "genres": ["Action"]}


def _run():
    # dedup by href + drop already-seen (by watchKey)
    payloads = [
        _card("/a", "k1"), _card("/a", "k1"),  # dup href
        _card("/b", "k2"),
        _card("/c", "seen"),                     # excluded
    ]
    deduped = ai_rec._dedup_payloads(payloads, {"seen"})
    hrefs = [p["href"] for p in deduped]
    assert hrefs == ["/a", "/b"], hrefs

    # index + apply_picks grounds against real ids and honours the limit
    index, prompt_items = ai_rec._index_candidates(deduped)
    assert [pi["id"] for pi in prompt_items] == ["c0", "c1"], prompt_items
    picks = [
        {"id": "c1", "reason": "you like B", "bucket": "discovery"},
        {"id": "c999", "reason": "hallucinated", "bucket": "comfort"},  # dropped
        {"id": "c0", "reason": "you like A", "bucket": "comfort"},
    ]
    items = ai_rec._apply_picks(picks, index, limit=10)
    assert [i["href"] for i in items] == ["/b", "/a"], items          # hallucinated id gone
    assert items[0]["recReason"] == "you like B" and items[0]["bucket"] == "discovery"
    assert items[1]["bucket"] == "comfort"

    # limit is respected
    assert len(ai_rec._apply_picks(picks, index, limit=1)) == 1

    # a bad bucket value normalises to comfort
    normed = ai_rec._apply_picks([{"id": "c0", "reason": "x", "bucket": "weird"}], index, 10)
    assert normed[0]["bucket"] == "comfort"

    # cold-start detection
    assert ai_rec._is_cold({}, {}, []) is True
    assert ai_rec._is_cold({"seeds": [(1, "movie")]}, {}, deduped * 3) is False

    print("ai_rec self-check OK")


if __name__ == "__main__":
    _run()
