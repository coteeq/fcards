# fcard

A server-rendered Flask + SQLite flashcard app with username-only login, primarily for
English ↔ German vocabulary. Small vanilla JavaScript handlers provide live search
and Enter shortcuts, client-side row editing, and German classification hints. All controls also work without
JavaScript using their buttons. The visual design and base stylesheet come from the
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

The database is created automatically under `instance/`. The session-signing key is
random and ephemeral by default: restarting the server logs everyone out and discards
unfinished questions, but does not erase users, cards, or finalized progress. No key
file is written. Database initialization is transactional and safe for simultaneous
worker startup. Unknown schema versions are rejected; there is no destructive reset
command.

The current schema is version 3. **Stop the old server and back up the database before
upgrading.** On startup, version-2 databases are migrated transactionally in place:
classification fields are added, existing canonical Back forms are classified with the
rules below, and known nouns receive a new gender question. Stored form text, metadata,
all existing question IDs, users, progress, and review history are preserved. New gender
questions start unseen for every user. The migration checks foreign keys before commit;
a failure rolls back the entire upgrade, including DDL and the schema version.
Simultaneous startup is serialized, and reopening v3 does not reclassify saved values.

Never reset or replace a populated database as part of an upgrade. Future changes must
ship explicit, transactional migrations. Unsupported versions are rejected, not erased.

For another database path:

```sh
FCARD_DATABASE=/absolute/path/to/fcard.sqlite3 uv run --locked flask --app fcard run
```

Optional settings:

- `FCARD_SECRET_KEY`: optionally use an explicit, long random secret to preserve
  sessions across restarts or share sessions between independent server instances.
- `FCARD_SESSION_COOKIE_SECURE=true`: require HTTPS for the session cookie when
  deployed behind TLS.
- `FCARD_DEFAULT_ALGORITHM=buckets-v1`: default scheduler for previously unseen
  questions. Existing progress retains its recorded algorithm.

Flask's built-in server is for development. For deployment, use a WSGI server with
`fcard:create_app()` and a writable local database directory. With multiple workers,
create the application before forking so workers share its ephemeral key (Gunicorn
`--preload`, already configured in Docker), or set one shared `FCARD_SECRET_KEY`.
Back up the SQLite database using SQLite's backup API (or stop the app before copying
the database and its WAL files). Keep `instance/` out of publicly served directories.

## Docker

From `fcards/`:

```sh
docker build -t fcard .
docker run --rm --name fcard -p 5000:5000 -v fcard-data:/app/instance fcard
```

Open <http://127.0.0.1:5000>. The image runs Gunicorn with two workers as a non-root
user, exposes port 5000, and includes an HTTP health check. Dependencies are installed
with uv from `uv.lock`; the final image does not include uv or build tooling.

The named volume keeps the SQLite database across container replacements. Sessions
are intentionally invalidated on restart unless you set a stable `FCARD_SECRET_KEY`.
Do not remove the volume unless you intend to erase the database.
For a bind mount instead, ensure its directory is writable by UID/GID **10001**.

Pass configuration with `docker run -e`, for example
`-e FCARD_SESSION_COOKIE_SECURE=true` when serving behind HTTPS. An explicit
`FCARD_SECRET_KEY` is optional; keeping it stable preserves sessions across restarts.
The default database path is
`/app/instance/fcard.sqlite3`; keep any custom database path on a persistent volume.

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
   Each form is one compact row: text, optional description, and a square trash button.
   Add/remove buttons update rows immediately in the browser, keeping entered values
   and promoting the next form when the first is deleted. The verb preset is client-side
   too. Without JavaScript, these buttons still work through a server-rendered fallback.
   The first remaining form on each side is canonical; each side must keep one form.
   Blank extra rows are ignored. Each form can have a short description. The Verb
   shortcut on either side prefills an undescribed infinitive row followed by `1sg`,
   `1pl`, `2sg`, `2pl`, `3sg`, `3pl`. It keeps entered text and any rows beyond those
   seven; applying it again does not duplicate rows. Fill in or remove the conjugation
   rows before saving. Saving trims leading/trailing whitespace from form text,
   descriptions, and metadata; interior spaces, punctuation, and spelling are kept.
   Trimming also runs server-side, including for quick-add and edits without JavaScript.
4. Every card has front → back and back → front questions, each scheduled independently.
   Nonblank metadata adds a question; a noun with a known gender adds a separate gender
   question. German belongs on **Back**; classification uses only its first form.
5. Type an answer and check it. Unicode case folding is the only normalization:
   spaces, accents, and punctuation still matter. Only the canonical first form is
   automatically accepted. Enter checks the answer; Shift+Enter inserts a newline.
   Gender questions instead offer radio choices, with no choice selected initially.
   They show the German Back word without its leading article, so the prompt does not
   give away the answer. All forms and both sides are revealed after checking.
6. The Finalize review card is collapsed by default. Open it to correct the verdict
   or adjust the scheduler; Next question stays outside and is focused after reveal,
   so Enter advances. Advancing finalizes exactly one review. Empty answers are
   incorrect, but can also be manually corrected.
   Closing the tab or navigating away before advancing counts nothing and keeps the
   pending question for resumption. “Discard question and return to deck” also counts
   nothing, but explicitly clears that pending question.
7. Review history shows the automatic verdict, final rating, and state transition.
   Raw answers are not stored in that history.

