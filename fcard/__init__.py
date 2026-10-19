"""fcard application factory."""

from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import tempfile

from flask import Flask

from . import db
from .schedulers import Buckets


def persistent_secret(instance):
    """Publish a complete key atomically, safe for simultaneous worker startup."""
    key_path = Path(instance) / "secret.key"
    if not key_path.exists():
        with tempfile.NamedTemporaryFile(dir=instance) as temp:
            temp.write(secrets.token_bytes(32))
            temp.flush()
            try:
                os.link(temp.name, key_path)
            except FileExistsError:
                pass
    key = key_path.read_bytes()
    if len(key) < 32:
        raise RuntimeError("instance/secret.key must contain at least 32 bytes")
    return key


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=str(Path(app.instance_path) / "fcard.sqlite3"),
        SECRET_KEY=os.environ.get("FCARD_SECRET_KEY"),
        DEFAULT_ALGORITHM="buckets-v1",
        SCHEDULERS=None,
        MAX_CONTENT_LENGTH=256 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    app.config.from_prefixed_env(prefix="FCARD")
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = persistent_secret(app.instance_path)
    if app.config["SCHEDULERS"] is None:
        app.config["SCHEDULERS"] = {"buckets-v1": Buckets()}
    if app.config["DEFAULT_ALGORITHM"] not in app.config["SCHEDULERS"]:
        raise RuntimeError("DEFAULT_ALGORITHM must name a registered scheduler")
    database = app.config["DATABASE"]
    if database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(db.close_db)
    with app.app_context():
        db.initialize()

    from .views import bp
    app.register_blueprint(bp)

    @app.template_filter("timestamp")
    def timestamp(value):
        if value is None:
            return "—"
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return app
