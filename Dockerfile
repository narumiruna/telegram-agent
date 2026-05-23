# https://docs.astral.sh/uv/guides/integration/docker/#non-editable-installs
ARG PYTHON_VERSION=3.14
ARG DEBIAN_VERSION=bookworm
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-${DEBIAN_VERSION}-slim AS uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev --no-editable

ADD . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable
RUN --mount=type=cache,target=/root/.cache/uv \
    uv run --no-sync playwright install chromium

FROM python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && chown -R app:app /app

COPY --from=uv --chown=app:app /app/.venv /app/.venv
RUN /app/.venv/bin/playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv --chown=app:app /ms-playwright /ms-playwright

ENV PATH="/app/.venv/bin:$PATH"
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

USER app

ENTRYPOINT ["telegramagent"]
