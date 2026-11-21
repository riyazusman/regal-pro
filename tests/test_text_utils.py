"""Unit test suite for text_utils movie title sanitization."""

import unittest
from core.text_utils import clean_movie_title, generate_regal_url, normalize_search_text


class TestTextUtils(unittest.TestCase):
    def test_leading_prefixes(self):
        """Format and curated event prefixes must be stripped."""
        self.assertEqual(clean_movie_title("IMAX: Dune: Part Two"), "Dune: Part Two")
        self.assertEqual(clean_movie_title("RPX: Gladiator II"), "Gladiator II")
        self.assertEqual(clean_movie_title("Popcorn Reef: Bloodsport"), "Bloodsport")
        self.assertEqual(clean_movie_title("Fangoria Staff Picks: Sinister"), "Sinister")

    def test_anniversary_tags(self):
        """Anniversary markers must be stripped."""
        self.assertEqual(clean_movie_title("Interstellar: 10th Anniversary"), "Interstellar")
        self.assertEqual(clean_movie_title("The Matrix: 25th Anniv."), "The Matrix")

    def test_early_access_and_fan_events(self):
        """Early access, fan events, and member previews must be stripped."""
        self.assertEqual(clean_movie_title("Wicked - Early Access Screening"), "Wicked")
        self.assertEqual(clean_movie_title("Moana 2 - Fan Event"), "Moana 2")
        self.assertEqual(clean_movie_title("Red One - Prime Member Screening"), "Red One")

    def test_live_qa_and_intros(self):
        """Live Q&A, livestream, and cast intros must be stripped."""
        self.assertEqual(clean_movie_title("Anora + Livestream Q&A"), "Anora")
        self.assertEqual(clean_movie_title("Conclave: Live Intro w/Cast"), "Conclave")
        self.assertEqual(clean_movie_title("Talking Heads: Live"), "Talking Heads")

    def test_film_formats_and_reissues(self):
        """3D, 4K, 70mm, and reissue tags must be stripped."""
        self.assertEqual(clean_movie_title("Avatar: The Way of Water 3D"), "Avatar: The Way of Water")
        self.assertEqual(clean_movie_title("Seven 4K Re-release"), "Seven")
        self.assertEqual(clean_movie_title("Oppenheimer: 70mm Film Reissue"), "Oppenheimer")
        self.assertEqual(clean_movie_title("Inception: Reissue"), "Inception")

    def test_parentheticals(self):
        """Parenthetical years or notes must be stripped."""
        self.assertEqual(clean_movie_title("Nosferatu (2024)"), "Nosferatu")
        self.assertEqual(clean_movie_title("Alien (Director's Cut)"), "Alien")

    def test_empty_and_none(self):
        """Empty and None inputs should safely return empty string."""
        self.assertEqual(clean_movie_title(""), "")
        self.assertEqual(clean_movie_title(None), "")

    def test_generate_regal_url(self):
        """Canonical Regal movie URL slug generation."""
        self.assertEqual(
            generate_regal_url("Dune: Part Two", "0342"),
            "https://www.regmovies.com/movies/dune-part-two-0342"
        )
        self.assertEqual(
            generate_regal_url("A Complete Unknown (2024)", "0512"),
            "https://www.regmovies.com/movies/a-complete-unknown-2024-0512"
        )
        self.assertEqual(
            generate_regal_url('Movie | "Quoted" <Tag>', "0999"),
            "https://www.regmovies.com/movies/movie-quoted-tag-0999"
        )
        self.assertEqual(generate_regal_url("", "0512"), "")
        self.assertEqual(generate_regal_url("Dune", None), "")

    def test_normalize_search_text(self):
        """Search query normalization."""
        self.assertEqual(normalize_search_text("Spider-Man: No Way Home!"), "spiderman no way home")
        self.assertEqual(normalize_search_text(""), "")
        self.assertEqual(normalize_search_text(None), "")


if __name__ == "__main__":
    unittest.main()
