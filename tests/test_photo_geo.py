"""Photos geo search — query-builder geometry, lat/lon parsing, and the
geocode bucket/label helpers. Pure tests: no Mongo, no network (the
Nominatim fetch path is mocked at the HTTP boundary).
"""
import os
import unittest
from unittest.mock import patch, AsyncMock

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("BIN_CHANNEL", "-1001")
os.environ.setdefault("OWNER_ID", "1")

from main.utils.geocode import bucket_key, _place_label
from main.utils.photo_store import build_timeline_query, _parse_latlon


class ParseLatLonTest(unittest.TestCase):
    def test_valid_pair(self):
        self.assertEqual(_parse_latlon("38.7, -9.1"), (38.7, -9.1))

    def test_garbage_forms_return_none(self):
        self.assertIsNone(_parse_latlon(""))
        self.assertIsNone(_parse_latlon("not coords"))
        self.assertIsNone(_parse_latlon("1,2,3"))
        self.assertIsNone(_parse_latlon("abc,def"))
        self.assertIsNone(_parse_latlon("91,0"))    # lat out of range
        self.assertIsNone(_parse_latlon("0,181"))   # lon out of range


class NearQueryTest(unittest.TestCase):
    def test_near_builds_center_sphere(self):
        q = build_timeline_query(7, near="38.7,-9.1", radius_km=25)
        sphere = q["location"]["$geoWithin"]["$centerSphere"]
        # GeoJSON order: [lon, lat]; radius in radians (25 / 6378.1).
        self.assertEqual(sphere[0], [-9.1, 38.7])
        self.assertAlmostEqual(sphere[1], 25 / 6378.1)

    def test_near_without_radius_omits_clause(self):
        self.assertNotIn("location", build_timeline_query(7, near="38.7,-9.1"))

    def test_bad_near_omits_clause(self):
        self.assertNotIn("location", build_timeline_query(7, near="garbage", radius_km=10))

    def test_near_composes_with_other_filters(self):
        q = build_timeline_query(7, near="0,0", radius_km=5, kind="photo", q="sunset")
        self.assertEqual(q["kind"], "photo")
        self.assertIn("$text", q)
        self.assertIn("location", q)


class GeocodeBucketTest(unittest.TestCase):
    def test_rounding_shares_buckets(self):
        # ~200 m apart — same 2-decimal bucket, one cached lookup.
        self.assertEqual(bucket_key(38.7123, -9.1357), bucket_key(38.7131, -9.1361))
        self.assertEqual(bucket_key(38.71, -9.13), "38.71,-9.13")


class PlaceLabelTest(unittest.TestCase):
    def test_prefers_city_then_country(self):
        self.assertEqual(
            _place_label({"address": {"city": "Lisbon", "country": "Portugal"}}),
            "Lisbon, Portugal",
        )
        self.assertEqual(
            _place_label({"address": {"town": "Sintra", "country": "Portugal"}}),
            "Sintra, Portugal",
        )

    def test_falls_back_to_display_name_parts(self):
        self.assertEqual(
            _place_label({"display_name": "Atlantic Ocean, Earth"}),
            "Atlantic Ocean, Earth",
        )

    def test_empty_on_garbage(self):
        self.assertEqual(_place_label(None), "")
        self.assertEqual(_place_label({}), "")


class ReverseGeocodeHttpTest(unittest.TestCase):
    def test_second_call_in_bucket_served_from_cache(self):
        import asyncio

        from main.utils import geocode

        geocode.reset_cache()
        calls = []

        async def fake_lookup(lat, lon):
            calls.append((lat, lon))
            return "Lisbon, Portugal"

        async def run():
            with patch.object(geocode, "_nominatim_lookup", side_effect=fake_lookup):
                first = await geocode.reverse_geocode(38.7123, -9.1357)
                # ~200 m away — same 2-decimal bucket, must NOT re-hit HTTP.
                second = await geocode.reverse_geocode(38.7131, -9.1361)
            return first, second

        first, second = asyncio.run(run())
        geocode.reset_cache()
        self.assertEqual(first, "Lisbon, Portugal")
        self.assertEqual(second, "Lisbon, Portugal")
        self.assertEqual(len(calls), 1, "bucket cache must serve the second call")

    def test_failure_is_not_cached_as_empty(self):
        import asyncio

        from main.utils import geocode

        geocode.reset_cache()
        calls = []

        async def flaky(lat, lon):
            calls.append(lat)
            return "" if len(calls) == 1 else "Lisbon, Portugal"

        async def run():
            with patch.object(geocode, "_nominatim_lookup", side_effect=flaky):
                first = await geocode.reverse_geocode(38.72, -9.13)
                second = await geocode.reverse_geocode(38.72, -9.13)
            return first, second

        first, second = asyncio.run(run())
        geocode.reset_cache()
        self.assertEqual(first, "")
        self.assertEqual(second, "Lisbon, Portugal", "transient failure must not pin an empty label")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
