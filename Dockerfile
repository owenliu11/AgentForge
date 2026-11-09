# One image for api, worker, mock_effects, and the migrate task.
FROM python:3.12-slim

# The worker starts tests.run sandboxes through the host's Docker socket, so it
# needs the docker CLI (not the daemon).
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /srv

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY mock_effects ./mock_effects
COPY tests/fixtures ./tests/fixtures
COPY drills/scripts ./drills/scripts
RUN uv sync --frozen --no-dev

EXPOSE 8000 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
