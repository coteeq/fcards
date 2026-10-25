# fcard

A server-rendered Flask + SQLite flashcard app with username-only login, primarily for
English ↔ German vocabulary. Vanilla JavaScript provides finite, fast lessons with
local answer checking, background sync, live search, row editing, and classification
hints. Vocabulary management works without JavaScript; lessons require it. The visual design and base stylesheet come from the
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
random and ephemeral by default: restarting logs everyone out, but saved lessons,
users, cards, and committed scheduling progress survive. Sign in with the same username
to resume unfinished lessons. No key
file is written. Database initialization is transactional and safe for simultaneous
worker startup. Unknown schema versions are rejected; there is no destructive reset
command.

The current schema is version 4. **Stop all old workers before upgrading.** Run the
separate migration command with a new backup path (from `fcards/`):

```sh
uv run --locked python migrations/migrate.py instance/fcard.sqlite3 \
  --backup /safe/path/fcard-before-v4.sqlite3
```

For production without host Python/uv, use the standalone Docker migration runner:

```sh
# From fcards/; stop the old app container before running the migration.
docker build -f migrations/Dockerfile -t fcard-migrate .
docker stop fcard
docker run --rm -v fcard-data:/app/instance fcard-migrate
```

Use the same existing data volume as your app. By default this creates
`fcard-before-migration.sqlite3` beside the database and refuses to overwrite it.
See [migrations/README.md](migrations/README.md) for bind mounts, custom paths, and
permissions. Restart/recreate the updated app only after migration succeeds.
The v3 → v4 SQL is in
`migrations/004_lessons.sql`. This one-time upgrade **drops the old per-answer
`review_log` history**, as agreed. Added words/forms, metadata, classification, users,
question IDs, and current scheduling progress are preserved. Lesson tables are added.
Version-2 databases first receive the data-preserving classification upgrade to v3.

Startup can also apply these upgrades automatically, so back up before starting the new
version if you do not use the migration command. DDL/data/version changes are atomic;
failures roll back the entire upgrade. Concurrent startups serialize. Reopening an
already-upgraded database does not reclassify saved words or reapply lesson results.

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
5. Type an answer and check it. Case is ignored, `ß`/`ss` and `ü`/`ue` match, but
   plain `u` does **not** match `ü`. Spaces, other accents, and punctuation still matter. Only the canonical first form is
   automatically accepted. Enter checks the answer; Shift+Enter inserts a newline.
   Gender questions instead offer radio choices, with no choice selected initially.
   They show the German Back word without its leading article, so the prompt does not
   give away the answer. All forms and both sides are revealed after checking.
6. Choose **Create lesson · 20 questions** on a deck or the front page. The lesson
   gets a stable UUID, with all questions/answers loaded once. Check, correction, and
   Next question run locally without waiting for the server. The correction/adjustment
   panel is collapsed by default; Next is outside and focused after reveal.
7. The last verdict has **End lesson** instead of Next. Only ending commits all verdicts
   to scheduling. Leave earlier and resume from **Uncompleted lessons** on the front
   page. Completed lessons stay retrievable by UUID, without automatic deletion.

Card/form text, metadata, and answers are limited to 500 characters; descriptions to
160 characters; each side to 100 forms. Edits preserve question IDs and progress for
all users. Removing metadata or noun gender deletes only its optional question and
progress, not saved lesson snapshots. Changing a known gender preserves its question ID
and schedule. Concurrent/stale card edits and conflicting lesson sync are rejected. Row additions/removals take effect only when saving the card.
Whole-card and deck deletion are not exposed.

The fixed header contains a Decks link and a username dropdown. Choose Light, Dark,
System, or Logout there. The dropdown and theme selection work without JavaScript.
Deck cards and revealed answers show Front on the left and Back on the right, each
with its own top-aligned list of forms. At widths of 600px or less, the sides stack
vertically with a prominent divider. Each form's description occupies the adjacent
cell of the same row, including on mobile; long text wraps within its own cell.
The UI uses the same optional Google Font as `cgi`; if the font cannot be loaded, it
falls back to monospace.

## Lessons and asynchronous sync

Lessons contain exactly 20 ordered question appearances. Selection uses the current
scheduler, without replacement until the eligible pool is exhausted. Tiny decks repeat
questions in additional cycles to reach 20, avoiding immediate repeats when possible.
If no questions are eligible, no lesson is created.

