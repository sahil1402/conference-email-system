"""Where the quoted original message starts, and what is left once it is gone.

Two layers, tested separately on purpose: the boundary functions return an
offset and never text, and `extract_reply_text` returns text and finds no
boundary of its own. A detection bug and a slicing bug fail differently, so
neither set of tests is allowed to stand in for the other.

SCOPE LIMIT: no pipeline wiring. Nothing calls this module in `app/` yet, so
these are unit tests against the function directly; there is no end-to-end path
to assert on.
"""

from app.pipeline.quoted_reply import (
    extract_reply_text,
    find_quote_boundary,
    find_quote_cues,
)

# A person's own reply, used as the prefix for most fixtures so the expected
# boundary is always "right after this".
_REPLY = "Dear Chairs,\n\nI will provide review before the deadline.\n\n"

# The real reported shape from this project: a Chinese mail client's quote, with
# Chinese field labels and — note — an EMPTY 抄送 (Cc) value.
_CHINESE_QUOTE = (
    "-----原始邮件-----\n"
    '发件人:"AAAI 2027" <aaai2027-notifications@openreview.net>\n'
    "发送时间:2026-08-27 15:28:28 (星期四)\n"
    "收件人: pengshaohui@iscas.ac.cn\n"
    "抄送:\n"
    "主题: [AAAI 2027] Senior Program Committee 6UDQ commented on a paper\n"
)

_ENGLISH_QUOTE = (
    "-----Original Message-----\n"
    "From: AAAI 2027 <aaai2027-notifications@openreview.net>\n"
    "Sent: Wednesday, August 27, 2026 3:28 PM\n"
    "To: pengshaohui@iscas.ac.cn\n"
    "Subject: [AAAI 2027] SPC commented on a paper\n"
)


# ---------------------------------------------------------------------------
# The formats we know we must handle
# ---------------------------------------------------------------------------
def test_real_chinese_quote_boundary_lands_before_the_divider():
    """The reported real-world case, asserted the way it was described.

    The boundary must fall right after "...before the deadline." and right
    before "-----原始邮件-----", so both halves are checked rather than just the
    integer.
    """
    body = _REPLY + _CHINESE_QUOTE
    boundary = find_quote_boundary(body)

    assert boundary == len(_REPLY)
    assert body[:boundary].rstrip().endswith("I will provide review before the deadline.")
    assert body[boundary:].startswith("-----原始邮件-----")


def test_english_original_message_block():
    body = _REPLY + _ENGLISH_QUOTE
    boundary = find_quote_boundary(body)

    assert boundary == len(_REPLY)
    assert body[boundary:].startswith("-----Original Message-----")


def test_on_date_someone_wrote_attribution():
    quote = "On Wed, Aug 27, 2026 at 3:28 PM AAAI <a@openreview.net> wrote:\n"
    body = _REPLY + quote

    assert find_quote_boundary(body) == len(_REPLY)


def test_attribution_wrapped_across_two_lines():
    """Gmail wraps long attributions so `wrote:` lands on its own line.

    Both offline scripts anchor `On .+ wrote:` to a SINGLE line and miss this
    entirely, which is a real gap rather than a hypothetical one.
    """
    quote = "On Wed, Aug 27, 2026 at 3:28 PM AAAI 2027 <a@openreview.net>\nwrote:\n"
    body = _REPLY + quote

    assert find_quote_boundary(body) == len(_REPLY)


def test_angle_bracket_quoted_lines():
    body = _REPLY + "> the original message\n> second quoted line\n"

    assert find_quote_boundary(body) == len(_REPLY)


def test_outlook_bare_horizontal_rule():
    body = _REPLY + "________________________________\nFrom: X <x@y.com>\n"

    assert find_quote_boundary(body) == len(_REPLY)


def test_gmail_forwarded_message_divider():
    body = _REPLY + "---------- Forwarded message ---------\nFrom: X <x@y.com>\n"

    assert find_quote_boundary(body) == len(_REPLY)


def test_header_block_without_any_divider():
    """Some clients emit the header block with no rule above it, so the block
    has to stand on its own as a cue."""
    body = _REPLY + (
        "From: AAAI 2027 <a@openreview.net>\n"
        "Sent: Wednesday, August 27, 2026 3:28 PM\n"
        "To: pengshaohui@iscas.ac.cn\n"
    )

    assert find_quote_boundary(body) == len(_REPLY)


def test_chinese_header_block_without_divider():
    """The same shape in another script — the SHAPE is what is matched, so the
    labels being Chinese must not matter."""
    body = _REPLY + (
        '发件人:"AAAI 2027" <a@openreview.net>\n'
        "发送时间:2026-08-27 15:28:28\n"
        "收件人: pengshaohui@iscas.ac.cn\n"
    )

    assert find_quote_boundary(body) == len(_REPLY)


def test_full_width_colon_is_accepted():
    """CJK clients emit `：` (U+FF1A) rather than an ASCII colon."""
    body = _REPLY + (
        '发件人："AAAI 2027" <a@openreview.net>\n'
        "发送时间：2026-08-27 15:28:28\n"
        "收件人： pengshaohui@iscas.ac.cn\n"
    )

    assert find_quote_boundary(body) == len(_REPLY)


def test_header_block_tolerates_an_empty_value():
    """The real sample carries a bare `抄送:` with no recipients. Requiring a
    value would break the run at exactly that line."""
    body = _REPLY + (
        '发件人:"AAAI 2027" <a@openreview.net>\n'
        "抄送:\n"
        "收件人: pengshaohui@iscas.ac.cn\n"
    )

    assert find_quote_boundary(body) == len(_REPLY)


# ---------------------------------------------------------------------------
# No quote, and the whole-body-is-quote decision
# ---------------------------------------------------------------------------
def test_no_quote_returns_none():
    body = "Dear Chairs,\n\nCould you clarify the page limit for the appendix?\n\nBest,\nJane\n"

    assert find_quote_boundary(body) is None
    assert find_quote_cues(body) == []


def test_body_that_is_entirely_quote_returns_zero_not_none():
    """DECIDED, not undefined: a body opening with quoted material returns 0.

    A caller slicing `body[:0]` gets an empty reply — visibly wrong to whoever
    reviews it. `None` would instead hand back the ENTIRE quoted notification as
    though the person had written it, which looks plausible and would be sent.
    Failing toward obviously-empty beats failing toward plausibly-wrong.
    """
    boundary = find_quote_boundary(_CHINESE_QUOTE)

    assert boundary == 0
    assert boundary is not None  # 0 and None are different answers here
    assert _CHINESE_QUOTE[:boundary] == ""


def test_zero_boundary_is_distinguishable_from_none():
    """The two must never be conflated by a caller using a falsy check."""
    assert find_quote_boundary(_CHINESE_QUOTE) == 0
    assert find_quote_boundary("just some text with no quote") is None


def test_empty_and_whitespace_bodies():
    assert find_quote_boundary("") is None
    assert find_quote_boundary("   \n\n  ") is None


# ---------------------------------------------------------------------------
# Multiple / nested quote levels — the FIRST cue wins
# ---------------------------------------------------------------------------
def test_nested_reply_chain_takes_the_first_boundary_only():
    """A reply to a reply to a reply. The first cue is where the newest author
    stopped writing; depth past that point is irrelevant."""
    body = _REPLY + (
        "On Thu X wrote:\n"
        "> On Wed Y wrote:\n"
        "> > On Tue Z wrote:\n"
        "> > > the original\n"
    )

    assert find_quote_boundary(body) == len(_REPLY)


def test_first_of_two_different_cue_types_wins():
    """An attribution above a divider: the earlier offset is the boundary even
    though a later, arguably stronger, cue also matched."""
    body = _REPLY + "On Thu X <x@y.com> wrote:\n\n-----Original Message-----\nFrom: A <a@b.com>\n"
    cues = find_quote_cues(body)

    assert [c.cue for c in cues][0] == "attribution"
    assert find_quote_boundary(body) == len(_REPLY)


def test_cues_are_returned_earliest_first():
    body = _REPLY + _CHINESE_QUOTE
    offsets = [c.offset for c in find_quote_cues(body)]

    assert offsets == sorted(offsets)
    assert len(offsets) >= 2  # divider and header block both present here


def test_cue_names_identify_which_signal_fired():
    """Carried for diagnosis: an offset alone cannot say why it was chosen."""
    assert find_quote_cues(_REPLY + _ENGLISH_QUOTE)[0].cue == "divider"
    assert find_quote_cues(_REPLY + "On Thu X wrote:\n")[0].cue == "attribution"
    assert find_quote_cues(_REPLY + "> a\n> b\n")[0].cue == "quoted_lines"
    assert (
        find_quote_cues(_REPLY + "From: A <a@b.com>\nSent: Wed\nTo: c@d.com\n")[0].cue
        == "header_block"
    )


# ---------------------------------------------------------------------------
# False positives — the dangerous direction
#
# A missed quote leaves extra text on a draft a human reviews. A WRONG boundary
# silently truncates what the person actually wrote. These cover the second.
# ---------------------------------------------------------------------------
def test_users_own_colon_list_is_not_a_header_block():
    """THE false positive the address guard exists for.

    Three consecutive `Label: value` lines, exactly the shape of a quoted
    header — but naming no one. Without the address requirement this truncates
    the message right before "Please advise."
    """
    body = (
        "Dear Chairs,\n\n"
        "Paper number: 1030\n"
        "Title: BlindTune\n"
        "Status: under review\n\n"
        "Please advise on the next step.\n"
    )

    assert find_quote_boundary(body) is None


def test_prose_containing_colons_is_not_a_header_block():
    body = "Hi,\n\nNote: I will do that.\nBut: only if the chairs allow it.\n\nThanks\n"

    assert find_quote_boundary(body) is None


def test_two_header_lines_are_not_enough_even_with_an_address():
    """Below the run threshold: two lines is not a block."""
    body = "Hi,\n\nContact: someone@example.com\nRegarding: the appendix\n\nThanks\n"

    assert find_quote_boundary(body) is None


def test_signature_delimiter_is_not_a_quote_boundary():
    """`-- ` marks a signature, not a quote. Signatures are out of scope."""
    body = "Dear Chairs,\n\nPlease advise.\n\n-- \nJane Roe\nExample University\n"

    assert find_quote_boundary(body) is None


def test_short_dashed_rule_is_not_a_divider():
    body = "Dear Chairs,\n\n--- update ---\n\nI resubmitted the paper.\n"

    assert find_quote_boundary(body) is None


