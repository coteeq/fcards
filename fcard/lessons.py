"""Finite, durable lessons. Sync stores final choices; only completion schedules them."""

import json
import uuid

from .db import get_db, transaction
from .models import question_data
from .reviews import candidates
from .schedulers import choose_question

LENGTH = 20


class LessonMissing(Exception):
    pass


class LessonConflict(Exception):
    pass


def owned(lesson_id, user_id):
    row = get_db().execute("SELECT * FROM lessons WHERE id=? AND user_id=?",
                           (lesson_id, user_id)).fetchone()
    if row is None:
        raise LessonMissing("Lesson not found.")
    return row


def status(row):
    return {key: row[key] for key in
            ("id", "deck_name", "revision", "completed_at", "skipped")} | {
                "state": json.loads(row["state_json"]), "length": LENGTH,
            }


def items(lesson_id):
    return get_db().execute("SELECT * FROM lesson_items WHERE lesson_id=? ORDER BY position",
                           (lesson_id,)).fetchall()


def load(lesson_id, user_id):
    row = owned(lesson_id, user_id)
    questions = [json.loads(item["snapshot_json"]) for item in items(lesson_id)]
    # Older snapshots predate the type hint. Recover it only from an unchanged
    # source card; never reinterpret a snapshot using a later edited classification.
    if any("word_type" not in q for q in questions):
        ids = list({q["card_id"] for q in questions})
        placeholders = ",".join("?" for _ in ids)
        cards = {c["id"]: c for c in get_db().execute(
            f"SELECT id, revision, word_type FROM cards WHERE id IN ({placeholders})", ids,
        )}
        for question in questions:
            if "word_type" not in question:
                card = cards.get(question["card_id"])
                question["word_type"] = (card["word_type"] if card is not None
                                         and card["revision"] == question["card_revision"] else None)
    return status(row) | {"questions": questions}


def unfinished(user_id):
    return get_db().execute(
        """SELECT id, deck_name, created_at, updated_at,
                  json_array_length(state_json, '$.ratings') AS answered
           FROM lessons WHERE user_id=? AND completed_at IS NULL
           ORDER BY updated_at DESC, id""", (user_id,),
    ).fetchall()


def create(deck_name, user_id, default_algorithm, registry, now):
    with transaction(get_db()) as db:
        pool = candidates(deck_name, user_id, default_algorithm)
        if any(c.algorithm not in registry for c in pool):
            raise LookupError("A recorded scheduler is unavailable. Restore it before creating a lesson.")
        pool = [c for c in pool if registry[c.algorithm].eligible(c, now)]
        if not pool:
            raise ValueError("No questions are available for a lesson in this deck yet.")
        lesson_id = str(uuid.uuid4())
        db.execute("INSERT INTO lessons (id, user_id, deck_name, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?, ?)", (lesson_id, user_id, deck_name, now, now))
        remaining = list(pool)
        previous = last_algorithm = None
        for position in range(LENGTH):
            if not remaining:
                remaining = list(pool)
            candidate = choose_question(remaining, registry, now, previous, last_algorithm)
            remaining.remove(candidate)
            previous, last_algorithm = candidate.question_id, candidate.algorithm
            data = question_data(candidate.question_id, deck_name)
            scheduler = registry[candidate.algorithm]
            snapshot = {
                "question_id": candidate.question_id, "card_id": data["question"]["card_id"],
                "card_revision": data["question"]["card_revision"], "kind": data["question"]["kind"],
                "word_type": data["question"]["word_type"],
                "label": data["label"], "prompt": data["prompt"], "expected": data["expected"],
                "expected_key": data["expected"].casefold(), "meta": data["question"]["meta"],
                "forms": {side: [{"text": f["text"], "description": f["description"]} for f in forms]
                          for side, forms in data["forms"].items()},
                "ratings": [{"value": r.value, "label": r.label, "correct": r.correct}
                            for r in scheduler.ratings],
                "controls": [{"value": c.value, "label": c.label} for c in scheduler.controls],
                "defaults": {v: scheduler.default_rating(v) for v in ("correct", "incorrect")},
            }
            db.execute("INSERT INTO lesson_items VALUES (?, ?, ?, ?, ?)",
                       (lesson_id, position, candidate.question_id, candidate.algorithm,
                        json.dumps(snapshot, ensure_ascii=False)))
    return lesson_id


