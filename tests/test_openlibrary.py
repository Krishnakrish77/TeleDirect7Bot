import os

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils.openlibrary import cover_url, normalise_search_doc


def test_normalise_search_result_and_client_round_trip():
    raw = {
        "key": "/works/OL12345W",
        "title": "The Example Book",
        "author_name": ["Ada Author", "Ben Writer"],
        "first_publish_year": 1999,
        "cover_i": 42,
        "isbn": ["9781234567890"],
        "publisher": ["Example Press"],
        "language": ["eng"],
        "number_of_pages_median": 321,
        "first_sentence": "An opening sentence.",
    }
    candidate = normalise_search_doc(raw)

    assert candidate == {
        "key": "/works/OL12345W", "title": "The Example Book",
        "authors": ["Ada Author", "Ben Writer"], "year": 1999,
        "coverId": 42, "isbn": "9781234567890", "publisher": "Example Press",
        "language": "eng", "pageCount": 321, "description": "An opening sentence.",
    }
    assert normalise_search_doc(candidate) == candidate
    assert cover_url(42) == "https://covers.openlibrary.org/b/id/42-L.jpg"


def test_normalise_rejects_non_work_keys():
    assert normalise_search_doc({"key": "/books/OL12M", "title": "Nope"}) is None
    assert normalise_search_doc("not a result") is None