def test_dashed_line_that_does_not_close_is_not_a_divider():
    """A labelled rule must CLOSE with rule characters.

    Added after a mutation removing that requirement survived the suite: the
    existing `--- update ---` case has only three dashes, so it is rejected by
    the length minimum and never reaches this guard. These do reach it — four or
    more leading dashes with ordinary text after and no closing run — and would
    each truncate a real message if a bare `----` prefix counted as a quote.
    """
    for line in ("---- update", "----Section 2", "---- my notes on the appendix"):
        body = "Dear Chairs,\n\n" + line + "\n\nI resubmitted the paper.\n"
        assert find_quote_boundary(body) is None, line


def test_single_quoted_line_is_not_enough():
    """One `>` is likelier a stray character than a quoted message."""
    body = "Hi,\n\n> maybe\n\nThat was my only thought.\n"

    assert find_quote_boundary(body) is None


def test_attribution_needs_the_wrote_marker_not_just_a_leading_on():
    body = "Hi,\n\nOn the other hand I wrote: some notes about the appendix.\n"

    assert find_quote_boundary(body) is None


def test_attribution_does_not_run_away_across_a_whole_body():
    """The character bound is what stops `On ...` at the top matching a
    `wrote:` hundreds of characters later."""
    body = "On Monday I started drafting.\n\n" + ("filler text. " * 40) + "\nshe wrote:\n"

    assert find_quote_boundary(body) is None


# ---------------------------------------------------------------------------
# Offset contract
# ---------------------------------------------------------------------------
def test_boundary_is_the_start_of_the_quote_line_itself():
    """`body[boundary:]` must BEGIN with the marker — the offset points at the
    quote's first character, not after it and not at the preceding blank line."""
    body = _REPLY + _ENGLISH_QUOTE
    boundary = find_quote_boundary(body)

    assert body[boundary:].startswith("-----Original Message-----")
    assert not body[:boundary].endswith("-")


def test_whitespace_between_reply_and_quote_stays_on_the_reply_side():
    """Trimming is the caller's decision, so the blank line is not consumed."""
    body = _REPLY + _ENGLISH_QUOTE
    boundary = find_quote_boundary(body)

    assert body[:boundary].endswith("\n\n")


# ---------------------------------------------------------------------------
# extract_reply_text — the text above the boundary, trimmed at the ends ONLY
#
# TRIM CONTRACT under test: `str.strip()` and nothing more. Several tests below
# exist specifically to pin what is NOT done — interior blank lines, indentation
# and signatures all survive — because the failure they guard against is silent
# reformatting of text that gets posted essentially verbatim.
# ---------------------------------------------------------------------------
def test_reply_text_from_the_real_chinese_quote():
    """The reported case end to end: only what the person typed comes back."""
    body = _REPLY + _CHINESE_QUOTE

    assert extract_reply_text(body) == (
        "Dear Chairs,\n\nI will provide review before the deadline."
    )


def test_reply_text_from_the_english_quote():
    body = _REPLY + _ENGLISH_QUOTE

    assert extract_reply_text(body) == (
        "Dear Chairs,\n\nI will provide review before the deadline."
    )


def test_reply_text_from_an_attribution_quote():
    body = _REPLY + "On Wed, Aug 27, 2026 X <x@y.com> wrote:\n> hello\n> there\n"

    assert extract_reply_text(body) == (
        "Dear Chairs,\n\nI will provide review before the deadline."
    )


def test_reply_text_with_no_quote_returns_the_whole_body_trimmed():
    body = "  \n Dear Chairs,\n\nCould you clarify the page limit?  \n\n  "

    assert extract_reply_text(body) == "Dear Chairs,\n\nCould you clarify the page limit?"


def test_reply_text_when_the_body_is_entirely_quote_is_empty():
    """Empty string — not None, and emphatically not the quote itself.

    Returning the quote here would post a notification back as though the
    person had written it; returning None would break the `-> str` contract.
    """
    result = extract_reply_text(_CHINESE_QUOTE)

    assert result == ""
    assert result is not None
    assert isinstance(result, str)


def test_reply_text_is_empty_when_only_whitespace_precedes_the_quote():
    """The boundary here is NOT 0 — a blank line pushes it to 2 — yet there is
    still no reply. This is why an empty RESULT is the dependable "nothing new"
    signal, and a `find_quote_boundary(body) == 0` check is not.
    """
    body = "\n\n" + _CHINESE_QUOTE

    assert find_quote_boundary(body) == 2  # not zero
    assert extract_reply_text(body) == ""


def test_reply_text_strips_surrounding_whitespace_only():
    body = "\n\n   Please advise on the appendix.   \n\n\n" + _ENGLISH_QUOTE

    assert extract_reply_text(body) == "Please advise on the appendix."


def test_reply_text_strips_unicode_whitespace_a_cjk_client_leaves():
    """`strip()` with no argument covers ideographic and non-breaking spaces;
    an ASCII-only trim would leave them on a CJK reply."""
    body = "\u3000\u3000Understood, thank you.\u3000\xa0\n\n" + _CHINESE_QUOTE

    assert extract_reply_text(body) == "Understood, thank you."


# --- what trimming must NOT touch -------------------------------------------
def test_reply_text_preserves_interior_blank_lines():
    """THE conservative-trim guard — REVISED, deliberately, not relaxed.

    Commit 6 refused to touch interior blank lines at all, reasoning that "one
    extra blank line is cosmetic; a dropped one changes what was written". That
    asymmetry still holds and still governs. What changed is the recognition
    that a run of SIX blank lines is not authored structure at all — it is
    Zendesk's HTML-to-text conversion, and protecting it protected debris.

    So the guard now has a threshold instead of an absolute: up to
    `_MAX_BLANK_LINES` (2) survive untouched, which is already more than anyone
    types on purpose. This fixture's THREE blank lines cap to two.

    The single- and double-blank cases — the ones that are plausibly authored —
    are pinned separately below, and those are the assertions that matter.
    """
    body = "First paragraph.\n\n\n\nSecond paragraph.\n\n" + _ENGLISH_QUOTE

    assert extract_reply_text(body) == "First paragraph.\n\n\nSecond paragraph."


def test_reply_text_preserves_interior_indentation():
    """A hand-aligned list is authored structure, not stray whitespace."""
    body = "My concerns:\n\n    1. the appendix\n    2. the page limit\n\n" + _ENGLISH_QUOTE

    assert extract_reply_text(body) == (
        "My concerns:\n\n    1. the appendix\n    2. the page limit"
    )


def test_reply_text_preserves_the_signature():
    """NO signature stripping, deliberately — see the module docstring.

    A sign-off is part of what the person wrote. Guessing where their words end
    risks cutting real content, and the same guess would have to work in every
    language this receives mail in.
    """
    body = "Thanks for the update.\n\nBest regards,\nJane Roe\nExample University\n\n" + _ENGLISH_QUOTE

    assert extract_reply_text(body) == (
        "Thanks for the update.\n\nBest regards,\nJane Roe\nExample University"
    )


def test_reply_text_preserves_a_sent_from_my_device_footer():
    """Also not stripped. `mine_extract_marc` cuts these offline; that is a
    corpus-cleanliness decision, not a safe one for text about to be posted."""
    body = "Will do.\n\nSent from my iPhone\n\n" + _ENGLISH_QUOTE

    assert extract_reply_text(body) == "Will do.\n\nSent from my iPhone"


# --- very short and degenerate inputs ---------------------------------------
def test_reply_text_single_word():
    assert extract_reply_text("Yes\n\n" + _ENGLISH_QUOTE) == "Yes"


def test_reply_text_emoji_only():
    """A one-character non-ASCII reply must survive the trim intact."""
    assert extract_reply_text("\U0001F44D\n\n" + _ENGLISH_QUOTE) == "\U0001F44D"


def test_reply_text_single_character():
    assert extract_reply_text("K\n\n" + _ENGLISH_QUOTE) == "K"


def test_reply_text_empty_and_whitespace_bodies():
    """Never raises on an empty or absent body — this will meet NULL columns."""
    assert extract_reply_text("") == ""
    assert extract_reply_text("   \n\n  ") == ""


def test_reply_text_always_returns_a_string():
    for body in ["", "plain text", _CHINESE_QUOTE, _REPLY + _ENGLISH_QUOTE]:
        assert isinstance(extract_reply_text(body), str)


# --- the layering holds ------------------------------------------------------
def test_reply_text_agrees_with_the_boundary_it_is_built_on():
    """Whatever the boundary says, the text is what sits above it — the two are
    not allowed to disagree about the same body."""
    body = _REPLY + _CHINESE_QUOTE
    boundary = find_quote_boundary(body)

    assert extract_reply_text(body) == body[:boundary].strip()


def test_reply_text_does_not_include_any_part_of_the_quote():
    """The strongest single assertion here: no marker leaks into the output."""
    for quote in (_CHINESE_QUOTE, _ENGLISH_QUOTE):
        result = extract_reply_text(_REPLY + quote)
        for marker in ("-----", "From:", "\u53d1\u4ef6\u4eba", "openreview.net", "Subject:"):
            assert marker not in result, marker


# =============================================================================
# LINE ENDINGS
#
# READ THIS BEFORE ADDING A FIXTURE TO THIS FILE.
#
# Everything above uses "\n" \u2014 175 LF escapes and, until this section existed,
# ZERO "\r". That was not a style choice but a structural blind spot: it made
# the whole suite incapable of observing a bug class that silently disabled the
# two cues scanned by regex.
#
# The bug: `_DIVIDER_RE` and `_ATTRIBUTION_RE` both end `[ \t]*$` under
# re.MULTILINE, where `$` matches immediately BEFORE the "\n". On CRLF input the
# "\r" sits between the last matched character and the anchor, and "\r" is in
# neither `[ \t]` nor `[-_=]`, so the match fails \u2014 silently, producing no cue at
# all, which the caller reads as "the whole body is the person's own text" and
# hands on the entire quoted notification. Ten of fifteen real marker variants
# were dead this way.
#
# CRLF IS THE NORMAL CASE, not an edge case: it is the RFC 5322 line ending, and
# Zendesk's `plain_body` is a mechanical HTML-to-text conversion that emits it.
# Every LF-only fixture above is therefore testing the rarer input.
#
# `header_block` and `quoted_lines` were never affected \u2014 they read lines through
# `_iter_lines`, which strips the ending itself. Only the two raw-body regexes
# needed fixing, and the parametrisation below covers both endings for every cue
# so that asymmetry cannot silently return.
# =============================================================================

import pytest

_EOLS = [pytest.param("\n", id="lf"), pytest.param("\r\n", id="crlf")]


def _body(eol: str, *lines: str) -> str:
    """Join lines with the given ending \u2014 the one knob these tests turn."""
    return eol.join(lines)


