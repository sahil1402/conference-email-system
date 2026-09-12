"""HTML entities are decoded where Zendesk's plain text enters the system.

Zendesk's ``plain_body`` is a MECHANICAL HTML-to-text conversion and leaves
entities behind as literal characters. Nothing decoded them, so ``&nbsp;``
reached the chair's screen — and, once the OpenReview relay existed, would have
reached a public venue — as six visible characters instead of a space.

⚠️ WHY THESE TESTS LIVE AT THE ADAPTER AND NOT IN THE EXTRACTOR. The extractor
is only one consumer of the body; the DISTILLER's LLM prompt and the
conversation view read the same string. Decoding at ingestion is what makes all
three correct at once, and it is also what keeps ``find_quote_boundary``'s
offsets valid — ``html.unescape`` shortens the string, so decoding after the
body is fixed would reintroduce the drift hazard the CRLF commit rejected
normalisation to avoid. The offset tests below exist to pin that.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.models import Email, EmailThreadMessage
from app.integrations.zendesk.adapter import _decode_entities

from tests.test_zendesk_adapter import (  # reuse the established fakes
    FakeAsyncClient,
    FakePipeline,
    FakeProvider,
    ZendeskIngestAdapter,
    _comment,
    _incremental_page,
    _nosleep,
    _ticket,
    _user,
)

#: One body carrying every entity the diagnostic named, plus the observed
#: trailing `&nbsp;` that started this.
ENTITY_BODY = (
    "He said &quot;we can&#39;t&quot; &amp; I disagreed.\n"
    "Use &lt;tag&gt; carefully.\n"
    "Sorry for the inconvenience.&nbsp;"
)
DECODED_BODY = (
    'He said "we can\'t" & I disagreed.\n'
    "Use <tag> carefully.\n"
    "Sorry for the inconvenience. "
)


@pytest_asyncio.fixture
async def adb():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _adapter():
    return ZendeskIngestAdapter(provider=FakeProvider(), pipeline=FakePipeline())


# ---------------------------------------------------------------------------
# The decoder itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        pytest.param("Sorry.&nbsp;", "Sorry. ", id="nbsp"),
        pytest.param("A &amp; B", "A & B", id="amp"),
        pytest.param("He said &quot;hi&quot;", 'He said "hi"', id="quot"),
        pytest.param("don&#39;t", "don't", id="numeric-apostrophe"),
        pytest.param("&#x27;quoted&#x27;", "'quoted'", id="hex-apostrophe"),
        pytest.param("&lt;script&gt;", "<script>", id="lt-gt"),
        pytest.param("50% &gt; 40%", "50% > 40%", id="gt-in-prose"),
    ],
)
def test_each_entity_variant_decodes(raw, expected):
    assert _decode_entities(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("plain text, nothing to do", id="plain"),
        # No valid entity: a bare `&` followed by a letter must survive, or
        # every "R&D" in the corpus would be mangled.
        pytest.param("R&D budget", id="bare-ampersand"),
        pytest.param("", id="empty"),
        pytest.param("100% & rising", id="ampersand-with-spaces"),
    ],
)
def test_already_plain_text_is_untouched(text):
    """Decoding must be a no-op on text that was never encoded."""
    assert _decode_entities(text) == text


def test_decoding_a_decoded_string_again_changes_nothing(subtests=None):
    """Idempotency for SINGLE-encoded input — the case that actually occurs.

    Each body is decoded exactly once on the way in, so this is belt-and-braces
    rather than load-bearing; it matters because a future caller adding a second
    decode somewhere must not corrupt anything.
    """
    once = _decode_entities(ENTITY_BODY)
    assert _decode_entities(once) == once


def test_double_encoded_input_is_a_known_limitation():
    """⚠️ PINS AN ACCEPTED COST, not a feature.

    ``html.unescape`` is not idempotent for DOUBLE-encoded text: ``&amp;amp;``
    decodes to ``&amp;`` and a second pass would reach ``&``. Ingestion decodes
    exactly once, so reaching the unstable state needs a genuinely
    double-encoded source. Named here rather than guarded against, because a
    guard would have to guess what the author meant.
    """
    assert _decode_entities("&amp;amp;") == "&amp;"
    assert _decode_entities(_decode_entities("&amp;amp;")) == "&"


def test_none_passes_through():
    """`plain_body` is nullable on a Zendesk comment."""
    assert _decode_entities(None) is None


# ---------------------------------------------------------------------------
# End-to-end through a real sync
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_synced_ticket_stores_a_decoded_body(adb):
    """The whole point: what lands in `Email.body` is already decoded."""
    requester = _user(500, "end-user")
    page = _incremental_page([_ticket(100)], users=[requester], cursor="C1")
    comments = {
        100: {
            "comments": [_comment(9001, 500, body=ENTITY_BODY)],
            "users": [requester],
        }
    }

    await _adapter().sync(
        adb, client=FakeAsyncClient([page], comments), sleep=_nosleep
    )

    email = (await adb.execute(select(Email))).scalars().one()
    assert email.body == DECODED_BODY
    for entity in ("&nbsp;", "&amp;", "&quot;", "&#39;", "&lt;", "&gt;"):
        assert entity not in email.body, entity


@pytest.mark.asyncio
async def test_thread_messages_are_decoded_too(adb):
    """The conversation view reads `plain_body` off the child rows.

    Decoding only `Email.body` would leave the chair reading `&nbsp;` in the
    thread while the extractor saw clean text — the same defect, one surface
    over.
    """
    requester = _user(500, "end-user")
    page = _incremental_page([_ticket(100)], users=[requester], cursor="C1")
    comments = {
        100: {
            "comments": [_comment(9001, 500, body=ENTITY_BODY)],
            "users": [requester],
        }
    }

    await _adapter().sync(
        adb, client=FakeAsyncClient([page], comments), sleep=_nosleep
    )

    msg = (await adb.execute(select(EmailThreadMessage))).scalars().one()
    assert msg.plain_body == DECODED_BODY


@pytest.mark.asyncio
async def test_the_pipeline_receives_decoded_text(adb):
    """Covers the consumer the narrow fix could not have reached.

    The orchestrator hands this same string to the DISTILLER as its LLM prompt
    and to the classifier. Asserted on what the pipeline was actually called
    with, not on the stored row, because those are different code paths and only
    one of them is the model's input.
    """
    requester = _user(500, "end-user")
    page = _incremental_page([_ticket(100)], users=[requester], cursor="C1")
    comments = {
        100: {
            "comments": [_comment(9001, 500, body=ENTITY_BODY)],
            "users": [requester],
        }
    }
    pipeline = FakePipeline()
    adapter = ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline)

    await adapter.sync(adb, client=FakeAsyncClient([page], comments), sleep=_nosleep)

    (call,) = pipeline.calls
    assert call["body"] == DECODED_BODY
    assert "&#39;" not in call["body"]


@pytest.mark.asyncio
async def test_the_ticket_description_fallback_is_decoded(adb):
    """⚠️ THE SECOND entry point, and the one a mutation caught as untested.

    When no public requester comment can be identified, the body falls back to
    `ticket["description"]` — a field that NEVER passes through the comment
    projection, so the decode there is not redundant with it. Reached here by
    giving the ticket only an agent-authored comment, so `_find_initial_inquiry`
    returns None.
    """
    requester = _user(500, "end-user")
    agent = _user(700, "agent")
    ticket = _ticket(100)
    ticket["description"] = "Please see &quot;the attachment&quot; &amp; reply."
    page = _incremental_page([ticket], users=[requester, agent], cursor="C1")
    comments = {
        100: {
            "comments": [_comment(9001, 700, body="agent note")],
            "users": [requester, agent],
        }
    }

    await _adapter().sync(
        adb, client=FakeAsyncClient([page], comments), sleep=_nosleep
    )

    email = (await adb.execute(select(Email))).scalars().one()
    assert email.body == 'Please see "the attachment" & reply.'


@pytest.mark.asyncio
async def test_a_double_encoded_entity_is_decoded_only_once(adb):
    """⚠️ THE REGRESSION THAT ALMOST SHIPPED.

    `html.unescape` is not idempotent, so "decode at ingestion" is only correct
    if ingestion decodes ONCE. The first version of this change applied it at
    every place Zendesk text is read — but three of those consume
    already-PROJECTED message dicts rather than raw comments, so a body went
    through twice and `&amp;amp;` (how one writes a literal `&amp;`) came out as
    a bare `&`.

    `&amp;amp;` must survive as `&amp;`: decoded once, not twice. This asserts
    it on BOTH surfaces, because the thread row and the email body are populated
    by different call sites and only one of them was double-decoding.
    """
    requester = _user(500, "end-user")
    page = _incremental_page([_ticket(100)], users=[requester], cursor="C1")
    comments = {
        100: {
            "comments": [_comment(9001, 500, body="Escape it as &amp;amp; please")],
            "users": [requester],
        }
    }

    await _adapter().sync(
        adb, client=FakeAsyncClient([page], comments), sleep=_nosleep
    )

    email = (await adb.execute(select(Email))).scalars().one()
    msg = (await adb.execute(select(EmailThreadMessage))).scalars().one()
    assert email.body == "Escape it as &amp; please"
    assert msg.plain_body == "Escape it as &amp; please"


@pytest.mark.asyncio
async def test_the_subject_is_deliberately_not_decoded(adb):
    """⚠️ PINS A SCOPE BOUNDARY, not an oversight.

    `subject` comes from Zendesk's own plain-text metadata off the MIME header,
    NOT from the HTML-to-text conversion that produces `plain_body`. It has a
    different provenance and no observed artifact, so decoding it would be
    speculative — and would mangle a subject that legitimately writes `&amp;`.
    """
    requester = _user(500, "end-user")
    page = _incremental_page(
        [_ticket(100, subject="Papers &amp; posters")], users=[requester], cursor="C1"
    )
    comments = {100: {"comments": [_comment(9001, 500)], "users": [requester]}}

    await _adapter().sync(
        adb, client=FakeAsyncClient([page], comments), sleep=_nosleep
    )

    email = (await adb.execute(select(Email))).scalars().one()
    assert email.subject == "Papers &amp; posters"


# ---------------------------------------------------------------------------
# The offset hazard this placement avoids
# ---------------------------------------------------------------------------
def test_decoding_shortens_the_string_which_is_why_it_happens_first():
    """⚠️ THE REASON THE FIX IS AT INGESTION AND NOT IN quoted_reply.

    `html.unescape` is not length-preserving — `&nbsp;` is six characters and
    becomes one. `find_quote_boundary` returns an offset that
    `extract_reply_text` slices the body with, so decoding at ANY point after
    the body is fixed would desynchronise the two: the same drift the CRLF
    commit chose a regex anchor over normalisation to avoid.

    Decoding before the body is stored means every consumer downstream sees one
    self-consistent string, and the hazard cannot arise.
    """
    raw = "Thanks.&nbsp;&nbsp;more text here"

    assert len(_decode_entities(raw)) < len(raw)
    assert len(raw) - len(_decode_entities(raw)) == 10


def test_a_decoded_body_slices_consistently_end_to_end():
    """Boundary + slice on the decoded string agree, because it is one string."""
    from app.pipeline.quoted_reply import extract_reply_text, find_quote_boundary

    body = _decode_entities(
        "Thanks, I will fix it.&nbsp;\r\n"
        "\r\n"
        "-----Original Message-----\r\n"
        "From: AAAI <a@openreview.net>\r\n"
    )

    boundary = find_quote_boundary(body)

    assert body[boundary:].startswith("-----Original Message-----")
    assert extract_reply_text(body) == "Thanks, I will fix it."


def test_an_nbsp_entity_separator_line_now_bridges_a_header_run():
    """⚠️ THE COMPOSITION the previous commit predicted and pinned.

    `_is_blank` tolerates `\\u00a0` but not the literal six characters
    `&nbsp;` — deliberately, so that the missing decode could not be papered
    over at the wrong layer. Now that decoding happens at ingestion, such a
    separator arrives as `\\u00a0` and the bridge accepts it, so a divider-less
    header block broken up by `&nbsp;` lines is detected.

    Asserted on the DECODED body, which is what the extractor actually receives
    in production. The unit-level test in `test_quoted_reply.py` still pins the
    literal form as resetting the run, and that remains correct: by the time a
    body reaches that module, entities are already gone.
    """
    from app.pipeline.quoted_reply import extract_reply_text, find_quote_cues

    raw = "\r\n".join(
        [
            "Thanks.",
            "",
            "From: AAAI <a@openreview.net>",
            "&nbsp;",
            "Date: 2026/09/01",
            "&nbsp;",
            "To: reviewer <r@x.edu>",
            "",
            "original comment text",
        ]
    )

    # Before decoding: the literal entity lines break the run (unchanged).
    assert find_quote_cues(raw) == []

    # After decoding, exactly as ingestion now does it:
    body = _decode_entities(raw)
    assert [c.cue for c in find_quote_cues(body)] == ["header_block"]
    assert extract_reply_text(body) == "Thanks."
