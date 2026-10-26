import os
import unittest
from unittest.mock import patch, AsyncMock


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.utils import open_library


SEARCH_DOC = {
    "key": "/works/OL17358795W",
    "title": "Dark Matter",
    "author_name": ["Blake Crouch"],
    "first_publish_year": 2016,
    "cover_i": 7436634,
    "isbn": ["9781447297579", "1447297573"],
    "publisher": ["Orbit"],
    "language": ["eng"],
    "number_of_pages_median": "352",
    "first_sentence": "A story.",
    "subject": ["Fiction", "Thriller"],
}


class OpenLibraryNormaliseTest(unittest.TestCase):
    def test_normalises_search_doc(self):
        c = open_library.normalise_doc(SEARCH_DOC)
        self.assertEqual(c["key"], "openlibrary:OL17358795W")
        self.assertEqual(c["source"], "Open Library")
        self.assertEqual(c["title"], "Dark Matter")
        self.assertEqual(c["authors"], ["Blake Crouch"])
        self.assertEqual(c["year"], 2016)
        self.assertEqual(c["coverUrl"], "openlibrary-cover:7436634")
        self.assertEqual(c["isbn"], "9781447297579")  # prefers ISBN-13
        self.assertEqual(c["publisher"], "Orbit")
        self.assertEqual(c["language"], "eng")
        self.assertEqual(c["pageCount"], 352)
        self.assertEqual(c["description"], "A story.")
        self.assertEqual(c["subjects"], ["Fiction", "Thriller"])

    def test_rejects_invalid_docs(self):
        self.assertIsNone(open_library.normalise_doc(None))
        self.assertIsNone(open_library.normalise_doc({"title": "no key"}))
        self.assertIsNone(open_library.normalise_doc({"key": "/works/nope", "title": "bad id"}))
        self.assertIsNone(open_library.normalise_doc({"key": "/works/OL123W"}))  # no title

    def test_search_doc_round_trip(self):
        c = open_library.normalise_doc(SEARCH_DOC)
        self.assertEqual(open_library.normalise_search_doc(c), c)

    def test_search_doc_rejects_foreign_keys(self):
        self.assertIsNone(open_library.normalise_search_doc({"key": "google:abc", "title": "x"}))
        self.assertIsNone(open_library.normalise_search_doc({"key": "openlibrary:", "title": "x"}))

    def test_clean_isbn(self):
        self.assertEqual(open_library.clean_isbn("978-1-4472-9757-9"), "9781447297579")
        self.assertEqual(open_library.clean_isbn("1-44729-757-3"), "1447297573")
        self.assertEqual(open_library.clean_isbn("1234"), "")
        self.assertEqual(open_library.clean_isbn(""), "")
        self.assertEqual(open_library.clean_isbn(None), "")


class OpenLibraryNetworkTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_books_returns_normalised(self):
        payload = {"docs": [SEARCH_DOC, {"title": "no key"}]}
        async def fake_get(self, **kwargs):
            raise AssertionError("should use patched session")

        class FakeResponse:
            status = 200
            def raise_for_status(self): pass
            async def json(self, content_type=None): return payload
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        class FakeSession:
            def __init__(self, *a, **k): pass
            def get(self, url, params=None): return FakeResponse()
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        with patch.object(open_library.aiohttp, "ClientSession", FakeSession):
            items = await open_library.search_books("dark matter blake crouch")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["key"], "openlibrary:OL17358795W")

    async def test_search_books_swallows_network_errors(self):
        class BrokenSession:
            def __init__(self, *a, **k): pass
            def get(self, url, params=None): raise aiohttp_connect_error()
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        def aiohttp_connect_error():
            return open_library.aiohttp.ClientError("down")

        with patch.object(open_library.aiohttp, "ClientSession", BrokenSession):
            items = await open_library.search_books("anything at all")
        self.assertEqual(items, [])

    async def test_lookup_isbn_rejects_garbage_without_network(self):
        self.assertIsNone(await open_library.lookup_isbn("not-an-isbn"))
        self.assertIsNone(await open_library.lookup_isbn(""))


