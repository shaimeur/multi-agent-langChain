# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Model weights land here rather than in a random home directory, so the layer
# below can be cached and the volume mounted.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/opt/hf

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies resolve from the lockfile alone, so this layer survives every
# source edit. Torch + BGE-M3 make it expensive to rebuild.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra dev

COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --extra dev

# The committed fixtures are what make the container runnable with no API keys.
COPY data/fixtures/ data/fixtures/

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 app && chown -R app:app /app /opt/hf
USER app

EXPOSE 8000 8501

CMD ["uvicorn", "forge.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
