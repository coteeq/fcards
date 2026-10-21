"""Shared vocabulary reads and atomic card creation/editing."""

import re
import time

from .db import get_db, transaction
from .search import search_keys
from .german import INFER, GENDERS, classification, split_article

DECK_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,119}")
QUESTION_LABELS = {
    "front_to_back": "Front → Back",
    "back_to_front": "Back → Front",
    "meta": "Front → Metadata",
    "gender": "Back → Gender",
}


class CardConflict(Exception):
    """An edit was based on an old card revision."""


def valid_deck_name(name):
    return DECK_NAME.fullmatch(name) is not None


def deck_or_none(deck_name):
    return get_db().execute(
        """SELECT d.*, u.username AS author FROM decks d
           JOIN users u ON u.id = d.author_id WHERE d.name = ?""",
        (deck_name,),
    ).fetchone()


def card_or_none(deck_name, card_id):
    return get_db().execute(
        "SELECT * FROM cards WHERE id = ? AND deck_name = ?", (card_id, deck_name),
    ).fetchone()


def deck_stats(deck_name, user_id):
    return get_db().execute(
        """SELECT COUNT(*) AS total, COUNT(p.question_id) AS seen,
                  COALESCE(SUM(p.reviews), 0) AS reviews,
                  COALESCE(SUM(p.lapses), 0) AS lapses
           FROM questions q JOIN cards c ON c.id = q.card_id
           LEFT JOIN progress p ON p.question_id = q.id AND p.user_id = ?
           WHERE c.deck_name = ?""",
        (user_id, deck_name),
    ).fetchone()


def create_deck(name, description, author_id):
    if not valid_deck_name(name):
        raise ValueError("Deck name must match [A-Za-z_][A-Za-z0-9_]* (max 120 characters).")
    if len(description) > 2000:
        raise ValueError("Description must be at most 2000 characters.")
    with transaction(get_db()) as db:
        db.execute(
            "INSERT INTO decks (name, description, author_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, description, author_id, int(time.time())),
        )
    return name


def validate_card(forms, meta):
    """Trim entry fields before validation; never change interior spelling/spaces."""
    trimmed = {}
    for side in ("front", "back"):
        if not 1 <= len(forms.get(side, [])) <= 100:
            raise ValueError("Both sides need 1–100 forms")
        trimmed[side] = []
        for text, description in forms[side]:
            text, description = text.strip(), description.strip()
            if not text or len(text) > 500 or len(description) > 160:
                raise ValueError("Use form text of 1–500 characters and descriptions up to 160.")
            trimmed[side].append((text, description))
    meta = meta.strip()
    if len(meta) > 500:
        raise ValueError("Metadata is too long")
    return trimmed, meta


def insert_forms(db, card_id, forms):
    for side in ("front", "back"):
        db.executemany(
            """INSERT INTO card_forms
               (card_id, side, position, text, description) VALUES (?, ?, ?, ?, ?)""",
            [
                (card_id, side, position, text, description)
                for position, (text, description) in enumerate(forms[side])
            ],
        )


def create_card(deck_name, forms, meta, word_type=INFER, gender=INFER):
    forms, meta = validate_card(forms, meta)
    word_type, gender = classification(forms["back"][0][0], word_type, gender)
    with transaction(get_db()) as db:
        card_id = db.execute(
            "INSERT INTO cards (deck_name, meta, word_type, gender, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (deck_name, meta, word_type, gender, int(time.time())),
        ).lastrowid
        insert_forms(db, card_id, forms)
        kinds = (["front_to_back", "back_to_front"] + (["meta"] if meta else [])
                 + (["gender"] if word_type == "noun" and gender else []))
        db.executemany(
            "INSERT INTO questions (card_id, kind) VALUES (?, ?)",
            [(card_id, kind) for kind in kinds],
        )
    return card_id