def validate(payload, rows, finishing):
    if not isinstance(payload, dict) or set(payload) != {"revision", "state"}:
        raise ValueError("Supply revision and state only; raw answers are not stored.")
    revision, state = payload["revision"], payload["state"]
    if type(revision) is not int or not 0 <= revision < 2**63 - 1:
        raise ValueError("Invalid lesson revision.")
    if not isinstance(state, dict) or set(state) != {"cursor", "ratings"}:
        raise ValueError("Invalid lesson state.")
    cursor, ratings = state["cursor"], state["ratings"]
    if (type(cursor) is not int or not 0 <= cursor < LENGTH or not isinstance(ratings, list)
            or len(ratings) not in (cursor, cursor + 1) or len(ratings) > LENGTH):
        raise ValueError("Answer questions in lesson order.")
    if finishing and (cursor != LENGTH - 1 or len(ratings) != LENGTH):
        raise ValueError("Complete all 20 questions before ending the lesson.")
    for value, row in zip(ratings, rows):
        snapshot = json.loads(row["snapshot_json"])
        if (not isinstance(value, dict) or set(value) != {"rating", "control"}
                or not isinstance(value["rating"], str)
                or value["rating"] not in {r["value"] for r in snapshot["ratings"]}
                or value["control"] not in [None, *[c["value"] for c in snapshot["controls"]]]):
            raise ValueError("Unknown rating or scheduler control.")
    return revision, state


def apply(db, user_id, rows, ratings, registry, now):
    skipped = 0
    for row, choice in zip(rows, ratings):
        snapshot = json.loads(row["snapshot_json"])
        current = db.execute(
            "SELECT c.id, c.revision, q.kind FROM questions q JOIN cards c ON c.id=q.card_id "
            "WHERE q.id=?", (row["question_id"],),
        ).fetchone()
        old = db.execute("SELECT * FROM progress WHERE user_id=? AND question_id=?",
                         (user_id, row["question_id"])).fetchone()
        # Keep immutable lesson snapshots useful after edits/deletions, but don't train
        # a changed question using an answer to its old wording. Other lessons can safely
        # finish first: apply to the latest progress, not the creation-time bucket.
        if (current is None or current["id"] != snapshot["card_id"]
                or current["revision"] != snapshot["card_revision"]
                or current["kind"] != snapshot["kind"]
                or (old and old["algorithm"] != row["algorithm"])):
            skipped += 1
            continue
        scheduler = registry.get(row["algorithm"])
        if scheduler is None:
            raise LookupError("A lesson scheduler is unavailable. Restore it before ending the lesson.")
        before = json.loads(old["state_json"]) if old else None
        updated = scheduler.review(before, choice["rating"], now, choice["control"])
        correct = next(r.correct for r in scheduler.ratings if r.value == choice["rating"])
        db.execute(
            """INSERT INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (user_id, question_id) DO UPDATE SET
                   algorithm=excluded.algorithm, state_json=excluded.state_json,
                   due_at=excluded.due_at, last_reviewed_at=excluded.last_reviewed_at,
                   reviews=excluded.reviews, lapses=excluded.lapses""",
            (user_id, row["question_id"], row["algorithm"], json.dumps(updated.state), updated.due_at,
             now, (old["reviews"] if old else 0) + 1,
             (old["lapses"] if old else 0) + int(not correct)),
        )
    return skipped


def save(lesson_id, user_id, payload, registry, now, finishing=False):
    with transaction(get_db()) as db:
        row = owned(lesson_id, user_id)
        if row["completed_at"] is not None:
            return status(row)  # Idempotent, including racing sync/finish requests.
        rows = items(lesson_id)
        revision, state = validate(payload, rows, finishing)
        previous = json.loads(row["state_json"])
        if revision != row["revision"] and state != previous:
            raise LessonConflict("This lesson changed in another tab. Reload its saved state.")
        if not finishing and state == previous:
            return status(row)  # Also tolerates retry after a lost acknowledgement.
        skipped = apply(db, user_id, rows, state["ratings"], registry, now) if finishing else 0
        db.execute(
            "UPDATE lessons SET state_json=?, revision=revision+1, updated_at=?, "
            "completed_at=?, skipped=? WHERE id=?",
            (json.dumps(state, separators=(",", ":")), now, now if finishing else None,
             skipped, lesson_id),
        )
        return status(owned(lesson_id, user_id))
