"""Server-rendered routes; all mutations use CSRF-protected POST requests."""

from functools import wraps
import secrets
import sqlite3
import time

from flask import (
    Blueprint, abort, current_app, flash, g, redirect, render_template,
    request, session, url_for,
)
from werkzeug.exceptions import HTTPException

from .db import get_db, transaction
from . import models, reviews

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
    if session.get("user_id") is not None:
        g.user = get_db().execute(
            "SELECT * FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
        if g.user is None:
            session.clear()
    if request.method == "POST":
        expected = session.get("csrf", "")
        supplied = request.form.get("csrf", "")
        if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
            abort(400, "This form expired. Reload the page and try again.")


@bp.app_context_processor
def template_context():
    return {
        "csrf_token": csrf_token,
        "theme": session.get("theme", "system"),
        "question_labels": models.QUESTION_LABELS,
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
    return render_template("index.html", decks=decks)


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
        models.create_card(deck_name, forms, "")
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
    revision = None
    if editing:
        saved = models.forms_for_cards([card["id"]])[card["id"]]
        forms = {
            side: [(row["text"], row["description"]) for row in rows]
            for side, rows in saved.items()
        }
        meta = card["meta"]
        revision = card["revision"]
    error = None
    if request.method == "POST":
        forms = read_forms()
        meta = request.form.get("meta", "")
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
                        )
                        card_id = card["id"]
                    else:
                        card_id = models.create_card(deck["name"], cleaned, meta)
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
        revision=revision, error=error,
    ), (400 if error else 200)


def scheduler_for(algorithm):
    scheduler = current_app.config["SCHEDULERS"].get(algorithm)
    if scheduler is None:
        abort(503, "A recorded scheduler is unavailable. Restore it before reviewing.")
    return scheduler


def current_pending(deck_name, require_answer=False):
    pending = session.get("study")
    if (not pending or pending["deck_name"] != deck_name
            or pending["user_id"] != g.user["id"]):
        abort(409, "This question is no longer active. Open Study again.")
    token = request.form.get("attempt", "")
    if not secrets.compare_digest(pending["token"].encode(), token.encode()):
        abort(409, "This is an old study form. Reload Study before answering.")
    if require_answer and "answer" not in pending:
        abort(409, "Submit an answer before finalizing this review.")
    return pending


@bp.get("/decks/<deck_name>/study")
@login_required
def study(deck_name):
    deck = require_deck(deck_name)
    pending = session.get("study")
    if pending and (pending["deck_name"] != deck_name or pending["user_id"] != g.user["id"]):
        pending = None
    if pending:
        progress = get_db().execute(
            "SELECT reviews FROM progress WHERE user_id = ? AND question_id = ?",
            (g.user["id"], pending["question_id"]),
        ).fetchone()
        data = models.question_data(pending["question_id"], deck_name)
        if (data is None or data["question"]["card_revision"] != pending["card_revision"]
                or (progress["reviews"] if progress else 0) != pending["revision"]):
            pending = None
            session.pop("study", None)
            flash("That question changed or was already reviewed; selected a fresh question.")
    if not pending:
        try:
            selected = reviews.select_question(
                deck_name, g.user["id"], current_app.config["DEFAULT_ALGORITHM"],
                current_app.config["SCHEDULERS"], int(time.time()),
                session.get("queue", {}),
            )
        except LookupError:
            abort(503, "A recorded scheduler is unavailable. Restore it before reviewing.")
        if selected is None:
            session.pop("study", None)
            return render_template("study.html", deck=deck, pending=None)
        data = models.question_data(selected.question_id, deck_name)
        if data is None:
            abort(409, "This question no longer exists. Reload Study.")
        pending = {
            "deck_name": deck_name, "user_id": g.user["id"],
            "question_id": selected.question_id, "revision": selected.reviews,
            "card_revision": data["question"]["card_revision"],
            "algorithm": selected.algorithm, "token": secrets.token_urlsafe(24),
        }
        session["study"] = pending
    data = models.question_data(pending["question_id"], deck_name)
    if data is None:
        session.pop("study", None)
        abort(409, "This question no longer exists.")
    scheduler = scheduler_for(pending["algorithm"])
    auto = reviews.verdict(pending["answer"], data["expected"]) if "answer" in pending else None
    return render_template(
        "study.html", deck=deck, pending=pending, data=data, scheduler=scheduler,
        auto_verdict=auto,
        default_rating=scheduler.default_rating(auto) if auto else None,
    )


def data_for_pending(pending, deck_name):
    data = models.question_data(pending["question_id"], deck_name)
    if data is None or data["question"]["card_revision"] != pending["card_revision"]:
        session.pop("study", None)
        abort(409, "This card was edited. Open Study again before answering.")
    return data


@bp.post("/decks/<deck_name>/study/answer")
@login_required
def answer(deck_name):
    require_deck(deck_name)
    pending = current_pending(deck_name)
    if "answer" in pending:
        abort(409, "This question has already been answered. Use the revealed verdict.")
    submitted = request.form.get("answer", "")
    # Keep bounded submitted text in the signed session; expected answers are never
    # put in cookies, HTML, or hidden fields before reveal.
    if len(submitted) > 500:
        abort(400, "An answer must be at most 500 characters.")
    data = data_for_pending(pending, deck_name)
    pending = dict(
        pending, answer=submitted,
        auto_verdict=reviews.verdict(submitted, data["expected"]),
    )
    session["study"] = pending
    return redirect(url_for("web.study", deck_name=deck_name))


@bp.post("/decks/<deck_name>/study/finish")
@login_required
def finish(deck_name):
    require_deck(deck_name)
    pending = current_pending(deck_name, require_answer=True)
    scheduler = scheduler_for(pending["algorithm"])
    data = data_for_pending(pending, deck_name)
    auto = reviews.verdict(pending["answer"], data["expected"])
    rating = request.form.get("rating", scheduler.default_rating(auto))
    control = request.form.get("control") or None
    try:
        reviews.finalize(
            g.user["id"], deck_name, pending, rating, control,
            current_app.config["SCHEDULERS"], int(time.time()),
        )
    except ValueError as exc:
        abort(400, str(exc))
    except reviews.StaleReview as exc:
        session.pop("study", None)
        abort(409, str(exc))
    session.pop("study", None)
    session["queue"] = {
        "deck_name": deck_name, "question_id": pending["question_id"],
        "algorithm": pending["algorithm"],
    }
    return redirect(url_for("web.study", deck_name=deck_name))


@bp.post("/decks/<deck_name>/study/pause")
@login_required
def pause(deck_name):
    require_deck(deck_name)
    current_pending(deck_name)
    session.pop("study", None)
    flash("Question discarded. The unfinished review was not counted.")
    return redirect(url_for("web.deck_detail", deck_name=deck_name))


@bp.get("/decks/<deck_name>/history")
@login_required
def history(deck_name):
    deck = require_deck(deck_name)
    page = page_number()
    logs = get_db().execute(
        """SELECT l.*, q.kind, q.card_id,
                  (SELECT text FROM card_forms f
                   WHERE f.card_id = q.card_id AND f.side = 'front'
                   ORDER BY position LIMIT 1) AS front
           FROM review_log l JOIN questions q ON q.id = l.question_id
           JOIN cards c ON c.id = q.card_id
           WHERE l.user_id = ? AND c.deck_name = ?
           ORDER BY l.id DESC LIMIT 31 OFFSET ?""",
        (g.user["id"], deck_name, (page - 1) * 30),
    ).fetchall()
    return render_template(
        "history.html", deck=deck, logs=logs[:30], more=len(logs) > 30, page=page,
    )
