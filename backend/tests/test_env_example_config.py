"""Guard: every value assigned in backend/.env.example must be accepted by the
typed Settings in app/core/config.py.

Catches the "documented a value the type doesn't accept" class of bug — e.g.
shipping ``MODEL_PROVIDER=external_api`` in .env.example when the config Literal
has no ``external_api`` member (exactly the value this branch deliberately does
NOT support). Such a mismatch would make the app crash at settings-load time for
anyone who copies .env.example → .env.
"""

from pathlib import Path
from typing import get_args

from app.core.config import Settings

_ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _active_assignments(key: str) -> list[str]:
    """Return the RHS of every uncommented ``KEY=value`` line for ``key``."""
    values: list[str] = []
    for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            values.append(value.strip())
    return values


def _literal_values(field: str) -> set[str]:
    """The allowed string members of a Literal-typed Settings field."""
    return set(get_args(Settings.model_fields[field].annotation))


def test_env_example_model_provider_is_a_valid_literal():
    allowed = _literal_values("MODEL_PROVIDER")
    values = _active_assignments("MODEL_PROVIDER")
    assert values, "backend/.env.example should set MODEL_PROVIDER"
    for value in values:
        assert value in allowed, (
            f"MODEL_PROVIDER={value!r} in .env.example is not a valid "
            f"config.py Literal member {sorted(allowed)}"
        )


def test_env_example_model_provider_loads_into_settings():
    """Every documented MODEL_PROVIDER value actually constructs a Settings."""
    for value in _active_assignments("MODEL_PROVIDER"):
        # Would raise pydantic ValidationError if the value is not in the Literal.
        assert Settings(MODEL_PROVIDER=value).MODEL_PROVIDER == value
