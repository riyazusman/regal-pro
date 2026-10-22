"""Unit test suite for itinerary sharing and plan compression."""

import unittest
from datetime import datetime
from core.share_utils import (
    serialize_showing,
    deserialize_showing,
    encode_plan_token,
    decode_plan_token,
    format_single_day_summary,
    format_multi_day_summary,
    build_plan_share_url,
    build_search_share_url,
)


class TestShareUtils(unittest.TestCase):
    def setUp(self):
        self.showing1 = {
            "master_code": "0342",
            "Title": "Dune: Part Two",
            "TheaterCode": "1673",
            "Showtime": datetime(2026, 10, 24, 13, 0),
            "Duration": 166,
            "ScreenType": "IMAX",
            "Auditorium": "1",
        }
        self.showing2 = {
            "master_code": "0512",
            "Title": "Gladiator II",
            "TheaterCode": "1673",
            "Showtime": datetime(2026, 10, 24, 16, 15),
            "Duration": 150,
            "ScreenType": "RPX",
            "Auditorium": "4",
        }
        self.name_map = {"1673": "Regal UA King of Prussia"}

    def test_showing_serialization_roundtrip(self):
        """Showings serialize to compact dicts and deserialize cleanly."""
        compact = serialize_showing(self.showing1, self.name_map)
        self.assertEqual(compact["mc"], "0342")
        self.assertEqual(compact["tn"], "Regal UA King of Prussia")

        restored = deserialize_showing(compact)
        self.assertEqual(restored["master_code"], "0342")
        self.assertEqual(restored["Title"], "Dune: Part Two")
        self.assertEqual(restored["Showtime"], datetime(2026, 10, 24, 13, 0))
        self.assertEqual(restored["Duration"], 166)
        self.assertEqual(restored["ScreenType"], "IMAX")
        self.assertEqual(restored["Auditorium"], "1")

    def test_plan_token_roundtrip(self):
        """Full itinerary payloads compress into short tokens and decompress identically."""
        plan_data = {
            "v": 1,
            "label": "Smart Marathon (Best Efficiency)",
            "date": "10-24-2026",
            "primary_code": "1673",
            "shows": [
                serialize_showing(self.showing1, self.name_map),
                serialize_showing(self.showing2, self.name_map),
            ],
            "cfg": {
                "theaters": ["1673"],
                "days": ["10-24-2026"],
                "movies": ["0342", "0512"],
                "start": "13:00",
                "end": "22:00",
                "buff": 10,
                "g_cap": 90,
                "unlimited": True,
                "fudge": False,
                "b_after": None,
                "b_val": 0,
                "fmts": ["IMAX", "RPX"],
                "strat": "Minimize Days",
                "max_day": 2,
            },
        }
        token = encode_plan_token(plan_data)
        self.assertIsInstance(token, str)
        self.assertLess(len(token), 500)

        decoded = decode_plan_token(token)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["v"], 1)
        self.assertEqual(decoded["label"], "Smart Marathon (Best Efficiency)")
        self.assertEqual(len(decoded["shows"]), 2)
        self.assertEqual(decoded["shows"][0]["mc"], "0342")
        self.assertEqual(decoded["cfg"]["buff"], 10)
        self.assertEqual(decoded["cfg"]["movies"], ["0342", "0512"])

    def test_corrupted_token(self):
        """Invalid, random, or tampered tokens return None safely."""
        self.assertIsNone(decode_plan_token(None))
        self.assertIsNone(decode_plan_token(""))
        self.assertIsNone(decode_plan_token("invalid_garbage_token!@#$"))

    def test_format_single_day_summary(self):
        """Summary text contains movie titles, times, auditoriums, and gap times."""
        summary = format_single_day_summary(
            [self.showing1, self.showing2],
            label="Smart Marathon",
            date_display="Saturday, Oct 24",
            theater_name_map=self.name_map,
        )
        self.assertIn("Dune: Part Two", summary)
        self.assertIn("Gladiator II", summary)
        self.assertIn("1:00 PM", summary)
        self.assertIn("Audi 1", summary)
        self.assertIn("IMAX", summary)
        self.assertIn("Regal UA King of Prussia", summary)

    def test_format_multi_day_summary(self):
        """Multi-day summary includes headers for each day and movie details."""
        multi_dict = {
            "10-24-2026": [self.showing1],
            "10-25-2026": [self.showing2],
        }
        summary = format_multi_day_summary(
            multi_dict,
            label="Full Weekend Marathon",
            theater_name_map=self.name_map,
        )
        self.assertIn("Full Weekend Marathon", summary)
        self.assertIn("Saturday, Oct 24", summary)
        self.assertIn("Sunday, Oct 25", summary)
        self.assertIn("Dune: Part Two", summary)
        self.assertIn("Gladiator II", summary)

    def test_url_builders(self):
        """URLs format with clean query parameters."""
        plan_url = build_plan_share_url("https://regalpro.app", "test_tok", "1673", "10-24-2026")
        self.assertIn("tab=scheduler", plan_url)
        self.assertIn("plan=test_tok", plan_url)
        self.assertIn("theater=1673", plan_url)

        search_url = build_search_share_url(
            "https://regalpro.app",
            "1673",
            "10-24-2026",
            target_movies=["0342", "0512"],
            target_theaters=["1673", "1042"],
        )
        self.assertIn("targets=0342%2C0512", search_url)
        self.assertIn("sched_theaters=1673%2C1042", search_url)

    def test_day_plan_isolation(self):
        """Day plan encoding strictly isolates that day's theaters, dates, and movies."""
        day_path = [self.showing1]
        day_theaters = list(dict.fromkeys([s['TheaterCode'] for s in day_path]))
        day_movies = [s['master_code'] for s in day_path]
        
        plan_data = {
            "v": 1,
            "label": "Day Plan (Saturday, Oct 24)",
            "date": "10-24-2026",
            "primary_code": day_theaters[0],
            "shows": [serialize_showing(s, self.name_map) for s in day_path],
            "cfg": {
                "theaters": day_theaters,
                "days": ["10-24-2026"],
                "movies": day_movies,
                "strat": "Minimize Days",
                "max_day": len(day_path),
            }
        }
        token = encode_plan_token(plan_data)
        decoded = decode_plan_token(token)
        self.assertEqual(decoded["cfg"]["theaters"], ["1673"])
        self.assertEqual(decoded["cfg"]["days"], ["10-24-2026"])
        self.assertEqual(decoded["cfg"]["movies"], ["0342"])
        self.assertEqual(decoded["primary_code"], "1673")


if __name__ == "__main__":
    unittest.main()