#: Every divider/attribution form measured dead on CRLF before the fix, with the
#: client that emits it. Kept as data so a new client shape is one row rather
#: than a new test, and so the ids name the real thing being covered.
_MARKERS = [
    pytest.param("-----Original Message-----", "divider", id="outlook-en"),
    # The live production case that prompted this fix.
    pytest.param("---- Replied Message ----", "divider", id="netease-replied"),
    pytest.param(
        "------------------ Original ------------------", "divider", id="qqmail-en"
    ),
    pytest.param(
        "---------- Forwarded message ---------", "divider", id="gmail-forward"
    ),
    pytest.param("-----\u539f\u59cb\u90ae\u4ef6-----", "divider", id="outlook-zh"),
    pytest.param(
        "---- \u56de\u590d\u7684\u539f\u90ae\u4ef6 ----", "divider", id="netease-zh"
    ),
    pytest.param(
        "------------------ \u539f\u59cb\u90ae\u4ef6 ------------------",
        "divider",
        id="qqmail-zh",
    ),
    pytest.param(
        "---------- \u8f6c\u53d1\u90ae\u4ef6 ----------", "divider", id="forward-zh"
    ),
    pytest.param("________________________________", "divider", id="outlook-rule"),
    pytest.param(
        "On Mon, Sep 1, 2026 at 10:00 AM X <a@b.net> wrote:",
        "attribution",
        id="attribution-en",
    ),
]


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize("marker, expected_cue", _MARKERS)
def test_marker_is_detected_under_both_line_endings(eol, marker, expected_cue):
    """The regression table, asserted as behaviour.

    Under LF every one of these passed before the fix; under CRLF every one
    returned NO CUE.
    """
    body = _body(eol, "Thanks, I will fix it.", "", marker, "quoted note body")

    cues = find_quote_cues(body)

    assert cues, f"no cue fired for {marker!r} under {eol!r}"
    assert cues[0].cue == expected_cue
    assert find_quote_boundary(body) == body.index(marker)


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize("marker, expected_cue", _MARKERS)
def test_no_part_of_the_marker_survives_into_the_reply(eol, marker, expected_cue):
    """The consequence that actually matters.

    A cue firing at the wrong offset is as bad as one not firing: this is the
    text that gets posted to a public venue.
    """
    body = _body(eol, "Thanks, I will fix it.", "", marker, "quoted note body")

    assert extract_reply_text(body) == "Thanks, I will fix it."


@pytest.mark.parametrize("eol", _EOLS)
def test_the_boundary_indexes_the_original_string_not_a_normalized_copy(eol):
    """Guards the fix that was NOT taken.

    Normalising "\\r\\n" to "\\n" before scanning also finds the marker, but every
    offset then under-counts by one per preceding line \u2014 and `extract_reply_text`
    slices the ORIGINAL body with it, so the reply comes back truncated by
    exactly the number of lines above the quote. The deeper the quote, the more
    words are silently eaten.

    Several lines sit above the boundary here so that drift, if reintroduced,
    could not be mistaken for a stray trailing character.
    """
    body = _body(
        eol,
        "Dear chairs,",
        "",
        "Line one of my reply.",
        "Line two of my reply.",
        "Line three of my reply.",
        "",
        "-----Original Message-----",
        "From: AAAI <a@b.net>",
    )

    boundary = find_quote_boundary(body)

    assert boundary == body.index("-----Original Message-----")
    assert body[boundary:].startswith("-----Original Message-----")
    # No word is clipped off the end of the kept text.
    assert extract_reply_text(body).endswith("Line three of my reply.")


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("-- ", id="signature-delimiter"),
        pytest.param("--- update ---", id="casual-three-dash"),
        pytest.param("---", id="too-short"),
        pytest.param("____", id="bare-rule-under-eight"),
        pytest.param(
            "On the other hand I wrote: some notes", id="prose-containing-wrote"
        ),
    ],
)
def test_non_cues_are_still_rejected_under_both_line_endings(eol, text):
    """The `\\r?` must widen only the line ending, never the cue itself.

    These are the false positives commit 5's guards exist to reject; a fix that
    bought CRLF support by relaxing the pattern would show up here first.
    """
    body = _body(eol, "Some reply text.", "", text, "more of my own text")

    assert find_quote_cues(body) == []
    assert find_quote_boundary(body) is None


def test_the_reported_production_case_crlf_replied_message():
    """The exact shape that leaked a whole OpenReview notification into a reply.

    CRLF, a `---- Replied Message ----` divider, and blank lines between the
    quoted header fields. Before this fix it produced NO CUES: the divider was
    killed by the "\\r", and the blank lines independently broke the
    header-block run (Root Cause B, a SEPARATE defect still open here).

    The CRLF commit fixed the divider half, and because the divider is the
    EARLIEST cue that alone was enough to strip this body \u2014 the header-block
    defect was bypassed rather than repaired, and this test asserted exactly one
    cue to record that.

    The blank-bridge commit then repaired the other half, so BOTH cues now fire.
    The assertion was widened accordingly rather than relaxed: the divider must
    still be FIRST, because it is the earliest cue that sets the boundary, and a
    header block appearing ahead of it would mean the boundary moved.
    """
    body = "\r\n".join(
        [
            "Thanks, I will fix it.",
            "",
            "---- Replied Message ----",
            "",
            "From: AAAI 2027 <aaai2027-notifications@openreview.net>",
            "",
            "Date: 2026/09/01 10:00",
            "",
            "To: reviewer <r@x.edu>",
            "",
            "Subject: SPC commented on a paper",
            "",
            "Please see the original comment text here.",
        ]
    )

    cues = [c.cue for c in find_quote_cues(body)]
    assert cues[0] == "divider", "the divider must remain the earliest cue"
    assert set(cues) == {"divider", "header_block"}
    assert find_quote_boundary(body) == body.index("---- Replied Message ----")
    kept = extract_reply_text(body)
    assert kept == "Thanks, I will fix it."
    for leaked in ("openreview.net", "From:", "Subject:", "original comment"):
        assert leaked not in kept, leaked


def test_the_divider_less_case_is_now_caught_by_the_header_block():
    """INVERTED from `test_root_cause_b_is_still_open_without_a_divider`.

    That test pinned this exact body as a KNOWN, UNFIXED defect: with the
    divider removed, the blank lines between the header fields broke
    `_find_header_block`'s consecutive-run requirement, no cue fired, and the
    entire quoted notification came back as the person's own reply.

    Blank lines now bridge the run, so the header block is found on its own \u2014 no
    divider required. Kept rather than deleted, and renamed rather than left
    with its "this is broken" framing, so the history of the gap stays legible.
    """
    body = "\r\n".join(
        [
            "Thanks, I will fix it.",
            "",
            "From: AAAI 2027 <aaai2027-notifications@openreview.net>",
            "",
            "Date: 2026/09/01 10:00",
            "",
            "To: reviewer <r@x.edu>",
            "",
            "Subject: SPC commented on a paper",
            "",
            "Please see the original comment text here.",
        ]
    )

    assert [c.cue for c in find_quote_cues(body)] == ["header_block"]
    kept = extract_reply_text(body)
    assert kept == "Thanks, I will fix it."
    for leaked in ("openreview.net", "From:", "Subject:", "original comment"):
        assert leaked not in kept, leaked


@pytest.mark.parametrize("eol", _EOLS)
def test_the_real_chinese_quote_under_both_line_endings(eol):
    """This file's headline real-world fixture, which was LF-only."""
    body = (_REPLY + _CHINESE_QUOTE).replace("\n", eol)

    boundary = find_quote_boundary(body)

    assert boundary is not None
    assert body[boundary:].startswith("-----\u539f\u59cb\u90ae\u4ef6-----")
    assert "openreview.net" not in extract_reply_text(body)


@pytest.mark.parametrize("eol", _EOLS)
def test_quoted_lines_and_header_block_were_never_affected(eol):
    """Pins the asymmetry the fix did NOT need to touch.

    Both cues read through `_iter_lines`, which strips the line ending itself,
    so both already worked on CRLF. Asserted so that a future tidy-up routing
    them through a raw regex reintroduces the bug loudly instead of silently.
    """
    quoted = _body(eol, "My reply.", "", "> quoted line one", "> quoted line two")
    headers = _body(
        eol,
        "My reply.",
        "",
        "From: A <a@b.net>",
        "To: B <c@d.net>",
        "Subject: something",
    )

    assert [c.cue for c in find_quote_cues(quoted)] == ["quoted_lines"]
    assert [c.cue for c in find_quote_cues(headers)] == ["header_block"]
    assert extract_reply_text(quoted) == "My reply."
    assert extract_reply_text(headers) == "My reply."


# =============================================================================
# BLANK LINES BRIDGE A HEADER RUN
#
# `_find_header_block` wants three header-shaped lines with an address among
# them. It used to require them ADJACENT, and real quoted headers arrive with
# blank lines between the fields often enough that whole blocks were lost.
#
# The tolerance is narrow on purpose, and these tests are mostly about its
# EDGES rather than its happy path: a blank line bridges, a content line still
# breaks, and a bridged line is SKIPPED rather than counted — so "three" still
# means three real headers.
# =============================================================================

_NBSP = " "
_IDEOGRAPHIC = "　"

#: The quoted notification's fields, as a divider-less block.
_NOTIFICATION_FIELDS = [
    "From: AAAI 2027 <aaai2027-notifications@openreview.net>",
    "Date: 2026/09/01 10:00",
    "To: reviewer <r@x.edu>",
    "Subject: SPC commented on a paper",
]


def _notification(eol: str, gap: str | None) -> str:
    """A reply above a divider-less quoted header block, `gap` between fields."""
    lines = ["Thanks, I will fix it.", ""]
    for field in _NOTIFICATION_FIELDS:
        lines.append(field)
        if gap is not None:
            lines.append(gap)
    lines.append("Please see the original comment text here.")
    return eol.join(lines)


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "gap",
    [
        pytest.param(None, id="adjacent"),
        pytest.param("", id="empty-line"),
        pytest.param("   ", id="spaces-only"),
        pytest.param("\t", id="tab-only"),
        pytest.param(_NBSP, id="nbsp-only"),
        pytest.param(_IDEOGRAPHIC, id="ideographic-space-only"),
        pytest.param(_NBSP + " " + _IDEOGRAPHIC, id="mixed-invisible"),
    ],
)
def test_a_blank_gap_between_header_fields_still_finds_the_block(eol, gap):
    """The reported production shape, across every blank a converter emits.

    The NBSP and ideographic cases are the ones that made this worth doing:
    those lines LOOK empty to everyone reading the email, so a rule that
    tolerated only `""` would still have failed on exactly the bodies that
    prompted the fix.
    """
    body = _notification(eol, gap)

    assert [c.cue for c in find_quote_cues(body)] == ["header_block"]
    kept = extract_reply_text(body)
    assert kept == "Thanks, I will fix it."
    assert "openreview.net" not in kept


