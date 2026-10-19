from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fcard import create_app
from fcard.db import get_db, transaction
from fcard.models import create_card, create_deck, question_data
from fcard.reviews import finalize, StaleReview, verdict
from fcard.schedulers import Buckets, TimeBased, Transition


class FirstChoice:
    def choices(self, population, weights, k):
        # Deterministic test queue: highest weight, stable order for ties.
        return [population[max(range(len(population)), key=lambda i: weights[i])]]


class ClockScheduler(TimeBased):
    id = "clock-test"
    label = "Test clock"
    ratings = Buckets.ratings
    controls = ()
    default_rating = Buckets.default_rating

    def review(self, state, rating, now, control=None):
        return Transition({"n": (state or {}).get("n", 0) + 1}, now + 3600)


class AppTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / "fcard.sqlite3")
        self.app = create_app({
            "TESTING": True,
            "DATABASE": self.database,
            "SECRET_KEY": "test-key-not-for-deployment",
            "SCHEDULERS": {"buckets-v1": Buckets(FirstChoice())},
        })
        self.client = self.app.test_client()
        self.login("Alice")
        with self.app.app_context():
            self.alice = get_db().execute(
                "SELECT id FROM users WHERE username = 'Alice'"
            ).fetchone()[0]
            self.deck_id = create_deck("German", "A shared deck", self.alice)
            self.card_id = create_card(self.deck_id, {
                "front": [("Brot", "nominative singular"), ("Brote", "plural")],
                "back": [("bread", "noun")],
            }, "neuter")
        self.study_url = f"/decks/{self.deck_id}/study"

    def tearDown(self):
        self.temp.cleanup()

    def session(self, client=None):
        with (client or self.client).session_transaction() as session:
            return dict(session)

    def post(self, path, data=None, client=None, follow=False):
        client = client or self.client
        if "csrf" not in self.session(client):
            client.get("/login", follow_redirects=True)
        payload = {"csrf": self.session(client)["csrf"], **(data or {})}
        return client.post(path, data=payload, follow_redirects=follow)

    def login(self, name, client=None):
        client = client or self.client
        client.get("/login")
        response = self.post("/login", {"username": name}, client)
        self.assertEqual(response.status_code, 302)

    def rows(self, sql, params=()):
        with self.app.app_context():
            return [dict(row) for row in get_db().execute(sql, params).fetchall()]

    def pending(self):
        return self.session()["study"]

    def start(self):
        response = self.client.get(self.study_url)
        self.assertEqual(response.status_code, 200)
        return response

    def answer(self, text):
        return self.post(self.study_url + "/answer", {
            "attempt": self.pending()["token"], "answer": text,
        }, follow=True)

    def finish(self, rating=None, control=None):
        data = {"attempt": self.pending()["token"]}
        if rating is not None:
            data["rating"] = rating
        if control is not None:
            data["control"] = control
        return self.post(self.study_url + "/finish", data)

    def test_login_is_case_sensitive_and_users_resume(self):
        self.post("/logout")
        self.login("alice")
        self.assertEqual(len(self.rows("SELECT * FROM users")), 2)
        self.post("/logout")
        self.login("Alice")
        self.assertEqual(self.session()["user_id"], self.alice)
        self.assertEqual(len(self.rows("SELECT * FROM users")), 2)

    def test_login_required_and_passwordless(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/").status_code, 302)
        self.assertEqual(client.get(self.study_url).status_code, 302)
        self.assertIn(b"No passwords", client.get("/login").data)
        self.login("Bob", client)
        self.assertIn(b"German", client.get("/").data)

    def test_deck_author_and_other_users_can_add(self):
        bob = self.app.test_client()
        self.login("Bob", bob)
        response = self.post(f"/decks/{self.deck_id}/cards/new", {
            "front_text": ["Haus"], "front_description": [""],
            "back_text": ["house"], "back_description": [""],
        }, bob)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT author_id FROM decks")[0]["author_id"], self.alice)
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 2)
        response = self.post("/decks/new", {"name": "French"}, bob)
        self.assertEqual(response.status_code, 302)
        author = self.rows("SELECT u.username FROM decks d JOIN users u "
                           "ON u.id = d.author_id WHERE d.name = 'French'")
        self.assertEqual(author[0]["username"], "Bob")
        self.assertEqual(self.post("/decks/new", {"name": "French"}, bob).status_code, 400)
        self.assertEqual(self.post("/decks/new", {"name": "  "}, bob).status_code, 400)

    def test_ordered_forms_and_optional_metadata(self):
        with self.app.app_context():
            db = get_db()
            questions = db.execute("SELECT id, kind FROM questions ORDER BY id").fetchall()
            front = question_data(questions[0]["id"], self.deck_id)
            back = question_data(questions[1]["id"], self.deck_id)
            meta = question_data(questions[2]["id"], self.deck_id)
            self.assertEqual((front["prompt"], front["expected"]), ("Brot", "bread"))
            self.assertEqual((back["prompt"], back["expected"]), ("bread", "Brot"))
            self.assertEqual((meta["prompt"], meta["expected"]), ("Brot", "neuter"))
            card_id = create_card(self.deck_id, {
                "front": [("z", "first"), ("a", "second")],
                "back": [("last", ""), ("first", "")],
            }, "   ")
            question_id = db.execute("SELECT id FROM questions WHERE card_id = ? "
                                     "AND kind = 'front_to_back'", (card_id,)).fetchone()[0]
            data = question_data(question_id, self.deck_id)
            self.assertEqual((data["prompt"], data["expected"]), ("z", "last"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM questions WHERE card_id = ?",
                                        (card_id,)).fetchone()[0], 2)

    def test_html_add_rows_preserves_input_and_order(self):
        path = f"/decks/{self.deck_id}/cards/new"
        data = {
            "front_text": ["gehen"], "front_description": ["infinitive"],
            "back_text": ["go"], "back_description": ["verb"], "meta": "irregular",
            "action": "add_front",
        }
        response = self.post(path, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="gehen"', response.data)
        self.assertEqual(response.data.count(b'name="front_text"'), 2)
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 1)
        data.update(front_text=["gehen", "ging", ""],
                    front_description=["infinitive", "past", ""], action="save")
        self.assertEqual(self.post(path, data).status_code, 302)
        rows = self.rows("SELECT text, position FROM card_forms WHERE card_id = 2 "
                         "AND side = 'front' ORDER BY position")
        self.assertEqual(rows, [{"text": "gehen", "position": 0},
                                {"text": "ging", "position": 1}])

    def test_validation_is_atomic(self):
        path = f"/decks/{self.deck_id}/cards/new"
        valid = {"front_text": ["x"], "front_description": [""],
                 "back_text": ["y"], "back_description": [""]}
        for changes in ({"front_text": [""]}, {"back_text": ["x" * 501]},
                        {"front_text": ["x", ""], "front_description": ["", "past"]},
                        {"meta": "m" * 501}, {"back_description": ["d" * 161]}):
            self.assertEqual(self.post(path, {**valid, **changes}).status_code, 400)
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM questions")), 3)
        with self.app.app_context():
            with self.assertRaises(ValueError):
                create_card(self.deck_id, {"front": [], "back": [("y", "")]}, "")
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 1)

    def test_answer_no_leak_reveal_and_finalize(self):
        response = self.start()
        self.assertIn(b"Brot", response.data)
        self.assertNotIn(b"bread", response.data)
        self.assertNotIn(b"Brote", response.data)
        response = self.answer("BREAD")
        self.assertIn(b"Correct", response.data)
        self.assertIn(b"bread", response.data)
        self.assertIn(b"Brote", response.data)
        self.assertEqual(self.rows("SELECT * FROM progress"), [])
        self.assertEqual(self.finish().status_code, 302)
        progress = self.rows("SELECT * FROM progress")[0]
        self.assertEqual((progress["reviews"], progress["lapses"]), (1, 0))
        self.assertIsNone(progress["due_at"])
        self.assertIsInstance(progress["last_reviewed_at"], int)
        self.assertEqual(json.loads(progress["state_json"]), {"bucket": 1})
        log = self.rows("SELECT * FROM review_log")[0]
        self.assertEqual((log["rating"], log["auto_verdict"]), ("correct", "correct"))
        self.assertEqual(log["before_state_json"], "null")
        self.assertEqual(self.client.get(f"/decks/{self.deck_id}/history").status_code, 200)
        self.assertEqual(self.client.get(f"/decks/{self.deck_id}").status_code, 200)

    def test_wrong_answer_can_be_corrected_and_extra_control_logged(self):
        self.start()
        self.assertIn(b"Incorrect", self.answer("bun").data)
        self.assertEqual(self.finish("correct", "up5").status_code, 302)
        log = self.rows("SELECT * FROM review_log")[0]
        self.assertEqual((log["auto_verdict"], log["rating"], log["control"]),
                         ("incorrect", "correct", "up5"))
        self.assertEqual(json.loads(log["after_state_json"]), {"bucket": 6})
        self.assertEqual(self.rows("SELECT lapses FROM progress")[0]["lapses"], 0)

    def test_correct_answer_can_be_marked_wrong(self):
        self.start()
        self.answer("bread")
        self.finish("incorrect")
        progress = self.rows("SELECT * FROM progress")[0]
        self.assertEqual(progress["lapses"], 1)
        self.assertEqual(json.loads(progress["state_json"]), {"bucket": 0})

    def test_exact_comparison_and_empty_answers(self):
        self.assertEqual(verdict("Straße", "STRASSE"), "correct")
        self.assertEqual(verdict("Bread", "bread"), "correct")
        for answer in (" bread", "bread ", "bréad", "bread.", ""):
            self.assertEqual(verdict(answer, "bread"), "incorrect")
        self.start()
        self.answer("")
        self.finish()
        self.assertEqual(self.rows("SELECT rating FROM review_log")[0]["rating"], "incorrect")

    def test_question_kinds_and_users_progress_independently(self):
        for expected in ("bread", "Brot", "neuter"):
            self.start()
            self.answer(expected)
            self.finish()
        progress = self.rows("SELECT * FROM progress")
        self.assertEqual(len(progress), 3)
        self.assertTrue(all(p["reviews"] == 1 and p["lapses"] == 0 for p in progress))
        bob = self.app.test_client()
        self.login("Bob", bob)
        response = bob.get(f"/decks/{self.deck_id}")
        self.assertIn(b"0 seen", response.data)
        bob.get(self.study_url)
        self.assertEqual(self.session(bob)["study"]["revision"], 0)
        self.assertEqual(len(self.rows("SELECT * FROM progress")), 3)

    def test_replay_cannot_double_advance_or_log(self):
        self.start()
        self.answer("bread")
        old_session = self.session()
        payload = {"attempt": self.pending()["token"]}
        self.assertEqual(self.post(self.study_url + "/finish", payload).status_code, 302)
        self.assertEqual(self.post(self.study_url + "/finish", payload).status_code, 409)
        with self.client.session_transaction() as session:
            session.clear()
            session.update(old_session)
        self.assertEqual(self.post(self.study_url + "/finish", payload).status_code, 409)
        self.assertEqual(len(self.rows("SELECT * FROM review_log")), 1)
        self.assertEqual(self.rows("SELECT reviews FROM progress")[0]["reviews"], 1)

    def test_invalid_order_tokens_ratings_and_pause(self):
        self.start()
        self.assertEqual(self.finish().status_code, 409)
        self.assertEqual(self.post(self.study_url + "/answer", {
            "attempt": "old", "answer": "bread",
        }).status_code, 409)
        self.answer("bread")
        self.assertEqual(self.answer("bread").status_code, 409)
        self.assertEqual(self.finish("forged").status_code, 400)
        self.assertEqual(self.finish("correct", "forged").status_code, 400)
        self.assertEqual(self.rows("SELECT * FROM progress"), [])
        response = self.post(self.study_url + "/pause", {"attempt": self.pending()["token"]})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("study", self.session())
        self.assertEqual(self.rows("SELECT * FROM progress"), [])

    def test_refresh_preserves_question_and_reveal(self):
        self.start()
        before = self.pending()
        self.start()
        self.assertEqual(self.pending(), before)
        self.answer("bread")
        response = self.start()
        self.assertIn(b"Expected answer", response.data)
        self.assertEqual(self.pending()["answer"], "bread")
        self.assertEqual(self.rows("SELECT * FROM progress"), [])

    def test_csrf_theme_escaping_and_no_javascript(self):
        self.assertEqual(self.client.post("/logout").status_code, 400)
        self.assertEqual(self.client.post("/logout", data={"csrf": "☃"}).status_code, 400)
        response = self.post("/theme", {"return_to": "https://evil.example"})
        self.assertEqual(response.headers["Location"], "/")
        self.assertEqual(self.session()["theme"], "light")
        self.post("/decks/new", {"name": '<script>alert("x")</script>'})
        response = self.client.get("/")
        self.assertNotIn(b"<script", response.data)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertIn("script-src 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_missing_empty_pagination_and_limits(self):
        self.assertEqual(self.client.get("/decks/999").status_code, 404)
        self.assertEqual(self.client.get(f"/decks/{self.deck_id}?page=no").status_code, 400)
        self.assertEqual(self.client.get(f"/decks/{self.deck_id}?page=0").status_code, 400)
        with self.app.app_context():
            empty = create_deck("Empty", "", self.alice)
            for i in range(21):
                create_card(empty, {"front": [(str(i), "")], "back": [("x", "")]}, "")
            blank = create_deck("Blank", "", self.alice)
        self.assertIn(b"Next", self.client.get(f"/decks/{empty}").data)
        self.assertIn(b"Previous", self.client.get(f"/decks/{empty}?page=2").data)
        self.assertIn(b"No questions available", self.client.get(f"/decks/{blank}/study").data)
        self.assertEqual(self.client.post("/login", data=b"x" * 300_000,
                                         content_type="application/x-www-form-urlencoded").status_code, 413)

    def test_default_change_preserves_recorded_algorithm(self):
        self.start()
        self.answer("bread")
        self.finish()
        self.app.config["SCHEDULERS"]["clock-test"] = ClockScheduler()
        self.app.config["DEFAULT_ALGORITHM"] = "clock-test"
        self.start()
        self.assertEqual(self.pending()["algorithm"], "clock-test")
        self.answer("Brot")
        self.finish()
        self.assertIsNotNone(self.rows("SELECT due_at FROM progress "
                                      "WHERE algorithm = 'clock-test'")[0]["due_at"])
        self.start()
        self.assertEqual(self.pending()["algorithm"], "buckets-v1")
        self.assertEqual(self.pending()["revision"], 1)

    def test_unavailable_scheduler_is_not_silently_replaced(self):
        self.start()
        self.answer("bread")
        self.finish()
        with self.app.app_context(), transaction(get_db()) as db:
            db.execute("UPDATE progress SET algorithm = 'unknown-v1'")
        self.assertEqual(self.client.get(self.study_url).status_code, 503)

    def test_concurrent_finalization_commits_only_once(self):
        self.start()
        self.answer("bread")
        pending = self.pending()

        def finish_review(_):
            with self.app.app_context():
                try:
                    finalize(self.alice, self.deck_id, pending, "correct", None,
                             self.app.config["SCHEDULERS"], 1000)
                    return "committed"
                except StaleReview:
                    return "stale"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(finish_review, range(2)))
        self.assertCountEqual(results, ["committed", "stale"])
        self.assertEqual(len(self.rows("SELECT * FROM review_log")), 1)
        self.assertEqual(self.rows("SELECT reviews FROM progress")[0]["reviews"], 1)

    def test_new_cards_appear_for_existing_users(self):
        self.start()
        self.answer("bread")
        self.finish()
        with self.app.app_context():
            create_card(self.deck_id, {
                "front": [("Haus", "")], "back": [("house", "")],
            }, "")
        response = self.client.get(f"/decks/{self.deck_id}")
        self.assertIn(b"1 seen", response.data)
        self.assertIn(b"4 new", response.data)

    def test_reinitialization_versions_and_constraints(self):
        new_app = create_app({"TESTING": True, "DATABASE": self.database, "SECRET_KEY": "test"})
        with new_app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError), transaction(db):
                db.execute("DELETE FROM users WHERE id = ?", (self.alice,))
            with self.assertRaises(sqlite3.IntegrityError), transaction(db):
                db.execute("INSERT INTO questions (card_id, kind) VALUES (999, 'meta')")
            with transaction(db):
                db.execute("PRAGMA user_version = 99")
        with self.assertRaises(RuntimeError):
            create_app({"TESTING": True, "DATABASE": self.database, "SECRET_KEY": "test"})


if __name__ == "__main__":
    unittest.main()
