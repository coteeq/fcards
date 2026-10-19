"""Shared vocabulary reads and atomic creation."""

import time

from .db import get_db, transaction

QUESTION_LABELS = {
    "front_to_back": "Front → Back",
    "back_to_front": "Back → Front",
    "meta": "Front → Metadata",
}


def deck_or_none(deck_id):
    return get_db().execute(
        """SELECT d.*, u.username AS author FROM decks d
           JOIN users u ON u.id = d.author_id WHERE d.id = ?""",
        (deck_id,),
    ).fetchone()


def deck_stats(deck_id, user_id):
    return get_db().execute(
        """SELECT COUNT(*) AS total, COUNT(p.question_id) AS seen,
                  COALESCE(SUM(p.reviews), 0) AS reviews,
                  COALESCE(SUM(p.lapses), 0) AS lapses
           FROM questions q JOIN cards c ON c.id = q.card_id
           LEFT JOIN progress p ON p.question_id = q.id AND p.user_id = ?
           WHERE c.deck_id = ?""",
        (user_id, deck_id),
    ).fetchone()


def create_deck(name, description, author_id):
    with transaction(get_db()) as db:
        return db.execute(
            "INSERT INTO decks (name, description, author_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, description, author_id, int(time.time())),
        ).lastrowid


def create_card(deck_id, forms, meta):
    # This is also an invariant boundary for callers other than the HTML form.
    for side in ("front", "back"):
        if not forms.get(side):
            raise ValueError("Both sides need at least one form")
        for text, description in forms[side]:
            if not text.strip() or len(text) > 500 or len(description) > 160:
                raise ValueError("Invalid form text or description")
    if len(meta) > 500:
        raise ValueError("Metadata is too long")
    if not meta.strip():
        meta = ""
    with transaction(get_db()) as db:
        card_id = db.execute(
            "INSERT INTO cards (deck_id, meta, created_at) VALUES (?, ?, ?)",
            (deck_id, meta, int(time.time())),
        ).lastrowid
        for side in ("front", "back"):
            db.executemany(
                """INSERT INTO card_forms
                   (card_id, side, position, text, description) VALUES (?, ?, ?, ?, ?)""",
                [
                    (card_id, side, position, text, description)
                    for position, (text, description) in enumerate(forms[side])
                ],
            )
        kinds = ["front_to_back", "back_to_front"] + (["meta"] if meta else [])
        db.executemany(
            "INSERT INTO questions (card_id, kind) VALUES (?, ?)",
            [(card_id, kind) for kind in kinds],
        )
    return card_id


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


def question_data(question_id, deck_id):
    question = get_db().execute(
        """SELECT q.*, c.meta FROM questions q JOIN cards c ON c.id = q.card_id
           WHERE q.id = ? AND c.deck_id = ?""",
        (question_id, deck_id),
    ).fetchone()
    if question is None:
        return None
    forms = forms_for_cards([question["card_id"]])[question["card_id"]]
    kind = question["kind"]
    prompt = forms["back" if kind == "back_to_front" else "front"][0]["text"]
    if kind == "meta":
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