Card/form text, metadata, and answers are limited to 500 characters; descriptions to
160 characters; each side to 100 forms. Edits preserve question IDs and progress for
all users. Removing metadata deletes only its question and that question's progress
and review history. Likewise, clearing a noun's gender or changing to a non-noun removes
only its gender question and that question's progress/history. Changing a known gender
preserves its question ID and schedule. Concurrent/stale edits and study
submissions are rejected. Row additions/removals take effect only when saving the card.
Whole-card and deck deletion are not exposed.

The fixed header contains a Decks link and a username dropdown. Choose Light, Dark,
System, or Logout there. The dropdown and theme selection work without JavaScript.
Deck cards and revealed answers show Front on the left and Back on the right, each
with its own top-aligned list of forms. At widths of 600px or less, the sides stack
vertically with a prominent divider. Each form's description occupies the adjacent
cell of the same row, including on mobile; long text wraps within its own cell.
The UI uses the same optional Google Font as `cgi`; if the font cannot be loaded, it
falls back to monospace.

## German word type and gender

Both add forms offer **Adjective / Verb / Noun / Phrase** radios. Only the canonical
first Back form drives the local JavaScript hints, in this order:

1. Remove a leading `der`, `die`, or `das` for counting purposes. If more than one word
   remains, choose Phrase (e.g. `spazieren gehen` or `der gute Morgen`).
2. Otherwise, a leading article means Noun (`der Morgen`, despite its `-en` ending).
3. Otherwise, a word ending in `-en` means Verb.
4. Otherwise, leave the type unselected; adjectives can be selected manually.

Article matching is case-insensitive and accepts whitespace such as tabs/nonbreaking
spaces. The original article and spelling stay stored in Back. For nouns, `der`
infers masculine, `die` feminine, and `das` neuter. These are simple dictionary-entry
hints, not grammatical parsing: plural/case forms can be ambiguous. Override them when
needed; indefinite articles are not inferred.

Quick-add infers gender silently on the server. In the full add/edit form, choosing
Noun shows separate gender radios with the same article hint, plus **Unknown / no
gender question**. A manually selected gender can differ from the written article.
Without JavaScript, the full form leaves gender choices visible and the server infers
omitted new-card values; explicitly selected choices still win.

**Blue means automatic; orange means manual/locked.** Tapping an option—even one
already selected automatically—or changing it by keyboard locks that group. Type and
gender lock independently, so manually selecting Noun still lets gender follow the
article until gender is selected manually. Hidden gender choices are retained in the
draft if you switch away from Noun and back, but non-nouns persist with no gender.

Automatic/manual provenance is never stored in the database. When editing a saved card,
both values are manual/locked, including unknown values; changing Back does not silently
reclassify it. On creation, the server supplies missing hints even without JavaScript.
Only known-gender nouns get a gender question, independently scheduled and finalized
through the same reveal/correction flow as other questions.

## Existing-word hints while adding

Quick-add and the extended add/edit form show advisory hints **while typing**, before
submission. JavaScript batches the entered form texts after a 300ms pause and looks up
existing words across all decks and all forms on both sides. Matches use whole-form
English/German normalized equality (not arbitrary substring matches). The current card
is excluded during editing. Descriptions and metadata do not trigger lookups.

Hints show up to five existing cards with links opening in a new tab so drafts are not
lost. Matches, lookup failures, and outstanding requests never require confirmation or
disable Save: duplicate cards remain allowed. Adding/removing form rows refreshes the
hints; stale responses are ignored. The lookup uses a CSRF-protected read-only request
and does not create cards or change progress. Like search, this currently scans forms;
duplicate hints themselves need no stored index, migration, or data rewrite.

## Word search

Use the search box on the deck list or a deck page. Search covers all decks and every
form on both sides, including noncanonical forms. Results show full cards with their
deck link and edit button, and each matching card appears only once.

With JavaScript, typing shows live previews after a 250ms pause, up to eight cards.
Previews include each card's first front/back forms, deck name, and a matching extra
form when the match is not canonical. Click a preview to open its full card or choose
View all results. Arrow keys navigate preview links; Escape or clicking outside closes
previews. Text composition is supported. Clearing/changing the query cancels outdated
requests and ignores stale responses. Loading, empty, and error states are shown.
The Search button/Enter still opens the full results page, including without JavaScript.

Search is a literal substring match after English/German ASCII normalization:

- Case, punctuation, symbols, and spaces are ignored.
- `ä` / `ae` / `a`, `ö` / `oe` / `o`, and `ü` / `ue` / `u` match.
- `ß` / `ẞ` / `ss` match; decomposed umlauts also work.
- `buck`, `bueck`, and `BÜCK` all find `die, Bückerei?`.
- Both full ASCII and digraph-folded spellings are checked, preserving ordinary
  English/German substrings such as `ell` in `Duell`. These spelling equivalences
  deliberately broaden search, so ordinary `ae` / `oe` / `ue` can also match
  `a` / `o` / `u`.
- Queries containing no supported letters or digits return no cards. There is no
  general transliteration of other scripts. Query length is limited to 500 characters.

Descriptions and metadata are not word forms and are not searched. Pagination keeps
the query. Search reads existing form text directly (no schema migration or rebuilt
index is needed), so edits and new cards are reflected immediately. Live previews use
that same search logic and still scan forms across all decks; debouncing reduces the
frequency of requests, not the amount of data each query can scan. Stored spelling and
answer grading are unchanged: search's looser matching is not used to decide whether a
study answer is correct.

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
The optional JavaScript checks use Node's built-in assertions (no npm packages):

```sh
node tests/test_quick_add.cjs
node tests/test_study_keyboard.cjs
node tests/test_live_search.cjs
node tests/test_card_editor.cjs
node tests/test_card_entry.cjs
node tests/test_word_classification.cjs
```