@pytest.mark.parametrize("eol", _EOLS)
def test_several_blank_lines_between_fields_still_bridge(eol):
    """Bridging is not limited to a single line.

    The converter artifacts this exists for come in runs; a one-line allowance
    would fix the tidy example and miss the real ones.
    """
    body = eol.join(
        [
            "Thanks.",
            "",
            "From: AAAI <a@openreview.net>",
            "",
            "",
            "",
            "Date: 2026/09/01",
            "",
            "",
            "To: reviewer <r@x.edu>",
            "",
            "original comment text",
        ]
    )

    assert [c.cue for c in find_quote_cues(body)] == ["header_block"]
    assert extract_reply_text(body) == "Thanks."


# --- what must STILL break a run ---------------------------------------------
@pytest.mark.parametrize("eol", _EOLS)
def test_a_real_content_line_still_resets_the_run(eol):
    """The tolerance is for INVISIBLE lines, not for any line.

    Two header lines, a genuine sentence, then one more header line: three
    header-shaped lines in total, but never three in one run, so no cue. If this
    passed, `_find_header_block` would have become "any three colon lines
    anywhere near each other", which is the thing commit 5's guards exist to
    prevent.
    """
    body = eol.join(
        [
            "Reply.",
            "",
            "From: A <a@b.net>",
            "Date: 2026/09/01",
            "I am writing a real sentence here.",
            "To: B <c@d.net>",
            "original text",
        ]
    )

    assert find_quote_cues(body) == []
    assert find_quote_boundary(body) is None


@pytest.mark.parametrize("eol", _EOLS)
def test_a_literal_nbsp_entity_line_still_resets_the_run(eol):
    """⚠️ CORRECT, and it stays correct — read this before "fixing" it.

    An undecoded `&nbsp;` is six ordinary characters, not whitespace, so it
    breaks the run. This test was written expecting to FLIP once entity decoding
    landed. It did not, and that is the right outcome: decoding was placed at
    INGESTION (`adapter._decode_entities`), not in this module. By the time any
    body reaches `find_quote_cues` in production, `&nbsp;` has already become
    `\\u00a0` — which the bridge above accepts — so the literal form pinned here
    is simply unreachable rather than tolerated.

    Keeping it asserts the layering: this module handles whitespace, not HTML.
    The composition it used to predict is now proven end-to-end in
    `test_zendesk_entity_decoding.py::
    test_an_nbsp_entity_separator_line_now_bridges_a_header_run`.
    """
    body = eol.join(
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

    assert find_quote_cues(body) == []


# --- skipped, not counted -----------------------------------------------------
@pytest.mark.parametrize("eol", _EOLS)
def test_a_bridged_blank_does_not_count_toward_the_three(eol):
    """⚠️ THE TEST THAT DISTINGUISHES THE TWO COUNTING RULES.

    Exactly TWO real header lines with one blank between them. Under "skipped",
    the run is 2 and no cue fires — correct, because two fields are not evidence
    of a quoted message. Under "counted", From + blank + To reaches three and the
    block qualifies on the strength of one address and two fields.

    Constructed at precisely the threshold: any more header lines and both rules
    agree, which is why the ordinary fixtures above cannot catch this.
    """
    body = eol.join(
        [
            "Thanks.",
            "",
            "From: A <a@b.net>",
            "",
            "To: B <c@d.net>",
            "",
            "some trailing text",
        ]
    )

    assert find_quote_cues(body) == []


@pytest.mark.parametrize("eol", _EOLS)
def test_one_header_line_padded_with_blanks_is_not_a_block(eol):
    """The degenerate end of the same rule: one field, two blanks.

    Under a counting rule this reaches three on a SINGLE header line, which is
    the clearest possible statement that blanks are not evidence.
    """
    body = eol.join(["Thanks.", "", "From: A <a@b.net>", "", "", "trailing text"])

    assert find_quote_cues(body) == []


@pytest.mark.parametrize("eol", _EOLS)
def test_the_boundary_points_at_the_first_header_not_a_preceding_blank(eol):
    """A blank line cannot START a run.

    Otherwise `run_start` would drift up into the blank lines that belong to the
    reply, and the boundary would cut earlier than the quote actually begins.
    """
    body = _notification(eol, "")

    boundary = find_quote_boundary(body)

    assert boundary == body.index("From: AAAI 2027")
    assert body[boundary:].startswith("From: AAAI 2027")


# --- commit 5's address guard is untouched -----------------------------------
@pytest.mark.parametrize("eol", _EOLS)
def test_a_users_own_field_list_is_still_rejected_even_with_blank_lines(eol):
    """⚠️ THE NAMED REGRESSION RISK for this commit.

    `Paper number:` / `Title:` / `Status:` stacks three header-SHAPED lines and
    is exactly what commit 5's address requirement was added to reject. Blank
    lines between the entries must not smuggle it past that guard — the bridge
    changes which lines are adjacent, and nothing about what counts as evidence.
    """
    body = eol.join(
        [
            "Hello chairs,",
            "",
            "Paper number: 1030",
            "",
            "Title: A Study of Things",
            "",
            "Status: under review",
            "",
            "Could you advise?",
        ]
    )

    assert find_quote_cues(body) == []
    # Compared against the LF form: right-trimming each line also removes the
    # "\r" of a CRLF ending, so the returned text always uses "\n". That is a
    # transport artifact with no authored meaning — see the note on
    # `_normalize_reply` — and every character the person actually wrote is
    # still here, which is what this assertion is about.
    assert extract_reply_text(body) == body.replace("\r\n", "\n").strip()


@pytest.mark.parametrize("eol", _EOLS)
def test_the_address_guard_is_what_rejects_it_not_the_adjacency(eol):
    """Proves the guard above is load-bearing rather than incidental.

    The same list with one address added DOES qualify — so the previous test
    passes because of the address requirement, not because blank lines happened
    to break the run. See the next test for what that costs.
    """
    body = eol.join(
        [
            "Hello chairs,",
            "",
            "Paper number: 1030",
            "",
            "Contact: someone@uni.edu",
            "",
            "Status: under review",
            "",
            "Could you advise?",
        ]
    )

    assert [c.cue for c in find_quote_cues(body)] == ["header_block"]


@pytest.mark.parametrize("eol", _EOLS)
def test_a_users_own_contact_block_is_a_known_false_positive(eol):
    """⚠️ PINS AN ACCEPTED COST, not a feature.

    Someone writing their own `Name:` / `Email:` / `Affiliation:` block has three
    header-shaped lines and a real address, so it qualifies and their message is
    truncated to the line above it.

    This class is NOT new — the adjacent form already matched before this commit
    (verified against the pre-change code). What changed is that the
    blank-separated form now matches too, so the class is wider.

    Accepted deliberately: a missed header block posts a whole quoted
    notification to a public venue, while this truncates a draft a chair reads
    before anything is sent. The lever, if it bites, is bounding how many
    consecutive blanks may bridge. Pinned so the behaviour is visible rather
    than discovered.
    """
    body = eol.join(
        [
            "Hello chairs,",
            "",
            "Name: Wei Zhang",
            "",
            "Email: wei@uni.edu",
            "",
            "Affiliation: Example University",
            "",
            "Could you advise on my submission?",
        ]
    )

    assert [c.cue for c in find_quote_cues(body)] == ["header_block"]
    assert extract_reply_text(body) == "Hello chairs,"


# =============================================================================
# CONVERSION-ARTIFACT NORMALISATION
#
# Zendesk's HTML-to-text rendering leaves debris nobody typed: runs of six blank
# lines, trailing spaces on every line. Commit 6 refused to touch any of it, and
# that refusal was RIGHT about authored structure and WRONG about debris.
#
# The whole section is one harm asymmetry: this text is posted essentially
# verbatim to a public venue, so removing something a person meant is materially
# worse than leaving a stray blank line. Every threshold below errs that way,
# and the preservation tests are the ones that matter — the collapsing tests
# only prove the feature does anything at all.
# =============================================================================


@pytest.mark.parametrize("eol", _EOLS)
def test_an_excessive_blank_run_is_capped(eol):
    """The reported symptom: a wall of blank lines between salutation and body."""
    body = _body(
        eol, "Dear chairs,", "", "", "", "", "", "", "I think there is a mix-up."
    )

    assert extract_reply_text(body) == "Dear chairs,\n\n\nI think there is a mix-up."


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize("blanks", [pytest.param(1, id="one"), pytest.param(2, id="two")])
def test_authored_blank_lines_are_preserved_exactly(eol, blanks):
    """⚠️ THE CRITICAL REGRESSION TEST for commit 6's original intent.

    One blank line is an ordinary paragraph break; two is a deliberate section
    break. Both are plausibly typed, both survive byte-for-byte. The cap exists
    to remove debris, not to reformat prose — if this ever fails, the
    normalisation has started editing people's writing.
    """
    body = _body(eol, "Para one.", *([""] * blanks), "Para two.")

    assert extract_reply_text(body) == "Para one." + "\n" * (blanks + 1) + "Para two."


@pytest.mark.parametrize("eol", _EOLS)
def test_indentation_is_preserved_including_nesting_depth(eol):
    """⚠️ THE OFFLINE MINER'S RULE, MEASURED AND REJECTED.

    `mine_extract_marc.py` collapses every space/tab run to one
    (`_WS.sub(" ", text)`). Applied here it would turn "    - first" into
    " - first" and flatten a 4-space and an 8-space indent to the same thing,
    erasing nesting outright — on somebody's words on their way to a public
    forum. It was also a no-op on the leading-space artifact it was cited for.

    So indentation is preserved, at every depth. Asserted on the DEPTHS, not
    just on presence, because collapsing preserves "some indentation" while
    destroying the structure.
    """
    body = _body(
        eol,
        "My points:",
        "    - first sub-point",
        "        * nested detail",
        "    - second sub-point",
    )

    assert extract_reply_text(body) == (
        "My points:\n    - first sub-point\n        * nested detail"
        "\n    - second sub-point"
    )


@pytest.mark.parametrize("eol", _EOLS)
def test_trailing_whitespace_is_removed_from_every_line(eol):
    """Safe by construction: trailing whitespace renders as nothing anywhere.

    It is also what makes the cap work at all — see the next test.
    """
    body = _body(eol, "First line.   ", "Second line.\t", "Third line.")

    assert extract_reply_text(body) == "First line.\nSecond line.\nThird line."


@pytest.mark.parametrize("eol", _EOLS)
def test_whitespace_only_lines_count_as_blank_for_the_cap(eol):
    """⚠️ WHY THE RIGHT-TRIM IS NOT COSMETIC.

    A converter's "blank" line usually carries spaces, or a decoded NBSP. Those
    lines look empty to a person and are not empty to a regex, so without the
    per-line trim the cap would silently do nothing on exactly the bodies it
    exists for.
    """
    body = _body(
        eol, "Dear chairs,", "   ", " ", "\t", "　", "  ", "The actual ask."
    )

    assert extract_reply_text(body) == "Dear chairs,\n\n\nThe actual ask."


@pytest.mark.parametrize("eol", _EOLS)
def test_normalisation_applies_above_a_quote_too(eol):
    """The debris sits in the reply half, so cutting the quote is not enough."""
    body = _body(
        eol,
        "Thanks.",
        "",
        "",
        "",
        "",
        "I will fix it.",
        "",
        "-----Original Message-----",
        "From: AAAI <a@openreview.net>",
    )

    assert extract_reply_text(body) == "Thanks.\n\n\nI will fix it."


# --- boundary detection must be COMPLETELY unaffected ------------------------
@pytest.mark.parametrize("eol", _EOLS)
def test_normalisation_does_not_touch_boundary_detection(eol):
    """⚠️ THE PLACEMENT GUARD.

    Normalising happens AFTER the slice. `find_quote_boundary` reads the RAW
    body and returns an offset into it, so normalising first would change the
    string's length and desynchronise the offset from the text being cut — the
    same drift the CRLF commit chose a regex anchor to avoid, and the entity
    commit chose ingestion-time decoding to avoid.

    Asserted as an exact offset into the raw body, so any reordering shows up
    here rather than as a quietly truncated reply.
    """
    body = _body(
        eol,
        "Dear chairs,",
        "",
        "",
        "",
        "",
        "",
        "Please see below.   ",
        "",
        "-----Original Message-----",
        "From: AAAI <a@openreview.net>",
        "Date: 2026/09/01",
        "To: reviewer <r@x.edu>",
    )

    assert find_quote_boundary(body) == body.index("-----Original Message-----")
    assert body[find_quote_boundary(body):].startswith("-----Original Message-----")
    # Both cues still fire, and in the same order, on the untouched body — the
    # full header block is present here so this is a real check on cue
    # computation rather than on the divider alone.
    assert [c.cue for c in find_quote_cues(body)] == ["divider", "header_block"]


@pytest.mark.parametrize("eol", _EOLS)
def test_a_body_that_is_entirely_quoted_still_yields_nothing(eol):
    """Normalisation must not resurrect text from an empty slice."""
    body = _body(eol, "-----Original Message-----", "From: AAAI <a@openreview.net>")

    assert find_quote_boundary(body) == 0
    assert extract_reply_text(body) == ""


# --- Root Cause G, ENDS ONLY --------------------------------------------------
def test_zero_width_characters_are_trimmed_from_the_ends():
    """⚠️ FOLDED IN DELIBERATELY, and only at the two ends.

    `extract_reply_text`'s docstring claimed `str.strip()` handled the invisible
    characters a CJK client leaves around the text. That claim was true for NBSP
    and the ideographic space and FALSE for zero-width characters, which Python
    does not classify as whitespace — the diagnostic measured it. Since this
    commit rewrites that exact trim, leaving a known-false claim beside it was
    the worse option.

    INTERIOR zero-width removal is a different operation — deletion, not
    trimming — and stays with the rest of Root Cause G. Pinned below.
    """
    for invisible in ("​", "‌", "‍", "﻿"):
        body = invisible + "Thanks, I will fix it." + invisible

        assert extract_reply_text(body) == "Thanks, I will fix it."


def test_interior_zero_width_spaces_are_now_removed():
    """INVERTED from `test_interior_zero_width_characters_are_left_alone`.

    That test pinned this as a KNOWN, UNFIXED gap: a zero-width space inside
    the text survived, because Fix 4 only trimmed the two ENDS. It is now
    deleted from anywhere in the reply.

    Renamed rather than left with its "this is broken" framing, and kept in
    place so the history of the gap stays legible. Note that only ZWSP and the
    BOM are removed — ZWNJ and ZWJ are TYPOGRAPHY and are pinned as preserved
    in the Root Cause G section below.
    """
    body = "Thanks​, I will fix it."

    assert extract_reply_text(body) == "Thanks, I will fix it."


def test_a_reply_of_only_invisible_characters_is_empty(eol="\n"):
    """The empty-reply signal must survive the wider trim."""
    assert extract_reply_text("​   ﻿\n\n　") == ""



# =============================================================================
# UNICODE-INDENTED HEADER LINES
#
# `_HEADER_LINE_RE` used to allow only `[ \t]` before the label. NBSP and the
# ideographic space are `\s` to Python but are in neither that class nor the
# label's own `[^\s:：]` opening, so a header line indented with either matched
# NOTHING — it could not even begin contributing to a run.
#
# ⚠️ DIFFERENT FROM THE BLANK-LINE BRIDGE, though both involve NBSP. That one
# was about a blank line INTERRUPTING a run of otherwise-matching headers. This
# is about a header line being unrecognised because ITS OWN indentation is not
# ASCII — a different failure at a different point in the same regex.
# =============================================================================

from app.pipeline.quoted_reply import _HEADER_LINE_RE

_ZERO_WIDTH = "​"


@pytest.mark.parametrize(
    "indent",
    [
        pytest.param("", id="none"),
        pytest.param(" ", id="ascii-space"),
        pytest.param("\t", id="tab"),
        pytest.param(_NBSP, id="nbsp"),
        pytest.param(_NBSP * 4, id="nbsp-run"),
        pytest.param(_IDEOGRAPHIC, id="ideographic-space"),
        pytest.param(_NBSP + " " + _IDEOGRAPHIC, id="mixed"),
    ],
)
def test_a_header_line_is_recognised_whatever_indents_it(indent):
    """The regex-level fact, asserted directly.

    Everything else in this section is a consequence of this one line matching.
    """
    assert _HEADER_LINE_RE.match(f"{indent}From: AAAI <a@openreview.net>")


def test_zero_width_indentation_is_unaffected_by_this_change():
    """⚠️ PINS A NON-CHANGE.

    Zero-width characters already matched before this commit — they are NOT
    `\\s` to Python, so the label's `[^\\s:：]` opening accepts one directly and
    the leading class never had to. This commit neither helped nor broke that,
    and asserting it keeps a future widening from being credited here.
    """
    assert _HEADER_LINE_RE.match(f"{_ZERO_WIDTH}From: AAAI <a@openreview.net>")


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "indent",
    [pytest.param(_NBSP * 2, id="nbsp"), pytest.param(_IDEOGRAPHIC, id="ideographic")],
)
def test_an_indented_header_block_is_now_detected(eol, indent):
    """The reported scenario, end to end.

    An HTML client that indents quoted headers with `&nbsp;` — a real NBSP now
    that entity decoding happens at ingestion — produced a block that read as
    plainly quoted to a human and yielded NO CUES at all, so the whole
    notification came back as the person's own reply.
    """
    body = _body(
        eol,
        "Thanks, I will fix it.",
        "",
        f"{indent}From: AAAI 2027 <aaai2027-notifications@openreview.net>",
        f"{indent}Date: 2026/09/01 10:00",
        f"{indent}To: reviewer <r@x.edu>",
        "",
        "Please see the original comment text here.",
    )

    assert [c.cue for c in find_quote_cues(body)] == ["header_block"]
    kept = extract_reply_text(body)
    assert kept == "Thanks, I will fix it."
    assert "openreview.net" not in kept


