"""Where the quoted original message starts.

Boundary detection only — this module returns an offset, never text, so nothing
here asserts on a "clean reply". Where a test does slice the body, it is to show
WHERE the offset landed in human-readable terms, not to test a cutting function
that does not exist yet.

SCOPE LIMIT: no pipeline wiring. Nothing calls this module in `app/` yet, so
these are unit tests against the function directly; there is no end-to-end path
to assert on.
"""

from app.pipeline.quoted_reply import find_quote_boundary, find_quote_cues

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