def update_card(deck_name, card_id, forms, meta, revision, word_type=INFER, gender=INFER):
    """Committed classification stays manual; preserve all remaining question schedules."""
    forms, meta = validate_card(forms, meta)
    with transaction(get_db()) as db:
        saved = card_or_none(deck_name, card_id)
        if saved is None:
            raise CardConflict("This card no longer exists.")
        word_type, gender = classification(
            forms["back"][0][0],
            saved["word_type"] if word_type is INFER else word_type,
            saved["gender"] if gender is INFER else gender,
        )
        updated = db.execute(
            """UPDATE cards SET meta = ?, word_type = ?, gender = ?, revision = revision + 1
               WHERE id = ? AND deck_name = ? AND revision = ?""",
            (meta, word_type, gender, card_id, deck_name, revision),
        )
        if updated.rowcount != 1:
            raise CardConflict("This card changed in another tab. Reload before editing.")
        db.execute("DELETE FROM card_forms WHERE card_id = ?", (card_id,))
        insert_forms(db, card_id, forms)
        if meta:
            db.execute(
                "INSERT INTO questions (card_id, kind) VALUES (?, 'meta') "
                "ON CONFLICT (card_id, kind) DO NOTHING", (card_id,),
            )
        else:
            # Removing metadata deletes its progress, but saved lesson snapshots survive.
            db.execute("DELETE FROM questions WHERE card_id = ? AND kind = 'meta'", (card_id,))
        if word_type == "noun" and gender:
            db.execute(
                "INSERT INTO questions (card_id, kind) VALUES (?, 'gender') "
                "ON CONFLICT (card_id, kind) DO NOTHING", (card_id,),
            )
        else:
            # Lesson snapshots survive; only this question's scheduling progress is removed.
            db.execute("DELETE FROM questions WHERE card_id = ? AND kind = 'gender'", (card_id,))


def search_cards(query, user_id, page, limit=20):
    """Search every form independently, returning each matching card only once."""
    rows = get_db().execute(
        """SELECT c.*, COUNT(q.id) AS question_count,
                  COUNT(p.question_id) AS seen_count
           FROM cards c JOIN questions q ON q.card_id = c.id
           LEFT JOIN progress p ON p.question_id = q.id AND p.user_id = ?
           WHERE EXISTS (
               SELECT 1 FROM card_forms f
               WHERE f.card_id = c.id AND word_matches(f.text, ?)
           )
           GROUP BY c.id ORDER BY c.id DESC LIMIT ? OFFSET ?""",
        (user_id, query, limit + 1, (page - 1) * limit),
    ).fetchall()
    return rows[:limit], len(rows) > limit


def suggest_cards(words, exclude=None, limit=5):
    """Advisory whole-form matches, normalized like search, across all decks."""
    keys = {key for word in words for key in search_keys(word) if key}
    if not keys:
        return [], {}, False
    matches = {}
    rows = get_db().execute(
        "SELECT card_id, text FROM card_forms WHERE card_id != ? "
        "ORDER BY card_id DESC, side, position", (exclude or 0,),
    )
    try:
        for row in rows:
            card_id = row["card_id"]
            if card_id in matches:
                continue
            if keys.intersection(search_keys(row["text"])):
                matches[card_id] = row["text"]
                if len(matches) > limit:
                    break
    finally:
        rows.close()
    ids = list(matches)[:limit]
    if not ids:
        return [], {}, False
    placeholders = ",".join("?" for _ in ids)
    cards = get_db().execute(
        f"""SELECT c.*, 1 + (
                SELECT COUNT(*) FROM cards newer
                WHERE newer.deck_name = c.deck_name AND newer.id > c.id
            ) / 20 AS page
            FROM cards c WHERE c.id IN ({placeholders}) ORDER BY c.id DESC""",
        ids,
    ).fetchall()
    return cards, matches, len(matches) > limit


def forms_for_cards(card_ids):
    result = {card_id: {"front": [], "back": []} for card_id in card_ids}
    if card_ids:
        placeholders = ",".join("?" for _ in card_ids)
        rows = get_db().execute(
            f"""SELECT * FROM card_forms WHERE card_id IN ({placeholders})
                ORDER BY card_id, side, position""",
            list(card_ids),
        )
        for row in rows:
            result[row["card_id"]][row["side"]].append(row)
    return result


def question_data(question_id, deck_name):
    question = get_db().execute(
        """SELECT q.*, c.meta, c.gender, c.revision AS card_revision FROM questions q
           JOIN cards c ON c.id = q.card_id WHERE q.id = ? AND c.deck_name = ?""",
        (question_id, deck_name),
    ).fetchone()
    if question is None:
        return None
    forms = forms_for_cards([question["card_id"]])[question["card_id"]]
    kind = question["kind"]
    prompt = forms["back" if kind == "back_to_front" else "front"][0]["text"]
    if kind == "gender":
        _, prompt = split_article(forms["back"][0]["text"])
        expected = GENDERS[question["gender"]]
    elif kind == "meta":
        expected = question["meta"]
    else:
        expected = forms["front" if kind == "back_to_front" else "back"][0]["text"]
    return {
        "question": question,
        "forms": forms,
        "prompt": prompt,
        "expected": expected,
        "label": QUESTION_LABELS[kind],
    }
