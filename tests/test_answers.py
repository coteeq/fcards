import json
from pathlib import Path
import unittest

from fcard.reviews import answer_key, verdict


class AnswerTest(unittest.TestCase):
    def test_shared_client_python_matching_cases(self):
        cases = json.loads((Path(__file__).parent / "fixtures/answer_matches.json").read_text())
        for answer, expected, matches in cases:
            with self.subTest(answer=answer, expected=expected):
                self.assertEqual(verdict(answer, expected), "correct" if matches else "incorrect")

    def test_search_folding_is_not_used_for_grading(self):
        self.assertEqual(answer_key("Üß"), "uess")
        self.assertNotEqual(answer_key("u"), answer_key("ü"))
        self.assertNotEqual(answer_key("u"), answer_key("ue"))
