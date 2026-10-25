# fcard data model

## Shared vocabulary

- **users**: `id`, unique case-sensitive `username`, `created_at`. A username is an
  identity, not a security credential. No passwords.
- **decks**: `name` (text primary key), `description`, `author_id` (FK to users),
  `created_at`. Names match `[A-Za-z_][A-Za-z0-9_]*` (ASCII, max 120 characters).
  Names, foreign keys, and URLs are case-sensitive. Every deck is shared; authorship
  is attribution, not an access restriction. There is no enrollment table.
- **cards**: `id`, `deck_name` (FK), nonnegative `revision`, optional free-text `meta`,
  nullable `word_type` and `gender`, `created_at`. Revision starts at 0 and increments
  on every edit. A card belongs to one deck; duplicate content is allowed.
- **card_forms**: `id`, `card_id` (FK), `side` (`front` or `back`), nonnegative
  `position`, `text`, `description`. Unique `(card_id, side, position)`.
  Each side has 1–100 ordered forms. The minimum position identifies the canonical
  prompt/expected answer, not spelling or form ID. Other forms are revealed afterward,
  not accepted automatically as canonical answers. Descriptions are arbitrary free text.
- German is always on Back. Classification and gender questions use its first form.
  `word_type` is NULL or `adjective`, `verb`, `noun`, `phrase`. `gender` is NULL or
  `masculine`, `feminine`, `neuter`; a non-NULL gender requires type `noun`.
- Missing new-card classifications use Back: remove a leading case-insensitive
  `der/die/das` for counting; multiple remaining words mean phrase, otherwise an article
  means noun, otherwise an `-en` ending means verb. Other words stay unclassified.
  Nouns infer masculine/feminine/neuter from `der/die/das` unless explicitly overridden,
  including with unknown gender. Plural/case ambiguity makes these hints, not parsing.
- Inferred and manual choices persist identically, with no provenance flag. Saved
  values,
  including unknowns, are not reinterpreted automatically during edits.
- Creation/editing trims outer whitespace from text, descriptions, and metadata before
  validation/persistence. Interior whitespace and spelling stay unchanged. Existing form
  text is not rewritten by migrations. Metadata remains arbitrary text, separate from
  structured gender; a metadata question asks for its complete value.

## Questions are not cards

- **questions**: `id`, `card_id` (FK), `kind`; unique `(card_id, kind)`.
- `front_to_back`: first Front form → first Back form.
- `back_to_front`: first Back form → first Front form.
- `meta`: first Front form → nonblank metadata; absent for blank metadata.
- `gender`: first Back form without leading `der/die/das` → the article for the stored
  gender. Only exists for nouns with known gender. Explicit gender overrides source
  text.
- Two mandatory and up to two optional questions are created atomically with the card.
  Forms are not separate questions. Each question has its own per-user schedule.

## Durable finite lessons

- **lessons**: UUID text primary key `id`, `user_id` (FK), `deck_name` (FK),
  `created_at`, `updated_at`, nullable `completed_at`, nonnegative `revision`,
  `state_json`, `skipped` (0–20). The UUID permanently identifies the saved lesson.
  Only the matching user can retrieve or mutate it; deck vocabulary remains shared.
- **lesson_items**: `lesson_id` (FK), `position` (0–19), nullable `question_id` (FK),
  `algorithm`, `snapshot_json`; primary key `(lesson_id, position)`.
- Creation selects exactly 20 appearances, without updating schedules. It applies the
  recorded scheduler's eligibility/weighted selection, without replacement until the
  eligible pool is exhausted. Small decks repeat in additional cycles to fill 20 slots;
  avoid immediate repeats when an alternative exists. Mixed algorithms rotate groups.
- Selection, order, algorithm, and snapshots persist in one transaction. A snapshot
  contains original question/card IDs and card revision, kind, label, prompt, expected
  answer and its Unicode-casefold key, both sides/forms/descriptions, metadata, rating
  choices/defaults, and scheduler controls. Questions and answers load together once.
- Snapshots are immutable, including after vocabulary edits. Deleting a source question
  sets the item's live FK to NULL, not deleting the lesson. Snapshots retain original
  identity/text independently. A newly created question does not replace an old slot.
- `state_json` is exactly `{cursor, ratings}`. Cursor is 0–19. Ratings form a contiguous
  answered prefix of length `cursor` (current question unanswered) or `cursor + 1`
  (current verdict revealed). Each entry contains only final `rating` and nullable
  scheduler `control`, validated against its item's snapshotted options. The cursor
  represents furthest lesson progress; viewing an earlier question does not rewind it.
- Raw submitted answers, automatic verdicts, correction flags, and correction history
  never persist in the database. Original answers and retype acknowledgements can live
  in a separate browser-local cache, not the lesson sync payload or scheduling state.
  Answer checking uses NFC and Unicode casefold, plus `ü` → `ue`.
  Thus `ß/ss` and `ü/ue` match; plain `u` does not match `ü`. Spaces, punctuation,
  and other accents stay significant. Matching uses original expected text, not the
  snapshot's legacy casefold key, so saved lessons use the current rules unchanged.
  Gender choices submit an article.
  Both automatic and manually corrected choices become indistinguishable final ratings.
