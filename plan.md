# fcard data model

## Shared vocabulary

- **users**: `id`, unique case-sensitive `username`, `created_at`. A username is an
  identity, not a security credential. No passwords.
- **decks**: `name` (text primary key), `description`, `author_id` (FK to users),
  `created_at`. Names match `[A-Za-z_][A-Za-z0-9_]*` (ASCII, max 120 characters).
  There is no numeric deck ID: foreign keys and URLs use the case-sensitive name.
  The author is the user who created the deck, not an access restriction.
  Every deck is visible to every user. No membership/enrollment table is needed:
  opening a deck is sufficient. Author attribution is required and does not change
  when another user adds cards.
- **cards**: `id`, `deck_name` (FK to decks.name), `revision` (nonnegative integer),
  `meta` (optional free text), `created_at`. Revision starts at 0 and increments on
  every edit, independently of per-user progress revisions.
  A card belongs to one deck in this MVP; duplicate content is allowed. Its sides are
  opaque **front** and **back**, with no source/target language semantics. Side text
  lives in ordered forms, not duplicated columns on the card.
- **card_forms**: `id`, `card_id` (FK), `side` (`front` or `back`), `position`
  (zero-based within the side), `text`, `description`. Unique
  `(card_id, side, position)`. Each side must have at least one form, enforced by the
  card creation/edit transaction. Symmetric forms avoid privileging either side; a side
  with no variants simply has one form.
- The first form on a side is the one with the smallest position, never the smallest
  spelling or row ID. It is that side's canonical display/prompt/expected answer.
  Other forms and their descriptions are shown after answering. Descriptions are short
  free text, not a fixed grammar enum. Alternate forms are not automatically accepted
  as canonical answers; the user can correct the verdict when appropriate.
- Metadata is one arbitrary text value (e.g. `feminine; irregular plural`). It is not a
  language-specific schema. Recovering it asks for the entire value, prompted by the
  front. Separate attributes/questions could be introduced later without changing forms.

## Questions are not cards

- **questions**: `id`, `card_id` (FK), `kind` constrained to `front_to_back`,
  `back_to_front`, or `meta`; unique `(card_id, kind)`.
- `front_to_back`: first front form → first back form.
- `back_to_front`: first back form → first front form.
- `meta`: first front form → metadata. Only created for nonblank metadata.
- Two mandatory questions plus one optional question are created atomically with the
  card and its forms. Forms are not separate questions in this MVP.

## Answers and verdicts

- Each question accepts a typed answer. The server compares it to the canonical
  expected answer using Unicode `casefold()` on both strings. No whitespace trimming,
  accent removal, punctuation normalization, or fuzzy matching: apart from case,
  matching is exact. The same rule applies to metadata questions.
- A match automatically produces `correct`; a mismatch produces `incorrect`. In both
  cases the expected answer, both sides, forms, and metadata are revealed. The user
  may change either verdict before moving to the next question.
- The automatic verdict is the default; the user need not explicitly self-grade.
  Advancing finalizes that verdict (or its correction) and updates progress once.
  Corrections do not create additional reviews or apply the scheduler twice. Leaving
  before finalization does not count a review. Post-finalization corrections are outside
  the MVP; they would require replaying subsequent scheduler transitions.
- Pending answer state is session-scoped: question ID, captured progress revision,
  captured card revision, submitted answer, automatic verdict, and reveal status.
  It is not review history.
  The server recomputes the automatic verdict rather than trusting a submitted verdict.
  Finalization permits only the original verdict or an explicit user correction.

## Personal scheduling

- **progress**: composite PK `(user_id, question_id)`, `algorithm`, `state_json`,
  nullable `due_at`, `last_reviewed_at`, `reviews`, `lapses`. Foreign keys to user and
  question. A persisted row represents at least one finalized review.
- No progress row means *new*, not missing/broken. This avoids eagerly generating the
  user × question Cartesian product. Questions added after a user starts a deck are
  immediately available.
- A user's three question kinds have independent schedules. There is no global
  card-level scheduling state and no user-specific copy of shared vocabulary.
- `algorithm` is a versioned scheduler identifier; `state_json` is algorithm-owned,
  versioned state. Existing questions use their recorded algorithm; the configured
  default applies only to new questions. Switching an existing question requires an
  explicit migration, not interpreting old state as a different algorithm.
- `last_reviewed_at` is the UTC Unix timestamp in seconds of the most recent finalized
  review. It is audit information, not necessarily a scheduling input. `reviews` counts
  finalized reviews; `lapses` counts finalized `incorrect` verdicts, including the first
  review if incorrect.
