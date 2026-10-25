"""Read scheduling candidates. Lesson completion owns all progress writes."""

import json
import unicodedata

from .db import get_db
from .schedulers import Candidate


def answer_key(text, *, kind=None, word_type=None):
    """Spelling equivalences and optional English infinitive marker for verb answers."""
    key = unicodedata.normalize("NFC", text).casefold().replace("ü", "ue")
    if kind == "back_to_front" and word_type == "verb":
        key = key.removeprefix("to ")
    return key


def verdict(answer, expected, *, kind=None, word_type=None):
    context = {"kind": kind, "word_type": word_type}
    return "correct" if answer_key(answer, **context) == answer_key(expected, **context) else "incorrect"


def candidates(deck_name, user_id, default_algorithm):
    rows = get_db().execute(
        """SELECT q.id, p.algorithm, p.state_json, p.due_at, p.reviews
           FROM questions q JOIN cards c ON c.id = q.card_id
           LEFT JOIN progress p ON p.question_id = q.id AND p.user_id = ?
           WHERE c.deck_name = ? ORDER BY q.id""",
        (user_id, deck_name),
    ).fetchall()
    return [
        Candidate(row["id"], row["algorithm"] or default_algorithm,
                  json.loads(row["state_json"]) if row["state_json"] is not None else None,
                  row["due_at"], row["reviews"] or 0)
        for row in rows
    ]
