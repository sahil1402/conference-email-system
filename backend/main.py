"""FastAPI application entry point for the Conference Email System.

This is the composition root: it wires together middleware, lifespan, and the
API routers. Business logic lives in the pipeline / db modules — not here.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import audit, auto_replies, dashboard, emails

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
app.include_router(audit.router, prefix="/api")


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {
        "status": "ok",
        "version": API_VERSION,
        "service": SERVICE_NAME,
    }