class BookFilenameParseTest(unittest.TestCase):
    def test_author_first(self):
        from main.utils.media_index import _parse_book_filename
        self.assertEqual(
            _parse_book_filename("Thomas Erikson - Surrounded by Idiots.epub"),
            ("Surrounded by Idiots", "Thomas Erikson"),
        )

    def test_title_first(self):
        from main.utils.media_index import _parse_book_filename
        self.assertEqual(
            _parse_book_filename("Dark Matter - Blake Crouch.epub"),
            ("Dark Matter", "Blake Crouch"),
        )

    def test_no_separator(self):
        from main.utils.media_index import _parse_book_filename
        self.assertEqual(_parse_book_filename("The Time Machine.epub"), ("The Time Machine", ""))

    def test_both_sides_name_like_prefers_second_as_author(self):
        from main.utils.media_index import _parse_book_filename
        self.assertEqual(
            _parse_book_filename("Surrounded by Idiots - Thomas Erikson.pdf"),
            ("Surrounded by Idiots", "Thomas Erikson"),
        )

    def test_subtitle_fallthrough(self):
        from main.utils.media_index import _parse_book_filename
        self.assertEqual(
            _parse_book_filename("Build a Large Language Model (From Scratch).pdf"),
            ("Build a Large Language Model (From Scratch)", ""),
        )

    def test_dash_glued_author(self):
        # Live BIN example: "An Echo of Oblivion -Sofi Aguilera.epub".
        from main.utils.media_index import _parse_book_filename
        self.assertEqual(
            _parse_book_filename("An Echo of Oblivion -Sofi Aguilera.epub"),
            ("An Echo of Oblivion", "Sofi Aguilera"),
        )

    def test_missing_extension(self):
        # Telegram documents can arrive without an extension.
        from main.utils.media_index import _parse_book_filename
        self.assertEqual(
            _parse_book_filename("Surrounded by Idiots - Thomas Erikson"),
            ("Surrounded by Idiots", "Thomas Erikson"),
        )

    def test_underscore_separated(self):
        # Underscores become spaces; the hyphen inside "Silo 1-3" survives.
        from main.utils.media_index import _parse_book_filename
        self.assertEqual(
            _parse_book_filename("The_Silo_Saga_Omnibus_Silo_1-3_Hugh_Howey.epub"),
            ("The Silo Saga Omnibus Silo 1-3 Hugh Howey", ""),
        )


def _hub_item(**kw):
    from main.utils.hub_query import HubItem
    base = dict(message_id=1, secure_hash="h", title="T", year=None,
                description="", tags=[], duration=0, file_size=0,
                has_thumb=False)
    base.update(kw)
    return HubItem(**base)


class BookEnrichmentGateTest(unittest.TestCase):
    _hub_item = staticmethod(_hub_item)

    def test_title_similarity(self):
        from main.utils.media_index import _title_similarity
        self.assertEqual(_title_similarity("Dark Matter", "Dark Matter"), 1.0)
        self.assertGreaterEqual(_title_similarity("Dark Matter", "dark  matter!"), 0.9)
        self.assertLess(_title_similarity("Dark Matter", "The Da Vinci Code"), 0.3)
        self.assertEqual(_title_similarity("", "Dark Matter"), 0.0)

    def test_book_needs_enrichment(self):
        from main.utils.media_index import _book_needs_enrichment

        self.assertTrue(_book_needs_enrichment(self._hub_item(file_name="t.epub", media_kind="book")))
        self.assertFalse(_book_needs_enrichment(self._hub_item(
            file_name="t.epub", media_kind="book",
            book_authors=["A"], book_cover_url="openlibrary-cover:1")))
        self.assertFalse(_book_needs_enrichment(self._hub_item(file_name="t.epub", media_kind="book", hidden=True)))
        self.assertFalse(_book_needs_enrichment(self._hub_item(media_kind="video")))

    def test_apply_book_candidate_fills_gaps_only(self):
        from main.utils.media_index import _apply_book_candidate
        from main.utils.hub_query import HubItem

        item = self._hub_item(title="Dark Matter", file_name="dm.epub", media_kind="book")
        candidate = {"key": "openlibrary:OL1W", "title": "Dark Matter", "authors": ["Blake Crouch"],
                     "year": 2016, "coverUrl": "openlibrary-cover:7", "isbn": "9781447297579",
                     "publisher": "Orbit", "language": "eng", "pageCount": 352,
                     "description": "A story.", "subjects": ["Fiction"]}
        self.assertTrue(_apply_book_candidate(item, candidate))
        self.assertEqual(item.book_authors, ["Blake Crouch"])
        self.assertEqual(item.book_cover_url, "openlibrary-cover:7")
        self.assertEqual(item.book_source_key, "openlibrary:OL1W")
        self.assertEqual(item.year, 2016)
        self.assertEqual(item.book_isbn, "9781447297579")

        # Idempotent: nothing left to fill.
        self.assertFalse(_apply_book_candidate(item, candidate))

    def test_apply_respects_admin_locked_title(self):
        from main.utils.media_index import _apply_book_candidate
        from main.utils.hub_query import HubItem

        item = self._hub_item(title="My Edition Title", file_name="dm.epub", media_kind="book", admin_locked=["title"])
        candidate = {"key": "openlibrary:OL1W", "title": "Dark Matter", "authors": ["Blake Crouch"],
                     "coverUrl": "openlibrary-cover:7"}
        self.assertTrue(_apply_book_candidate(item, candidate))
        self.assertEqual(item.title, "My Edition Title")
        self.assertEqual(item.book_authors, ["Blake Crouch"])

    def test_apply_ignores_dissimilar_title(self):
        from main.utils.media_index import _apply_book_candidate
        from main.utils.hub_query import HubItem

        item = self._hub_item(title="Totally Different Thing", file_name="x.epub", media_kind="book")
        candidate = {"key": "openlibrary:OL1W", "title": "Dark Matter", "authors": ["Blake Crouch"],
                     "coverUrl": "openlibrary-cover:7"}
        changed = _apply_book_candidate(item, candidate)
        # Fills gaps but does NOT adopt the dissimilar provider title.
        self.assertEqual(item.title, "Totally Different Thing")
        self.assertTrue(changed)


