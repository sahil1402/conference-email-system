"""OpenReview integration.

Exposes two layers, in separate modules: authentication (``client.py`` — the
credential/auth error types and the config-driven client factory) and note
operations (``notes.py`` — fetch one note, post one threaded reply). The split
mirrors the Zendesk integration, where auth and writes also live apart.

Nothing in ``app/`` imports this package yet; it is deliberately standalone
until the piece that posts Official_Comments arrives.
"""

from app.integrations.openreview.client import (
    OpenReviewAuthError,
    OpenReviewCredentialError,
    OpenReviewDependencyError,
    get_openreview_client,
)
from app.integrations.openreview.notes import (
    OpenReviewAPIError,
    OpenReviewNote,
    OpenReviewNoteError,
    OpenReviewNoteNotFoundError,
    OpenReviewPermissionError,
    OpenReviewThreadMismatchError,
    PostedComment,
    get_note,
    post_comment_reply,
)

__all__ = [
    # auth (client.py)
    "OpenReviewCredentialError",
    "OpenReviewAuthError",
    "OpenReviewDependencyError",
    "get_openreview_client",
    # note operations (notes.py)
    "get_note",
    "post_comment_reply",
    "OpenReviewNote",
    "PostedComment",
    "OpenReviewNoteError",
    "OpenReviewNoteNotFoundError",
    "OpenReviewPermissionError",
    "OpenReviewAPIError",
    "OpenReviewThreadMismatchError",
]
