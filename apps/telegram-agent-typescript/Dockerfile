FROM node:24-bookworm-slim AS build

WORKDIR /build

COPY package.json package-lock.json ./
COPY apps/telegram-agent-typescript/package.json apps/telegram-agent-typescript/package.json
COPY packages/kabigon/package.json packages/kabigon/package.json
RUN --mount=type=cache,target=/root/.npm npm ci --workspace telegramagent-typescript --include-workspace-root=false

COPY apps/telegram-agent-typescript/ apps/telegram-agent-typescript/
RUN npm run build --workspace telegramagent-typescript \
    && npm prune --omit=dev --workspace telegramagent-typescript --include-workspace-root=false

FROM node:24-bookworm-slim

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/apps/telegram-agent-typescript /app/.telegramagent /app/.events /app/.agents \
    && chown -R app:app /app

COPY --from=build --chown=app:app /build/apps/telegram-agent-typescript/dist /app/apps/telegram-agent-typescript/dist
COPY --from=build --chown=app:app /build/node_modules /app/node_modules
COPY --from=build --chown=app:app /build/apps/telegram-agent-typescript/node_modules /app/apps/telegram-agent-typescript/node_modules
COPY --from=build --chown=app:app /build/apps/telegram-agent-typescript/package.json /app/apps/telegram-agent-typescript/package.json
COPY --chown=app:app SOUL.md /app/SOUL.md

ENV NODE_ENV=production
ENV TELEGRAMAGENT_PROJECT_ROOT=/app

USER app

ENTRYPOINT ["node", "/app/apps/telegram-agent-typescript/dist/index.js"]
