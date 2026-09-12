"""Tests for the OpenReview note operations (fetch one note, post one reply).

FULLY HERMETIC — NO NETWORK. The client is a hand-written fake recording every
call; the real ``OpenReviewClient`` is never constructed, and openreview-py's
``Note`` class is injected via monkeypatching ``_load_note_class`` so the SDK is
not imported either. Verified separately by running this file with every network
syscall blocked.

Follows ``test_openreview_client.py`` / ``test_zendesk_credential_provider.py``:
hand-written fakes over ``unittest.mock``, ``monkeypatch`` for module-level
lookups.
"""

import pytest

from app.integrations.openreview import notes as orn
from app.integrations.openreview import (
    OpenReviewAPIError,
    OpenReviewNote,
    OpenReviewNoteNotFoundError,
    OpenReviewPermissionError,
    OpenReviewThreadMismatchError,
    get_note,
    post_comment_reply,
)

FORUM = "ll0avn6ylq"
PARENT = "jnHgRMHgrm"
VENUE = "AAAI.org/2027/Conference"
READERS = [f"{VENUE}/Program_Chairs", f"{VENUE}/Submission1030/Reviewers"]


# --- test doubles ----------------------------------------------------------


class FakeNote:
    """Stands in for an openreview-py Note as RETURNED by the SDK."""

    def __init__(self, *, id=PARENT, forum=FORUM, readers=None, content=None, ddate=None):
        self.id = id
        self.forum = forum
        self.readers = readers if readers is not None else list(READERS)
        self.content = content if content is not None else {"comment": {"value": "hi"}}
        self.ddate = ddate


class SpyNote:
    """Stands in for the Note CLASS used to build an outgoing reply.

    Records the kwargs it was constructed with — that record is what the
    replyto/readers assertions inspect.
    """

    last = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        SpyNote.last = self


class FakeEdit:
    def __init__(self, id="edit-1", note_id="new-note-1"):
        self.id = id
        self.note = type("N", (), {"id": note_id})()


_UNSET = object()


class FakeClient:
    """Records calls; raises whatever it was told to raise.

    ``note`` uses a sentinel default rather than ``None`` so a test can express
    "the SDK returned None" — with ``None`` as the default that case was
    silently substituted with a real note, and the test for it passed for the
    wrong reason.
    """

    def __init__(self, *, note=_UNSET, get_exc=None, post_exc=None, edit=None):
        self._note = FakeNote() if note is _UNSET else note
        self._get_exc = get_exc
        self._post_exc = post_exc
        self._edit = edit if edit is not None else FakeEdit()
        self.get_calls = []
        self.post_calls = []

    def get_note(self, note_id, details=None):
        self.get_calls.append(note_id)
        if self._get_exc is not None:
            raise self._get_exc
        return self._note

    def post_note_edit(self, **kwargs):
        self.post_calls.append(kwargs)
        if self._post_exc is not None:
            raise self._post_exc
        return self._edit


class FakeOpenReviewException(Exception):
    """Mimics openreview-py's exception: the API's JSON body as args[0]."""


@pytest.fixture(autouse=True)
def _inject_note_class(monkeypatch):
    """Never import the real SDK; every test builds replies with SpyNote."""
    SpyNote.last = None
    monkeypatch.setattr(orn, "_load_note_class", lambda: SpyNote)


def _post(client, **overrides):
    kwargs = {
        "venue_id": VENUE,
        "submission_number": 1030,
        "parent_note_id": PARENT,
        "forum_id": FORUM,
        "comment_text": "Thank you, I will review before the deadline.",
        "readers": list(READERS),
    }
    kwargs.update(overrides)
    return post_comment_reply(client, **kwargs)


# ---------------------------------------------------------------------------
# get_note
# ---------------------------------------------------------------------------
def test_get_note_returns_the_fields_we_need():
    client = FakeClient(note=FakeNote())

    note = get_note(client, PARENT)

    assert isinstance(note, OpenReviewNote)
    assert note.id == PARENT
    assert note.forum == FORUM
    assert note.readers == READERS
    assert note.content == {"comment": {"value": "hi"}}
    assert client.get_calls == [PARENT]


def test_get_note_returns_a_wrapper_not_the_sdk_object():
    """The SDK type must not leak into calling code — see OpenReviewNote's note."""
    raw = FakeNote()
    note = get_note(FakeClient(note=raw), PARENT)

    assert note is not raw
    assert not hasattr(note, "ddate")  # only the four fields we declared


def test_get_note_copies_readers_rather_than_aliasing_them():
    """A caller mutating the result must not reach back into the SDK object."""
    raw = FakeNote(readers=["a", "b"])
    note = get_note(FakeClient(note=raw), PARENT)

    note.readers.append("c")

    assert raw.readers == ["a", "b"]


