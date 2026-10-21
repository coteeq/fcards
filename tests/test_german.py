import json
from pathlib import Path
import unittest

from fcard.german import classification, split_article


class GermanTest(unittest.TestCase):
    def test_shared_client_server_hints_and_articleless_prompts(self):
        cases = json.loads((Path(__file__).parent / "fixtures/german_hints.json").read_text())
        for case in cases:
            with self.subTest(text=case["text"]):
                self.assertEqual(classification(case["text"]), (case["type"], case["gender"]))
                self.assertEqual(split_article(case["text"])[1], case["word"])

    def test_explicit_choices_override_hints_and_unknown_suppresses_gender_question(self):
        self.assertEqual(classification("der Morgen", "noun", "feminine"), ("noun", "feminine"))
        self.assertEqual(classification("Morgen", "noun", "masculine"), ("noun", "masculine"))
        self.assertEqual(classification("der Morgen", "adjective"), ("adjective", None))
        self.assertEqual(classification("der gute Morgen", "noun"), ("noun", "masculine"))
        self.assertEqual(classification("der Morgen", None), (None, None))
        for unknown in (None, ""):
            self.assertEqual(classification("der Morgen", "noun", unknown), ("noun", None))

    def test_unknown_values_are_rejected_even_for_non_nouns(self):
        with self.assertRaisesRegex(ValueError, "word type"):
            classification("der Morgen", "adverb")
        with self.assertRaisesRegex(ValueError, "gender"):
            classification("gehen", "verb", "invalid")
