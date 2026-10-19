# fcard

A server-rendered Flask + SQLite flashcard app. No client-side JavaScript and no
passwords. The visual design and base stylesheet come from the sibling `cgi` app;
its MIT notice is preserved in `fcard/static/CGI-LICENSE.txt`.

## Run

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+ (SQLite with JSON
functions). Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`.

```sh
cd fcards
uv sync --locked
uv run --locked flask --app fcard run
```

Open <http://127.0.0.1:5000>. uv manages `.venv` automatically; no manual activation,
`pip`, or system `ensurepip` is needed. `uv run` also synchronizes the environment,
so the explicit `uv sync` step is optional.

Use `uv add <package>` to add dependencies and `uv lock --upgrade` to update the
lockfile. Keep `pyproject.toml` and `uv.lock` together when sharing the app.

The database and a persistent, private session-signing key are created automatically
under `instance/`. Restarting does not erase data or invalidate sessions. Database
initialization is transactional and safe for simultaneous worker startup. Unknown
schema versions are rejected; there is no destructive reset command.

For another database path:

```sh
FCARD_DATABASE=/absolute/path/to/fcard.sqlite3 uv run --locked flask --app fcard run
```

Optional settings:

- `FCARD_SECRET_KEY`: use an explicit, long random secret instead of the local key.
- `FCARD_SESSION_COOKIE_SECURE=true`: require HTTPS for the session cookie when
  deployed behind TLS.
- `FCARD_DEFAULT_ALGORITHM=buckets-v1`: default scheduler for previously unseen
  questions. Existing progress retains its recorded algorithm.

Flask's built-in server is for development. For deployment, use a WSGI server with
`fcard:create_app()`, a writable local database directory, and a stable secret.
Back up the SQLite database using SQLite's backup API (or stop the app before copying
the database and its WAL files). Keep `instance/` out of publicly served directories.

## Use

1. Enter a username. An unseen name creates a user; a known name resumes its progress.
   Names are case-sensitive. Anyone can assume any username: this is intentionally
   not secure authentication. Do not use this app to store secrets.
2. Create a shared deck. Its creator remains its attributed author, but any user can
   browse it or add cards.
3. Add a card with front and back text. Use “Add another … form” to append ordered
   forms without JavaScript. The first form on each side is canonical; blank extra
   rows are ignored. Each form can have a short description.
4. Optional metadata creates a third question. Every card always has front → back and
   back → front questions, each scheduled independently.
5. Type an answer and check it. Unicode case folding is the only normalization:
   spaces, accents, and punctuation still matter. Only the canonical first form is
   automatically accepted. All forms and both sides are then revealed.
6. Keep or correct the verdict and advance. Advancing finalizes exactly one review.
   Pausing or leaving before advancing does not count that unfinished question.
   Empty answers are incorrect, but can also be manually corrected.
7. Review history shows the automatic verdict, final rating, and state transition.
   Raw answers are not stored in that history.

Card/form text, metadata, and answers are limited to 500 characters; descriptions to
160 characters; each side to 100 forms. Creation and browsing are supported, but
editing and deletion are deliberately outside the MVP.

The UI has system/light/dark themes, is responsive, and uses the same optional Google
Font as `cgi`. If the font cannot be loaded, it falls back to monospace.

## Scheduling

`buckets-v1` is the bundled scheduler, not an approximation of FSRS:

- Buckets range from 0 to 10; new questions start at 0.
- Correct: +1 bucket. Incorrect: −3 buckets, clamped at the boundaries.
- Optional controls apply an extra +5 or −5 after the verdict, also clamped.
- Selection probability is proportional to `2 ** (-bucket)`.
- The immediately preceding question is excluded when another eligible question exists.
- There are no due dates or daily completion limits. All bucket questions remain
  available. `due_at` is NULL; review timestamps are audit information.

See **[plan.md](plan.md)** for the complete data model, rating semantics, and lifecycle.

### Another scheduler

The small interface is in `fcard/schedulers.py`; persistence is in
`fcard/reviews.py`. Supply scheduler instances through the application factory:

```python
from fcard import create_app
from fcard.schedulers import Buckets

app = create_app({
    "SCHEDULERS": {"buckets-v1": Buckets(), "my-algorithm-v1": MyScheduler()},
    "DEFAULT_ALGORITHM": "my-algorithm-v1",
})
```

A scheduler declares ratings (with a correctness flag), optional controls, default
ratings for automatic verdicts, eligibility, selection, and state transitions.
`TimeBased` is an optional eligibility/queue helper, not a bundled scheduling
algorithm. State is a JSON object. A transition returns new state and an optional UTC
Unix due timestamp. The application handles transactions, audit logs, CSRF, and
stale-review rejection. Mixed algorithm groups are selected round-robin within the
current study session.

Keep old schedulers registered while progress references them. Changing the default
does not reinterpret existing state. Migrating old progress is an explicit future
operation. No third-party scheduler code or FSRS dependency is included.

## Tests

From `fcards/`:

```sh
uv run --locked python -m unittest discover -s tests -v
```

Tests use temporary databases and Flask's test client; no live server is needed.