def test_get_note_missing_raises_not_found():
    """A 404 payload, the shape openreview-py wraps the API body in."""
    exc = FakeOpenReviewException({"name": "NotFoundError", "message": "no", "status": 404})

    with pytest.raises(OpenReviewNoteNotFoundError) as err:
        get_note(FakeClient(get_exc=exc), PARENT)

    assert PARENT in str(err.value)


def test_get_note_none_result_raises_not_found_rather_than_returning_none():
    """`None` must never reach a caller: it is one `if` away from being read as
    'found, but no readers'."""
    with pytest.raises(OpenReviewNoteNotFoundError):
        get_note(FakeClient(note=None), PARENT)


def test_get_note_deleted_note_raises_not_found():
    """A ddate makes a note an invalid reply target, not merely an odd one."""
    client = FakeClient(note=FakeNote(ddate=1234567890))

    with pytest.raises(OpenReviewNoteNotFoundError) as err:
        get_note(client, PARENT)

    assert "deleted" in str(err.value)


def test_get_note_permission_denied_is_its_own_error():
    exc = FakeOpenReviewException({"name": "ForbiddenError", "message": "nope", "status": 403})

    with pytest.raises(OpenReviewPermissionError):
        get_note(FakeClient(get_exc=exc), PARENT)


def test_get_note_transport_failure_is_an_api_error():
    """A network error carries no JSON payload at all, so it must not be
    mis-read as a 404."""
    with pytest.raises(OpenReviewAPIError) as err:
        get_note(FakeClient(get_exc=ConnectionError("connection reset")), PARENT)

    assert not isinstance(err.value, OpenReviewNoteNotFoundError)
    assert "connection reset" in str(err.value)


def test_get_note_classifies_by_status_when_the_name_is_unfamiliar():
    """Status is the more stable signal; error names have changed across API
    revisions while 404 has not."""
    exc = FakeOpenReviewException({"name": "SomethingNew", "message": "gone", "status": 404})

    with pytest.raises(OpenReviewNoteNotFoundError):
        get_note(FakeClient(get_exc=exc), PARENT)


def test_get_note_blank_id_fails_without_calling_the_api():
    client = FakeClient()

    with pytest.raises(OpenReviewNoteNotFoundError):
        get_note(client, "   ")

    assert client.get_calls == []


def test_get_note_without_a_forum_is_unusable():
    with pytest.raises(OpenReviewAPIError) as err:
        get_note(FakeClient(note=FakeNote(forum=None)), PARENT)

    assert "forum" in str(err.value)


# ---------------------------------------------------------------------------
# post_comment_reply — the threading contract
# ---------------------------------------------------------------------------
def test_post_replies_to_the_PARENT_NOTE_not_the_forum():
    """THE most important assertion in this module.

    The reference template replies to the forum, which threads at the top level.
    Every reply ConfMail sends answers one specific comment and must nest under
    it. Both ids are legal, so the wrong one posts successfully into the wrong
    place — a failure with no error to notice.
    """
    client = FakeClient()

    _post(client)

    assert SpyNote.last.kwargs["replyto"] == PARENT
    assert SpyNote.last.kwargs["replyto"] != FORUM
    assert SpyNote.last.kwargs["forum"] == FORUM


def test_post_uses_the_submission_numbered_invitation_and_role_signature():
    client = FakeClient()

    _post(client)

    assert client.post_calls[0]["invitation"] == (
        f"{VENUE}/Submission1030/-/Official_Comment"
    )
    assert client.post_calls[0]["signatures"] == [f"{VENUE}/Program_Chairs"]


def test_post_signature_is_a_role_group_never_a_person():
    """Project decision: replies carry no personal identity."""
    client = FakeClient()

    _post(client)

    (signature,) = client.post_calls[0]["signatures"]
    assert signature.endswith("/Program_Chairs")
    assert "@" not in signature


def test_post_puts_the_comment_text_in_the_v2_content_shape():
    client = FakeClient()

    _post(client, comment_text="Understood, thank you.")

    assert SpyNote.last.kwargs["content"] == {
        "comment": {"value": "Understood, thank you."}
    }


# --- readers pass straight through ------------------------------------------
def test_post_passes_readers_through_unchanged():
    """No recomputation, no filtering, no widening — exactly what was given."""
    client = FakeClient()
    supplied = ["group/A", "group/B", "group/C"]

    _post(client, readers=supplied)

    assert SpyNote.last.kwargs["readers"] == supplied


def test_post_does_not_substitute_the_parent_notes_readers():
    """The parent's readers differ from the supplied list here, so a function
    that quietly re-derived them would be caught."""
    client = FakeClient(note=FakeNote(readers=["everyone"]))
    supplied = ["only/these"]

    _post(client, readers=supplied)

    assert SpyNote.last.kwargs["readers"] == ["only/these"]


