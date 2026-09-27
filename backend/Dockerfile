FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy HF_HOME=/models

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Bake the three local models (~2 GB) into the image, before the app code so code changes
# don't invalidate this layer. Names must match app/config.py defaults.
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
ARG RERANKER_MODEL=BAAI/bge-reranker-base
ARG INJECTION_MODEL=protectai/deberta-v3-base-prompt-injection-v2
RUN uv run --no-sync python -c "\
from sentence_transformers import CrossEncoder, SentenceTransformer; \
from transformers import pipeline; \
SentenceTransformer('${EMBEDDING_MODEL}'); \
CrossEncoder('${RERANKER_MODEL}'); \
pipeline('text-classification', model='${INJECTION_MODEL}')"

# Models are in the image: never reach out to the Hugging Face Hub at runtime.
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync alembic upgrade head && uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port 8000"]