- Async sync changes only lesson state/revision/time, never scheduling progress. Writers
  compare the lesson revision under an immediate transaction. An identical retried state
  is idempotent; a stale revision with different state is rejected, not overwritten.
  The sync outbox retains only pending final choices. A separate local answer cache
  supports original-mistake review and is not deleted when the lesson completes.
  If unavailable, the database cannot reconstruct original answers or corrections.
- Incomplete lessons are queryable by user, including all-answered lessons not yet
  ended.
  They survive logout, session expiration, and server restart. Completed lessons remain
  retrievable at their UUID but are absent from the incomplete list.

## Completion and personal scheduling

- **progress**: composite PK `(user_id, question_id)`, `algorithm`, `state_json`,
  nullable `due_at`, `last_reviewed_at`, `reviews`, `lapses`. Foreign keys to users and
  questions. No row means unseen; rows represent at least one committed review.
- Only explicit lesson completion commits verdicts. It requires all 20 final choices and
  applies them in lesson order, marking completion in the same immediate transaction.
  A failure rolls back every scheduling write and the completion marker.
- Completed lessons are immutable. Concurrent/replayed completion or delayed sync cannot
  apply ratings twice or reopen the lesson. An already-completed response is idempotent.
- Apply each appearance to the latest progress, not creation-time scheduling state.
  Separate lessons can finish in either order without discarding one another's reviews.
  Repeated appearances of a question each count as a review, applied sequentially.
- Each still-live question must match its snapshotted card ID/revision/kind and recorded
  algorithm. Edited/deleted questions and changed algorithm assignments are skipped,
  counted in `skipped`; they do not invalidate or change the lesson's snapshot/results.
  Unavailable required scheduler code aborts completion for later retry, not
  substitution.
- `last_reviewed_at` is the UTC completion timestamp, not an answer timestamp. `reviews`
  counts committed appearances; `lapses` counts final incorrect ratings. An explicitly
  corrected rating is treated exactly like the same uncorrected rating.
- `algorithm` is versioned; `state_json` belongs to that scheduler. A new default
  applies
  only to unseen questions. Existing progress keeps its algorithm/state. Algorithm
  changes require explicit migration. Scheduling state has no shared card-level bucket.
- `due_at` is the earliest next review time for time-based algorithms; future questions
  are ineligible. NULL means no time gate. A recorded scheduler must remain registered.
- `buckets-v1`: integer bucket 0–10, unseen bucket 0. Correct +1, incorrect −3, clamped.
  Optional controls apply another +5/−5 after the verdict, also clamped. Selection
  weight
  is `2 ** (-bucket)`, not due-date order. All bucket questions stay available; dates
  are not used, and `due_at` remains NULL.
- The scheduler owns eligibility, choice, ratings/controls, and transitions to a new
  state
  and optional due date. Persistence, snapshots, transactions, and lessons stay outside
  the plugin. Future FSRS state can live in JSON without altering vocabulary tables.
- There is no per-answer audit/history table. Completed lesson snapshots and final
  ratings retain lesson results, without the user's answer or whether it was corrected.

## Integrity and lifecycle

SQLite foreign keys enforce relationships during normal operation. Deck deletion
cascades
through cards/forms/questions/progress and lessons/items. User deletion cascades through
progress/lessons, but deck author deletion is restricted. Whole-card/deck/user deletion
is not exposed in the UI. Question deletion sets lesson references to NULL; it does not
remove saved lessons. At-least-one-form-per-side, optional-question, exact
lesson-length,
and state-prefix invariants span tables and are enforced by transactional write paths.

Edits compare card revisions, replace forms in submitted order, and preserve remaining
question IDs/progress. Removing the first form promotes the next. Blank metadata or
removed noun gender deletes only its optional question/progress; existing lesson
snapshots survive. Changing known gender or nonblank metadata preserves its question's
schedule. Card edits never rewrite snapshots; stale lesson items are skipped on ending.

Quick creation shares the card transaction: one form per side, empty descriptions and
metadata, two mandatory questions, and a gender question for a known-gender noun.
Search and advisory duplicate checks read existing forms globally with English/German
normalization at query time, without stored search columns or uniqueness constraints.
Search is substring-based; duplicate hints compare whole normalized forms and exclude
the edited card. Neither changes grading, lessons, or progress.

## Schema evolution

SQLite `user_version` is currently 4. The supported v2 → v3 migration adds
classification,
backfills canonical Back forms, and adds known-gender noun questions, preserving old
IDs,
forms, metadata, revisions, users, and scheduling progress. The v3 → v4 migration in
`migrations/004_lessons.sql` adds lesson tables and, with explicit one-time permission,
drops `review_log`. Vocabulary and current per-user schedules remain unchanged by v4.

`migrations/migrate.py` upgrades an existing database with an optional non-overwriting
SQLite backup. Startup also applies supported upgrades. All DDL, data backfill, and
version changes share one immediate transaction; failure rolls everything back. FK
checking is temporarily disabled only on the migration connection outside its
transaction
to safely rebuild v3's question constraint, checked before commit, and always restored.
Concurrent startups serialize; already-upgraded databases are not reclassified. Future
schema changes must ship explicit transactional migrations preserving populated data;
unsupported versions are rejected, never reset or replaced.
