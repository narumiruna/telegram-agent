FROM node:24-bookworm-slim AS build

WORKDIR /build/typescript

COPY typescript/package.json typescript/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY typescript/ ./
RUN npm run build \
    && npm prune --omit=dev

FROM node:24-bookworm-slim

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/typescript /app/.telegramagent /app/.events /app/.agents \
    && chown -R app:app /app

COPY --from=build --chown=app:app /build/typescript/dist /app/typescript/dist
COPY --from=build --chown=app:app /build/typescript/node_modules /app/typescript/node_modules
COPY --from=build --chown=app:app /build/typescript/package.json /app/typescript/package.json
COPY --chown=app:app SOUL.md /app/SOUL.md

ENV NODE_ENV=production
ENV TELEGRAMAGENT_PROJECT_ROOT=/app

USER app

ENTRYPOINT ["node", "/app/typescript/dist/index.js"]
