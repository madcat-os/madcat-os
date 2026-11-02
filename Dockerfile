# syntax=docker/dockerfile:1
# madcat-os — MADCAT MCP server (`madcat mcp serve --transport http`) on :4097.
# Built by Kamal on forge (amd64); see config/deploy.yml.

FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
# The venv must point at /usr/local/bin/python3.13, which the runtime image has too.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3.13
WORKDIR /app
# Dependencies first, from the lockfile only, so code changes reuse this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim-bookworm
# uid 1001 matches the `kamal` user on forge.
RUN useradd --create-home --uid 1001 --shell /bin/bash madcat
COPY --from=build --chown=madcat /app/.venv /app/.venv
USER madcat
WORKDIR /home/madcat
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MADCAT_HOST=0.0.0.0 \
    MADCAT_PORT=4097
EXPOSE 4097
CMD ["madcat", "mcp", "serve", "--transport", "http"]
