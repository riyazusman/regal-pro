"""Unit test suite for Regal Pro SchedulerEngine."""

import unittest
from datetime import date, datetime, time, timedelta

from core.constants import UNLIMITED_GAP_MINS
from core.models import SchedulerConstraints
from core.engine import SchedulerEngine


class TestSchedulerEngine(unittest.TestCase):
    def setUp(self):
        self.dummy_drive_map = {
            ("1001", "1002"): (15, 6.0),
            ("1002", "1001"): (15, 6.0),
        }

        def mock_drive_stats(code_a, code_b):
            if code_a == code_b:
                return 0, 0.0
            return self.dummy_drive_map.get((code_a, code_b), (20, 5.0))

        self.engine = SchedulerEngine(
            drive_stats_fn=mock_drive_stats,
            movie_meta={
                "M1": {"title": "Movie One", "rating": "PG-13"},
                "M2": {"title": "Movie Two", "rating": "R"},
                "M3": {"title": "Movie Three", "rating": "PG"},
            },
            theater_name_map={"1001": "Regal Main", "1002": "Regal North"},
        )
        self.today = date(2026, 10, 22)

    def test_unlimited_gap_enforcement(self):
        """Showings starting < 91 mins apart must be rejected when unlimited=True."""
        show1 = {
            "master_code": "M1",
            "Title": "Movie One",
            "TheaterCode": "1001",
            "Showtime": datetime.combine(self.today, time(13, 0)),
            "Duration": 80,  # ends at 14:20
            "ScreenType": "Standard",
        }
        # Starts at 14:25 (85 mins after show1 start, which is < 91 mins)
        show2_too_early = {
            "master_code": "M2",
            "Title": "Movie Two",
            "TheaterCode": "1001",
            "Showtime": datetime.combine(self.today, time(14, 25)),
            "Duration": 90,
            "ScreenType": "Standard",
        }
        # Starts at 14:35 (95 mins after show1 start, which is >= 91 mins)
        show2_valid = {
            "master_code": "M2",
            "Title": "Movie Two",
            "TheaterCode": "1001",
            "Showtime": datetime.combine(self.today, time(14, 35)),
            "Duration": 90,
            "ScreenType": "Standard",
        }

        params = SchedulerConstraints(
            theaters=["1001"],
            buffer=10,
            gap_cap=120,
            unlimited=True,
            fudge=False,
        )

        # Testing too early show: only show1 can be placed
        res_early = self.engine.find_itineraries(
            [], ["M1", "M2"], [show1, show2_too_early], params, self.today
        )
        self.assertTrue(all(len(path) == 1 for path in res_early))

        # Testing valid show: both show1 and show2 can be placed
        res_valid = self.engine.find_itineraries(
            [], ["M1", "M2"], [show1, show2_valid], params, self.today
        )
        two_movie_paths = [p for p in res_valid if len(p) == 2]
        self.assertEqual(len(two_movie_paths), 1)
        self.assertEqual(two_movie_paths[0][0]["master_code"], "M1")
        self.assertEqual(two_movie_paths[0][1]["master_code"], "M2")

    def test_fudge_factor(self):
        """A 5-minute overlap is allowed when fudge=True."""
        show1 = {
            "master_code": "M1",
            "Title": "Movie One",
            "TheaterCode": "1001",
            "Showtime": datetime.combine(self.today, time(12, 0)),
            "Duration": 120,  # ends at 14:00
            "ScreenType": "Standard",
        }
        # Starts at 14:00 (exact end of show1, buffer=5 normally requires 14:05)
        show2 = {
            "master_code": "M2",
            "Title": "Movie Two",
            "TheaterCode": "1001",
            "Showtime": datetime.combine(self.today, time(14, 0)),
            "Duration": 100,
            "ScreenType": "Standard",
        }

        # Without fudge, buffer=5 requires 14:05 -> should fail to chain
        params_no_fudge = SchedulerConstraints(
            theaters=["1001"],
            buffer=5,
            gap_cap=120,
            unlimited=False,
            fudge=False,
        )
        res_no_fudge = self.engine.find_itineraries(
            [], ["M1", "M2"], [show1, show2], params_no_fudge, self.today
        )
        self.assertTrue(all(len(p) < 2 for p in res_no_fudge))

        # With fudge=True, effective end is 13:55 + 5 min buffer = 14:00 -> should succeed
        params_fudge = SchedulerConstraints(
            theaters=["1001"],
            buffer=5,
            gap_cap=120,
            unlimited=False,
            fudge=True,
        )
        res_fudge = self.engine.find_itineraries(
            [], ["M1", "M2"], [show1, show2], params_fudge, self.today
        )
        two_movie_paths = [p for p in res_fudge if len(p) == 2]
        self.assertEqual(len(two_movie_paths), 1)

    def test_theater_hop_travel_buffer(self):
        """Switching theaters requires drive time + buffer."""
        show1 = {
            "master_code": "M1",
            "Title": "Movie One",
            "TheaterCode": "1001",
            "Showtime": datetime.combine(self.today, time(12, 0)),
            "Duration": 100,  # ends at 13:40
            "ScreenType": "Standard",
        }
        # 1001 -> 1002 takes 15 mins drive + 10 mins buffer = 25 mins minimum gap
        # 13:40 + 25m = 14:05
        show2_too_close = {
            "master_code": "M2",
            "Title": "Movie Two",
            "TheaterCode": "1002",
            "Showtime": datetime.combine(self.today, time(13, 55)),  # only 15m gap
            "Duration": 100,
            "ScreenType": "Standard",
        }
        show2_adequate = {
            "master_code": "M2",
            "Title": "Movie Two",
            "TheaterCode": "1002",
            "Showtime": datetime.combine(self.today, time(14, 10)),  # 30m gap
            "Duration": 100,
            "ScreenType": "Standard",
        }

        params = SchedulerConstraints(
            theaters=["1001", "1002"],
            buffer=10,
            gap_cap=120,
            unlimited=False,
            fudge=False,
        )

        res_close = self.engine.find_itineraries(
            [], ["M1", "M2"], [show1, show2_too_close], params, self.today
        )
        self.assertTrue(all(len(p) < 2 for p in res_close))

        res_adequate = self.engine.find_itineraries(
            [], ["M1", "M2"], [show1, show2_adequate], params, self.today
        )
        two_movie_paths = [p for p in res_adequate if len(p) == 2]
        self.assertEqual(len(two_movie_paths), 1)

    def test_anchored_search(self):
        """Anchored search must always include the anchor show in the results."""
        anchor = {
            "master_code": "M1",
            "Title": "Movie One",
            "TheaterCode": "1001",
            "Showtime": datetime.combine(self.today, time(13, 0)),
            "Duration": 100,
            "ScreenType": "Standard",
        }
        other_show = {
            "master_code": "M2",
            "Title": "Movie Two",
            "TheaterCode": "1001",
            "Showtime": datetime.combine(self.today, time(15, 0)),
            "Duration": 100,
            "ScreenType": "Standard",
        }
        params = SchedulerConstraints(
            theaters=["1001"],
            buffer=10,
            gap_cap=120,
            unlimited=False,
            fudge=False,
        )
        paths = self.engine.run_anchored_search(
            anchor, ["M1", "M2"], [anchor, other_show], params, self.today
        )
        for p in paths:
            self.assertTrue(any(s["master_code"] == "M1" for s in p))

    def test_path_scoring(self):
        """Score should accurately apply movie bonus, hop penalty, and gap penalty."""
        show1 = {
            "master_code": "M1",
            "TheaterCode": "1001",
            "Showtime": datetime.combine(self.today, time(12, 0)),
            "Duration": 100,
        }
        show2 = {
            "master_code": "M2",
            "TheaterCode": "1002",  # 1 hop, 6 miles
            "Showtime": datetime.combine(self.today, time(14, 0)),  # 20 min gap
            "Duration": 100,
        }
        # Score calculation:
        # count: 2 * 250 = 500
        # hops: 1 * 40 = -40
        # miles: 6.0 * 2 = -12
        # gap: 20 * 0.1 = -2
        # expected score = 500 - 40 - 12 - 2 = 446.0
        score = self.engine.calculate_path_score([show1, show2], primary_code="1001")
        self.assertEqual(score["count"], 2)
        self.assertEqual(score["hops"], 1)
        self.assertEqual(score["miles"], 6.0)
        self.assertEqual(score["gap"], 20)
        self.assertEqual(score["score"], 446.0)


if __name__ == "__main__":
    unittest.main()
