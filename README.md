# fcard

A server-rendered Flask + SQLite flashcard app with username-only login. A small
vanilla JavaScript handler makes Enter advance/submit the quick-add form; all other
controls work without JavaScript. The visual design and base stylesheet come from the
sibling `cgi` app; its MIT notice is preserved in `fcard/static/CGI-LICENSE.txt`.

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

The current schema is version 2 and requires a fresh database. No migration or URL
compatibility layer for version 1 is included. If you have an earlier database, keep it
as a backup and point `FCARD_DATABASE` to a new file rather than overwriting it.

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
2. Create a shared deck with a C-style name: `[A-Za-z_][A-Za-z0-9_]*`, at most 120
   characters. Names are case-sensitive primary keys, used directly in URLs such as
   `/decks/German_1`. Its creator remains its attributed author, but any user can browse
   it or add/edit cards. The deck-creation page is `/new-deck`, so `new` is a valid name.
3. Use the quick-add form on a deck page to enter just Front and Back. Enter moves from
   Front to Back and submits from Back; mobile keyboards show Next/Done hints. Without
   JavaScript, use Tab and the Add button. For more detail, choose Add card or Edit card.
   Use “Add another … form” or “Remove this form” to change rows without JavaScript.
   The first remaining form on each side is canonical; each side must keep one form.
   Blank extra rows are ignored. Each form can have a short description.
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
160 characters; each side to 100 forms. Edits preserve question IDs and progress for
all users. Removing metadata deletes only its question and that question's progress
and review history. Other question progress is kept. Concurrent/stale edits and study
submissions are rejected. Row additions/removals take effect only when saving the card.
Whole-card and deck deletion are not exposed.

The fixed header contains a Decks link and a username dropdown. Choose Light, Dark,
System, or Logout there. The dropdown and theme selection work without JavaScript.
The UI is responsive and uses the same optional Google Font as `cgi`; if the font
cannot be loaded, it falls back to monospace.

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
The optional keyboard-handler checks use Node's built-in assertions (no npm packages):

```sh
node tests/test_quick_add.cjs
```
