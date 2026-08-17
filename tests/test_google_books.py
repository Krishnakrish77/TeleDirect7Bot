import os

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")

from main.utils.google_books import normalise_search_doc, normalise_volume


def test_normalises_google_volume_and_validates_round_trip():
    candidate = normalise_volume({"id": "abc_123", "volumeInfo": {
        "title": "Example Book", "authors": ["Ada Author"], "publishedDate": "2024-01-01",
        "publisher": "Example Press", "language": "en", "pageCount": 300,
        "description": "A description", "categories": ["Fiction"],
        "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9781234567890"}],
        "imageLinks": {"thumbnail": "https://example.test/cover.jpg"},
    }})
    assert candidate is not None
    assert candidate["key"] == "google:abc_123"
    assert candidate["coverUrl"] == "google-books:abc_123"
    assert normalise_search_doc(candidate) == candidate


def test_rejects_non_google_candidate():
    assert normalise_search_doc({"key": "/works/OL123W", "title": "Nope"}) is None
