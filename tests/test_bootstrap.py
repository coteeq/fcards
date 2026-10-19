from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fcard import create_app
from fcard.db import get_db


class BootstrapTest(unittest.TestCase):
    def login_cookie(self, app):
        client = app.test_client()
        client.get("/login")
        with client.session_transaction() as session:
            csrf = session["csrf"]
        response = client.post("/login", data={"csrf": csrf, "username": "Alice"})
        self.assertEqual(response.status_code, 302)
        return client.get_cookie("session").value

    def test_ephemeral_key_changes_and_logs_out_without_erasing_users(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {
                "TESTING": True, "DATABASE": str(Path(temp) / "db.sqlite3"),
                "SECRET_KEY": None,
            }
            first = create_app(config)
            cookie = self.login_cookie(first)
            second = create_app(config)
            self.assertEqual(len(first.config["SECRET_KEY"]), 32)
            self.assertNotEqual(first.config["SECRET_KEY"], second.config["SECRET_KEY"])
            client = second.test_client()
            client.set_cookie("session", cookie)
            response = client.get("/")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["Location"], "/login")
            with second.app_context():
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_explicit_key_can_preserve_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {
                "TESTING": True, "DATABASE": str(Path(temp) / "db.sqlite3"),
                "SECRET_KEY": "explicit-shared-secret",
            }
            first = create_app(config)
            cookie = self.login_cookie(first)
            second = create_app(config)
            client = second.test_client()
            client.set_cookie("session", cookie)
            self.assertEqual(client.get("/").status_code, 200)

    def test_schema_startup_is_concurrent_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            database = str(Path(temp) / "db.sqlite3")

            def bootstrap(_):
                app = create_app({
                    "TESTING": True, "DATABASE": database, "SECRET_KEY": "test",
                })
                with app.app_context():
                    return get_db().execute("PRAGMA user_version").fetchone()[0]

            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(bootstrap, range(8))), [2] * 8)

    def test_nonempty_unversioned_database_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as temp:
            database = str(Path(temp) / "db.sqlite3")
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE unrelated (id INTEGER)")
            with self.assertRaisesRegex(RuntimeError, "nonempty, unversioned"):
                create_app({"DATABASE": database, "SECRET_KEY": "test"})
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute("SELECT name FROM sqlite_master "
                                            "WHERE type='table'").fetchall(), [("unrelated",)])


if __name__ == "__main__":
    unittest.main()
