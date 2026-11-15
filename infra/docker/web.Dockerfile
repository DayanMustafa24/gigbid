# syntax=docker/dockerfile:1
# Next.js standalone build. Build context = repo root (the build needs apps/api/openapi.json to generate the typed client).
# API_URL is baked into the /api rewrite at BUILD time: docker compose passes http://api:8000.
FROM node:22-slim AS deps
WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM node:22-slim AS build
WORKDIR /app/apps/web
ARG API_URL=http://api:8000
ENV API_URL=$API_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/apps/web/node_modules ./node_modules
COPY apps/web ./
COPY apps/api/openapi.json ../api/openapi.json
RUN npm run build

FROM node:22-slim AS run
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /app/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /app/apps/web/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
