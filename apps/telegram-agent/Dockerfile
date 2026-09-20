FROM node:24-bookworm-slim AS build

WORKDIR /build/apps/telegram-agent

COPY apps/telegram-agent/package.json apps/telegram-agent/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY apps/telegram-agent/ ./
RUN npm run build \
    && npm prune --omit=dev

FROM node:24-bookworm-slim

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/apps/telegram-agent /app/.telegramagent /app/.events /app/.agents \
    && chown -R app:app /app

COPY --from=build --chown=app:app /build/apps/telegram-agent/dist /app/apps/telegram-agent/dist
COPY --from=build --chown=app:app /build/apps/telegram-agent/node_modules /app/apps/telegram-agent/node_modules
COPY --from=build --chown=app:app /build/apps/telegram-agent/package.json /app/apps/telegram-agent/package.json
COPY --chown=app:app SOUL.md /app/SOUL.md

ENV NODE_ENV=production
ENV TELEGRAMAGENT_PROJECT_ROOT=/app

USER app

ENTRYPOINT ["node", "/app/apps/telegram-agent/dist/index.js"]
