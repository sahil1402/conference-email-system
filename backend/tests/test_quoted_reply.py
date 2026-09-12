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
    """THE conservative-trim guard.

    Collapsing these would silently reformat authored content. One extra blank
    line is cosmetic; a dropped one changes what was written.
    """
    body = "First paragraph.\n\n\n\nSecond paragraph.\n\n" + _ENGLISH_QUOTE

    assert extract_reply_text(body) == "First paragraph.\n\n\n\nSecond paragraph."


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

    This commit fixes the divider half \u2014 and because the divider is the EARLIEST
    cue, that alone fully strips this body. The header-block defect is bypassed
    rather than repaired; the test below pins the shape where it still bites.
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

    assert [c.cue for c in find_quote_cues(body)] == ["divider"]
    kept = extract_reply_text(body)
    assert kept == "Thanks, I will fix it."
    for leaked in ("openreview.net", "From:", "Subject:", "original comment"):
        assert leaked not in kept, leaked


def test_root_cause_b_is_still_open_without_a_divider():
    """NOT A PASSING FEATURE \u2014 a pin on a KNOWN, UNFIXED defect.

    The same body with the divider removed: blank lines between the header
    fields break `_find_header_block`'s consecutive-run requirement, no cue
    fires, and the entire quoted notification comes back as the person's reply.

    This commit deliberately does not address that (Root Cause B is the next
    commit). The test exists so the gap is visible in the suite rather than only
    in a report, and INVERTING it is part of fixing B.
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

    assert find_quote_cues(body) == []
    assert "openreview.net" in extract_reply_text(body)


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

