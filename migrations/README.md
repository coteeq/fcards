# Database migrations

Stop all old fcard workers first. Choose either Docker (no host Python/uv needed)
or the local runner below.

## Docker runner

Build from the **`fcards/` directory**, not from `migrations/`:

```sh
docker build -f migrations/Dockerfile -t fcard-migrate .
```

For the named volume used in the application README:

```sh
docker stop fcard
docker run --rm -v fcard-data:/app/instance fcard-migrate
```

Use your application's actual existing volume name. The runner defaults to
`/app/instance/fcard.sqlite3` and creates a backup at
`/app/instance/fcard-before-migration.sqlite3` on that same persistent volume. It exits
when done; it does not start Flask/Gunicorn or expose any ports. uv is used only in the
image's build stage, not on the host or in the runtime image.

For a bind mount and explicit filenames:

```sh
docker run --rm \
  --mount type=bind,src=/absolute/path/to/instance,dst=/app/instance \
  fcard-migrate /app/instance/fcard.sqlite3 \
  --backup /app/instance/fcard-before-v4.sqlite3
```

Arguments after `fcard-migrate` replace its defaults. Backup filenames must be new:
if one already exists, choose another filename; nothing is overwritten. Mount the
whole database directory, not just the SQLite file, so WAL/SHM files are available.
For a custom database filename, pass its container path explicitly. To see options:

```sh
docker run --rm fcard-migrate --help
```

The image runs as UID/GID **10001**, like the app. Ensure the mounted directory,
database, and any existing WAL/SHM files are writable by that user. For a bind mount
owned by another user, either adjust permissions or use `--user UID:GID` matching its
owner. Restart/recreate the app with its new image only after migration succeeds.
If you build elsewhere, transfer the image to production via your registry or
`docker save` / `docker load`; production only needs Docker.

## Local runner

From the `fcards/` directory, run:

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

The regular app image also contains Python and this runner. If reusing it instead of
building the dedicated image, override its Gunicorn entrypoint with
`--entrypoint python` and pass `migrations/migrate.py` followed by database/backup paths.
