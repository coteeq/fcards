"""Server-rendered routes; all mutations use CSRF-protected POST requests."""

from functools import wraps
import secrets
import sqlite3
import time

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
    request, session, url_for,
)
from werkzeug.exceptions import HTTPException

from .db import get_db, transaction
from . import models, lessons
from .search import matches_search, search_keys
from .german import INFER, WORD_TYPES, GENDERS, GENDER_LABELS

bp = Blueprint("web", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("web.login"))
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@bp.before_app_request
def load_user_and_check_csrf():
    g.user = None
    # Legacy cookie-scoped study attempts are superseded by durable lessons.
    session.pop("study", None)
    session.pop("queue", None)
    if session.get("user_id") is not None:
        g.user = get_db().execute(
            "SELECT * FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
        if g.user is None:
            session.clear()
    if request.method == "POST":
        expected = session.get("csrf", "")
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
        if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
            abort(400, "This form expired. Reload the page and try again.")


@bp.app_context_processor
def template_context():
    return {
        "csrf_token": csrf_token,
        "theme": session.get("theme", "system"),
        "question_labels": models.QUESTION_LABELS,
        "word_types": WORD_TYPES,
        "gender_labels": GENDER_LABELS,
        "gender_articles": GENDERS,
    }


@bp.after_app_request
def response_headers(response):
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    return response


@bp.app_errorhandler(HTTPException)
def http_error(error):
    if request.path.startswith("/api/lessons/"):
        return jsonify(error=error.description), error.code
    return render_template("error.html", error=error), error.code


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user is not None:
        return redirect(url_for("web.index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if not 1 <= len(username) <= 80:
            error = "Enter a username of 1–80 characters."
        else:
            with transaction(get_db()) as db:
                db.execute(
                    "INSERT INTO users (username, created_at) VALUES (?, ?) "
                    "ON CONFLICT (username) DO NOTHING",
                    (username, int(time.time())),
                )
                user_id = db.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()["id"]
            theme = session.get("theme", "system")
            session.clear()
            session.update(user_id=user_id, theme=theme)
            return redirect(url_for("web.index"))
    return render_template("login.html", error=error), (400 if error else 200)


@bp.post("/logout")
@login_required
def logout():
    theme = session.get("theme", "system")
    session.clear()
    session["theme"] = theme
    return redirect(url_for("web.login"))


@bp.post("/theme")
def theme():
    selected = request.form.get("theme")
    if selected not in {"system", "light", "dark"}:
        abort(400, "Unknown theme.")
    session["theme"] = selected
    # Only local paths, never arbitrary referrer URLs.
    target = request.form.get("return_to", "/")
    if (not target.startswith("/") or target.startswith("//") or "\\" in target
            or any(ord(char) < 32 or ord(char) == 127 for char in target)):
        target = "/"
    return redirect(target)


@bp.get("/")
@login_required
def index():
    decks = get_db().execute(
        """SELECT d.*, u.username AS author,
                  (SELECT COUNT(*) FROM cards c WHERE c.deck_name = d.name) AS card_count,
                  (SELECT COUNT(*) FROM questions q JOIN cards c ON c.id = q.card_id
                   WHERE c.deck_name = d.name) AS question_count,
                  (SELECT COUNT(*) FROM progress p
                   JOIN questions q ON q.id = p.question_id
                   JOIN cards c ON c.id = q.card_id
                   WHERE c.deck_name = d.name AND p.user_id = ?) AS seen_count
           FROM decks d JOIN users u ON u.id = d.author_id ORDER BY d.created_at DESC, d.name""",
        (g.user["id"],),
    ).fetchall()
    return render_template("index.html", decks=decks, lessons=lessons.unfinished(g.user["id"]))


@bp.get("/search")
@login_required
def search():
    query = request.args.get("q", "")
    if len(query) > 500:
        abort(400, "Search text must be at most 500 characters.")
    page = page_number()
    searchable = bool(search_keys(query)[0])
    cards, more = models.search_cards(query, g.user["id"], page) if searchable else ([], False)
    forms = models.forms_for_cards([card["id"] for card in cards])
    return render_template(
        "search.html", query=query, searchable=searchable, cards=cards,
        forms=forms, page=page, more=more,
    )


@bp.get("/search/live")
def live_search():
    if g.user is None:
        return jsonify(error="Session expired. Use Search to sign in."), 401
    query = request.args.get("q", "")
    if len(query) > 500:
        return jsonify(error="Search text must be at most 500 characters."), 400
    searchable = bool(search_keys(query)[0])
    cards, more = (
        models.search_cards(query, g.user["id"], 1, limit=8)
        if searchable else ([], False)
    )
    forms = models.forms_for_cards([card["id"] for card in cards])
    results = []
    for card in cards:
        sides = forms[card["id"]]
        front, back = sides["front"][0]["text"], sides["back"][0]["text"]
        match = None
        if not matches_search(front, query) and not matches_search(back, query):
            for side in ("front", "back"):
                for form in sides[side][1:]:
                    if matches_search(form["text"], query):
                        match = {"text": form["text"], "description": form["description"]}
                        break
                if match:
                    break
        results.append({
            "id": card["id"], "deck": card["deck_name"], "front": front, "back": back,
            "match": match,
            "url": url_for("web.search", q=query, _anchor=f"card-{card['id']}"),
        })
    return jsonify(query=query, searchable=searchable, results=results, more=more)


@bp.post("/cards/suggestions")
def card_suggestions():
    if g.user is None:
        return jsonify(error="Sign in to check existing words."), 401
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Expected a JSON object."), 400
    words = payload.get("words")
    exclude = payload.get("exclude")
    if (not isinstance(words, list) or len(words) > 200
            or any(not isinstance(word, str) or len(word) > 500 for word in words)):
        return jsonify(error="Supply up to 200 words of at most 500 characters."), 400
    if exclude is not None and (type(exclude) is not int or not 1 <= exclude < 2**63):
        return jsonify(error="Invalid card ID."), 400
    cards, matches, more = models.suggest_cards(words, exclude)
    forms = models.forms_for_cards([card["id"] for card in cards])
    return jsonify(more=more, results=[{
        "id": card["id"], "deck": card["deck_name"],
        "front": forms[card["id"]]["front"][0]["text"],
        "back": forms[card["id"]]["back"][0]["text"], "match": matches[card["id"]],
        "url": url_for(
            "web.deck_detail", deck_name=card["deck_name"], page=card["page"],
            _anchor=f"card-{card['id']}",
        ),
    } for card in cards])


@bp.route("/new-deck", methods=("GET", "POST"))
@login_required
def new_deck():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "")
        description = request.form.get("description", "").strip()
        if not models.valid_deck_name(name):
            error = "Use a C-style identifier: [A-Za-z_][A-Za-z0-9_]* (max 120 characters)."
        elif len(description) > 2000:
            error = "Description must be at most 2000 characters."
        else:
            try:
                deck_name = models.create_deck(name, description, g.user["id"])
            except sqlite3.IntegrityError:
                error = "A deck with this name already exists."
            else:
                flash("Deck created. Add its first card.")
                return redirect(url_for("web.new_card", deck_name=deck_name))
    return render_template("new_deck.html", error=error), (400 if error else 200)


def require_deck(deck_name):
    deck = models.deck_or_none(deck_name)
    if deck is None:
        abort(404, "Deck not found.")
    return deck


def page_number():
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        abort(400, "Invalid page number.")
    if not 1 <= page <= 1_000_000:
        abort(400, "Invalid page number.")
    return page


@bp.get("/decks/<deck_name>")
@login_required
def deck_detail(deck_name):
    return render_deck(require_deck(deck_name))


def render_deck(deck, quick_error=None):
    page = page_number()
    cards = get_db().execute(
        """SELECT c.*, COUNT(q.id) AS question_count,
                  COUNT(p.question_id) AS seen_count
           FROM cards c JOIN questions q ON q.card_id = c.id
           LEFT JOIN progress p ON p.question_id = q.id AND p.user_id = ?
           WHERE c.deck_name = ? GROUP BY c.id ORDER BY c.id DESC
           LIMIT 21 OFFSET ?""",
        (g.user["id"], deck["name"], (page - 1) * 20),
    ).fetchall()
    more = len(cards) > 20
    cards = cards[:20]
    forms = models.forms_for_cards([card["id"] for card in cards])
    return render_template(
        "deck.html", deck=deck, cards=cards, forms=forms,
        stats=models.deck_stats(deck["name"], g.user["id"]), page=page, more=more,
        quick_error=quick_error,
    ), (400 if quick_error else 200)


@bp.post("/decks/<deck_name>/cards/quick")
@login_required
def quick_card(deck_name):
    deck = require_deck(deck_name)
    forms = {
        "front": [(request.form.get("front", ""), "")],
        "back": [(request.form.get("back", ""), "")],
    }
    try:
        models.create_card(deck_name, forms, "", request.form.get("word_type", INFER))
    except ValueError as exc:
        return render_deck(deck, quick_error=str(exc))
    flash("Card added.")
    return redirect(url_for("web.deck_detail", deck_name=deck_name))


def read_forms():
    forms = {}
    for side in ("front", "back"):
        texts = request.form.getlist(f"{side}_text")
        descriptions = request.form.getlist(f"{side}_description")
        if len(texts) != len(descriptions) or not 1 <= len(texts) <= 100:
            abort(400, "Invalid form rows.")
        forms[side] = list(zip(texts, descriptions))
    return forms


@bp.route("/decks/<deck_name>/cards/new", methods=("GET", "POST"))
@login_required
def new_card(deck_name):
    return card_editor(require_deck(deck_name))


@bp.route("/decks/<deck_name>/cards/<int:card_id>/edit", methods=("GET", "POST"))
@login_required
def edit_card(deck_name, card_id):
    deck = require_deck(deck_name)
    card = models.card_or_none(deck_name, card_id)
    if card is None:
        abort(404, "Card not found in this deck.")
    return card_editor(deck, card)


def card_editor(deck, card=None):
    editing = card is not None
    forms = {"front": [("", "")], "back": [("", "")]}
    meta = ""
    word_type = gender = None
    revision = None
    if editing:
        saved = models.forms_for_cards([card["id"]])[card["id"]]
        forms = {
            side: [(row["text"], row["description"]) for row in rows]
            for side, rows in saved.items()
        }
        meta = card["meta"]
        word_type, gender = card["word_type"], card["gender"]
        revision = card["revision"]
    error = None
    if request.method == "POST":
        forms = read_forms()
        meta = request.form.get("meta", "")
        word_type = request.form.get("word_type", word_type)
        gender = request.form.get("gender", gender)
        if editing:
            try:
                revision = int(request.form["revision"])
                if not 0 <= revision < 2**63 - 1:
                    raise ValueError
            except (KeyError, ValueError):
                abort(400, "Invalid card revision.")
        action = request.form.get("action", "save")
        if action in ("verb_front", "verb_back"):
            side = action.removeprefix("verb_")
            descriptions = ("", "1sg", "1pl", "2sg", "2pl", "3sg", "3pl")
            rows = forms[side]
            forms[side] = [
                (rows[index][0] if index < len(rows) else "", description)
                for index, description in enumerate(descriptions)
            ] + rows[len(descriptions):]
        elif action in ("add_front", "add_back"):
            side = action.removeprefix("add_")
            if len(forms[side]) >= 100:
                error = "A side can have at most 100 forms."
            else:
                forms[side].append(("", ""))
        elif action.startswith("remove_"):
            parts = action.split("_")
            if len(parts) != 3 or parts[1] not in forms:
                abort(400, "Invalid form removal.")
            try:
                side, index = parts[1], int(parts[2])
            except ValueError:
                abort(400, "Invalid form index.")
            if not 0 <= index < len(forms[side]):
                abort(400, "Invalid form index.")
            if len(forms[side]) == 1:
                error = "Each side must keep at least one form."
            else:
                forms[side].pop(index)
        elif action == "save":
            if any(not forms[side][0][0].strip() for side in forms):
                error = "The first form on each side is required."
            elif any(not text.strip() and desc.strip()
                     for rows in forms.values() for text, desc in rows):
                error = "A description needs form text."
            else:
                cleaned = {
                    side: [(text, desc) for text, desc in rows if text.strip()]
                    for side, rows in forms.items()
                }
                try:
                    if editing:
                        models.update_card(
                            deck["name"], card["id"], cleaned, meta, revision,
                            word_type=word_type, gender=gender,
                        )
                        card_id = card["id"]
                    else:
                        card_id = models.create_card(
                            deck["name"], cleaned, meta,
                            word_type=request.form.get("word_type", INFER),
                            gender=request.form.get("gender", INFER),
                        )
                except ValueError as exc:
                    error = str(exc)
                except models.CardConflict as exc:
                    abort(409, str(exc))
                else:
                    flash("Card updated." if editing else "Card added.")
                    newer = get_db().execute(
                        "SELECT COUNT(*) FROM cards WHERE deck_name = ? AND id > ?",
                        (deck["name"], card_id),
                    ).fetchone()[0]
                    return redirect(url_for(
                        "web.deck_detail", deck_name=deck["name"],
                        page=newer // 20 + 1, _anchor=f"card-{card_id}",
                    ))
        else:
            abort(400, "Unknown form action.")
    return render_template(
        "new_card.html", deck=deck, card=card, forms=forms, meta=meta,
        revision=revision, error=error, word_type=word_type, gender=gender,
    ), (400 if error else 200)


@bp.post("/decks/<deck_name>/lessons")
@login_required
def create_lesson(deck_name):
    require_deck(deck_name)
    try:
        lesson_id = lessons.create(deck_name, g.user["id"],
                                   current_app.config["DEFAULT_ALGORITHM"],
                                   current_app.config["SCHEDULERS"], int(time.time()))
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("web.deck_detail", deck_name=deck_name))
    except LookupError as exc:
        abort(503, str(exc))
    return redirect(url_for("web.lesson", lesson_id=lesson_id))


@bp.get("/lessons/<uuid:lesson_id>")
@login_required
def lesson(lesson_id):
    try:
        row = lessons.owned(str(lesson_id), g.user["id"])
    except lessons.LessonMissing:
        abort(404, "Lesson not found.")
    return render_template("lesson.html", lesson=row)


def lesson_result(lesson_id, finishing=None):
    if g.user is None:
        abort(401, "Sign in again, then reload this lesson. Local pending changes are kept.")
    try:
        if finishing is None:
            data = lessons.load(str(lesson_id), g.user["id"])
            for question in data["questions"]:
                question["edit_url"] = url_for("web.edit_card", deck_name=data["deck_name"],
                                               card_id=question["card_id"])
        else:
            data = lessons.save(str(lesson_id), g.user["id"], request.get_json(silent=True),
                                current_app.config["SCHEDULERS"], int(time.time()), finishing)
    except lessons.LessonMissing as exc:
        abort(404, str(exc))
    except lessons.LessonConflict as exc:
        abort(409, str(exc))
    except ValueError as exc:
        abort(400, str(exc))
    except LookupError as exc:
        abort(503, str(exc))
    return jsonify(data)


@bp.get("/api/lessons/<uuid:lesson_id>")
def lesson_data(lesson_id):
    return lesson_result(lesson_id)


@bp.post("/api/lessons/<uuid:lesson_id>/state")
def lesson_state(lesson_id):
    return lesson_result(lesson_id, finishing=False)


@bp.post("/api/lessons/<uuid:lesson_id>/finish")
def lesson_finish(lesson_id):
    return lesson_result(lesson_id, finishing=True)


@bp.get("/decks/<deck_name>/study")
@login_required
def study(deck_name):
    # Old bookmarks no longer launch an infinite, cookie-scoped study sequence.
    require_deck(deck_name)
    return redirect(url_for("web.deck_detail", deck_name=deck_name))
