"""OpenReview client factory — authenticated access, and nothing else.

This module owns exactly one job: hand back an authenticated OpenReview API
client. It contains no note-fetching and no comment-posting logic; those are
separate pieces built on top of this one.

Shape follows ``app.integrations.zendesk.credential_provider``: credentials come
from the typed ``Settings`` object rather than a stray ``os.environ`` read, a
missing or blank value raises a ``ValueError`` subclass at construction naming
EVERY absent field, and a runtime auth failure raises a distinct
``RuntimeError`` subclass so a config mistake is never confused with a network
one.

TWO DELIBERATE DIVERGENCES FROM THE ZENDESK MODULE
--------------------------------------------------
* No ABC and no provider seam. Zendesk has ``ZendeskCredentialProvider`` with
  two implementations because it genuinely has two auth modes selected by a
  config flag. OpenReview has one — username and password — so an interface
  here would be a seam with a single side, invented for symmetry rather than
  need. If a second mode ever appears (an API token, say), the shape to copy is
  already next door.
* No token caching. See :func:`get_openreview_client`.

API VERSION: v2 (``openreview.api.OpenReviewClient``, base
``https://api2.openreview.net``). v2 is the version that supports the
Official_Comment note posting this integration exists for, and openreview-py
actively rejects a v1 base URL passed to the v2 client, so the two cannot be
mixed up silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings


class OpenReviewCredentialError(ValueError):
    """Raised when OpenReview credential config is missing or invalid.

    Subclasses :class:`ValueError` so it reads as a configuration failure. The
    message always names exactly which fields are missing — a misconfiguration
    must fail loudly here, never as a silent ``None`` that surfaces later as a
    confusing login rejection from OpenReview.
    """


class OpenReviewAuthError(RuntimeError):
    """Raised when authenticating against OpenReview fails.

    Distinct from :class:`OpenReviewCredentialError` (a config problem): this is
    a runtime failure of the login itself — wrong password, a network error, or
    the API refusing the request. Kept separate so "you forgot to set a variable"
    and "the credentials were rejected" never look alike to a caller.
    """


class OpenReviewDependencyError(RuntimeError):
    """Raised when the ``openreview`` package is not installed.

    Its own error rather than a bare ImportError because the fix is specific and
    worth stating: install the dependency. See the note on lazy importing in
    :func:`_load_client_class`.
    """


def _load_client_class() -> type:
    """Import and return openreview-py's v2 client class.

    Imported LAZILY, inside the call, for two reasons rather than one:

    * ``import openreview`` costs ~570 ms. Nothing imports this module at
      startup today, and the fast test suite would pay that price on every run
      for a dependency it never exercises.
    * The package needs a prebuilt wheel to install cleanly (its transitive
      ``editdistance`` dependency has no wheel for Python 3.13 on Windows and
      wants a C compiler). A module-level import would make this whole package —
      and anything that imports it — uncollectable in an environment where that
      install failed, instead of failing only at the point of real use.
    """
    try:
        from openreview.api import OpenReviewClient
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise OpenReviewDependencyError(
            "The 'openreview' package is required to talk to OpenReview but is "
            "not installed. Install it with: "
            "pip install --only-binary=:all: openreview-py"
        ) from exc
    return OpenReviewClient


def _require_credentials(settings: Settings) -> tuple[str, str, str]:
    """Return ``(username, password, base_url)``, or raise naming what is absent.

    Values are stripped, and a whitespace-only value counts as missing — an
    ``OPENREVIEW_PASSWORD=`` line in a ``.env`` is a forgotten credential, not a
    password of zero characters.
    """
    username = (settings.OPENREVIEW_USERNAME or "").strip()
    password = (settings.OPENREVIEW_PASSWORD or "").strip()

    missing = [
        name
        for name, value in (
            ("OPENREVIEW_USERNAME", username),
            ("OPENREVIEW_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise OpenReviewCredentialError(
            "OpenReview access requires "
            + ", ".join(missing)
            + " to be set (check your .env / environment)."
        )

    # Never allowed to fall through to openreview-py's own default, which is
    # `http://localhost:3001` — see OPENREVIEW_BASE_URL in config.py.
    base_url = (getattr(settings, "OPENREVIEW_BASE_URL", "") or "").strip()
    if not base_url:
        raise OpenReviewCredentialError(
            "OpenReview access requires OPENREVIEW_BASE_URL to be set "
            "(check your .env / environment). It has a production default, so "
            "an empty value means it was explicitly blanked."
        )

    return username, password, base_url


def get_openreview_client(
    settings: Settings,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> Any:
    """Return an authenticated OpenReview v2 client.

    ``client_factory`` is the injection seam, and it is not optional politeness:
    openreview-py's ``OpenReviewClient.__init__`` performs a real login over the
    network whenever a username and password are supplied. Tests therefore
    cannot construct the real class at all, so they pass a fake here. This
    mirrors the injected ``httpx.Client`` in the Zendesk OAuth provider.

    NO TOKEN CACHING, deliberately. The Zendesk OAuth provider caches its bearer
    token because it is consulted on every request of a polling loop that runs
    every few minutes — there, a cache saves a login per request. This client is
    built for a chair-approval-gated action: a human decides, then one comment is
    posted. Building a client costs one login round-trip on an action that
    already waited on a person, while a cache would need expiry handling,
    invalidation on a rejected token, and thread-safety — and its failure mode, a
    stale token surfacing as a confusing 401 midway through a post, is worse than
    the cost it removes. A cache that is almost always cold is also the weakest
    case for caching there is.

    That decision is not load-bearing on the caller: this returns a client
    object, so anything posting several comments at once can hold one and reuse
    it. Reuse stays available; it is simply the caller's choice rather than
    hidden module state.

    Raises :class:`OpenReviewCredentialError` if a credential is missing,
    :class:`OpenReviewDependencyError` if the package is absent, and
    :class:`OpenReviewAuthError` if the login itself fails.
    """
    username, password, base_url = _require_credentials(settings)
    factory = client_factory if client_factory is not None else _load_client_class()

    try:
        return factory(baseurl=base_url, username=username, password=password)
    except (OpenReviewCredentialError, OpenReviewDependencyError):
        raise
    except Exception as exc:  # noqa: BLE001 - any login failure is an auth error
        # The message deliberately carries the base URL and the exception text
        # but NEVER the username or password: this string reaches logs.
        raise OpenReviewAuthError(
            f"OpenReview login failed against {base_url}: {exc}"
        ) from exc
