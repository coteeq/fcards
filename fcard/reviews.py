"""Personal queue and review persistence, independent of HTTP/session mechanics."""

import json

from .db import get_db, transaction
from .models import question_data
from .schedulers import Candidate, choose_question


class StaleReview(Exception):
    pass


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
        Candidate(
            row["id"], row["algorithm"] or default_algorithm,
            json.loads(row["state_json"]) if row["state_json"] is not None else None,
            row["due_at"], row["reviews"] or 0,
        )
        for row in rows
    ]


def select_question(deck_name, user_id, default_algorithm, registry, now, queue):
    pool = candidates(deck_name, user_id, default_algorithm)
    if any(c.algorithm not in registry for c in pool):
        raise LookupError("A recorded scheduler is unavailable")
    previous = queue.get("question_id") if queue.get("deck_name") == deck_name else None
    last_algorithm = queue.get("algorithm") if queue.get("deck_name") == deck_name else None
    return choose_question(pool, registry, now, previous, last_algorithm)


def finalize(user_id, deck_name, pending, rating, control, registry, now):
    """Apply exactly one final verdict, rejecting old tabs and replayed cookies."""
    algorithm = pending["algorithm"]
    scheduler = registry[algorithm]
    ratings = {r.value: r for r in scheduler.ratings}
    if rating not in ratings:
        raise ValueError("Unknown rating")
    if control not in {None, *(c.value for c in scheduler.controls)}:
        raise ValueError("Unknown scheduler control")
    with transaction(get_db()) as db:
        data = question_data(pending["question_id"], deck_name)
        if data is None:
            raise StaleReview("The question no longer exists")
        if data["question"]["card_revision"] != pending["card_revision"]:
            raise StaleReview("This card was edited. Start a fresh question before reviewing.")
        old = db.execute(
            "SELECT * FROM progress WHERE user_id = ? AND question_id = ?",
            (user_id, pending["question_id"]),
        ).fetchone()
        revision = old["reviews"] if old else 0
        if revision != pending["revision"] or (old and old["algorithm"] != algorithm):
            raise StaleReview("This question was already reviewed in another tab")
        before = json.loads(old["state_json"]) if old else None
        auto = verdict(pending["answer"], data["expected"])
        updated = scheduler.review(before, rating, now, control)
        state_json = json.dumps(updated.state, separators=(",", ":"))
        lapses = (old["lapses"] if old else 0) + int(not ratings[rating].correct)
        db.execute(
            """INSERT INTO progress
               (user_id, question_id, algorithm, state_json, due_at,
                last_reviewed_at, reviews, lapses)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (user_id, question_id) DO UPDATE SET
                 algorithm = excluded.algorithm, state_json = excluded.state_json,
                 due_at = excluded.due_at, last_reviewed_at = excluded.last_reviewed_at,
                 reviews = excluded.reviews, lapses = excluded.lapses""",
            (user_id, pending["question_id"], algorithm, state_json,
             updated.due_at, now, revision + 1, lapses),
        )
        db.execute(
            """INSERT INTO review_log
               (user_id, question_id, reviewed_at, auto_verdict, rating, control,
                algorithm, before_state_json, after_state_json, due_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, pending["question_id"], now, auto, rating, control, algorithm,
             json.dumps(before, separators=(",", ":")), state_json, updated.due_at),
        )
    return updated