@pytest.mark.parametrize("eol", _EOLS)
def test_an_indented_block_broken_by_blank_lines_is_still_detected(eol):
    """Composes with the blank-line bridge: both defects in one body.

    A converter that indents with `&nbsp;` tends to leave blank lines between
    the fields too, so a realistic body needs both fixes to be found at all.
    """
    body = _body(
        eol,
        "Thanks.",
        "",
        f"{_NBSP * 2}From: AAAI <a@openreview.net>",
        "",
        f"{_NBSP * 2}Date: 2026/09/01",
        "",
        f"{_NBSP * 2}To: reviewer <r@x.edu>",
        "",
        "original comment text",
    )

    assert [c.cue for c in find_quote_cues(body)] == ["header_block"]
    assert extract_reply_text(body) == "Thanks."


@pytest.mark.parametrize("eol", _EOLS)
def test_the_boundary_includes_the_indentation_of_the_first_header(eol):
    """The cut lands before the indent, not after it.

    Otherwise the NBSP run would be left dangling on the end of the reply — the
    quote stripped, but its indentation posted.
    """
    body = _body(
        eol,
        "Thanks.",
        "",
        f"{_NBSP * 2}From: AAAI <a@openreview.net>",
        f"{_NBSP * 2}Date: 2026/09/01",
        f"{_NBSP * 2}To: reviewer <r@x.edu>",
    )

    boundary = find_quote_boundary(body)

    assert body[boundary:].startswith(f"{_NBSP * 2}From:")
    assert _NBSP not in extract_reply_text(body)


# --- the address guard still does the work -----------------------------------
@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "indent",
    [pytest.param(_NBSP * 2, id="nbsp"), pytest.param(_IDEOGRAPHIC, id="ideographic")],
)
def test_an_indented_user_field_list_is_still_rejected(eol, indent):
    """⚠️ THE NAMED REGRESSION RISK for this commit.

    Widening what counts as header-shaped widens what could truncate somebody's
    message. `Paper number:` / `Title:` / `Status:` is the list commit 5's
    address requirement exists to reject; indenting it with NBSP instead of
    spaces must not smuggle it past that guard. This change alters which lines
    are SHAPED like headers and nothing about what counts as evidence.
    """
    body = _body(
        eol,
        "Hello chairs,",
        "",
        f"{indent}Paper number: 1030",
        f"{indent}Title: A Study of Things",
        f"{indent}Status: under review",
        "",
        "Could you advise?",
    )

    assert find_quote_cues(body) == []
    assert find_quote_boundary(body) is None


@pytest.mark.parametrize("eol", _EOLS)
def test_an_indented_content_line_still_breaks_the_run(eol):
    """Indentation does not make a sentence header-shaped.

    Two indented headers, an indented real sentence, one more header: three
    header-shaped lines in total, but never three in one run.
    """
    body = _body(
        eol,
        "Reply.",
        "",
        f"{_NBSP}From: A <a@b.net>",
        f"{_NBSP}Date: 2026/09/01",
        f"{_NBSP}I am writing a real sentence here.",
        f"{_NBSP}To: B <c@d.net>",
        "original text",
    )

    assert find_quote_cues(body) == []