class BookEnrichSweepTest(unittest.IsolatedAsyncioTestCase):
    _hub_item = staticmethod(_hub_item)

    async def test_enrich_book_one_matches_via_gated_search(self):
        import main.utils.media_index as mi
        from main.utils.hub_query import HubItem

        item = self._hub_item(message_id=42, title="Dark Matter", file_name="Dark Matter - Blake Crouch.epub", media_kind="book")
        mi._items[42] = item
        try:
            candidate = {"key": "openlibrary:OL17358795W", "title": "Dark Matter",
                         "authors": ["Blake Crouch"], "year": 2016,
                         "coverUrl": "openlibrary-cover:7436634", "isbn": "9781447297579",
                         "publisher": "Orbit", "language": "eng", "pageCount": 352,
                         "description": "A story.", "subjects": ["Fiction"]}
            with patch("main.utils.open_library.lookup_isbn", new=AsyncMock(return_value=None)), \
                 patch("main.utils.open_library.search_books", new=AsyncMock(return_value=[candidate])), \
                 patch("main.utils.open_library.fetch_work_details", new=AsyncMock(return_value=None)), \
                 patch.object(mi, "_store_upsert", new=AsyncMock(return_value=None)):
                ok = await mi.enrich_book_one(42)
            self.assertTrue(ok)
            self.assertEqual(item.book_authors, ["Blake Crouch"])
            self.assertEqual(item.book_cover_url, "openlibrary-cover:7436634")
        finally:
            mi._items.pop(42, None)

    async def test_enrich_book_one_rejects_dissimilar_match(self):
        import main.utils.media_index as mi
        from main.utils.hub_query import HubItem

        item = self._hub_item(message_id=43, title="A Very Obscure Local Text", file_name="obscure.epub", media_kind="book")
        mi._items[43] = item
        try:
            wrong = {"key": "openlibrary:OL999W", "title": "Completely Unrelated Book",
                     "authors": ["Someone Else"], "coverUrl": "openlibrary-cover:9"}
            with patch("main.utils.open_library.lookup_isbn", new=AsyncMock(return_value=None)), \
                 patch("main.utils.open_library.search_books", new=AsyncMock(return_value=[wrong])), \
                 patch("main.utils.google_books.search_books", new=AsyncMock(return_value=[])):
                ok = await mi.enrich_book_one(43)
            self.assertFalse(ok)
            self.assertEqual(item.book_authors, [])
        finally:
            mi._items.pop(43, None)

    async def test_enrich_cleans_polluted_title_and_matches(self):
        # Live failure: item.title was the full filename stem
        # "Dark Matter - Blake Crouch", so the query duplicated the author
        # and the gate scored 0.61 < 0.82. It must now match AND the
        # stored title must lose the author suffix.
        import main.utils.media_index as mi

        item = self._hub_item(message_id=45, title="Dark Matter - Blake Crouch",
                              file_name="Dark Matter - Blake Crouch.epub", media_kind="book")
        mi._items[45] = item
        try:
            candidate = {"key": "openlibrary:OL17358795W", "title": "Dark Matter",
                         "authors": ["Blake Crouch"], "coverUrl": "openlibrary-cover:7436634"}
            with patch("main.utils.open_library.lookup_isbn", new=AsyncMock(return_value=None)), \
                 patch("main.utils.open_library.search_books", new=AsyncMock(return_value=[candidate])), \
                 patch("main.utils.open_library.fetch_work_details", new=AsyncMock(return_value=None)), \
                 patch("main.utils.google_books.search_books", new=AsyncMock(return_value=[])), \
                 patch.object(mi, "_store_upsert", new=AsyncMock(return_value=None)):
                ok = await mi.enrich_book_one(45)
            self.assertTrue(ok)
            self.assertEqual(item.title, "Dark Matter")
            self.assertEqual(item.book_authors, ["Blake Crouch"])
        finally:
            mi._items.pop(45, None)

    async def test_enrich_accepts_superset_title(self):
        # Live failure: provider "Silo Saga Omnibus" vs catalogue
        # "The Silo Saga Omnibus Silo 1-3" scored 0.74 and was rejected.
        # Token containment accepts it.
        import main.utils.media_index as mi

        item = self._hub_item(message_id=46, title="The Silo Saga Omnibus Silo 1-3",
                              file_name="The_Silo_Saga_Omnibus_Silo_1-3_Hugh_Howey.epub",
                              media_kind="book")
        mi._items[46] = item
        try:
            candidate = {"key": "openlibrary:OL21937089W", "title": "Silo Saga Omnibus",
                         "authors": ["Hugh Howey"], "coverUrl": "openlibrary-cover:15110955"}
            with patch("main.utils.open_library.lookup_isbn", new=AsyncMock(return_value=None)), \
                 patch("main.utils.open_library.search_books", new=AsyncMock(return_value=[candidate])), \
                 patch("main.utils.open_library.fetch_work_details", new=AsyncMock(return_value=None)), \
                 patch("main.utils.google_books.search_books", new=AsyncMock(return_value=[])), \
                 patch.object(mi, "_store_upsert", new=AsyncMock(return_value=None)):
                ok = await mi.enrich_book_one(46)
            self.assertTrue(ok)
            self.assertEqual(item.book_authors, ["Hugh Howey"])
            self.assertEqual(item.book_cover_url, "openlibrary-cover:15110955")
        finally:
            mi._items.pop(46, None)

    async def test_enrich_rejects_single_token_containment(self):
        # "Inference Engineering" must not match "Parallel inference engine"
        # off the shared "inference" token.
        import main.utils.media_index as mi

        item = self._hub_item(message_id=47, title="Inference Engineering",
                              file_name="Inference Engineering.epub", media_kind="book")
        mi._items[47] = item
        try:
            wrong = {"key": "openlibrary:OL8W", "title": "Parallel inference engine",
                     "authors": ["Someone"], "coverUrl": "openlibrary-cover:8"}
            with patch("main.utils.open_library.lookup_isbn", new=AsyncMock(return_value=None)), \
                 patch("main.utils.open_library.search_books", new=AsyncMock(return_value=[wrong])), \
                 patch("main.utils.google_books.search_books", new=AsyncMock(return_value=[])):
                ok = await mi.enrich_book_one(47)
            self.assertFalse(ok)
            self.assertEqual(item.book_authors, [])
        finally:
            mi._items.pop(47, None)

    async def test_enrich_books_all_reports_state(self):
        import main.utils.media_index as mi
        from main.utils.hub_query import HubItem

        item = self._hub_item(message_id=44, title="Dark Matter", file_name="Dark Matter - Blake Crouch.epub", media_kind="book")
        mi._items[44] = item
        try:
            candidate = {"key": "openlibrary:OL17358795W", "title": "Dark Matter",
                         "authors": ["Blake Crouch"], "coverUrl": "openlibrary-cover:7436634"}
            async def fake_one(mid):
                mi._items[mid].book_authors = candidate["authors"]
                return True
            with patch.object(mi, "enrich_book_one", fake_one):
                result = await mi.enrich_books_all()
            self.assertEqual(result["enriched"], 1)
            self.assertEqual(result["total"], 1)
            self.assertFalse(mi.book_enrichment_state()["running"])
        finally:
            mi._items.pop(44, None)


if __name__ == "__main__":
    unittest.main()
