# syntax=docker/dockerfile:1.7
# ProvingGround dashboard (Next.js standalone server).

FROM node:24-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# Browser-facing links are inlined at build time.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_GRAFANA_URL=http://localhost:3001
ARG NEXT_PUBLIC_PROMETHEUS_URL=http://localhost:9090
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_GRAFANA_URL=$NEXT_PUBLIC_GRAFANA_URL \
    NEXT_PUBLIC_PROMETHEUS_URL=$NEXT_PUBLIC_PROMETHEUS_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:24-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --retries=10 CMD ["node", "-e", "fetch('http://localhost:3000/api/pg/v1/config').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
