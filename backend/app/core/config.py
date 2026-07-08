"""Application configuration.

Centralized, typed settings loaded from environment / .env. The four "swappable
flags" below are the architectural seams that let us replace pipeline modules
(classifier, retriever, router, drafter) without rewriting the app.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings.

    Values are read from environment variables first, then a local `.env` file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Swappable architecture flags -------------------------------------
    # Drafter backend. "anthropic_api"/"anthropic" → hosted API; "local" →
    # OpenAI-compatible local endpoint (e.g. self-hosted inference server);
    # "template" → zero-dependency drafter that fills a response template from
    # retrieved policy text with no model call (safest offline fallback);
    # "fallback" → deterministic no-network stub. Both anthropic spellings are
    # accepted so the historical default ("anthropic_api") and the shorter
    # "anthropic" both work.
    MODEL_PROVIDER: Literal[
        "anthropic_api", "anthropic", "local", "template", "fallback"
    ] = "anthropic_api"
    CONFIDENCE_THRESHOLD: float = 0.75
    # Retriever backend: "bm25" → keyword BM25 over the KB (default); "faiss" →
    # dense sentence-embedding retrieval (FAISS IndexFlatIP, cosine); "fusion" →
    # Reciprocal Rank Fusion over both. Default stays "bm25".
    RETRIEVAL_BACKEND: Literal["bm25", "faiss", "fusion"] = "bm25"
    ROUTING_STRATEGY: Literal["rule_based", "rl"] = "rule_based"
    # Classifier backend. "keyword" → dependency-free baseline; "trainable" →
    # sentence-embedding + LogisticRegression model (auto-falls back to keyword
    # until a model is trained). Default stays "keyword".
    CLASSIFIER_BACKEND: Literal["keyword", "trainable"] = "keyword"

    # Confidence calibration (Phase 5B). When True AND a fitted calibrator
    # artifact exists for the active CLASSIFIER_BACKEND, the router uses the
    # calibrated confidence instead of the raw classifier score. Off by default
    # so behaviour is unchanged for anyone not opting in; the calibrator maps
    # raw confidence → P(correct) and never changes intent predictions or the
    # FAQ threshold itself.
    CALIBRATION_ENABLED: bool = False

    # --- Pipeline tuning --------------------------------------------------
    # Minimum classifier confidence for an email to qualify for the FAQ
    # auto-reply lane. Kept distinct from CONFIDENCE_THRESHOLD so the FAQ
    # gate can be tuned independently of the general confidence floor.
    FAQ_CONFIDENCE_THRESHOLD: float = 0.65
    # Max policy chunks the retriever returns as grounding context.
    MAX_RETRIEVED_CHUNKS: int = 3

    # --- Active learning (Phase 5G) ---------------------------------------
    # A chair-approved/rerouted email whose router-used confidence sat within
    # this margin BELOW FAQ_CONFIDENCE_THRESHOLD is flagged as a near-miss
    # candidate for future labeling (band = [threshold - margin, threshold)).
    AL_CONFIDENCE_MARGIN: float = 0.15
    # A chair edit is "meaningful" (flagged) when the word-level change ratio
    # between the original and edited draft exceeds this (a typo fix stays below).
    AL_EDIT_RATIO: float = 0.15
    # Sentence-transformers model used by the FAISS retriever (CPU). Only read
    # when RETRIEVAL_BACKEND == "faiss".
    FAISS_MODEL_NAME: str = "all-MiniLM-L6-v2"
    # Max tokens the drafter may generate for a reply.
    DRAFTER_MAX_TOKENS: int = 500
    # Model the drafter calls when MODEL_PROVIDER == "anthropic_api". Never
    # hardcode a model id in source — read it from here so it stays swappable.
    DRAFT_MODEL: str = "claude-sonnet-5"

    # --- Local model provider (OpenAI-compatible, e.g. Ollama) ------------
    # Used only when MODEL_PROVIDER == "local". Base URL of the OpenAI-style
    # API; the drafter POSTs to {base}/chat/completions. Model name and URL
    # are read here — never hardcoded in source.
    LOCAL_MODEL_BASE_URL: str = "http://localhost:11434/v1"
    LOCAL_MODEL_NAME: str = "llama3.1:8b"

    # --- Secrets / connections --------------------------------------------
    ANTHROPIC_API_KEY: str | None = None
    # Primary async connection string used by the app's async engine.
    #   PostgreSQL: postgresql+asyncpg://user:password@localhost:5432/confmail
    #   SQLite (tests): sqlite+aiosqlite:///./test.db
    # Defaults to local SQLite so dev/tests work with no .env present.
    DATABASE_URL: str = "sqlite:///./conference_email.db"
    # Synchronous connection string — used only by Alembic / sync tooling
    # (psycopg2 for PostgreSQL). The async app never reads this.
    #   PostgreSQL: postgresql+psycopg2://user:password@localhost:5432/confmail
    SYNC_DATABASE_URL: str = "sqlite:///./conference_email.db"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (single source of truth)."""
    return Settings()


settings = get_settings()
