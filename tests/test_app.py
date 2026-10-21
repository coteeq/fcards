from concurrent.futures import ThreadPoolExecutor
import json
import re
from pathlib import Path
from html import unescape
import sqlite3
import tempfile
import unittest

from fcard import create_app
from fcard.db import get_db, transaction
from fcard.models import create_card, create_deck, question_data
from fcard.reviews import verdict
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
            self.deck_name = create_deck("German", "A shared deck", self.alice)
            self.card_id = create_card(self.deck_name, {
                "front": [("Brot", "nominative singular"), ("Brote", "plural")],
                "back": [("bread", "noun")],
            }, "neuter")
        self.study_url = f"/decks/{self.deck_name}/study"

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









    def seed_review(self, question_id=None):
        question_id = question_id or self.rows("SELECT id FROM questions ORDER BY id LIMIT 1")[0]["id"]
        with self.app.app_context(), transaction(get_db()) as db:
            db.execute("INSERT INTO progress VALUES (?, ?, 'buckets-v1', ?, NULL, 123, 1, 0)",
                       (self.alice, question_id, '{"bucket":1}'))

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
        response = self.post(f"/decks/{self.deck_name}/cards/new", {
            "front_text": ["Haus"], "front_description": [""],
            "back_text": ["house"], "back_description": [""],
        }, bob)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT author_id FROM decks")[0]["author_id"], self.alice)
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 2)
        response = self.post("/new-deck", {"name": "French"}, bob)
        self.assertEqual(response.status_code, 302)
        author = self.rows("SELECT u.username FROM decks d JOIN users u "
                           "ON u.id = d.author_id WHERE d.name = 'French'")
        self.assertEqual(author[0]["username"], "Bob")
        self.assertEqual(self.post("/new-deck", {"name": "French"}, bob).status_code, 400)
        self.assertEqual(self.post("/new-deck", {"name": "  "}, bob).status_code, 400)

    def test_ordered_forms_and_optional_metadata(self):
        with self.app.app_context():
            db = get_db()
            questions = db.execute("SELECT id, kind FROM questions ORDER BY id").fetchall()
            front = question_data(questions[0]["id"], self.deck_name)
            back = question_data(questions[1]["id"], self.deck_name)
            meta = question_data(questions[2]["id"], self.deck_name)
            self.assertEqual((front["prompt"], front["expected"]), ("Brot", "bread"))
            self.assertEqual((back["prompt"], back["expected"]), ("bread", "Brot"))
            self.assertEqual((meta["prompt"], meta["expected"]), ("Brot", "neuter"))
            card_id = create_card(self.deck_name, {
                "front": [("z", "first"), ("a", "second")],
                "back": [("last", ""), ("first", "")],
            }, "   ")
            question_id = db.execute("SELECT id FROM questions WHERE card_id = ? "
                                     "AND kind = 'front_to_back'", (card_id,)).fetchone()[0]
            data = question_data(question_id, self.deck_name)
            self.assertEqual((data["prompt"], data["expected"]), ("z", "last"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM questions WHERE card_id = ?",
                                        (card_id,)).fetchone()[0], 2)

    def test_html_add_rows_preserves_input_and_order(self):
        path = f"/decks/{self.deck_name}/cards/new"
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

    def test_verb_shortcut_prefills_seven_rows_without_saving(self):
        path = f"/decks/{self.deck_name}/cards/new"
        page = self.client.get(path).get_data(as_text=True)
        self.assertIn('value="verb_front" formnovalidate', page)
        self.assertIn('value="verb_back" formnovalidate', page)
        for side in ("front", "back"):
            data = {
                "front_text": [""], "front_description": [""],
                "back_text": [""], "back_description": [""],
                "meta": "irregular", "action": f"verb_{side}",
            }
            response = self.post(path, data)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            descriptions = re.findall(fr'name="{side}_description" value="([^"]*)"', html)
            self.assertEqual(descriptions, ["", "1sg", "1pl", "2sg", "2pl", "3sg", "3pl"])
            self.assertEqual(html.count(f'name="{side}_text"'), 7)
            other = "back" if side == "front" else "front"
            self.assertEqual(html.count(f'name="{other}_text"'), 1)
            self.assertIn(">irregular</textarea>", html)
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 1)

    def test_verb_shortcut_keeps_text_and_extra_rows_and_is_repeatable(self):
        texts = ["gehen", "gehe", "gehen", "gehst", "geht", "geht", "gehen", "ging"]
        data = {
            "front_text": texts, "front_description": ["old"] * 7 + ["past"],
            "back_text": ["go"], "back_description": ["verb"],
            "meta": "irregular", "action": "verb_front",
        }
        response = self.post(self.edit_url, {**data, "revision": "0"})
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertEqual(re.findall(r'name="front_text" value="([^"]*)"', html), texts)
        descriptions = re.findall(r'name="front_description" value="([^"]*)"', html)
        self.assertEqual(descriptions, ["", "1sg", "1pl", "2sg", "2pl", "3sg", "3pl", "past"])
        self.assertIn('name="back_text" value="go"', html)
        self.assertIn('name="back_description" value="verb"', html)
        self.assertIn('name="revision" value="0"', html)
        repeat = self.post(self.edit_url, {
            **data, "revision": "0", "front_description": descriptions,
        })
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(repeat.data, response.data)
        self.assertEqual(self.rows("SELECT revision FROM cards")[0]["revision"], 0)
        self.assertEqual(len(self.rows("SELECT * FROM card_forms")), 3)

    def test_verb_forms_save_in_order_and_leave_first_canonical(self):
        descriptions = ["", "1sg", "1pl", "2sg", "2pl", "3sg", "3pl"]
        texts = ["gehen", "gehe", "gehen", "gehst", "geht", "geht", "gehen"]
        response = self.post(f"/decks/{self.deck_name}/cards/new", {
            "front_text": texts, "front_description": descriptions,
            "back_text": ["go"], "back_description": [""], "action": "save",
        })
        self.assertEqual(response.status_code, 302)
        rows = self.rows(
            "SELECT text, description, position FROM card_forms "
            "WHERE card_id = 2 AND side = 'front' ORDER BY position"
        )
        self.assertEqual(rows, [
            {"text": text, "description": desc, "position": i}
            for i, (text, desc) in enumerate(zip(texts, descriptions))
        ])
        self.assertEqual(len(self.rows("SELECT * FROM questions WHERE card_id=2")), 2)
        with self.app.app_context():
            qid = get_db().execute(
                "SELECT id FROM questions WHERE card_id=2 AND kind='front_to_back'"
            ).fetchone()[0]
            self.assertEqual(question_data(qid, self.deck_name)["prompt"], "gehen")

    def test_form_editor_has_two_inputs_and_icon_remove_per_row(self):
        for path in (f"/decks/{self.deck_name}/cards/new", self.edit_url):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            rows = re.findall(
                r'<div class="form-editor-row"[^>]*>(.*?)</div>', html, re.S,
            )
            self.assertTrue(rows)
            for row in rows:
                self.assertEqual(row.count("<input "), 2)
                self.assertEqual(row.count('<button class="form-remove"'), 1)
                self.assertEqual(row.count('class="sr-only"'), 2)
                self.assertIn('aria-label="Remove ', row)
                self.assertIn('formnovalidate', row)
                self.assertIn('<svg width="14" height="14"', row)
                self.assertIn('aria-hidden="true"', row)
                self.assertNotIn(">Remove this form</button>", row)
                self.assertNotIn("<fieldset", row)

    def test_trash_button_is_disabled_only_for_single_form_side(self):
        html = self.client.get(self.edit_url).get_data(as_text=True)
        buttons = re.findall(r'<button class="form-remove"([^>]*)>', html)
        self.assertEqual(len(buttons), 3)
        for button in buttons:
            if 'value="remove_back_0"' in button:
                self.assertIn("disabled", button)
            else:
                self.assertNotIn("disabled", button)
        self.assertEqual(self.post(self.edit_url, self.edit_payload(
            action="remove_front_1",
        )).status_code, 200)
        self.assertEqual(self.post(self.edit_url, self.edit_payload(
            action="remove_back_0",
        )).status_code, 400)

    def test_validation_is_atomic(self):
        path = f"/decks/{self.deck_name}/cards/new"
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
                create_card(self.deck_name, {"front": [], "back": [("y", "")]}, "")
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 1)







    def test_exact_comparison_and_empty_answers(self):
        self.assertEqual(verdict("Straße", "STRASSE"), "correct")
        self.assertEqual(verdict("Bread", "bread"), "correct")
        for answer in (" bread", "bread ", "bréad", "bread.", ""):
            self.assertEqual(verdict(answer, "bread"), "incorrect")










    def test_csrf_theme_escaping_and_script_policy(self):
        self.assertEqual(self.client.post("/logout").status_code, 400)
        self.assertEqual(self.client.post("/logout", data={"csrf": "☃"}).status_code, 400)
        response = self.post("/theme", {"theme": "light", "return_to": "https://evil.example"})
        self.assertEqual(response.headers["Location"], "/")
        self.assertEqual(self.session()["theme"], "light")
        self.post("/new-deck", {"name": "Escaping", "description": '<script>alert("x")</script>'})
        response = self.client.get("/")
        self.assertNotIn(b"<script>alert", response.data)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_missing_empty_pagination_and_limits(self):
        self.assertEqual(self.client.get("/decks/999").status_code, 404)
        self.assertEqual(self.client.get(f"/decks/{self.deck_name}?page=no").status_code, 400)
        self.assertEqual(self.client.get(f"/decks/{self.deck_name}?page=0").status_code, 400)
        with self.app.app_context():
            empty = create_deck("Empty", "", self.alice)
            for i in range(21):
                create_card(empty, {"front": [(str(i), "")], "back": [("x", "")]}, "")
            blank = create_deck("Blank", "", self.alice)
        self.assertIn(b"Next", self.client.get(f"/decks/{empty}").data)
        self.assertIn(b"Previous", self.client.get(f"/decks/{empty}?page=2").data)
        self.assertIn(b"No questions are available", self.post(f"/decks/{blank}/lessons", follow=True).data)
        self.assertEqual(self.client.post("/login", data=b"x" * 300_000,
                                         content_type="application/x-www-form-urlencoded").status_code, 413)







    def test_new_cards_appear_for_existing_users(self):
        self.seed_review()
        with self.app.app_context():
            create_card(self.deck_name, {
                "front": [("Haus", "")], "back": [("house", "")],
            }, "")
        response = self.client.get(f"/decks/{self.deck_name}")
        self.assertIn(b"1 seen", response.data)
        self.assertIn(b"4 new", response.data)

    def edit_payload(self, **changes):
        revision = self.rows("SELECT revision FROM cards WHERE id = ?", (self.card_id,))[0]["revision"]
        return {
            "revision": str(revision),
            "front_text": ["Brot", "Brote"],
            "front_description": ["nominative singular", "plural"],
            "back_text": ["bread"], "back_description": ["noun"], "meta": "neuter",
            **changes,
        }

    @property
    def edit_url(self):
        return f"/decks/{self.deck_name}/cards/{self.card_id}/edit"

    def test_deck_names_are_primary_keys_and_c_identifiers(self):
        self.assertEqual(self.deck_name, "German")
        with self.app.app_context():
            db = get_db()
            columns = db.execute("PRAGMA table_info(decks)").fetchall()
            self.assertNotIn("id", [column["name"] for column in columns])
            self.assertEqual([c["name"] for c in columns if c["pk"]], ["name"])
            self.assertEqual(db.execute("PRAGMA foreign_key_list(cards)").fetchone()["to"], "name")
        self.assertEqual(self.client.get("/decks/German").status_code, 200)
        self.assertEqual(self.client.get("/decks/1").status_code, 404)
        for name in ("new", "_", "_German_2", "Deck9"):
            response = self.post("/new-deck", {"name": name})
            self.assertEqual(response.status_code, 302, name)
            self.assertIn(f"/decks/{name}/", response.headers["Location"])
            self.assertEqual(self.client.get(f"/decks/{name}").status_code, 200)
        for name in ("", "1abc", "a-b", "a b", "déck", "foo/bar", " abc", "a" * 121):
            self.assertEqual(self.post("/new-deck", {"name": name}).status_code, 400, name)
            with self.app.app_context():
                with self.assertRaises(ValueError):
                    create_deck(name, "", self.alice)
                with self.assertRaises(sqlite3.IntegrityError), transaction(get_db()) as db:
                    db.execute("INSERT INTO decks (name, author_id, created_at) VALUES (?, ?, 0)",
                               (name, self.alice))

    def test_edit_form_preserves_question_progress(self):
        self.seed_review()
        questions = self.rows("SELECT * FROM questions")
        progress = self.rows("SELECT * FROM progress")
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="Brot"', response.data)
        self.assertIn(b'value="Brote"', response.data)
        response = self.post(self.edit_url, self.edit_payload(
            front_text=["Laib", "Laibe"], back_text=["loaf"],
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM questions"), questions)
        self.assertEqual(self.rows("SELECT * FROM progress"), progress)
        self.assertEqual(self.rows("SELECT revision FROM cards")[0]["revision"], 1)
        with self.app.app_context():
            data = question_data(questions[0]["id"], self.deck_name)
            self.assertEqual((data["prompt"], data["expected"]), ("Laib", "loaf"))

    def test_remove_forms_is_local_until_save_and_promotes_first(self):
        response = self.post(self.edit_url, self.edit_payload(action="remove_front_0"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'name="front_text"'), 1)
        self.assertNotIn(b'value="Brot"', response.data)
        self.assertIn(b'value="Brote"', response.data)
        self.assertIn(b'value="bread"', response.data)
        self.assertEqual(len(self.rows("SELECT * FROM card_forms WHERE side='front'")), 2)
        response = self.post(self.edit_url, self.edit_payload(
            front_text=["Brote"], front_description=["plural"],
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT text, position FROM card_forms WHERE side='front'"),
                         [{"text": "Brote", "position": 0}])
        response = self.post(self.edit_url, self.edit_payload(
            front_text=["Brote"], front_description=["plural"], action="remove_front_0",
        ))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.rows("SELECT * FROM card_forms WHERE side='front'")), 1)

    def test_create_form_can_remove_rows_without_saving(self):
        response = self.post(f"/decks/{self.deck_name}/cards/new", self.edit_payload(
            action="remove_front_1",
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'name="front_text"'), 1)
        self.assertIn(b'value="Brot"', response.data)
        self.assertNotIn(b'value="Brote"', response.data)
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 1)
        self.assertEqual(self.post(self.edit_url, self.edit_payload(
            action="remove_front_100",
        )).status_code, 400)

    def test_edit_metadata_removal_and_readdition(self):
        for question in self.rows("SELECT id FROM questions"):
            self.seed_review(question["id"])
        self.assertEqual(len(self.rows("SELECT * FROM progress")), 3)
        response = self.post(self.edit_url, self.edit_payload(meta=""))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.rows("SELECT * FROM questions")), 2)
        self.assertEqual(len(self.rows("SELECT * FROM progress")), 2)
        response = self.post(self.edit_url, self.edit_payload(meta="gender"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.rows("SELECT * FROM questions")), 3)
        self.assertEqual(len(self.rows("SELECT * FROM progress")), 2)

    def test_edit_stale_revision_and_validation_are_atomic(self):
        payload = self.edit_payload(back_text=["loaf"])
        self.assertEqual(self.post(self.edit_url, payload).status_code, 302)
        self.assertEqual(self.post(self.edit_url, payload).status_code, 409)
        saved = self.rows("SELECT * FROM card_forms")
        self.assertEqual(self.post(self.edit_url, self.edit_payload(
            front_text=[""], front_description=[""],
        )).status_code, 400)
        self.assertEqual(self.rows("SELECT * FROM card_forms"), saved)
        self.assertEqual(self.rows("SELECT revision FROM cards")[0]["revision"], 1)

    def test_edit_is_shared_but_card_is_scoped_to_its_deck(self):
        bob = self.app.test_client()
        self.login("Bob", bob)
        response = self.post(self.edit_url, self.edit_payload(), bob)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT author_id FROM decks")[0]["author_id"], self.alice)
        self.post("/new-deck", {"name": "Other"})
        path = f"/decks/Other/cards/{self.card_id}/edit"
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.post(path, self.edit_payload()).status_code, 404)



    def test_quick_add_and_validation(self):
        path = f"/decks/{self.deck_name}/cards/quick"
        response = self.post(path, {"front": "Haus", "back": "house"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Edit card", response.data)
        card = self.rows("SELECT * FROM cards WHERE id = 2")[0]
        self.assertEqual((card["deck_name"], card["meta"]), ("German", ""))
        self.assertEqual(len(self.rows("SELECT * FROM questions WHERE card_id=2")), 2)
        forms = self.rows("SELECT side, text, description FROM card_forms WHERE card_id=2")
        self.assertEqual({r["text"] for r in forms}, {"Haus", "house"})
        self.assertTrue(all(r["description"] == "" for r in forms))
        for changes in ({"front": ""}, {"front": " "}, {"back": "x" * 501}):
            response = self.post(path, {"front": "Tree", "back": "Baum", **changes})
            self.assertEqual(response.status_code, 400)
            self.assertIn(b'data-quick-add', response.data)
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 2)
        self.assertEqual(self.client.post(path, data={"front": "x", "back": "y"}).status_code, 400)
        self.assertEqual(self.client.get(path).status_code, 405)

    def test_quick_add_keyboard_script_is_external_and_scoped(self):
        response = self.client.get(f"/decks/{self.deck_name}")
        self.assertIn(b'enterkeyhint="next"', response.data)
        self.assertIn(b'enterkeyhint="done"', response.data)
        self.assertIn(b'src="/static/quick-add.js" defer', response.data)
        self.assertNotIn(b"quick-add.js", self.client.get("/").data)
        with self.client.get("/static/quick-add.js") as response:
            script = response.get_data(as_text=True)
        self.assertIn("event.isComposing", script)
        self.assertIn("back.focus()", script)
        self.assertIn("quickAdd.requestSubmit()", script)

    def test_header_is_fixed_and_username_menu_has_explicit_themes(self):
        lesson_url = self.post(f"/decks/{self.deck_name}/lessons").headers["Location"]
        for path in ("/", f"/decks/{self.deck_name}", lesson_url, self.edit_url, "/new-deck"):
            response = self.client.get(path)
            html = response.get_data(as_text=True)
            header = re.search(r"<header>(.*?)</header>", html, re.S).group(1)
            nav = re.search(r"<nav.*?</nav>", header, re.S).group(0)
            self.assertEqual(re.findall(r'href="([^"]+)"', nav), ["/"])
            self.assertIn("<summary>Alice</summary>", header)
            self.assertIn(">Logout</button>", header)
            self.assertNotIn("Signed in as", html)
            self.assertNotIn('class="brand"', html)
            self.assertNotIn("<footer", html)
            self.assertNotIn("All decks are shared. Progress is yours.", html)
        for theme in ("dark", "system", "light"):
            response = self.post("/theme", {"theme": theme, "return_to": f"/decks/{self.deck_name}"}, follow=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn(f'data-theme="{theme}"'.encode(), response.data)
        self.assertEqual(self.post("/theme", {"theme": "invalid"}).status_code, 400)
        self.assertEqual(self.post("/logout").status_code, 302)
        self.assertNotIn("user_id", self.session())







    def test_sides_layout_is_shared_and_descriptions_stay_in_form_rows(self):
        browse = self.client.get(f"/decks/{self.deck_name}").get_data(as_text=True)
        for html in (browse,):
            sides = re.findall(
                r'<section class="card-side card-side--(front|back)"[^>]*>(.*?)</section>',
                html, re.S,
            )
            self.assertEqual([side for side, _ in sides], ["front", "back"])
            for (side, content), expected in zip(sides, (
                [("Brot", "nominative singular"), ("Brote", "plural")],
                [("bread", "noun")],
            )):
                self.assertNotIn("side-heading", content)
                self.assertNotIn(f">{side.title()}<", content)
                self.assertIn(f'aria-label="{side.title()} forms"', content)
                rows = re.findall(r'<li class="form-row[^"]*">(.*?)</li>', content, re.S)
                self.assertEqual(len(rows), len(expected))
                for row, (text, description) in zip(rows, expected):
                    self.assertIn(f'<span class="form-text">{text}</span>', row)
                    self.assertIn(f'class="muted form-description">{description}</small>', row)
                self.assertEqual(content.count(" canonical"), 1)
            self.assertIn('class="card-meta"', html)
            self.assertIn("neuter", html)

    def test_card_sides_support_unequal_forms_and_blank_descriptions(self):
        response = self.post(self.edit_url, self.edit_payload(
            front_text=["<front>"], front_description=[""],
            back_text=["one", "two", "three"],
            back_description=["first", "", "<third>"],
        ), follow=True)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        sides = dict(re.findall(
            r'<section class="card-side card-side--(front|back)"[^>]*>(.*?)</section>',
            html, re.S,
        ))
        self.assertEqual(sides["front"].count('<li class="form-row'), 1)
        self.assertEqual(sides["back"].count('<li class="form-row'), 3)
        self.assertIn("form-row--plain", sides["front"])
        self.assertIn("&lt;front&gt;", sides["front"])
        self.assertIn("&lt;third&gt;", sides["back"])
        self.assertNotIn("<third>", sides["back"])
        self.assertLess(sides["back"].index(">one<"), sides["back"].index(">two<"))
        self.assertLess(sides["back"].index(">two<"), sides["back"].index(">three<"))

    def test_card_layout_has_responsive_dividers_and_two_cell_form_rows(self):
        with self.client.get("/static/fcard.css") as response:
            css = response.get_data(as_text=True)
        self.assertIn(".card-sides { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }", css)
        self.assertIn(".card-side--back { border-left: 2px solid var(--accent); }", css)
        self.assertIn("@media (max-width: 600px)", css)
        self.assertIn(".card-sides { grid-template-columns: minmax(0, 1fr); }", css)
        self.assertIn("border-top: 3px solid var(--accent)", css)
        self.assertRegex(css, r"(?s)\.form-row \{.*?grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\);.*?align-items: start;")
        self.assertNotIn(".form-description { display: block;", css)

    def test_edit_link_is_a_compact_icon_in_card_heading(self):
        html = self.client.get(f"/decks/{self.deck_name}").get_data(as_text=True)
        heading = re.search(
            r'<h3 class="card-header card-header--editable">(.*?)</h3>', html, re.S,
        ).group(1)
        self.assertIn(f'href="{self.edit_url}"', heading)
        self.assertIn(f'aria-label="Edit card #{self.card_id}"', heading)
        self.assertIn('title="Edit card"', heading)
        self.assertIn('<svg width="14" height="14"', heading)
        self.assertIn('aria-hidden="true"', heading)
        self.assertEqual(html.count(f'href="{self.edit_url}"'), 1)
        self.assertNotIn(">Edit card</a>", html)

    def search_result_ids(self, query, page=1):
        response = self.client.get("/search", query_string={"q": query, "page": page})
        self.assertEqual(response.status_code, 200)
        ids = [int(value) for value in re.findall(
            r'<section class="card" id="card-(\d+)">', response.get_data(as_text=True),
        )]
        return ids, response

    def test_search_covers_every_form_on_both_sides_and_all_decks(self):
        with self.app.app_context():
            other = create_deck("Other", "", self.alice)
            card_id = create_card(other, {
                "front": [("arbeiten", ""), ("gearbeitet", "past")],
                "back": [("work", ""), ("worked", "past")],
            }, "secretmetadata")
        for query in ("ARBEITEN", "gearbeitet", "work", "worked"):
            ids, response = self.search_result_ids(query)
            self.assertEqual(ids, [card_id])
            self.assertIn(b'href="/decks/Other"', response.data)
            self.assertIn(f'/decks/Other/cards/{card_id}/edit'.encode(), response.data)
        ids, _ = self.search_result_ids("Brote")
        self.assertEqual(ids, [self.card_id])
        ids, _ = self.search_result_ids("bread")
        self.assertEqual(ids, [self.card_id])
        for query in ("plural", "secretmetadata", "breadBrot"):
            self.assertEqual(self.search_result_ids(query)[0], [])

    def test_search_handles_umlauts_transliterations_and_punctuation(self):
        with self.app.app_context():
            card_id = create_card(self.deck_name, {
                "front": [("die, Bückerei?", "")],
                "back": [("bakehouse", "")],
            }, "")
        for query in ("buck", "bück", "bueck", "BÜCK", "b.u-c k"):
            self.assertEqual(self.search_result_ids(query)[0], [card_id])

    def test_search_returns_each_card_once_and_keeps_own_progress(self):
        self.seed_review()
        ids, response = self.search_result_ids("brot")
        self.assertEqual(ids, [self.card_id])
        self.assertIn(b"1/3 questions seen", response.data)
        self.assertEqual(len(self.rows("SELECT * FROM progress")), 1)
        bob = self.app.test_client()
        self.login("Bob", bob)
        response = bob.get("/search", query_string={"q": "brot"})
        self.assertIn(b"0/3 questions seen", response.data)
        self.assertEqual(len(self.rows("SELECT * FROM progress")), 1)

    def test_search_pagination_preserves_query(self):
        with self.app.app_context():
            for i in range(22):
                create_card(self.deck_name, {
                    "front": [(f"Bückerei {i}", "")], "back": [("bakery", "")],
                }, "")
        query = "BÜC-K!"
        first, response = self.search_result_ids(query)
        self.assertEqual(len(first), 20)
        html = unescape(response.get_data(as_text=True))
        links = re.findall(r'href="([^"]+)"', html)
        next_link = next(link for link in links if "page=2" in link)
        next_response = self.client.get(next_link)
        self.assertEqual(next_response.status_code, 200)
        second = [int(value) for value in re.findall(
            r'id="card-(\d+)"', next_response.get_data(as_text=True),
        )]
        self.assertEqual(len(second), 2)
        self.assertFalse(set(first) & set(second))
        self.assertIn('value="BÜC-K!"', next_response.get_data(as_text=True))

    def test_search_empty_limits_and_no_sql_or_html_injection(self):
        for query in ("", "!!!", "_%", "中文", "no-such-word"):
            self.assertEqual(self.search_result_ids(query)[0], [])
        ids, response = self.search_result_ids('<script>alert("x")</script>')
        self.assertEqual(ids, [])
        self.assertNotIn(b"<script>alert", response.data)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertEqual(self.search_result_ids("' OR 1=1 --")[0], [])
        self.assertEqual(self.client.get("/search", query_string={"q": "x" * 501}).status_code, 400)
        self.assertEqual(self.client.get("/search?q=brot&page=bad").status_code, 400)
        self.assertEqual(self.app.test_client().get("/search?q=brot").status_code, 302)

    def test_search_forms_are_get_requests_and_do_not_change_pending_review(self):
        self.post(f"/decks/{self.deck_name}/lessons")
        pending = self.rows("SELECT * FROM lessons")
        for path in ("/", f"/decks/{self.deck_name}", "/search"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn('class="word-search" method="get" action="/search"', html)
        self.search_result_ids("brot")
        self.assertEqual(self.rows("SELECT * FROM lessons"), pending)


    def test_search_uses_current_text_after_edit(self):
        self.assertEqual(self.search_result_ids("brot")[0], [self.card_id])
        self.post(self.edit_url, self.edit_payload(
            front_text=["Haus"], front_description=[""],
        ))
        self.assertEqual(self.search_result_ids("brot")[0], [])
        self.assertEqual(self.search_result_ids("haus")[0], [self.card_id])

    def test_search_on_reopened_populated_database_preserves_schema_and_data(self):
        with self.app.app_context():
            db = get_db()
            version = db.execute("PRAGMA user_version").fetchone()[0]
            schema = db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
            before = [tuple(row) for row in schema]
            forms = [tuple(row) for row in db.execute("SELECT * FROM card_forms")]
        reopened = create_app({
            "TESTING": True, "DATABASE": self.database, "SECRET_KEY": "test",
        })
        with reopened.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], version)
            self.assertEqual([tuple(r) for r in db.execute(
                "SELECT sql FROM sqlite_master ORDER BY name"
            )], before)
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM card_forms")], forms)
            self.assertEqual(db.execute(
                "SELECT word_matches(text, 'BROT') FROM card_forms WHERE text='Brot'"
            ).fetchone()[0], 1)

    def test_live_search_uses_same_normalization_and_all_forms(self):
        with self.app.app_context():
            other = create_deck("Other", "", self.alice)
            card_id = create_card(other, {
                "front": [("die, Bückerei?", ""), ("Bückereien", "plural")],
                "back": [("bakery", ""), ("bakeries", "plural")],
            }, "")
        for query in ("buck", "bueck", "BÜCK", "bakeries", "buckereien"):
            response = self.client.get("/search/live", query_string={"q": query})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual([card["id"] for card in data["results"]], [card_id])
            self.assertEqual(data["query"], query)
            self.assertTrue(data["searchable"])
            self.assertFalse(data["more"])
            result = data["results"][0]
            self.assertEqual(result["front"], "die, Bückerei?")
            self.assertEqual(result["back"], "bakery")
            self.assertEqual(result["deck"], "Other")
            if query == "bakeries":
                self.assertEqual(result["match"], {"text": "bakeries", "description": "plural"})
            if query == "buck":
                self.assertIsNone(result["match"])
            self.assertIn(f"#card-{card_id}", result["url"])
            self.assertEqual(self.client.get(result["url"]).status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_live_search_is_bounded_and_full_page_still_has_twenty(self):
        with self.app.app_context():
            for i in range(10):
                create_card(self.deck_name, {
                    "front": [(f"live word {i}", "")], "back": [("English", "")],
                }, "")
        response = self.client.get("/search/live?q=live")
        data = response.get_json()
        self.assertEqual(len(data["results"]), 8)
        self.assertTrue(data["more"])
        ids = [row["id"] for row in data["results"]]
        self.assertEqual(len(set(ids)), 8)
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(len(self.search_result_ids("live")[0]), 10)

    def test_live_search_limits_auth_and_does_not_change_progress(self):
        self.post(f"/decks/{self.deck_name}/lessons")
        pending = self.rows("SELECT * FROM lessons")
        for query in ("", "!!!", "中文"):
            data = self.client.get("/search/live", query_string={"q": query}).get_json()
            self.assertFalse(data["searchable"])
            self.assertEqual(data["results"], [])
        response = self.client.get("/search/live", query_string={"q": "x" * 501})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())
        response = self.app.test_client().get("/search/live?q=brot")
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.get_json())
        self.assertEqual(self.client.get("/search/live?q=brot").status_code, 200)
        self.assertEqual(self.rows("SELECT * FROM lessons"), pending)
        self.assertEqual(self.rows("SELECT * FROM progress"), [])


    def test_live_search_script_and_accessible_panel_are_on_search_pages(self):
        for path in ("/", f"/decks/{self.deck_name}", "/search"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn('src="/static/search.js" defer', html)
            self.assertIn('data-live-url="/search/live"', html)
            self.assertIn('aria-controls="live-search-results"', html)
            self.assertIn('aria-label="Live search results" hidden', html)
            self.assertIn('aria-live="polite"', html)
            self.assertIn('method="get" action="/search"', html)
        self.assertNotIn(b"static/search.js", self.client.get("/login", follow_redirects=False).data)
        with self.client.get("/static/search.js") as response:
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("innerHTML", response.get_data(as_text=True))

    def suggestion_request(self, words, exclude=None, client=None):
        client = client or self.client
        # Fetch a rendered page to obtain a CSRF token after login rotated the session.
        client.get("/")
        token = self.session(client)["csrf"]
        return client.post("/cards/suggestions", json={"words": words, "exclude": exclude},
                           headers={"X-CSRF-Token": token})

    def test_add_and_edit_trim_entry_fields_without_changing_inner_spaces(self):
        quick = self.post(f"/decks/{self.deck_name}/cards/quick", {
            "front": "  die  Tür \t", "back": "\n the  door  ",
        })
        self.assertEqual(quick.status_code, 302)
        self.assertEqual(self.rows(
            "SELECT text FROM card_forms WHERE card_id=2 ORDER BY side"
        ), [{"text": "the  door"}, {"text": "die  Tür"}])
        response = self.post(f"/decks/{self.deck_name}/cards/new", self.edit_payload(
            front_text=["  gehen  ", "\tging\n"], front_description=[" ", " past "],
            back_text=[" go "], back_description=[" verb "], meta=" irregular ",
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT meta FROM cards WHERE id=3")[0]["meta"], "irregular")
        self.assertEqual(self.rows(
            "SELECT text, description FROM card_forms WHERE card_id=3 AND side='front' "
            "ORDER BY position"
        ), [{"text": "gehen", "description": ""}, {"text": "ging", "description": "past"}])
        response = self.post(self.edit_url, self.edit_payload(
            front_text=["  Laib  "], front_description=["  singular  "],
            back_text=["  loaf  "], back_description=["  noun  "], meta="  neutral  ",
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows(
            "SELECT text, description FROM card_forms WHERE card_id=? AND side='front'",
            (self.card_id,),
        ), [{"text": "Laib", "description": "singular"}])

    def test_model_trims_before_validating_lengths_and_keeps_grading_strict(self):
        with self.app.app_context():
            card_id = create_card(self.deck_name, {
                "front": [("  " + "x" * 500 + "  ", " " + "d" * 160 + " ")],
                "back": [("\u00a0word\u00a0", "")],
            }, " " + "m" * 500 + " ")
        self.assertEqual(len(self.rows(
            "SELECT text FROM card_forms WHERE card_id=? AND side='front'", (card_id,),
        )[0]["text"]), 500)
        self.assertEqual(self.rows(
            "SELECT text FROM card_forms WHERE card_id=? AND side='back'", (card_id,),
        )[0]["text"], "word")
        self.assertEqual(verdict(" bread ", "bread"), "incorrect")

    def test_suggestions_match_whole_forms_on_either_side_in_all_decks(self):
        with self.app.app_context():
            other = create_deck("Other", "", self.alice)
            card_id = create_card(other, {
                "front": [("Bückerei", ""), ("Bückereien", "plural")],
                "back": [("bakery", ""), ("bakeries", "plural")],
            }, "")
        for word in ("BÜCKEREI", "bueckerei", "buckerei", "bakeries"):
            response = self.suggestion_request([word])
            self.assertEqual(response.status_code, 200)
            results = response.get_json()["results"]
            self.assertEqual([r["id"] for r in results], [card_id])
            self.assertEqual(results[0]["deck"], "Other")
            self.assertEqual(self.client.get(results[0]["url"]).status_code, 200)
        self.assertEqual(self.suggestion_request(["buck"]).get_json()["results"], [])
        self.assertEqual(self.suggestion_request(["brote", "bread"]).get_json()["results"][0]["id"],
                         self.card_id)
        self.assertEqual(len(self.suggestion_request(["brot", "bread"]).get_json()["results"]), 1)

    def test_suggestions_are_advisory_and_exclude_current_card(self):
        self.assertEqual(self.suggestion_request(["brot"], self.card_id).get_json()["results"], [])
        response = self.post(f"/decks/{self.deck_name}/cards/quick", {
            "front": "Brot", "back": "bread",
        })
        self.assertEqual(response.status_code, 302)
        ids = [r["id"] for r in self.suggestion_request(["brot"]).get_json()["results"]]
        self.assertEqual(ids, [2, self.card_id])
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 2)
        self.assertEqual(len(self.rows("SELECT * FROM questions")), 5)

    def test_suggestions_are_bounded_with_links_to_actual_card_pages(self):
        with self.app.app_context():
            # Many newer nonmatching cards push the original card onto another page.
            for i in range(21):
                create_card(self.deck_name, {"front": [(str(i), "")], "back": [("new", "")]}, "")
        response = self.suggestion_request(["brot"])
        url = response.get_json()["results"][0]["url"]
        self.assertIn("page=2", url)
        self.assertIn(f'id="card-{self.card_id}"', self.client.get(url).get_data(as_text=True))
        with self.app.app_context():
            for i in range(6):
                create_card(self.deck_name, {"front": [("same", "")], "back": [(str(i), "")]}, "")
        data = self.suggestion_request(["same"]).get_json()
        self.assertEqual(len(data["results"]), 5)
        self.assertTrue(data["more"])

    def test_suggestions_validate_csrf_payload_and_do_not_change_reviews(self):
        self.post(f"/decks/{self.deck_name}/lessons")
        pending = self.rows("SELECT * FROM lessons")
        self.assertEqual(self.client.post("/cards/suggestions", json={"words": ["brot"]}).status_code, 400)
        for words in (None, "brot", [1], ["x" * 501], ["x"] * 201):
            self.assertEqual(self.suggestion_request(words).status_code, 400)
        self.assertEqual(self.suggestion_request(["brot"], True).status_code, 400)
        for words in ([], ["!!!"], ["中文"]):
            self.assertEqual(self.suggestion_request(words).get_json()["results"], [])
        response = self.suggestion_request(["brot"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.rows("SELECT * FROM lessons"), pending)
        self.assertEqual(self.rows("SELECT * FROM progress"), [])


    def test_entry_enhancement_scripts_and_hints_are_on_both_forms(self):
        for path in (f"/decks/{self.deck_name}", f"/decks/{self.deck_name}/cards/new", self.edit_url):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn('static/card-entry.js', html)
            self.assertIn('data-card-entry', html)
            self.assertIn('data-suggestions-url="/cards/suggestions"', html)
            self.assertIn('aria-label="Possible existing words" hidden', html)
        html = self.client.get(self.edit_url).get_data(as_text=True)
        self.assertIn('static/card-editor.js', html)
        self.assertIn('data-form-rows="front"', html)
        self.assertIn(f'data-card-id="{self.card_id}"', html)
        self.assertIn('value="add_front" formnovalidate', html)

    def german_card(self, back="der Morgen", **choices):
        with self.app.app_context():
            return create_card(self.deck_name, {
                "front": [("morning", "")], "back": [(back, "")],
            }, "", **choices)



    def test_quick_add_infers_gender_from_back_not_front(self):
        for back, gender in ((" der Morgen ", "masculine"), ("die Katze", "feminine"),
                             ("das Haus", "neuter")):
            response = self.post(f"/decks/{self.deck_name}/cards/quick",
                                 {"front": "English", "back": back})
            self.assertEqual(response.status_code, 302)
            card = self.rows("SELECT * FROM cards ORDER BY id DESC LIMIT 1")[0]
            self.assertEqual((card["word_type"], card["gender"]), ("noun", gender))
            self.assertEqual(len(self.rows("SELECT * FROM questions WHERE card_id=?",
                                          (card["id"],))), 3)
        self.post(f"/decks/{self.deck_name}/cards/quick",
                  {"front": "der Morgen", "back": "morning"})
        self.assertEqual(self.rows("SELECT word_type, gender FROM cards ORDER BY id DESC LIMIT 1"),
                         [{"word_type": None, "gender": None}])

    def test_quick_type_override_is_authoritative_and_gender_stays_silent(self):
        self.post(f"/decks/{self.deck_name}/cards/quick",
                  {"front": "morning", "back": "der Morgen", "word_type": "phrase",
                   "word_type_mode": "auto"})
        self.assertEqual(self.rows("SELECT word_type, gender FROM cards WHERE id=2"),
                         [{"word_type": "phrase", "gender": None}])
        html = self.client.get(f"/decks/{self.deck_name}").get_data(as_text=True)
        quick = re.search(r'<form class="quick-add".*?</form>', html, re.DOTALL)[0]
        self.assertEqual(quick.count('type="radio" name="word_type"'), 4)
        self.assertNotIn('name="gender"', quick)
        self.assertIn('static/word-classification.js', html)

    def test_full_add_infers_without_js_and_allows_explicit_gender_or_unknown(self):
        path = f"/decks/{self.deck_name}/cards/new"
        fields = self.edit_payload(front_text=["morning"], front_description=[""],
                                   back_text=["der Morgen"], back_description=[""], meta="")
        self.assertEqual(self.post(path, fields).status_code, 302)
        self.assertEqual(self.rows("SELECT word_type, gender FROM cards WHERE id=2"),
                         [{"word_type": "noun", "gender": "masculine"}])
        self.assertEqual(self.post(path, dict(fields, word_type="noun", gender="feminine",
                                             word_type_mode="auto", gender_mode="auto")).status_code, 302)
        self.assertEqual(self.rows("SELECT gender FROM cards WHERE id=3"), [{"gender": "feminine"}])
        self.assertEqual(self.post(path, dict(fields, word_type="noun", gender="")).status_code, 302)
        self.assertEqual(self.rows("SELECT word_type, gender FROM cards WHERE id=4"),
                         [{"word_type": "noun", "gender": None}])
        self.assertEqual(len(self.rows("SELECT * FROM questions WHERE card_id=4")), 2)
        for choices in ({"word_type": "adverb"}, {"word_type": "verb", "gender": "invalid"}):
            self.assertEqual(self.post(path, dict(fields, **choices)).status_code, 400)
        self.assertEqual(len(self.rows("SELECT * FROM cards")), 4)

    def test_classification_uses_only_the_first_back_form(self):
        with self.app.app_context():
            card_id = create_card(self.deck_name, {
                "front": [("der Morgen", "")],
                "back": [("groß", ""), ("das Haus", "")],
            }, "")
        self.assertEqual(self.rows("SELECT word_type, gender FROM cards WHERE id=?", (card_id,)),
                         [{"word_type": None, "gender": None}])





    def test_edit_gender_preserves_remaining_questions_and_schedules(self):
        self.seed_review()
        original = self.rows("SELECT * FROM progress")
        old_ids = self.rows("SELECT id, kind FROM questions WHERE card_id=?", (self.card_id,))
        fields = self.edit_payload(front_text=["morning"], front_description=[""],
                                   back_text=["der Morgen"], back_description=[""],
                                   word_type="noun", gender="masculine")
        self.assertEqual(self.post(self.edit_url, fields).status_code, 302)
        gender_id = self.rows("SELECT id FROM questions WHERE kind=\'gender\'")[0]["id"]
        self.seed_review(gender_id)
        gender_progress = self.rows("SELECT * FROM progress WHERE question_id=?", (gender_id,))
        self.assertEqual(self.post(self.edit_url, dict(fields, revision="1", gender="feminine")).status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM progress WHERE question_id=?", (gender_id,)), gender_progress)
        with self.app.app_context():
            self.assertEqual(question_data(gender_id, self.deck_name)["expected"], "die")
        self.assertEqual(self.post(self.edit_url, dict(fields, revision="2", gender="")).status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM progress"), original)
        self.assertEqual(self.rows("SELECT id, kind FROM questions WHERE card_id=?", (self.card_id,)), old_ids)


    def test_committed_unknown_or_inferred_classification_does_not_reinfer_on_edit(self):
        # Old clients omitting the fields must also preserve committed values.
        fields = self.edit_payload(back_text=["der Morgen"], back_description=[""])
        self.assertEqual(self.post(self.edit_url, fields).status_code, 302)
        self.assertEqual(self.rows("SELECT word_type, gender FROM cards WHERE id=?", (self.card_id,)),
                         [{"word_type": None, "gender": None}])
        card_id = self.german_card()
        path = f"/decks/{self.deck_name}/cards/{card_id}/edit"
        fields = self.edit_payload(revision="0", front_text=["night"], front_description=[""],
                                   back_text=["die Nacht"], back_description=[""], meta="")
        self.assertEqual(self.post(path, fields).status_code, 302)
        self.assertEqual(self.rows("SELECT word_type, gender FROM cards WHERE id=?", (card_id,)),
                         [{"word_type": "noun", "gender": "masculine"}])
        html = self.client.get(path).get_data(as_text=True)
        self.assertEqual(html.count('data-selection-mode="manual"'), 2)
        self.assertIn('name="gender" value="masculine" checked', html)
        self.assertEqual(self.post(path, dict(fields, revision="1", word_type="phrase")).status_code, 302)
        self.assertEqual(self.rows("SELECT word_type, gender FROM cards WHERE id=?", (card_id,)),
                         [{"word_type": "phrase", "gender": None}])
        self.assertEqual(self.rows("SELECT * FROM questions WHERE card_id=? AND kind='gender'",
                                  (card_id,)), [])



    def test_classification_db_constraints_and_no_persisted_provenance(self):
        card_id = self.german_card()
        with self.app.app_context():
            db = get_db()
            names = [row["name"] for row in db.execute("PRAGMA table_info(cards)")]
            self.assertIn("word_type", names)
            self.assertIn("gender", names)
            self.assertFalse(any("mode" in name or "automatic" in name for name in names))
            for column, value in (("word_type", "invalid"), ("gender", "invalid"), ("word_type", "verb")):
                with self.assertRaises(sqlite3.IntegrityError), transaction(db):
                    db.execute(f"UPDATE cards SET {column}=? WHERE id=?", (value, card_id))

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
