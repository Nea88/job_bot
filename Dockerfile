FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project
COPY src ./src
COPY scripts ./scripts
RUN uv sync --no-dev

CMD ["uv", "run", "--no-dev", "job-bot"]
