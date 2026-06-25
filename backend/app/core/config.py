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
    MODEL_PROVIDER: Literal["anthropic_api", "local"] = "anthropic_api"
    CONFIDENCE_THRESHOLD: float = 0.75
    RETRIEVAL_BACKEND: Literal["bm25", "vector"] = "bm25"
    ROUTING_STRATEGY: Literal["rule_based", "rl"] = "rule_based"

    # --- Secrets / connections --------------------------------------------
    ANTHROPIC_API_KEY: str | None = None
    DATABASE_URL: str = "sqlite:///./conference_email.db"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (single source of truth)."""
    return Settings()


settings = get_settings()
