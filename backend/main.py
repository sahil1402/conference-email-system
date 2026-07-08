"""FastAPI application entry point for the Conference Email System.

This is the composition root: it wires together middleware, lifespan, and the
API routers. Business logic lives in the pipeline / db modules — not here.
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import audit, auto_replies, dashboard, emails
from app.api.routes.training import router as training_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.chairs import router as chairs_router
from app.api.v1.emails import router as emails_router
from app.api.v1.retrieval import router as retrieval_router
from app.core.config import settings

API_VERSION = "0.1.0"
SERVICE_NAME = "conference-email-system"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Empty for now. DB engine setup / disposal will be wired here in a later
    piece (Piece 5) once the persistence layer exists.
    """
    # --- startup ---
    yield
    # --- shutdown ---


app = FastAPI(
    title="Conference Email System",
    version=API_VERSION,
    description="Automated conference email reply & routing system.",
    lifespan=lifespan,
)

# CORS — permissive for local development. Tighten for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register router stubs. Routes themselves arrive in Phase 1.
app.include_router(emails.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(auto_replies.router, prefix="/api")

# v1 API — implemented endpoints.
app.include_router(emails_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(training_router, prefix="/api/v1")
app.include_router(retrieval_router, prefix="/api/v1")
app.include_router(chairs_router, prefix="/api/v1")


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {
        "status": "ok",
        "version": API_VERSION,
        "service": SERVICE_NAME,
    }


# How long to wait when probing a local model endpoint's readiness.
_MODEL_PROBE_TIMEOUT_SECONDS = 3.0


@app.get("/api/v1/health/model", tags=["system"])
async def model_health() -> dict:
    """Report the configured drafter provider and its reachability.

    For the local provider, performs a quick GET to ``{base}/models`` (3s
    timeout) so the frontend can show a live model-status badge. For any other
    provider there is nothing to probe, so status is always ``configured``.
    """
    # Normalize the historical "anthropic_api" spelling to the badge's enum.
    provider = "anthropic" if settings.MODEL_PROVIDER == "anthropic_api" else settings.MODEL_PROVIDER

    # The template provider has no external dependency, so it is always healthy.
    if provider == "template":
        return {
            "provider": "template",
            "local_model_url": None,
            "local_model_name": None,
            "status": "healthy",
        }

    if provider != "local":
        return {
            "provider": provider,
            "local_model_url": None,
            "local_model_name": None,
            "status": "configured",
        }

    base = settings.LOCAL_MODEL_BASE_URL.rstrip("/")
    status_value = "configured"
    try:
        async with httpx.AsyncClient(timeout=_MODEL_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{base}/models")
        if response.status_code != 200:
            status_value = "unreachable"
    except Exception:  # noqa: BLE001 - any failure means the endpoint is unreachable
        status_value = "unreachable"

    return {
        "provider": provider,
        "local_model_url": settings.LOCAL_MODEL_BASE_URL,
        "local_model_name": settings.LOCAL_MODEL_NAME,
        "status": status_value,
    }
