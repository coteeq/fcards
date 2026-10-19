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
        "default-src 'self'; script-src 'none'; style-src 'self' https://fonts.googleapis.com; "
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
    choices = ("system", "light", "dark")
    current = session.get("theme", "system")
    session["theme"] = choices[(choices.index(current) + 1) % len(choices)]
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
                  (SELECT COUNT(*) FROM cards c WHERE c.deck_id = d.id) AS card_count,
                  (SELECT COUNT(*) FROM questions q JOIN cards c ON c.id = q.card_id
                   WHERE c.deck_id = d.id) AS question_count,
                  (SELECT COUNT(*) FROM progress p
                   JOIN questions q ON q.id = p.question_id
                   JOIN cards c ON c.id = q.card_id
                   WHERE c.deck_id = d.id AND p.user_id = ?) AS seen_count
           FROM decks d JOIN users u ON u.id = d.author_id ORDER BY d.id DESC""",
        (g.user["id"],),
    ).fetchall()
    return render_template("index.html", decks=decks)


@bp.route("/decks/new", methods=("GET", "POST"))
@login_required
def new_deck():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not 1 <= len(name) <= 120 or len(description) > 2000:
            error = "Use a name of 1–120 characters and a description up to 2000 characters."
        else:
            try:
                deck_id = models.create_deck(name, description, g.user["id"])
            except sqlite3.IntegrityError:
                error = "A deck with this name already exists."
            else:
                flash("Deck created. Add its first card.")
                return redirect(url_for("web.new_card", deck_id=deck_id))
    return render_template("new_deck.html", error=error), (400 if error else 200)


def require_deck(deck_id):
    deck = models.deck_or_none(deck_id)
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


@bp.get("/decks/<int:deck_id>")
@login_required
def deck_detail(deck_id):
    deck = require_deck(deck_id)
    page = page_number()
    cards = get_db().execute(
        """SELECT c.*, COUNT(q.id) AS question_count,
                  COUNT(p.question_id) AS seen_count
           FROM cards c JOIN questions q ON q.card_id = c.id
           LEFT JOIN progress p ON p.question_id = q.id AND p.user_id = ?
           WHERE c.deck_id = ? GROUP BY c.id ORDER BY c.id
           LIMIT 21 OFFSET ?""",
        (g.user["id"], deck_id, (page - 1) * 20),
    ).fetchall()
    more = len(cards) > 20
    cards = cards[:20]
    forms = models.forms_for_cards([card["id"] for card in cards])
    return render_template(
        "deck.html", deck=deck, cards=cards, forms=forms,
        stats=models.deck_stats(deck_id, g.user["id"]), page=page, more=more,
    )


def read_forms():
    forms = {}
    for side in ("front", "back"):
        texts = request.form.getlist(f"{side}_text")
        descriptions = request.form.getlist(f"{side}_description")
        if len(texts) != len(descriptions) or not 1 <= len(texts) <= 100:
            abort(400, "Invalid form rows.")
        forms[side] = list(zip(texts, descriptions))
    return forms


@bp.route("/decks/<int:deck_id>/cards/new", methods=("GET", "POST"))
@login_required
def new_card(deck_id):
    deck = require_deck(deck_id)
    forms = {"front": [("", "")], "back": [("", "")]}
    meta = ""
    error = None
    if request.method == "POST":
        forms = read_forms()
        meta = request.form.get("meta", "")
        action = request.form.get("action", "save")
        if action in ("add_front", "add_back"):
            side = action.removeprefix("add_")
            if len(forms[side]) >= 100:
                error = "A side can have at most 100 forms."
            else:
                forms[side].append(("", ""))
        elif action == "save":
            if any(not forms[side][0][0].strip() for side in forms):
                error = "The first form on each side is required."
            elif any(not text.strip() and desc.strip()
                     for rows in forms.values() for text, desc in rows):
                error = "A description needs form text."
            elif len(meta) > 500:
                error = "Metadata must be at most 500 characters."
            else:
                cleaned = {
                    side: [(text, desc) for text, desc in rows if text.strip()]
                    for side, rows in forms.items()
                }
                try:
                    models.create_card(deck_id, cleaned, meta)
                except ValueError as exc:
                    error = str(exc)
                else:
                    flash("Card added, with independent questions for each direction.")
                    return redirect(url_for("web.deck_detail", deck_id=deck_id))
        else:
            abort(400, "Unknown form action.")
    return render_template(
        "new_card.html", deck=deck, forms=forms, meta=meta, error=error,
    ), (400 if error else 200)


def scheduler_for(algorithm):
    scheduler = current_app.config["SCHEDULERS"].get(algorithm)
    if scheduler is None:
        abort(503, "A recorded scheduler is unavailable. Restore it before reviewing.")
    return scheduler


def current_pending(deck_id, require_answer=False):
    pending = session.get("study")
    if (not pending or pending["deck_id"] != deck_id
            or pending["user_id"] != g.user["id"]):
        abort(409, "This question is no longer active. Open Study again.")
    token = request.form.get("attempt", "")
    if not secrets.compare_digest(pending["token"].encode(), token.encode()):
        abort(409, "This is an old study form. Reload Study before answering.")
    if require_answer and "answer" not in pending:
        abort(409, "Submit an answer before finalizing this review.")
    return pending


@bp.get("/decks/<int:deck_id>/study")
@login_required
def study(deck_id):
    deck = require_deck(deck_id)
    pending = session.get("study")
    if pending and (pending["deck_id"] != deck_id or pending["user_id"] != g.user["id"]):
        pending = None
    if pending:
        progress = get_db().execute(
            "SELECT reviews FROM progress WHERE user_id = ? AND question_id = ?",
            (g.user["id"], pending["question_id"]),
        ).fetchone()
        if (progress["reviews"] if progress else 0) != pending["revision"]:
            pending = None
            session.pop("study", None)
            flash("That question was already reviewed; selected a fresh question.")
    if not pending:
        try:
            selected = reviews.select_question(
                deck_id, g.user["id"], current_app.config["DEFAULT_ALGORITHM"],
                current_app.config["SCHEDULERS"], int(time.time()),
                session.get("queue", {}),
            )
        except LookupError:
            abort(503, "A recorded scheduler is unavailable. Restore it before reviewing.")
        if selected is None:
            session.pop("study", None)
            return render_template("study.html", deck=deck, pending=None)
        pending = {
            "deck_id": deck_id, "user_id": g.user["id"],
            "question_id": selected.question_id, "revision": selected.reviews,
            "algorithm": selected.algorithm, "token": secrets.token_urlsafe(24),
        }
        session["study"] = pending
    data = models.question_data(pending["question_id"], deck_id)
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


@bp.post("/decks/<int:deck_id>/study/answer")
@login_required
def answer(deck_id):
    require_deck(deck_id)
    pending = current_pending(deck_id)
    if "answer" in pending:
        abort(409, "This question has already been answered. Use the revealed verdict.")
    submitted = request.form.get("answer", "")
    # Keep bounded submitted text in the signed session; expected answers are never
    # put in cookies, HTML, or hidden fields before reveal.
    if len(submitted) > 500:
        abort(400, "An answer must be at most 500 characters.")
    data = models.question_data(pending["question_id"], deck_id)
    if data is None:
        abort(409, "This question no longer exists.")
    pending = dict(
        pending, answer=submitted,
        auto_verdict=reviews.verdict(submitted, data["expected"]),
    )
    session["study"] = pending
    return redirect(url_for("web.study", deck_id=deck_id))


@bp.post("/decks/<int:deck_id>/study/finish")
@login_required
def finish(deck_id):
    require_deck(deck_id)
    pending = current_pending(deck_id, require_answer=True)
    scheduler = scheduler_for(pending["algorithm"])
    data = models.question_data(pending["question_id"], deck_id)
    if data is None:
        abort(409, "This question no longer exists.")
    auto = reviews.verdict(pending["answer"], data["expected"])
    rating = request.form.get("rating", scheduler.default_rating(auto))
    control = request.form.get("control") or None
    try:
        reviews.finalize(
            g.user["id"], deck_id, pending, rating, control,
            current_app.config["SCHEDULERS"], int(time.time()),
        )
    except ValueError as exc:
        abort(400, str(exc))
    except reviews.StaleReview as exc:
        session.pop("study", None)
        abort(409, str(exc))
    session.pop("study", None)
    session["queue"] = {
        "deck_id": deck_id, "question_id": pending["question_id"],
        "algorithm": pending["algorithm"],
    }
    return redirect(url_for("web.study", deck_id=deck_id))


@bp.post("/decks/<int:deck_id>/study/pause")
@login_required
def pause(deck_id):
    require_deck(deck_id)
    current_pending(deck_id)
    session.pop("study", None)
    flash("Study paused. The unfinished question was not counted.")
    return redirect(url_for("web.deck_detail", deck_id=deck_id))


@bp.get("/decks/<int:deck_id>/history")
@login_required
def history(deck_id):
    deck = require_deck(deck_id)
    page = page_number()
    logs = get_db().execute(
        """SELECT l.*, q.kind, q.card_id,
                  (SELECT text FROM card_forms f
                   WHERE f.card_id = q.card_id AND f.side = 'front'
                   ORDER BY position LIMIT 1) AS front
           FROM review_log l JOIN questions q ON q.id = l.question_id
           JOIN cards c ON c.id = q.card_id
           WHERE l.user_id = ? AND c.deck_id = ?
           ORDER BY l.id DESC LIMIT 31 OFFSET ?""",
        (g.user["id"], deck_id, (page - 1) * 30),
    ).fetchall()
    return render_template(
        "history.html", deck=deck, logs=logs[:30], more=len(logs) > 30, page=page,
    )
