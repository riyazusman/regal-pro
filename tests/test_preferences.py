"""Unit tests for user preferences management and serialization."""

import unittest
from core.preferences import (
    DEFAULT_PREFERENCES,
    decode_preferences,
    encode_preferences,
    get_default_preferences,
    is_movie_watched,
    sanitize_preferences,
    set_home_theater,
    toggle_favorite_theater,
    toggle_watched_movie,
    update_filter_defaults,
    update_scheduler_defaults,
)


class TestPreferences(unittest.TestCase):
    def test_default_preferences(self):
        prefs = get_default_preferences()
        self.assertIsNone(prefs["home_theater"])
        self.assertEqual(prefs["favorite_theaters"], [])
        self.assertEqual(prefs["watched_movies"], [])
        self.assertTrue(prefs["filters"]["hide_past"])
        self.assertFalse(prefs["filters"]["new_only"])
        self.assertFalse(prefs["filters"]["hide_watched"])
        self.assertFalse(prefs["filters"]["only_here"])
        self.assertEqual(prefs["scheduler"]["buffer"], 15)
        self.assertEqual(prefs["scheduler"]["gap_cap"], 120)
        self.assertTrue(prefs["scheduler"]["unlimited"])
        self.assertFalse(prefs["scheduler"]["exclude_watched"])

    def test_encode_and_decode_roundtrip(self):
        prefs = get_default_preferences()
        prefs = set_home_theater(prefs, "1487")
        prefs = toggle_favorite_theater(prefs, "1487")
        prefs = toggle_favorite_theater(prefs, "1794")
        prefs = toggle_watched_movie(prefs, "HO00022597")
        prefs = update_filter_defaults(prefs, hide_past=False, new_only=True, hide_watched=True, only_here=True)
        prefs = update_scheduler_defaults(prefs, buffer=25, gap_cap=150, unlimited=False, exclude_watched=True)

        encoded = encode_preferences(prefs)
        self.assertIsInstance(encoded, str)
        # Should be valid ASCII and not contain quotes or spaces
        self.assertTrue(" " not in encoded and "'" not in encoded and '"' not in encoded)

        decoded = decode_preferences(encoded)
        self.assertEqual(decoded["home_theater"], "1487")
        self.assertEqual(decoded["favorite_theaters"], ["1487", "1794"])
        self.assertEqual(decoded["watched_movies"], ["HO00022597"])
        self.assertFalse(decoded["filters"]["hide_past"])
        self.assertTrue(decoded["filters"]["new_only"])
        self.assertTrue(decoded["filters"]["hide_watched"])
        self.assertTrue(decoded["filters"]["only_here"])
        self.assertEqual(decoded["scheduler"]["buffer"], 25)
        self.assertEqual(decoded["scheduler"]["gap_cap"], 150)
        self.assertFalse(decoded["scheduler"]["unlimited"])
        self.assertTrue(decoded["scheduler"]["exclude_watched"])

    def test_decode_invalid_and_corrupt_data(self):
        # None or empty string returns defaults
        self.assertEqual(decode_preferences(None), DEFAULT_PREFERENCES)
        self.assertEqual(decode_preferences(""), DEFAULT_PREFERENCES)
        self.assertEqual(decode_preferences("random_gibberish_12345"), DEFAULT_PREFERENCES)

        # Partial dictionary merges cleanly
        partial_json = '{"home_theater": "1487", "watched_movies": ["M1"]}'
        decoded = decode_preferences(partial_json)
        self.assertEqual(decoded["home_theater"], "1487")
        self.assertEqual(decoded["watched_movies"], ["M1"])
        self.assertTrue(decoded["filters"]["hide_past"])
        self.assertEqual(decoded["scheduler"]["buffer"], 15)

    def test_toggles(self):
        prefs = get_default_preferences()
        self.assertFalse(is_movie_watched(prefs, "M100"))
        
        # Toggle on
        prefs = toggle_watched_movie(prefs, "M100")
        self.assertTrue(is_movie_watched(prefs, "M100"))
        self.assertIn("M100", prefs["watched_movies"])

        # Toggle off
        prefs = toggle_watched_movie(prefs, "M100")
        self.assertFalse(is_movie_watched(prefs, "M100"))
        self.assertNotIn("M100", prefs["watched_movies"])

        # Favorite theater toggle
        prefs = toggle_favorite_theater(prefs, "1487")
        self.assertIn("1487", prefs["favorite_theaters"])
        prefs = toggle_favorite_theater(prefs, "1487")
        self.assertNotIn("1487", prefs["favorite_theaters"])

    def test_update_filter_defaults(self):
        prefs = get_default_preferences()
        self.assertFalse(prefs["filters"]["hide_watched"])
        self.assertFalse(prefs["filters"]["only_here"])
        updated = update_filter_defaults(prefs, hide_watched=True, only_here=True)
        self.assertTrue(updated["filters"]["hide_watched"])
        self.assertTrue(updated["filters"]["only_here"])
        updated2 = update_filter_defaults(updated, hide_watched=False, only_here=False)
        self.assertFalse(updated2["filters"]["hide_watched"])
        self.assertFalse(updated2["filters"]["only_here"])

    def test_sanitize_bounds(self):
        prefs = {
            "home_theater": 1487,
            "scheduler": {
                "buffer": 9999,  # clamped to 60
                "gap_cap": -50,  # clamped to 0
            }
        }
        sanitized = sanitize_preferences(prefs)
        self.assertEqual(sanitized["home_theater"], "1487")
        self.assertEqual(sanitized["scheduler"]["buffer"], 60)
        self.assertEqual(sanitized["scheduler"]["gap_cap"], 0)


if __name__ == "__main__":
    unittest.main()
