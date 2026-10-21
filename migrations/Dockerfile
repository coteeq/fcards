# Build from the fcards/ directory:
# docker build -f migrations/Dockerfile -t fcard-migrate .
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN groupadd --system --gid 10001 fcard \
    && useradd --system --uid 10001 --gid fcard --home-dir /app --no-create-home fcard \
    && mkdir /app/instance \
    && chown fcard:fcard /app/instance

COPY --from=builder /app/.venv /app/.venv
COPY fcard/ ./fcard/
COPY migrations/ ./migrations/

USER fcard
VOLUME ["/app/instance"]

ENTRYPOINT ["python", "migrations/migrate.py"]
CMD ["/app/instance/fcard.sqlite3", "--backup", "/app/instance/fcard-before-migration.sqlite3"]