# =============================================================================
# ROOT CAUSE C — the phrase/format gaps
#
# Three gaps were reported together and got THREE DIFFERENT treatments, because
# measuring them showed they are not the same problem:
#
#   Apple Mail "Begin forwarded message:"  -> NO CODE CHANGE. Already works.
#   "--- Original Message ---" (3 dashes)  -> absorbed into the header block,
#                                             NOT by widening the divider.
#   French / German / Chinese attribution  -> new siblings in the attribution
#                                             tier, completing a documented gap.
#
# Each section below records which, and why.
# =============================================================================


# --- Apple Mail: no change was needed ----------------------------------------
@pytest.mark.parametrize("eol", _EOLS)
def test_apple_mail_forward_is_already_covered_by_the_header_block(eol):
    """⚠️ PINS A NON-CHANGE, and the reason a cue was NOT added.

    "Begin forwarded message:" was reported as uncovered. It is not: the line is
    `Label:` with an EMPTY value — the same shape as the real Chinese sample's
    bare `抄送:` — so it already matches `_HEADER_LINE_RE` and becomes the FIRST
    line of the run it introduces. The boundary lands on it exactly.

    Adding a divider-tier cue for the phrase would have been redundant
    machinery, and a phrase list nobody needed. Asserted on the OFFSET, because
    "the quote was stripped" would pass even if the marker were left behind.
    """
    body = _body(
        eol,
        "Thanks — forwarding for your view.",
        "",
        "Begin forwarded message:",
        "",
        "From: AAAI 2027 <aaai2027-notifications@openreview.net>",
        "Subject: SPC commented on a paper",
        "To: reviewer <r@x.edu>",
        "",
        "original text",
    )

    assert [c.cue for c in find_quote_cues(body)] == ["header_block"]
    assert find_quote_boundary(body) == body.index("Begin forwarded message:")
    assert extract_reply_text(body) == "Thanks — forwarding for your view."


def test_begin_forwarded_message_is_header_shaped():
    """The single fact the test above rests on."""
    assert _HEADER_LINE_RE.match("Begin forwarded message:")


# --- short dashed rules: absorbed, never promoted to a divider ----------------
@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "rule",
    [
        pytest.param("--- Original Message ---", id="three-dash-en"),
        pytest.param("--- 原始邮件 ---", id="three-dash-zh"),
        pytest.param("=== Original Message ===", id="three-equals"),
        pytest.param("___ Original Message ___", id="three-underscore"),
    ],
)
def test_a_short_rule_above_a_header_block_is_absorbed(eol, rule):
    """The marker stops dangling on the end of the reply.

    Before this, the header block still cut the quote away correctly — but the
    boundary landed BELOW the rule, so `--- Original Message ---` was left as
    the last line of the text bound for a public venue.
    """
    body = _body(
        eol,
        "Thanks, I will fix it.",
        "",
        rule,
        "From: AAAI <a@openreview.net>",
        "Date: 2026/09/01",
        "To: reviewer <r@x.edu>",
        "",
        "original text",
    )

    assert find_quote_boundary(body) == body.index(rule)
    assert extract_reply_text(body) == "Thanks, I will fix it."


@pytest.mark.parametrize("eol", _EOLS)
def test_a_short_rule_with_no_header_block_under_it_means_nothing(eol):
    """⚠️ THE WHOLE REASON THE DIVIDER THRESHOLD WAS NOT LOWERED.

    `--- Original Message ---` and `--- update ---` are structurally IDENTICAL:
    same characters, same counts, free text between. Lowering `_DIVIDER_RE` to
    three per side makes BOTH match — measured, not assumed — which would cut a
    person's message at their own section heading.

    So a short rule is absorbed only where a header block vouches for it. With
    nothing underneath, it is still not a cue, and this body is still returned
    whole. That is a real remaining gap, named in LIMITATIONS, not a fix.
    """
    body = _body(
        eol, "Thanks.", "", "--- Original Message ---", "", "quoted prose, no headers"
    )

    assert find_quote_cues(body) == []


@pytest.mark.parametrize("eol", _EOLS)
def test_a_users_own_short_rule_heading_is_untouched(eol):
    """Commit 5's `--- update ---` guard, still holding after the change."""
    body = _body(
        eol, "Dear Chairs,", "", "--- update ---", "", "I resubmitted the paper."
    )

    assert find_quote_boundary(body) is None
    assert "I resubmitted the paper." in extract_reply_text(body)


@pytest.mark.parametrize("eol", _EOLS)
def test_absorbing_a_rule_does_not_reach_past_one_line(eol):
    """Only the line DIRECTLY above is absorbed.

    A rule with the person's own text between it and the header block is their
    text, and the boundary must not swallow it.
    """
    body = _body(
        eol,
        "Thanks.",
        "",
        "--- my own heading ---",
        "Some sentence I wrote myself.",
        "From: AAAI <a@openreview.net>",
        "Date: 2026/09/01",
        "To: reviewer <r@x.edu>",
    )

    assert find_quote_boundary(body) == body.index("From: AAAI")
    assert "Some sentence I wrote myself." in extract_reply_text(body)


# --- attribution in four languages -------------------------------------------
_ATTRIBUTIONS = [
    pytest.param(
        "On Wed, 27 Aug 2026 at 15:28, AAAI <a@b.net> wrote:", id="english"
    ),
    pytest.param(
        "Le mar. 1 sept. 2026 à 10:00, AAAI <a@b.net> a écrit :", id="french"
    ),
    pytest.param("Am 01.09.2026 um 10:00 schrieb AAAI <a@b.net>:", id="german"),
    pytest.param("在 2026年9月1日, AAAI <a@b.net> 写道:", id="chinese"),
    pytest.param("在 2026年9月1日, AAAI <a@b.net> 写道：", id="chinese-fullwidth"),
]


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize("line", _ATTRIBUTIONS)
def test_attribution_is_detected_in_every_supported_language(eol, line):
    """The tier was always language-specific; it was just short three languages.

    These carry no divider and no header block by construction — that is why
    attribution exists as a tier at all — so nothing structural can cover them.
    """
    body = _body(eol, "Thanks, I will fix it.", "", line, "the quoted original")

    assert [c.cue for c in find_quote_cues(body)] == ["attribution"]
    assert find_quote_boundary(body) == body.index(line[:2])
    assert extract_reply_text(body) == "Thanks, I will fix it."


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "prose",
    [
        pytest.param(
            "On the other hand I wrote: some notes about the appendix.",
            id="english-prose",
        ),
        pytest.param(
            "Le rapport que il a écrit : il est trop long selon moi.",
            id="french-prose",
        ),
        pytest.param("Am Ende schrieb ich meine Notizen auf.", id="german-prose"),
        pytest.param("在报告中我写道: 这个问题很重要。", id="chinese-prose"),
    ],
)
def test_attribution_does_not_fire_on_ordinary_prose(eol, prose):
    """⚠️ ONE REJECTION PER LANGUAGE, mirroring commit 5's English guard.

    Each pattern pins an opening word AND a closing verb phrase on the same
    line. The `$` anchor is what does the work: ordinary prose keeps going after
    the colon, so the line never ends where an attribution would.
    """
    body = _body(eol, "Hello,", "", prose, "", "Thanks.")

    assert find_quote_cues(body) == []
    assert find_quote_boundary(body) is None


@pytest.mark.parametrize("eol", _EOLS)
def test_the_earliest_attribution_wins_across_languages(eol):
    """A thread can carry more than one language; the first is the boundary."""
    body = _body(
        eol,
        "Merci.",
        "",
        "Le mar. 1 sept. 2026, AAAI <a@b.net> a écrit :",
        "quoted french",
        "On Wed, 27 Aug 2026, AAAI <a@b.net> wrote:",
        "quoted english",
    )

    assert find_quote_boundary(body) == body.index("Le mar.")
    assert extract_reply_text(body) == "Merci."


def test_the_supported_language_set_is_pinned():
    """INVERTED from `test_no_language_was_added_beyond_the_four`.

    That test pinned Spanish as deliberately ABSENT — a scope boundary for the
    commit that added French, German and Chinese, with a note that a fifth would
    be a decision rather than a tidy-up. Sahil made that decision: Spanish and
    Japanese are in, completing the set the LIMITATIONS docstring named.

    Kept and renamed rather than deleted, so the history of the boundary stays
    legible — and it still does the same job, one language further out: Italian
    is the obvious next candidate and is NOT supported. Adding a seventh remains
    a decision.
    """
    spanish = "Gracias.\n\nEl 1 sept 2026, AAAI <a@b.net> escribió:\ntexto original"
    japanese = "ありがとうございます。\n\nAAAI さんは書きました:\n元のメッセージ"
    italian = "Grazie.\n\nIl 1 set 2026, AAAI <a@b.net> ha scritto:\ntesto originale"

    assert [c.cue for c in find_quote_cues(spanish)] == ["attribution"]
    assert [c.cue for c in find_quote_cues(japanese)] == ["attribution"]
    # Still bounded: six languages, not "whatever looks like one".
    assert find_quote_cues(italian) == []


# =============================================================================
# ROOT CAUSE G — interior zero-width characters
#
# Four characters, all invisible, all Unicode category Cf, none classified as
# whitespace by Python. They are NOT interchangeable, and that is the whole
# content of this section:
#
#   REMOVED   U+200B ZWSP   a line-break hint; deleting it changes no word
#             U+FEFF BOM    meaningful only at the start of a STREAM
#
#   KEPT      U+200C ZWNJ   respells Persian/Arabic/Indic words if deleted
#             U+200D ZWJ    forms ligatures and joins emoji sequences
#
# Treating all four alike would have been one line shorter and would corrupt
# somebody's writing on its way to a public venue.
# =============================================================================

_ZWSP = "​"
_ZWNJ = "‌"
_ZWJ = "‍"
_BOM = "﻿"


@pytest.mark.parametrize(
    "char", [pytest.param(_ZWSP, id="zwsp"), pytest.param(_BOM, id="bom")]
)
@pytest.mark.parametrize(
    "template, expected",
    [
        pytest.param("Than{c}ks, I will fix it.", "Thanks, I will fix it.", id="mid-word"),
        pytest.param("Thanks,{c} I will fix it.", "Thanks, I will fix it.", id="mid-sentence"),
        pytest.param("Thanks.{c}{c}{c}", "Thanks.", id="run-at-end"),
        pytest.param("{c}Thanks.", "Thanks.", id="at-start"),
        pytest.param("A{c}B{c}C{c}D", "ABCD", id="scattered"),
    ],
)
def test_zwsp_and_bom_are_removed_from_anywhere(char, template, expected):
    """Fix 4 trimmed only the two ENDS; these live in the middle."""
    assert extract_reply_text(template.format(c=char)) == expected


