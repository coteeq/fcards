from concurrent.futures import ThreadPoolExecutor
import json
import uuid
import unittest
from unittest.mock import patch

import test_app
from fcard.db import get_db, transaction
from fcard import lessons
from fcard.models import create_card, update_card
from fcard.schedulers import Buckets


class LessonTest(unittest.TestCase):
    # Reuse setup/login/CRUD fixtures without inheriting the old application's tests.
    setUp = test_app.AppTest.setUp
    tearDown = test_app.AppTest.tearDown
    session = test_app.AppTest.session
    post = test_app.AppTest.post
    login = test_app.AppTest.login
    rows = test_app.AppTest.rows

    def create(self):
        response = self.post(f"/decks/{self.deck_name}/lessons")
        self.assertEqual(response.status_code, 302)
        url = response.headers["Location"]
        lesson_id = url.rsplit("/", 1)[1]
        self.assertEqual(str(uuid.UUID(lesson_id)), lesson_id)
        return lesson_id, self.client.get("/api/lessons/" + lesson_id).get_json()

    def save(self, lesson_id, data, finish=False, client=None):
        client = client or self.client
        token = self.session(client)["csrf"]
        return client.post(f"/api/lessons/{lesson_id}/{'finish' if finish else 'state'}",
                           json=data, headers={"X-CSRF-Token": token})

    def state(self, n=20, rating="correct", control=None):
        return {"cursor": min(n, 19), "ratings": [{"rating": rating, "control": control} for _ in range(n)]}

    def test_creation_is_stable_owned_and_exactly_twenty_questions(self):
        lesson_id, data = self.create()
        self.assertEqual(len(data["questions"]), 20)
        ids = [q["question_id"] for q in data["questions"]]
        self.assertEqual(len(set(ids[:3])), 3)
        self.assertTrue(all(a != b for a, b in zip(ids, ids[1:])))
        self.assertEqual(self.client.get("/api/lessons/" + lesson_id).get_json(), data)
        self.assertEqual(data["state"], {"cursor": 0, "ratings": []})
        self.assertEqual(self.rows("SELECT * FROM progress"), [])
        self.assertIn(lesson_id.encode(), self.client.get("/").data)
        html = self.client.get("/lessons/" + lesson_id).get_data(as_text=True)
        self.assertIn("static/lesson.js", html)
        self.assertIn("static/casefold.js", html)
        self.assertIn("data-lesson-stage", html)
        self.assertIn("noscript", html)

    def test_twenty_distinct_questions_when_pool_is_large_enough(self):
        with self.app.app_context():
            for i in range(12):
                create_card(self.deck_name, {"front": [(str(i), "")], "back": [("word", "")]}, "")
        _, data = self.create()
        self.assertEqual(len({q["question_id"] for q in data["questions"]}), 20)

    def test_partial_sync_survives_session_loss_without_scheduling(self):
        lesson_id, data = self.create()
        state = self.state(4)
        result = self.save(lesson_id, {"revision": 0, "state": state})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["revision"], 1)
        self.assertEqual(self.rows("SELECT * FROM progress"), [])
        self.post("/logout")
        self.login("Alice")
        response = self.client.get("/api/lessons/" + lesson_id)
        self.assertEqual(response.get_json()["state"], state)
        self.assertIsNone(response.get_json()["completed_at"])
        self.assertIn(b"4/20 checked", self.client.get("/").data)

    def test_whole_lesson_completion_is_atomic_and_idempotent(self):
        lesson_id, data = self.create()
        payload = {"revision": 0, "state": self.state()}
        response = self.save(lesson_id, payload, finish=True)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.get_json()["completed_at"])
        self.assertEqual(sum(p["reviews"] for p in self.rows("SELECT * FROM progress")), 20)
        before = self.rows("SELECT * FROM progress")
        self.assertEqual(self.save(lesson_id, payload, finish=True).status_code, 200)
        self.assertEqual(self.save(lesson_id, {"revision": 0, "state": self.state(1)}).status_code, 200)
        self.assertEqual(self.rows("SELECT * FROM progress"), before)
        self.assertNotIn(lesson_id.encode(), self.client.get("/").data)
        self.assertEqual(self.client.get("/lessons/" + lesson_id).status_code, 200)

    def test_two_lessons_apply_to_latest_schedules_not_creation_snapshots(self):
        first, _ = self.create()
        second, _ = self.create()
        for lesson_id in (first, second):
            self.assertEqual(self.save(lesson_id, {"revision": 0, "state": self.state()}, True).status_code, 200)
        self.assertEqual(sum(p["reviews"] for p in self.rows("SELECT * FROM progress")), 40)

    def test_no_answers_automatic_verdict_or_correction_flags_are_persisted(self):
        lesson_id, _ = self.create()
        payload = {"revision": 0, "state": self.state(1)}
        payload["state"]["ratings"][0]["answer"] = "bread"
        self.assertEqual(self.save(lesson_id, payload).status_code, 400)
        payload["state"]["ratings"][0] = {"rating": "incorrect", "control": "up5"}
        self.assertEqual(self.save(lesson_id, payload).status_code, 200)
        state = json.loads(self.rows("SELECT state_json FROM lessons")[0]["state_json"])
        self.assertEqual(state["ratings"], [{"rating": "incorrect", "control": "up5"}])
        self.assertEqual(self.rows("SELECT name FROM sqlite_master WHERE name='review_log'"), [])

    def test_conflicting_tabs_are_rejected_but_lost_acknowledgements_can_retry(self):
        lesson_id, _ = self.create()
        first = {"revision": 0, "state": self.state(2)}
        result = self.save(lesson_id, first).get_json()
        self.assertEqual(self.save(lesson_id, first).get_json()["revision"], result["revision"])
        stale = self.save(lesson_id, {"revision": 0, "state": self.state(1)})
        self.assertEqual(stale.status_code, 409)
        self.assertIn("another tab", stale.get_json()["error"])

    def test_malformed_states_and_premature_completion_cannot_schedule(self):
        lesson_id, _ = self.create()
        invalid = [None, [], {}, {"revision": True, "state": self.state()},
                   {"revision": 0, "state": {"cursor": 20, "ratings": []}},
                   {"revision": 0, "state": {"cursor": 0, "ratings": self.state(2)["ratings"]}},
                   {"revision": 0, "state": self.state(1, rating="bad")},
                   {"revision": 0, "state": self.state(1, control="bad")},
                   {"revision": 0, "state": self.state(1), "corrected": True}]
        for payload in invalid:
            self.assertEqual(self.save(lesson_id, payload).status_code, 400, payload)
        self.assertEqual(self.save(lesson_id, {"revision": 0, "state": self.state(19)}, True).status_code, 400)
        self.assertEqual(self.rows("SELECT * FROM progress"), [])
        self.assertEqual(self.rows("SELECT revision FROM lessons"), [{"revision": 0}])

    def test_ownership_authentication_and_csrf(self):
        lesson_id, _ = self.create()
        self.assertEqual(self.client.post(f"/api/lessons/{lesson_id}/state", json={}).status_code, 400)
        guest = self.app.test_client()
        self.assertEqual(guest.get("/api/lessons/" + lesson_id).status_code, 401)
        bob = self.app.test_client()
        self.login("Bob", bob)
        bob.get("/")  # initialize a CSRF token after login rotated the session
        self.assertEqual(bob.get("/api/lessons/" + lesson_id).status_code, 404)
        self.assertEqual(bob.get("/lessons/" + lesson_id).status_code, 404)
        self.assertEqual(self.save(lesson_id, {"revision": 0, "state": self.state()}, True, bob).status_code, 404)
        self.assertNotIn(lesson_id.encode(), bob.get("/").data)
        self.assertEqual(self.rows("SELECT * FROM progress"), [])

    def test_edits_preserve_lesson_text_but_skip_stale_scheduling(self):
        lesson_id, data = self.create()
        with self.app.app_context():
            update_card(self.deck_name, self.card_id,
                        {"front": [("new", "")], "back": [("neu", "")]}, "", 0)
        loaded = self.client.get("/api/lessons/" + lesson_id).get_json()
        self.assertEqual(loaded["questions"], data["questions"])
        self.assertIn(None, [r["question_id"] for r in self.rows("SELECT question_id FROM lesson_items")])
        response = self.save(lesson_id, {"revision": 0, "state": self.state()}, True)
        self.assertEqual(response.get_json()["skipped"], 20)
        self.assertEqual(self.rows("SELECT * FROM progress"), [])

    def test_scheduler_failure_rolls_back_every_progress_write_and_completion(self):
        lesson_id, _ = self.create()
        scheduler = self.app.config["SCHEDULERS"]["buckets-v1"]
        original = scheduler.review
        count = 0
        def fail(state, rating, now, control=None):
            nonlocal count
            count += 1
            if count == 5:
                raise ValueError("injected scheduler failure")
            return original(state, rating, now, control)
        with patch.object(scheduler, "review", side_effect=fail):
            self.assertEqual(self.save(lesson_id, {"revision": 0, "state": self.state()}, True).status_code, 400)
        self.assertEqual(self.rows("SELECT * FROM progress"), [])
        self.assertEqual(self.rows("SELECT completed_at, revision FROM lessons"), [{"completed_at": None, "revision": 0}])

    def test_unknown_scheduler_is_not_silently_replaced(self):
        lesson_id, _ = self.create()
        self.app.config["SCHEDULERS"] = {}
        response = self.save(lesson_id, {"revision": 0, "state": self.state()}, True)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.rows("SELECT * FROM progress"), [])

    def test_recorded_algorithms_and_due_dates_survive_default_changes(self):
        clock = test_app.ClockScheduler()
        self.app.config["SCHEDULERS"][clock.id] = clock
        self.app.config["DEFAULT_ALGORITHM"] = clock.id
        lesson_id, _ = self.create()
        self.assertEqual(self.save(lesson_id, {"revision": 0, "state": self.state()}, True).status_code, 200)
        self.app.config["DEFAULT_ALGORITHM"] = "buckets-v1"
        response = self.post(f"/decks/{self.deck_name}/lessons", follow=True)
        self.assertIn(b"No questions are available", response.data)
        self.assertEqual(self.rows("SELECT DISTINCT algorithm FROM progress"), [{"algorithm": clock.id}])
        with self.app.app_context():
            create_card(self.deck_name, {"front": [("new", "")], "back": [("neu", "")]}, "")
        new_id, _ = self.create()
        self.assertEqual(self.rows("SELECT DISTINCT algorithm FROM lesson_items WHERE lesson_id=?", (new_id,)),
                         [{"algorithm": "buckets-v1"}])

    def test_final_ratings_and_extra_adjustments_alone_drive_the_scheduler(self):
        lesson_id, _ = self.create()
        response = self.save(lesson_id, {"revision": 0, "state": self.state(rating="incorrect", control="up5")}, True)
        self.assertEqual(response.status_code, 200)
        progress = self.rows("SELECT * FROM progress")
        self.assertEqual(sum(row["lapses"] for row in progress), 20)
        self.assertTrue(all(json.loads(row["state_json"])["bucket"] == 10 for row in progress))

    def test_concurrent_finish_commits_once(self):
        lesson_id, _ = self.create()
        payload = {"revision": 0, "state": self.state()}
        def finish(_):
            with self.app.app_context():
                return lessons.save(lesson_id, self.alice, payload,
                                    self.app.config["SCHEDULERS"], 1234, finishing=True)["completed_at"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(finish, range(4))), [1234] * 4)
        self.assertEqual(sum(p["reviews"] for p in self.rows("SELECT * FROM progress")), 20)

    def test_gender_questions_have_articleless_snapshots_and_unicode_expected_keys(self):
        with self.app.app_context():
            create_card(self.deck_name, {"front": [("street", "")], "back": [("die Straße", "")]}, "")
        _, data = self.create()
        gender = next(q for q in data["questions"] if q["kind"] == "gender")
        self.assertEqual((gender["prompt"], gender["expected"]), ("Straße", "die"))
        back = next(q for q in data["questions"] if q["expected"] == "die Straße")
        self.assertEqual(back["expected_key"], "die strasse")
        self.assertEqual(back["forms"]["back"][0]["text"], "die Straße")
