"""OpenReview integration.

Exposes the authentication layer only: the credential/auth error types and the
config-driven client factory. No note-fetching and no comment-posting logic
lives here yet — those are later pieces built on top of
:func:`get_openreview_client`.

Nothing in ``app/`` imports this package yet; it is deliberately standalone
until the piece that posts Official_Comments arrives.
"""

from app.integrations.openreview.client import (
    OpenReviewAuthError,
    OpenReviewCredentialError,
    OpenReviewDependencyError,
    get_openreview_client,
)

__all__ = [
    "OpenReviewCredentialError",
    "OpenReviewAuthError",
    "OpenReviewDependencyError",
    "get_openreview_client",
]