@pytest.mark.parametrize(
    "char", [pytest.param(_ZWNJ, id="zwnj"), pytest.param(_ZWJ, id="zwj")]
)
def test_zwnj_and_zwj_are_preserved_in_running_text(char):
    """⚠️ THE DECISION THIS SECTION EXISTS FOR.

    Both are invisible, like ZWSP — and both are typography rather than debris,
    so they are kept. See the two tests below for what deleting them would
    actually do.
    """
    body = f"Thanks{char}, I will fix it."

    assert extract_reply_text(body) == body


def test_deleting_zwnj_would_respell_a_persian_word():
    """⚠️ THE MEASURED HARM, not an argument from principle.

    Persian "می‌رود" (mi-ravad) carries a ZWNJ between its two parts. Without it
    the string is "میرود" — five characters instead of six, and a different,
    misspelled word. This is somebody's text on its way to a public forum.
    """
    correct = f"می{_ZWNJ}رود"
    body = f"متن: {correct}"

    assert extract_reply_text(body) == body
    assert _ZWNJ in extract_reply_text(body)
    # And the harm the preservation avoids, stated outright.
    assert correct.replace(_ZWNJ, "") != correct


def test_deleting_zwj_would_split_an_emoji_sequence():
    """A ZWJ binds an emoji sequence into one glyph; removing it yields two."""
    family = f"👩{_ZWJ}👧"
    body = f"Thanks! {family}"

    assert extract_reply_text(body) == body
    assert len(family.replace(_ZWJ, "")) == len(family) - 1


def test_the_two_kinds_are_distinguished_within_one_body():
    """The discriminator, in a single string.

    A body carrying all four: the two debris characters go, the two typographic
    ones stay. A uniform rule — in either direction — fails this.
    """
    body = f"Th{_ZWSP}anks{_BOM} for می{_ZWNJ}رود and 👩{_ZWJ}👧."

    result = extract_reply_text(body)

    assert _ZWSP not in result
    assert _BOM not in result
    assert _ZWNJ in result
    assert _ZWJ in result
    assert result == f"Thanks for می{_ZWNJ}رود and 👩{_ZWJ}👧."


# --- interaction with the blank-line cap -------------------------------------
@pytest.mark.parametrize("eol", _EOLS)
def test_invisible_only_lines_count_toward_the_blank_line_cap(eol):
    """⚠️ WHY REMOVAL IS STEP ZERO AND NOT STEP THREE.

    A line holding nothing but a ZWSP is blank to every reader and NOT blank to
    any of the normalisation steps — it survives `rstrip()`, and `_is_blank`
    rejects it, because Python does not call these whitespace.

    Measured on four such lines between two paragraphs:
        remove first, then cap -> "A\\n\\n\\nB"        (capped, correct)
        cap, then remove last  -> "A\\n\\n\\n\\n\\nB"  (never capped)

    So ordering is load-bearing, not stylistic.
    """
    body = _body(eol, "A", _ZWSP, _ZWSP, _ZWSP, _ZWSP, "B")

    assert extract_reply_text(body) == "A\n\n\nB"


@pytest.mark.parametrize("eol", _EOLS)
def test_a_single_invisible_only_line_reads_as_one_blank_line(eol):
    """The other side of the same rule: it must not over-collapse either."""
    body = _body(eol, "Para one.", _ZWSP, "Para two.")

    assert extract_reply_text(body) == "Para one.\n\nPara two."


def test_a_reply_of_only_zwsp_is_empty():
    """The empty-reply signal survives: invisible content is no content."""
    assert extract_reply_text(f"{_ZWSP}{_BOM}  {_ZWSP}") == ""


def test_zwnj_is_kept_where_it_can_join_and_trimmed_where_it_cannot():
    """⚠️ THE PRESERVATION IS POSITIONAL, which is better than blanket.

    I expected a reply of one bare ZWNJ to survive, and it does not — Fix 4's
    edge trim already removes zero-width characters at the two ENDS, including
    these two. Checking rather than assuming turned up a more coherent rule than
    the one intended:

        ZWNJ/ZWJ are kept exactly where they can DO something — between two
        characters — and trimmed where they cannot, at a boundary with nothing
        on one side to join.

    So the typographic function is protected and the stray-at-the-edge case is
    still cleaned up. Nothing was changed to achieve this; it falls out of the
    two commits composing, and is pinned here so neither half is "tidied" later
    without noticing the other.
    """
    # Nothing to join: trimmed.
    assert extract_reply_text(_ZWNJ) == ""
    assert extract_reply_text(f"{_ZWNJ}Thanks.{_ZWNJ}") == "Thanks."

    # Between two characters: kept, because here it carries meaning.
    assert extract_reply_text(f"mi{_ZWNJ}ravad") == f"mi{_ZWNJ}ravad"
    assert extract_reply_text(f"می{_ZWNJ}رود") == (
        f"می{_ZWNJ}رود"
    )


# --- boundary detection is untouched -----------------------------------------
@pytest.mark.parametrize("eol", _EOLS)
def test_zero_width_removal_does_not_touch_boundary_detection(eol):
    """Same placement guard as every prior commit in this series.

    Removal happens inside `_normalize_reply`, which runs AFTER the slice. The
    boundary is computed on the raw body, so an offset can never desynchronise
    from the text being cut.
    """
    body = _body(
        eol,
        f"Than{_ZWSP}ks, I will fix it.",
        "",
        "-----Original Message-----",
        "From: AAAI <a@openreview.net>",
        "Date: 2026/09/01",
        "To: reviewer <r@x.edu>",
    )

    assert find_quote_boundary(body) == body.index("-----Original Message-----")
    assert [c.cue for c in find_quote_cues(body)] == ["divider", "header_block"]
    assert extract_reply_text(body) == "Thanks, I will fix it."


@pytest.mark.parametrize("eol", _EOLS)
def test_a_zwsp_indented_header_line_still_matches(eol):
    """Pins a NON-change from Fix 5.

    ZWSP is not `\\s`, so `_HEADER_LINE_RE`'s label opening always accepted one
    directly — that was true before this commit and is unaffected by it, because
    removal happens on the OUTPUT and never on the body being scanned.
    """
    body = _body(
        eol,
        "Thanks.",
        "",
        f"{_ZWSP}From: AAAI <a@openreview.net>",
        f"{_ZWSP}Date: 2026/09/01",
        f"{_ZWSP}To: reviewer <r@x.edu>",
    )

    assert [c.cue for c in find_quote_cues(body)] == ["header_block"]
    assert extract_reply_text(body) == "Thanks."


# =============================================================================
# ATTRIBUTION — Spanish and Japanese
#
# Completes the tier. Each phrasing below was checked against the real output of
# a major client rather than invented:
#
#   Spanish   Gmail emits `El <fecha> <hora>, "<nombre>" <correo> escribió:`
#             Outlook differs only in punctuation. Two anchors, like the others.
#
#   Japanese  Thunderbird's default is `<名前> さんは書きました:`; the other
#             attested shape is `… は次のように書きました:`. The invariant across
#             both is 書きました immediately before the colon.
#
# ⚠️ JAPANESE IS THE ONE PATTERN WITH NO OPENING ANCHOR — the language supplies
# none, because the line opens with the date itself. See the two tests at the
# end of this section for what that cost and how it is contained.
# =============================================================================


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "line",
    [
        pytest.param(
            'El 1 sept 2026, a las 10:00, AAAI <a@b.net> escribió:', id="es-outlook"
        ),
        pytest.param(
            'El 2/7/2026 3:11 p. m., "AAAI" <a@b.net> escribió:', id="es-gmail"
        ),
    ],
)
def test_spanish_attribution_is_detected(eol, line):
    """Both attested Spanish shapes — Gmail's and Outlook's."""
    body = _body(eol, "Gracias, lo corregiré.", "", line, "texto original")

    assert [c.cue for c in find_quote_cues(body)] == ["attribution"]
    assert find_quote_boundary(body) == body.index("El ")
    assert extract_reply_text(body) == "Gracias, lo corregiré."


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "line",
    [
        pytest.param("AAAI さんは書きました:", id="ja-thunderbird-default"),
        pytest.param(
            "2026年9月1日 10:00 AAAI <a@b.net> さんは書きました:", id="ja-with-date"
        ),
        pytest.param("AAAI は次のように書きました:", id="ja-next-you"),
        pytest.param("AAAI さんは書きました：", id="ja-fullwidth-colon"),
    ],
)
def test_japanese_attribution_is_detected(eol, line):
    """Every attested Japanese shape, including the full-width colon.

    The date-less form is Thunderbird's DEFAULT, which is why the pattern cannot
    anchor on a leading year.
    """
    body = _body(eol, "ありがとうございます。", "", line, "元のメッセージ")

    assert [c.cue for c in find_quote_cues(body)] == ["attribution"]
    assert extract_reply_text(body) == "ありがとうございます。"


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "prose",
    [
        pytest.param(
            "El informe que escribió: es demasiado largo en mi opinión.",
            id="spanish-colon-mid-line",
        ),
        pytest.param(
            "El autor escribió sus comentarios en el apéndice.",
            id="spanish-no-colon",
        ),
        pytest.param(
            "報告書に書きました。詳細は添付をご覧ください。", id="japanese-full-stop"
        ),
        pytest.param(
            "その件はすでにメールに書きましたが、再送します。", id="japanese-mid-sentence"
        ),
        # ⚠️ THE CASE THAT ISOLATES THE `$` ANCHOR. A mutation removing it
        # survived the two Japanese cases above, because both rely on there
        # being no colon at all. Here a colon FOLLOWS 書きました mid-line, in
        # ordinary prose introducing what was written — so only the end-of-line
        # requirement separates it from an attribution.
        pytest.param(
            "メールに書きました: 詳細は添付のとおりです。",
            id="japanese-colon-mid-line",
        ),
        # The Spanish twin of the same gap, for symmetry.
        pytest.param(
            "El equipo escribió: los detalles están en el apéndice.",
            id="spanish-colon-introducing-clause",
        ),
    ],
)
def test_the_new_languages_do_not_fire_on_ordinary_prose(eol, prose):
    """One rejection per language, the same methodology as the other four.

    The `$` anchor does the work: prose keeps going past the trigger, so the
    line never ends where an attribution would. Japanese prose also ends its
    sentences with 。rather than a colon, which is a second separation.
    """
    body = _body(eol, "Hola,", "", prose, "", "Gracias.")

    assert find_quote_cues(body) == []
    assert find_quote_boundary(body) is None