A lesson is available at `/lessons/<uuid>`, with owner-only JSON at
`/api/lessons/<uuid>`. The JSON deliberately includes answers: this is a personal study
tool, not an exam system. Checking uses the bundled full Unicode casefold map, not
JavaScript's narrower `toLowerCase()`. Canonically equivalent Unicode spellings are
normalized with NFC, and `ü`/`ue` are equivalent (including uppercase). Plain `u` does
not match `ü` or `ue`. Other accents, whitespace, and punctuation remain significant.
The matcher normalizes the original expected text, so saved lessons also receive these
rules without migrating or rewriting their snapshots.
For **German → English verb** questions only, a leading English `to ` is optional:
`go` and `to go` both match, case-insensitively. The card must be classified as a verb;
other types, unknown types, English → German questions, and metadata questions remain
strict about that prefix. The same rule applies to retyping after an incorrect answer.
New lessons snapshot the word type. Older lessons can recover it from an unchanged
source card; if that card was edited or deleted, its old unknown type stays strict.
No network request is required to check or advance.

Cursor and **final** rating/adjustment choices sync asynchronously. Raw answers,
automatic verdicts, and whether a verdict was corrected are never sent to or stored by
the server. A separate **browser-local** cache retains original answers and retype
acknowledgements, including after completion, so reloads and the final review can show
mistakes. Clearing this site's browser data removes that cache. On another device (or
when no local cache is available), original answers are marked unavailable rather than
invented. Corrections have no separate server audit trail.

Each question has a stable fragment URL such as `/lessons/<uuid>#question=3`.
Checking replaces the question's view with its verdict without adding a history entry;
advancing adds the next question. Browser Back/Forward therefore revisits earlier
verdicts, including after completion, without regrading, rewinding saved progress, or
applying schedules again. Past questions are review-only. `#complete` opens the final
review once the lesson is completed.

A final incorrect verdict requires retyping the correct answer before advancing.
Retyping uses the same matcher (including `ß/ss` and `ü/ue`); mismatches show feedback
and a short shake instead of advancing. Reduced-motion preferences disable the shake.
Enter submits; Shift+Enter keeps a newline and IME composition is left alone.
**Skip retype** explicitly bypasses this exercise, without changing the incorrect
rating. The same rule applies on the last question before End lesson. Changing the
verdict to correct removes the retype requirement.

The compact completion table shows each prompt, correct answer, and an **OK/NOK**
result column. Rows use the same muted green/red backgrounds as lesson verdicts.
Incorrect rows also show the original mistake, never the later retype. Number links
return to each saved verdict. The table scrolls horizontally on narrow screens. Completing the lesson does not erase the local
answer cache; it only clears the scheduling-sync outbox.

Writes are serialized in the browser and revision-checked on the server. A small local
storage outbox recovers unsynced choices after navigation; network failures retry with
backoff, and visibility/online events attempt another sync. Lost acknowledgements retry
the identical request before newer progress. Conflicting tabs stop rather than silently
overwriting one another; explicitly reload saved state to discard local pending changes.
If browser storage is unavailable, keep the tab open until progress is saved.

**End lesson** waits for server acknowledgement; its scheduling changes and completion
marker commit in one transaction. Completion is idempotent, including retries and
concurrent requests. Failed completion applies nothing. Separate lessons apply to the
latest scheduling state; repeated appearances each count as a review. During a pending
end request the last screen is locked, and failed requests can safely retry.

Each verdict has an **Edit card** button opening the original card in a new tab,
without leaving the lesson. Editing does not rewrite that lesson's saved wording.

Lesson wording/answers are immutable snapshots. Later card edits do not change an
existing lesson; changed/deleted source questions are skipped when applying schedules,
with a completion summary indicating how many were skipped. Other question progress
still applies atomically. No lesson is deleted automatically, and there is currently
no lesson deletion button or endpoint.

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
Only known-gender nouns get a gender question, independently scheduled through the same
local reveal/correction flow as other questions; scheduling commits when the lesson ends.

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
node tests/test_live_search.cjs
node tests/test_card_editor.cjs
node tests/test_card_entry.cjs
node tests/test_word_classification.cjs
node tests/test_lesson_sync.cjs
node tests/test_lesson_ui.cjs
node tests/test_answer_match.cjs
```
