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
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/login', timeout=3).close()"]

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--preload", "--no-control-socket", "--access-logfile", "-", "--error-logfile", "-", "fcard:create_app()"]
