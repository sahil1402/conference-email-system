# Backend image — FastAPI app served by uvicorn.
#
# IMPORTANT: build with the REPO ROOT as the context (see docker-compose.yml:
#   build: { context: ., dockerfile: backend/Dockerfile }
# ). The pipeline resolves the dataset from <repo-root>/data (the retriever and
# eval harness walk up the source tree to find it), so the image must mirror the
# repo layout: /app/backend/... alongside /app/data/....
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy the backend source and the shared dataset (kept at repo root by design).
COPY backend/ ./backend/
COPY data/ ./data/

WORKDIR /app/backend

# Runtime deps only (dev extras like pytest are for CI, not the served image).
RUN pip install --upgrade pip && pip install -e .

EXPOSE 8000

# Apply migrations, then serve. Shell form so both run in sequence.
# alembic reads SYNC_DATABASE_URL; uvicorn serves the async app on 0.0.0.0.
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
