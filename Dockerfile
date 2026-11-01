# ProvingGround Python services: API, worker, demo agents, and the CLI.
# One image, several commands (see docker-compose.yml).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first (cached unless a manifest changes).
COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/README.md packages/core/
COPY services/api/pyproject.toml services/api/
COPY services/demo_agents/pyproject.toml services/demo_agents/
RUN mkdir -p packages/core/src/provingground services/api/src/pground_api services/demo_agents/src/demo_agents \
 && touch packages/core/src/provingground/__init__.py services/api/src/pground_api/__init__.py services/demo_agents/src/demo_agents/__init__.py \
 && uv sync --frozen --no-dev --no-install-workspace

# Then the code.
COPY packages packages
COPY services services
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
RUN useradd --create-home --uid 1000 pground && mkdir -p /data/packs && chown pground:pground /data/packs
WORKDIR /app
COPY --from=builder --chown=pground:pground /app /app
COPY --chown=pground:pground provingground.yaml ./
COPY --chown=pground:pground datasets datasets
COPY --chown=pground:pground scripts scripts
COPY --chown=pground:pground inspect_tasks inspect_tasks
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PGROUND_CONFIG_PATH=/app/provingground.yaml PGROUND_ROOT=/app
USER pground
EXPOSE 8000 8100
CMD ["pground-api", "serve"]
