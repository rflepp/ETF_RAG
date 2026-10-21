# Agent API for Cloud Run: FastAPI + LangGraph, retrieval against Qdrant Cloud (QDRANT_URL / QDRANT_API_KEY).
#   docker build -t etf-agent . && docker run -p 8080:8080 -e LLM_CHAIN=mistral -e MISTRAL_API_KEY=... etf-agent
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    FASTEMBED_CACHE_PATH=/app/models PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app

# dependencies first, so code changes reuse this layer
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra agent --no-install-project

COPY pipeline pipeline
COPY agent agent
COPY site/rules site/rules
RUN uv sync --frozen --no-dev --extra agent

# bake the query-time models into the image: no download on cold start
RUN .venv/bin/python -c "from pipeline.embed import embed_query; from agent.retrieval import _sparse_query, _reranker, \
DEFAULT_RERANKER; embed_query('warm'); _sparse_query('warm'); list(_reranker(DEFAULT_RERANKER).rerank('a', ['b']))"

RUN useradd --uid 10001 --no-create-home app && chown -R app /app/models
USER app
EXPOSE 8080
CMD ["sh", "-c", "exec .venv/bin/uvicorn agent.api:app --host 0.0.0.0 --port ${PORT} --no-access-log"]
