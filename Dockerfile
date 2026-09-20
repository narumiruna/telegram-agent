# https://docs.astral.sh/uv/guides/integration/docker/#non-editable-installs
ARG PYTHON_VERSION=3.14
ARG DEBIAN_VERSION=bookworm

FROM node:22-bookworm-slim AS otter-cli
ARG OTTER_COMMIT=7f1af003c331f1f263f94385f9c2d4cdc2142f58
WORKDIR /src
RUN node -e \
        "fetch('https://github.com/narumiruna/otter/archive/' + process.env.OTTER_COMMIT + '.tar.gz') \
        .then(response => { if (!response.ok) throw new Error('HTTP ' + response.status); return response.arrayBuffer(); }) \
        .then(data => require('node:fs').writeFileSync('/tmp/otter.tar.gz', Buffer.from(data)))" \
    && tar -xzf /tmp/otter.tar.gz --strip-components=1 \
    && rm /tmp/otter.tar.gz
RUN npm ci \
    && npm run build:cli \
    && chmod 0755 packages/cli/dist/otter.cjs \
    && printf 'Source: https://github.com/narumiruna/otter\nCommit: %s\n' "${OTTER_COMMIT}" > OTTER_UPSTREAM

FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-${DEBIAN_VERSION}-slim AS uv-deps

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev --no-editable

RUN --mount=type=cache,target=/root/.cache/uv \
    uv run --no-sync playwright install chromium

FROM uv-deps AS uv

ADD . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-freefont-ttf \
        fonts-ipafont-gothic \
        fonts-liberation \
        fonts-noto-color-emoji \
        fonts-tlwg-loma-otf \
        fonts-unifont \
        fonts-wqy-zenhei \
        git \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libfontconfig1 \
        libfreetype6 \
        libgbm1 \
        libglib2.0-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        xfonts-scalable \
        xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && chown -R app:app /app

COPY --from=uv --chown=app:app /app/.venv /app/.venv
COPY --from=uv --chown=app:app /ms-playwright /ms-playwright
COPY --from=otter-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=otter-cli /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=otter-cli /src/packages/cli/dist/otter.cjs /usr/local/bin/otter
COPY --from=otter-cli /src/LICENSE /usr/share/doc/otter/LICENSE
COPY --from=otter-cli /src/OTTER_UPSTREAM /usr/share/doc/otter/UPSTREAM
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

ENV PATH="/app/.venv/bin:$PATH"
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

USER app

ENTRYPOINT ["telegramagent"]
