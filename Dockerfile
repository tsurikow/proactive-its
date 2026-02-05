FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY app ./app
COPY scripts ./scripts
COPY data ./data
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
