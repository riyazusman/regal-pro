import unittest
from datetime import datetime
from core.date_utils import parse_and_sort_dates


class TestDateUtils(unittest.TestCase):

    def test_sort_month_day_strings(self):
        # The user's exact case: Nov 01, Oct 31 -> Oct 31, Nov 01
        ref = datetime(2026, 10, 20)
        dates = ["Nov 01", "Oct 31"]
        sorted_dates, earliest = parse_and_sort_dates(dates, reference_date=ref)
        self.assertEqual(sorted_dates, ["Oct 31", "Nov 01"])
        self.assertEqual(earliest, datetime(2026, 10, 31))

    def test_sort_year_boundary(self):
        ref = datetime(2026, 11, 15)
        dates = ["Jan 10", "Dec 25", "Nov 20"]
        sorted_dates, earliest = parse_and_sort_dates(dates, reference_date=ref)
        self.assertEqual(sorted_dates, ["Nov 20", "Dec 25", "Jan 10"])
        self.assertEqual(earliest, datetime(2026, 11, 20))

    def test_heterogeneous_formats(self):
        ref = datetime(2026, 10, 1)
        dates = ["10-25-2026", "Oct 20", datetime(2026, 10, 22), "2026-10-30"]
        sorted_dates, earliest = parse_and_sort_dates(dates, reference_date=ref)
        self.assertEqual(sorted_dates, ["Oct 20", "Oct 22", "Oct 25", "Oct 30"])

    def test_empty_and_invalid(self):
        sorted_dates, earliest = parse_and_sort_dates([])
        self.assertEqual(sorted_dates, [])
        self.assertEqual(earliest, datetime.max)


if __name__ == '__main__':
    unittest.main()
