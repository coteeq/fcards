from itertools import product
import unittest

from fcard.reviews import verdict
from fcard.search import matches_search, search_keys


class SearchNormalizationTest(unittest.TestCase):
    def test_requested_example_and_case_punctuation(self):
        for query in ("buck", "BÜCK", "bueck", "BUC-K", "b.ü.c.k", " b u c k "):
            with self.subTest(query=query):
                self.assertTrue(matches_search("die, Bückerei?", query))
        self.assertEqual(search_keys("die, Bückerei?"), ("diebueckerei", "diebuckerei"))

    def test_all_mixed_umlaut_spellings_match_in_both_directions(self):
        variants = [
            "".join(parts)
            for parts in product(("ä", "ae", "a"), ("ö", "oe", "o"), ("ü", "ue", "u"))
        ]
        for text, query in product(variants, repeat=2):
            with self.subTest(text=text, query=query):
                self.assertTrue(matches_search(text, query))

    def test_uppercase_and_decomposed_german_letters(self):
        for text in ("Ärger über Größen", "A\u0308rger u\u0308ber Gro\u0308ßen"):
            for query in ("arger uber grossen", "AERGER UEBER GROESSEN", "ÄRGER, ÜBER-GRÖẞEN"):
                with self.subTest(text=text, query=query):
                    self.assertTrue(matches_search(text, query))

    def test_eszett_and_ss_are_equivalent(self):
        for text, query in product(("Straße", "STRASSE", "straẞe"), repeat=2):
            self.assertTrue(matches_search(text, query))

    def test_regular_ascii_spelling_is_not_lost(self):
        for text, query in (("Duell", "ell"), ("aerobic", "erob"), ("queen", "een"),
                            ("coexist", "exist"), ("ice cream", "icecream"), ("x-42", "X42")):
            with self.subTest(text=text, query=query):
                self.assertTrue(matches_search(text, query))

    def test_empty_and_punctuation_only_queries_do_not_match_everything(self):
        for query in ("", " ,?! ", "_%", "☃", "中文"):
            self.assertFalse(matches_search("anything", query))
        self.assertFalse(matches_search("bread", "unrelated"))

    def test_search_does_not_relax_answer_grading(self):
        self.assertTrue(matches_search("Bückerei", "buckerei"))
        self.assertEqual(verdict("buckerei", "Bückerei"), "incorrect")
        self.assertEqual(verdict("Bückerei", "Bückerei?"), "incorrect")


if __name__ == "__main__":
    unittest.main()
