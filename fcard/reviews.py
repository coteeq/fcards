"""Read scheduling candidates. Lesson completion owns all progress writes."""

import json

from .db import get_db
from .schedulers import Candidate


def verdict(answer, expected):
    return "correct" if answer.casefold() == expected.casefold() else "incorrect"


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
