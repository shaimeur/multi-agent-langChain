# syntax=docker/dockerfile:1

# --- stage 1: the React SPA ------------------------------------------------
# `web/` is a separate build (descope §2 / O1), but C9 asks for *one* command on a
# clean machine and §15.6 is a browser scenario — so the image has to contain the
# built UI, not just the API. Node never reaches the runtime image; only `dist/`.
FROM node:22-slim AS web
WORKDIR /web
# Lockfile first so `npm ci` survives every source edit.
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY web/ ./
RUN npm run build

# --- stage 2: the API ------------------------------------------------------
FROM python:3.12-slim AS base

# Model weights land here rather than in a random home directory, so the layer
# below can be cached and the volume mounted.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/opt/hf

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# git   — sessions are git worktrees (`git worktree add`), so this is load-bearing,
#         not a convenience; without it every POST /v1/sessions fails.
# ripgrep — the lexical retrieval path shells out to `rg` for identifier queries.
RUN --mount=type=cache,target=/var/cache/apt \
    apt-get update && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies resolve from the lockfile alone, so this layer survives every
# source edit. Torch + BGE-M3 make it expensive to rebuild.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra dev

COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --extra dev

# The entrypoint fetches the demo target repo on first boot; a clean clone has none.
COPY scripts/bootstrap_target.sh scripts/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x scripts/bootstrap_target.sh /usr/local/bin/entrypoint.sh

# The committed fixtures are what make the container runnable with no API keys.
COPY data/fixtures/ data/fixtures/

# The API mounts this at / when it exists, so one port serves both (api/main.py).
COPY --from=web /web/dist/ web/dist/

ENV PATH="/app/.venv/bin:$PATH"

# HF_HOME only names the path; the directory has to exist before it can be
# chowned, and the compose volume that later mounts over it is a runtime thing.
RUN mkdir -p /opt/hf \
    && useradd --create-home --uid 1000 app \
    && chown -R app:app /app /opt/hf
USER app

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "forge.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
