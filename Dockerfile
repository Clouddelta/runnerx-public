FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000
WORKDIR /app
RUN groupadd --gid 10001 runnerx && useradd --uid 10001 --gid runnerx --create-home runnerx
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY --chown=runnerx:runnerx alembic.ini ./
COPY --chown=runnerx:runnerx migrations ./migrations
COPY --chown=runnerx:runnerx data/sample ./data/sample
USER runnerx
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn runnerx.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
