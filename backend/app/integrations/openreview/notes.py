"""OpenReview note operations — fetch one note, post one threaded reply.

Sibling to :mod:`app.integrations.openreview.client`, not part of it. That
module's docstring states its scope outright ("It contains no note-fetching and
no comment-posting logic; those are separate pieces built on top of this one"),
and the split mirrors the Zendesk integration next door, where
``credential_provider.py`` owns auth and ``sender.py`` owns the writes. Auth and
operations fail for different reasons and are mocked differently in tests, so
they stay in different files.

Both functions take an already-authenticated ``client``; neither builds one.

WHAT THIS DOES NOT DO
---------------------
* No chair-approval gate. Nothing here asks whether a post is allowed — that
  decision belongs to the caller and to the send gate, exactly as
  ``ZendeskSender`` is pure transport with the policy elsewhere.
* No readers computation. :func:`post_comment_reply` posts to the readers it is
  GIVEN. Deriving them belongs to the caller, which fetches the parent note and
  passes its readers straight through, so a reply reaches exactly the audience
  that could see the comment it answers — never a list this module invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- errors -----------------------------------------------------------------
# openreview-py raises exactly one exception type, ``OpenReviewException``, with
# no hierarchy — but it carries the API's JSON error body as its first argument
# (``{"name": ..., "message": ..., "status": ...}``), and transport failures
# surface as ``requests`` exceptions instead. So the information needed to tell
# "the note is gone" from "you may not do that" from "the network broke" does
# exist; it is just not expressed as types. These classes express it, so callers
# can branch on the distinction instead of re-parsing a dict.


class OpenReviewNoteError(RuntimeError):
    """Base for a failed OpenReview note operation.

    A RuntimeError, like ``OpenReviewAuthError``: something went wrong talking
    to OpenReview, as opposed to a configuration or caller-input problem.
    """


class OpenReviewNoteNotFoundError(OpenReviewNoteError):
    """The requested note does not exist, or exists but is deleted.

    Deleted counts as not-found deliberately. A note with a ``ddate`` is not a
    valid reply target, and treating it as merely "present with unusual fields"
    invites a caller to thread a public comment under a retracted one.
    """


class OpenReviewPermissionError(OpenReviewNoteError):
    """OpenReview refused the operation for this account (HTTP 403).

    Kept apart from the generic API error because the remedy is completely
    different: not a retry, but a change to what the posting account is allowed
    to do in this venue.
    """


class OpenReviewAPIError(OpenReviewNoteError):
    """Any other failure of the call — a non-403/404 API error, or transport."""


class OpenReviewThreadMismatchError(ValueError):
    """The parent note and the forum id supplied do not belong together.

    A ``ValueError`` rather than a note error, and that is the point: nothing
    failed remotely. The caller handed over two identifiers naming different
    threads, and posting anyway would put a public comment in the wrong paper's
    discussion. See :func:`post_comment_reply`.
    """


# --- the note wrapper -------------------------------------------------------


@dataclass(frozen=True)
class OpenReviewNote:
    """The four things ConfMail actually needs from a note.

    A MINIMAL TYPED WRAPPER rather than openreview-py's ``Note``, for three
    reasons:

    * ``Note`` takes 23 constructor fields; callers here need four. Handing back
      the raw object invites reliance on any of the other nineteen, each one a
      dependency on a library version.
    * The whole package imports openreview-py lazily so that nothing outside
      this directory has to know the library exists. Returning its type would
      undo that on the first line of the first caller.
    * It matches the convention next door: ``ZendeskSender`` returns a small
      ``SendOutcome`` model, not a raw ``httpx.Response``.

    There is deliberately NO escape hatch to the underlying object. If a fifth
    field is needed, it is added here — which is a visible, reviewable change,
    unlike a caller quietly reaching through into SDK internals.
    """

    id: str
    forum: str
    readers: list[str] = field(default_factory=list)
    content: dict[str, Any] = field(default_factory=dict)


def _error_payload(exc: Exception) -> dict[str, Any]:
    """The API's JSON error body from an OpenReviewException, or ``{}``.

    Defensive: the payload is whatever the server sent, so it may be a dict, a
    string, or absent entirely on a transport failure.
    """
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], dict):
        return args[0]
    return {}


def _classify(exc: Exception, context: str) -> OpenReviewNoteError:
    """Map a raised SDK/transport exception onto a specific error type.

    Reads ``status`` first and falls back to ``name``, because the HTTP status
    is the more stable of the two — error names have changed across API
    revisions, while 403 and 404 have not. Anything unrecognised becomes a
    generic :class:`OpenReviewAPIError` rather than being guessed at.
    """
    payload = _error_payload(exc)
    status = payload.get("status")
    name = str(payload.get("name", ""))
    message = payload.get("message") or str(exc)

    if status == 404 or "NotFound" in name:
        return OpenReviewNoteNotFoundError(f"{context}: not found ({message})")
    if status == 403 or "Forbidden" in name:
        return OpenReviewPermissionError(
            f"{context}: OpenReview refused the request ({message})"
        )
    return OpenReviewAPIError(f"{context}: {message}")


def get_note(client: Any, note_id: str) -> OpenReviewNote:
    """Fetch one note by id.

    Raises rather than returning ``None`` for a missing note, on purpose. The
    caller's next step is to read ``readers`` off the result, and a ``None``
    there would have to be distinguished from "found, but no readers" at every
    call site — a distinction that is easy to get wrong once and then wrong
    forever. An exception cannot be ignored by accident.

    A DELETED note (one carrying a ``ddate``) raises
    :class:`OpenReviewNoteNotFoundError` too. It is not a valid reply target,
    and the alternative — handing back a note that looks ordinary apart from one
    field nobody checks — is how a reply ends up threaded under a retracted
    comment.
    """
    note_id = (note_id or "").strip()
    if not note_id:
        raise OpenReviewNoteNotFoundError("A note id is required, but none was given.")

    try:
        raw = client.get_note(note_id)
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
        raise _classify(exc, f"Fetching OpenReview note {note_id}") from exc

    if raw is None:
        raise OpenReviewNoteNotFoundError(
            f"OpenReview returned no note for id {note_id}."
        )

    if getattr(raw, "ddate", None):
        raise OpenReviewNoteNotFoundError(
            f"OpenReview note {note_id} is deleted (ddate is set) and cannot be "
            "used as a reply target."
        )

    forum = getattr(raw, "forum", None)
    if not forum:
        # Without a forum there is nothing to verify a reply against, so this is
        # unusable rather than merely incomplete.
        raise OpenReviewAPIError(
            f"OpenReview note {note_id} carries no forum id; it cannot be used "
            "as a reply target."
        )

    return OpenReviewNote(
        id=str(getattr(raw, "id", note_id)),
        forum=str(forum),
        readers=list(getattr(raw, "readers", None) or []),
        content=dict(getattr(raw, "content", None) or {}),
    )


@dataclass(frozen=True)
class PostedComment:
    """Identifiers for a comment that was posted.

    Both fields are best-effort. The edit response's exact shape is not
    something this module can verify without the live API, so they are read
    defensively and may be ``None`` — a successful post whose ids could not be
    read is still a successful post, and must not be reported as a failure.
    """

    edit_id: str | None = None
    note_id: str | None = None


def _official_comment_invitation(venue_id: str, submission_number: int) -> str:
    return f"{venue_id}/Submission{submission_number}/-/Official_Comment"


def _role_signature(venue_id: str) -> str:
    """The role group this account posts as.

    ⚠️ PENDING CONFIRMATION FROM AAAI / OPENREVIEW. This matches the reference
    implementation's convention, and it satisfies the project decision that
    replies carry no personal identity — a role group, never an individual. It
    has NOT been confirmed against the venue's real group names, and a wrong
    signature is rejected by OpenReview at post time rather than silently
    mis-attributed, so the failure is loud. Confirm before the first live post.
    """
    return f"{venue_id}/Program_Chairs"


def post_comment_reply(
    client: Any,
    *,
    venue_id: str,
    submission_number: int,
    parent_note_id: str,
    forum_id: str,
    comment_text: str,
    readers: list[str],
    parent_note: OpenReviewNote | None = None,
) -> PostedComment:
    """Post an Official Comment threaded under ``parent_note_id``.

    ``replyto`` IS ``parent_note_id``, NOT ``forum_id``. This is the one thing
    in this module most worth getting right: the reference template replies to
    the forum, which threads at the top level of the discussion, whereas every
    reply ConfMail sends is an answer to one specific comment and has to nest
    under it. Both values are legal identifiers, so the wrong one produces a
    post that succeeds and lands in the wrong place.

    READERS ARE PASSED THROUGH UNCHANGED. This function never computes, filters,
    widens or narrows them. The caller fetches the parent note and hands over its
    readers, so the reply reaches exactly whoever could see the comment it
    answers.

    THREAD VERIFICATION IS INTERNAL AND UNCONDITIONAL. ``parent_note_id`` and
    ``forum_id`` arrive as two separate strings, and nothing about their types
    stops them naming different papers; posting on a mismatch would put a public
    comment in another submission's discussion. Leaving that to the caller would
    make the safety of a public write depend on every future call site
    remembering — so it is checked here, every time, and a mismatch raises
    :class:`OpenReviewThreadMismatchError` before anything is sent.

    ``parent_note`` is purely an optimisation, never a way to skip that check: a
    caller that already fetched the note (the expected flow, since it needed the
    readers) passes it in to avoid a second read. Omit it and the note is fetched
    here. Either way the verification runs.

    Raises :class:`OpenReviewThreadMismatchError` for inconsistent inputs,
    :class:`OpenReviewNoteNotFoundError` if the parent is gone,
    :class:`OpenReviewPermissionError` if the account may not post, and
    :class:`OpenReviewAPIError` for anything else.
    """
    parent_note_id = (parent_note_id or "").strip()
    forum_id = (forum_id or "").strip()
    if not parent_note_id or not forum_id:
        raise OpenReviewThreadMismatchError(
            "Both parent_note_id and forum_id are required to post a reply "
            f"(parent_note_id={'set' if parent_note_id else 'missing'}, "
            f"forum_id={'set' if forum_id else 'missing'})."
        )

    note = parent_note if parent_note is not None else get_note(client, parent_note_id)

    if note.id != parent_note_id:
        raise OpenReviewThreadMismatchError(
            f"The supplied parent note ({parent_note_id}) does not match the "
            f"note provided for verification ({note.id})."
        )
    if note.forum != forum_id:
        raise OpenReviewThreadMismatchError(
            f"Refusing to post: note {parent_note_id} belongs to forum "
            f"{note.forum}, not the supplied forum {forum_id}. Posting would "
            "place this comment in a different submission's discussion."
        )

    note_class = _load_note_class()
    reply = note_class(
        forum=forum_id,
        replyto=parent_note_id,
        readers=list(readers),
        content={"comment": {"value": comment_text}},
    )

    try:
        result = client.post_note_edit(
            invitation=_official_comment_invitation(venue_id, submission_number),
            signatures=[_role_signature(venue_id)],
            note=reply,
        )
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed
        raise _classify(
            exc,
            f"Posting an Official Comment on submission {submission_number} "
            f"in {venue_id}",
        ) from exc

    posted = getattr(result, "note", None)
    return PostedComment(
        edit_id=_opt_str(getattr(result, "id", None)),
        note_id=_opt_str(getattr(posted, "id", None)),
    )


def _opt_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _load_note_class() -> type:
    """Import openreview-py's v2 ``Note`` lazily.

    Same reasoning as ``client._load_client_class``: the package costs ~570 ms
    to import and needs a prebuilt wheel to install, so nothing pays for it
    until a real post happens.
    """
    try:
        from openreview.api import Note
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        from app.integrations.openreview.client import OpenReviewDependencyError

        raise OpenReviewDependencyError(
            "The 'openreview' package is required to post to OpenReview but is "
            "not installed. Install it with: "
            "pip install --only-binary=:all: openreview-py"
        ) from exc
    return Note