# --- the Japanese pattern's missing opening anchor ---------------------------
@pytest.mark.parametrize("eol", _EOLS)
def test_the_japanese_pattern_does_not_swallow_the_reply_above_it(eol):
    """⚠️ REGRESSION TEST FOR A BUG THIS COMMIT ACTUALLY HAD.

    Every other attribution pattern carries `re.DOTALL`, so a Gmail
    attribution that WRAPS across two lines still matches; their opening anchor
    (`On`/`Le`/`Am`/`El`/`在`) keeps that span honest.

    The Japanese pattern has no opening anchor, and with DOTALL its leading
    `^.{0,200}?` began at the FIRST line of the body and ran across newlines to
    reach 書きました further down — so the boundary landed at offset 0 and
    `extract_reply_text` returned "". The person's entire reply disappeared.

    Fixed by compiling that one pattern WITHOUT DOTALL, which also matches
    reality: no wrapped Japanese attribution is attested. This test pins the
    boundary landing on the attribution line itself, several lines down.
    """
    body = _body(
        eol,
        "ありがとうございます。",
        "",
        "確認しました。",
        "",
        "AAAI さんは書きました:",
        "元のメッセージ",
    )

    assert find_quote_boundary(body) == body.index("AAAI さんは書きました")
    assert extract_reply_text(body) == "ありがとうございます。\n\n確認しました。"


@pytest.mark.parametrize("eol", _EOLS)
def test_the_english_wrapped_attribution_still_works(eol):
    """The other half of that fix: DOTALL was removed from ONE pattern only.

    Gmail wraps a long English attribution so `wrote:` lands on its own line,
    and commit 5 added DOTALL specifically for it. Pinned here so a future
    "consistency" pass that strips DOTALL from all six breaks loudly.
    """
    body = _body(
        eol,
        "Thanks.",
        "",
        "On Wed, 27 Aug 2026 at 15:28 AAAI 2027 <a@b.net>",
        "wrote:",
        "quoted original",
    )

    assert [c.cue for c in find_quote_cues(body)] == ["attribution"]
    assert extract_reply_text(body) == "Thanks."


@pytest.mark.parametrize("eol", _EOLS)
def test_the_earliest_attribution_still_wins_across_six_languages(eol):
    """Adding languages must not disturb the earliest-cue rule."""
    body = _body(
        eol,
        "Gracias.",
        "",
        "El 1 sept 2026, AAAI <a@b.net> escribió:",
        "texto citado",
        "AAAI さんは書きました:",
        "引用",
    )

    assert find_quote_boundary(body) == body.index("El ")
    assert extract_reply_text(body) == "Gracias."


# =============================================================================
# SHORT RULES ABOVE ANY CUE
#
# Fix 6 absorbed a short rule sitting above a HEADER BLOCK. Measuring the
# leftover case showed the dangling marker was never specific to header blocks —
# it appeared above every other cue too, and only the header-block variant was
# covered:
#
#   rule + quoted_lines  ->  kept "Thanks.\n\n--- Original Message ---"
#   rule + attribution   ->  kept "Thanks.\n\n--- Original Message ---"
#   rule + 4-dash rule   ->  kept "Thanks.\n\n--- Original Message ---"
#   rule + header block  ->  kept "Thanks."               (already handled)
#
# So the absorption moved to `find_quote_boundary`, where it applies to whatever
# cue won. The corroboration rule and the one-line bound are unchanged — those
# are what keep commit 5's `--- update ---` guard intact, and the guard has its
# own section at the end.
# =============================================================================

_SHORT_RULE = "--- Original Message ---"


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "quote_lines, expected_cue",
    [
        pytest.param(["> quoted one", "> quoted two"], "quoted_lines", id="quoted-lines"),
        pytest.param(
            ["On Mon, 1 Sep 2026, X <a@b.net> wrote:", "quoted"],
            "attribution",
            id="attribution",
        ),
        pytest.param(
            ["-----Original Message-----", "From: A <a@openreview.net>"],
            "divider",
            id="full-length-divider",
        ),
        pytest.param(
            ["From: A <a@openreview.net>", "Date: 2026/09/01", "To: B <c@d.net>"],
            "header_block",
            id="header-block-fix-6",
        ),
    ],
)
def test_a_short_rule_above_any_cue_is_absorbed(eol, quote_lines, expected_cue):
    """The marker is part of the quote's introduction, whatever follows it."""
    body = _body(eol, "Thanks, I will fix it.", "", _SHORT_RULE, *quote_lines)

    assert [c.cue for c in find_quote_cues(body)][0] == expected_cue
    assert find_quote_boundary(body) == body.index(_SHORT_RULE)
    assert extract_reply_text(body) == "Thanks, I will fix it."


@pytest.mark.parametrize("eol", _EOLS)
@pytest.mark.parametrize(
    "rule",
    [
        pytest.param("--- Original Message ---", id="dashes"),
        pytest.param("=== 原始邮件 ===", id="equals-cjk"),
        pytest.param("___ Forwarded ___", id="underscores"),
    ],
)
def test_every_short_rule_character_is_absorbed(eol, rule):
    """The rule characters `_DIVIDER_RE` accepts, at the length it refuses."""
    body = _body(eol, "Thanks.", "", rule, "> quoted one", "> quoted two")

    assert find_quote_boundary(body) == body.index(rule)
    assert extract_reply_text(body) == "Thanks."


@pytest.mark.parametrize("eol", _EOLS)
def test_absorption_is_bounded_to_one_line(eol):
    """Only the line DIRECTLY above the boundary.

    A rule with the person's own text between it and the quote is their text,
    and must survive — this is the same bound Fix 6 established, now carrying
    the guard for every cue rather than one.
    """
    body = _body(
        eol,
        "Thanks.",
        "",
        "--- my own heading ---",
        "Some sentence I wrote myself.",
        "> quoted one",
        "> quoted two",
    )

    assert find_quote_boundary(body) == body.index("> quoted one")
    assert extract_reply_text(body) == (
        "Thanks.\n\n--- my own heading ---\nSome sentence I wrote myself."
    )


@pytest.mark.parametrize("eol", _EOLS)
def test_absorption_does_not_run_away_up_the_body(eol):
    """Two stacked rules take one step, not two.

    Fix 6's header-block path may already have backtracked once; this must not
    compound into a second. Asserted with two rules in a row, where a runaway
    loop would eat both.
    """
    body = _body(
        eol,
        "Thanks.",
        "--- first rule ---",
        "--- second rule ---",
        "> quoted one",
        "> quoted two",
    )

    assert find_quote_boundary(body) == body.index("--- second rule ---")
    assert extract_reply_text(body) == "Thanks.\n--- first rule ---"


@pytest.mark.parametrize("eol", _EOLS)
def test_a_rule_at_the_very_start_of_the_body_is_handled(eol):
    """No line above it to inspect — the offset-0 guard."""
    body = _body(eol, _SHORT_RULE, "> quoted one", "> quoted two")

    assert find_quote_boundary(body) == 0
    assert extract_reply_text(body) == ""


# --- commit 5's guard, still the discriminator -------------------------------
@pytest.mark.parametrize("eol", _EOLS)
def test_a_users_own_rule_followed_by_their_own_prose_is_untouched(eol):
    """⚠️ THE CRITICAL REGRESSION GUARD — commit 5's exact case.

    `--- update ---` with the person's own text after it and no cue anywhere.
    Nothing corroborates the rule, so it is not a boundary and their whole
    message survives. This is the case that makes lowering `_DIVIDER_RE` to
    three per side unsafe, and it is unaffected by this commit.
    """
    body = _body(
        eol, "Dear Chairs,", "", "--- update ---", "", "I resubmitted the paper."
    )

    assert find_quote_cues(body) == []
    assert find_quote_boundary(body) is None
    assert "I resubmitted the paper." in extract_reply_text(body)


@pytest.mark.parametrize("eol", _EOLS)
def test_a_users_own_rule_survives_even_when_the_body_does_contain_a_quote(eol):
    """⚠️ THE SHARPER VERSION, and why the one-line bound is load-bearing.

    Here a real quote DOES follow further down, so a boundary exists. Their
    `--- update ---` still survives, because it is two lines above the boundary
    rather than one. The protection is structural, not luck: a person writing a
    section heading follows it with their own text, which is exactly what puts
    distance between their rule and any quote.
    """
    body = _body(
        eol,
        "Dear Chairs,",
        "",
        "--- update ---",
        "",
        "I resubmitted the paper.",
        "",
        "> your earlier note",
        "> second line",
    )

    assert find_quote_boundary(body) == body.index("> your earlier note")
    assert extract_reply_text(body) == (
        "Dear Chairs,\n\n--- update ---\n\nI resubmitted the paper."
    )


@pytest.mark.parametrize("eol", _EOLS)
def test_a_users_own_rule_directly_above_a_quote_is_absorbed(eol):
    """⚠️ PINS THE ACCEPTED COST, not a feature.

    The one shape where a person's own decorative rule IS taken: when it sits
    directly against quoted material. Indistinguishable from a client marker in
    that position — which is the whole reason short rules need corroboration —
    and Fix 6 already accepted exactly this trade for the header-block case.
    The cost is one decorative line; the alternative is leaving a client marker
    on every relayed reply.
    """
    body = _body(eol, "Dear Chairs,", "", "--- update ---", "> your note", "> second")

    assert find_quote_boundary(body) == body.index("--- update ---")
    assert extract_reply_text(body) == "Dear Chairs,"


# --- the gap that stays open -------------------------------------------------
@pytest.mark.parametrize("eol", _EOLS)
def test_a_short_rule_with_no_cue_beneath_is_still_not_detected(eol):
    """⚠️ PINS A KNOWN, DELIBERATELY UNFIXED DETECTION GAP.

    A quote introduced by a short rule and carrying no cue of its own — no
    header block, no `>` lines, no attribution, no full-length rule — is not
    detected, and the whole body comes back as the reply.

    This is NOT the dangling-marker problem this commit fixed; it is the
    detection problem underneath it. There is nothing left to corroborate the
    rule with, and promoting it unaided is precisely the change commit 5's
    `--- update ---` guard exists to prevent. Closing it needs evidence this
    module does not have, not a looser pattern.
    """
    body = _body(
        eol, "Thanks.", "", _SHORT_RULE, "", "quoted prose with no cue of its own"
    )

    assert find_quote_cues(body) == []
    assert "quoted prose with no cue of its own" in extract_reply_text(body)
