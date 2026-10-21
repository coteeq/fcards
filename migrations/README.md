# Database migrations

Stop all old fcard workers first. From the `fcards/` directory, run:

```sh
uv run --locked python migrations/migrate.py instance/fcard.sqlite3 \
  --backup instance/fcard-before-v4.sqlite3
```

Replace the database path if you use `FCARD_DATABASE`. The backup path must be new;
the command refuses to overwrite any existing file. The database must already exist,
so a typo cannot silently create an empty replacement.

The runner supports schema v2/v3 → v4, and is safe to rerun on v4. It uses SQLite's
backup API, then applies the supported migrations in one immediate transaction. All
DDL, backfills, and version changes roll back together if migration fails. Foreign
keys are checked before commit. Restart the application only after the command succeeds.

- **v2 → v3:** existing canonical German Back forms receive type/gender hints and
  eligible noun gender questions; original words/forms, metadata, IDs, and schedules
  are preserved.
- **v3 → v4:** `004_lessons.sql` creates the lesson tables and deliberately drops
  `review_log`, the old per-answer history table. Added words, decks, users, question
  IDs, classification, and current per-user scheduling progress survive unchanged.
- No existing lesson is deleted, and no scheduling results are applied by migration.

The application also runs supported migrations automatically on startup; use this
command before starting the new version if you want an explicit backup/upgrade step.
Do not execute the SQL through Python's `executescript()` inside a transaction: that
API commits implicitly. The runner and application execute complete statements without
breaking their transaction. Unsupported versions are rejected rather than reset.

In the Docker image, Python and this directory are available under `/app`; use
`python migrations/migrate.py /app/instance/fcard.sqlite3 --backup ...` from a one-off
container with the same data volume, after stopping the old app container. The runtime
image does not contain uv. Ensure the database and backup directory are writable by
UID/GID 10001.