def test_post_does_not_alias_the_callers_readers_list():
    client = FakeClient()
    supplied = ["group/A"]

    _post(client, readers=supplied)
    SpyNote.last.kwargs["readers"].append("group/injected")

    assert supplied == ["group/A"]


def test_post_never_fetches_a_second_time_to_derive_readers():
    """With the parent note supplied, the ONLY read is none at all — so readers
    cannot have been re-fetched."""
    client = FakeClient()
    parent = OpenReviewNote(id=PARENT, forum=FORUM, readers=["ignored"])

    _post(client, parent_note=parent)

    assert client.get_calls == []


# --- thread verification -----------------------------------------------------
def test_post_rejects_a_forum_id_that_does_not_match_the_parent_note():
    """The guard against posting into another submission's discussion."""
    client = FakeClient(note=FakeNote(forum="DIFFERENTforum"))

    with pytest.raises(OpenReviewThreadMismatchError) as err:
        _post(client)

    assert "DIFFERENTforum" in str(err.value)
    assert client.post_calls == []


def test_post_rejects_a_mismatch_even_when_the_note_is_supplied():
    """The optimisation must not become a way to skip verification."""
    client = FakeClient()
    parent = OpenReviewNote(id=PARENT, forum="OTHERforum1", readers=[])

    with pytest.raises(OpenReviewThreadMismatchError):
        _post(client, parent_note=parent)

    assert client.post_calls == []


def test_post_rejects_a_supplied_note_that_is_not_the_parent():
    client = FakeClient()
    parent = OpenReviewNote(id="someOtherNote", forum=FORUM, readers=[])

    with pytest.raises(OpenReviewThreadMismatchError) as err:
        _post(client, parent_note=parent)

    assert "someOtherNote" in str(err.value)
    assert client.post_calls == []


def test_post_verifies_by_fetching_when_no_note_is_supplied():
    client = FakeClient()

    _post(client)

    assert client.get_calls == [PARENT], "verification must fetch the parent"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_post_requires_both_identifiers(blank):
    client = FakeClient()

    with pytest.raises(OpenReviewThreadMismatchError):
        _post(client, forum_id=blank)
    with pytest.raises(OpenReviewThreadMismatchError):
        _post(client, parent_note_id=blank)

    assert client.post_calls == []


def test_post_does_not_happen_when_the_parent_is_missing():
    exc = FakeOpenReviewException({"name": "NotFoundError", "status": 404, "message": "x"})
    client = FakeClient(get_exc=exc)

    with pytest.raises(OpenReviewNoteNotFoundError):
        _post(client)

    assert client.post_calls == []


# --- failure classification on the write ------------------------------------
def test_post_permission_denied_is_its_own_error():
    exc = FakeOpenReviewException(
        {"name": "ForbiddenError", "message": "not a chair", "status": 403}
    )

    with pytest.raises(OpenReviewPermissionError) as err:
        _post(FakeClient(post_exc=exc))

    assert "1030" in str(err.value)


def test_post_invalid_invitation_is_a_generic_api_error():
    exc = FakeOpenReviewException(
        {"name": "ValidationError", "message": "bad invitation", "status": 400}
    )

    with pytest.raises(OpenReviewAPIError) as err:
        _post(FakeClient(post_exc=exc))

    assert not isinstance(err.value, OpenReviewPermissionError)
    assert "bad invitation" in str(err.value)


def test_post_transport_failure_is_an_api_error():
    with pytest.raises(OpenReviewAPIError):
        _post(FakeClient(post_exc=ConnectionError("dns failure")))


# --- the successful result ---------------------------------------------------
def test_post_returns_the_created_identifiers():
    result = _post(FakeClient(edit=FakeEdit(id="e-9", note_id="n-9")))

    assert result.edit_id == "e-9"
    assert result.note_id == "n-9"


def test_post_succeeds_even_when_the_response_shape_is_unfamiliar():
    """A post that worked but whose ids could not be read is still a success —
    reporting it as a failure would invite a duplicate post on retry."""

    class Bare:
        pass

    result = _post(FakeClient(edit=Bare()))

    assert result.edit_id is None
    assert result.note_id is None


# --- hermetic guarantee ------------------------------------------------------
def test_the_real_sdk_note_class_is_never_loaded_here():
    """`_load_note_class` is the ONLY route to openreview-py in this module, and
    the autouse fixture replaces it — so a post here can never construct a real
    SDK Note. Asserting on the injected class is the meaningful check; asserting
    on ``sys.modules`` would not be, since another test file may legitimately
    have imported the package already.
    """
    _post(FakeClient())

    assert orn._load_note_class() is SpyNote
    assert isinstance(SpyNote.last, SpyNote)