- `due_at` is a nullable UTC Unix timestamp in seconds: the earliest next review time
  for a time-based scheduler. It is always NULL for `buckets-v1`, which does not use
  time. NULL means no time gate, not a missing review or an overdue question.

### Bucket scheduler and plugin boundary

- Initial implementation: `buckets-v1`, with `state_json` containing integer `bucket`
  in the range 0–10. New questions start at bucket 0. Success advances one bucket;
  failure drops three, clamped to the range. No time intervals are implied. Optional
  controls apply an extra +5 or −5 after the verdict, also clamped.
- For a bucket-only queue, choose among all questions in the selected deck with
  weight `2 ** (-bucket)`, treating unseen questions as bucket 0. Higher buckets are
  less frequent, not postponed until a date. Exclude the immediately previous question
  when another eligible question exists; a one-question pool can repeat immediately.
- A scheduler owns queue eligibility/priority, state transitions, and optional extra
  controls. Its transition accepts the final verdict and returns updated state and an
  optional due timestamp. Persistence and HTTP handling stay outside the scheduler.
- For future time-based plugins, exclude future-due questions and prioritize overdue
  questions before unseen ones. For a mixed-algorithm deck, choose an eligible algorithm
  group round-robin, then let that plugin select within its group. Buckets must not be
  globally sorted by `due_at`; they have no due time.
- A future FSRS plugin can store stability/difficulty in JSON and use `due_at` without
  altering vocabulary tables. Additional algorithm-specific grading controls can be
  added through the plugin interface; binary correctness alone is not an FSRS rating.

### Review history and concurrency

- **review_log**: `id`, `user_id`, `question_id`, `reviewed_at`, `auto_verdict`,
  `rating`, nullable `control`, `algorithm`, `before_state_json`, `after_state_json`,
  nullable `due_at`. `control` records an optional algorithm-specific adjustment.
  Written in the same transaction as progress and immutable through the application.
  Absence of prior state is JSON `null`; the initial bucket is still interpreted as 0.
- `auto_verdict` stores `correct` or `incorrect` from the typed-answer comparison.
  `rating` stores the final scheduler input after any user correction. For
  `buckets-v1`, it is exactly `correct` or `incorrect`, not a bucket number or delta.
  Comparing these fields identifies corrected verdicts. Ratings are interpreted with
  their algorithm ID; a future FSRS plugin could use `again`, `hard`, `good`, `easy`.
- `reviewed_at` records finalization time in UTC Unix seconds. `due_at` records the
  resulting next due time, NULL for buckets. State snapshots preserve history for
  scheduler evaluation and migrations; raw submitted answers are not retained in logs.
- Grade transactions serialize writers and compare the progress review count to the
  revision captured when the question was selected (zero for unseen questions). A
  stale/replayed submission cannot advance progress twice. The card revision is also
  checked inside this transaction so edits cannot silently change an active question.
  Session state binds selection to the current user and requires answer/reveal before
  finalization.

## Integrity and lifecycle

SQLite foreign keys are enabled on every connection. Vocabulary and progress writes are
transactional. Deck → cards → forms/questions → progress/log foreign keys cascade on
deletion, as do user → progress/log. Deck author deletion is restricted so attribution
cannot disappear; user/deck/whole-card deletion is not exposed. Unique/check constraints
cover identities, sides, form order, question kinds, nonblank required text,
and nonnegative counters. Indexes cover deck cards, question lookup, user due queues,
and review history. At-least-one-form-per-side and optional-meta-question rules span
tables and are enforced at the application write boundary.

Card editing preserves the card ID and all remaining question IDs/progress. Forms are
replaced atomically in their submitted order; removing the first promotes the next.
At least one form on each side remains mandatory. Blank metadata deletes its question
and cascades to that question's progress/history for every user. Adding metadata creates
its question if absent; changing nonblank metadata preserves that question's progress.
Edits compare the submitted card revision before incrementing it, rejecting stale saves.
Form IDs are not progression identities and can change on save. Existing review logs
retain scheduling state, not historical side text; history displays current card text.

Quick creation uses the same card-creation transaction with one form per side, empty
form descriptions, and no metadata, yielding exactly two questions. It needs no separate
table or data model. Reusing a card in several decks would require a deck-card join
table and an explicit decision whether progress is shared across decks; currently each
deck's card is independent.

Schema evolution uses SQLite `user_version` (current version 2). A fresh database is
required for this revision; no compatibility shims or migration from version 1 are
provided. Other versions are rejected rather than silently mutated. Future schema
changes need an explicit migration or an explicitly chosen fresh start.
