"""Where does the quoted original message start?

A reply carries the message it replies to underneath it. This module reads that
structure and answers two LAYERED questions about a raw email body:

* :func:`find_quote_boundary` — at what character offset does the quoted part
  begin?
* :func:`extract_reply_text` — what did the person actually write?

The separation between them is deliberate and is kept at the FUNCTION level, not
by splitting the file. Finding a boundary and deciding what to keep have
different failure modes — a wrong boundary is a detection bug, a wrong slice is a
handling bug — and one function doing both would be testable at neither. So the
second calls the first and does nothing else clever: it slices and trims. Every
boundary case can still be exercised without going near the text, and vice
versa.

What this module still does NOT do, in either function: strip signatures, remove
sign-offs, reflow, or otherwise tidy what the person wrote. Trimming is
whitespace at the two ends and nothing more — see :func:`extract_reply_text`.

WHY THIS IS NOT THE OFFLINE SCRIPTS
-----------------------------------
Two offline scripts already cut quoted history —
``scripts/distill_style_guide.py`` (marker substrings) and
``scripts/data_mining/mine_extract_marc.py`` (a regex list). Both were written
for batch corpus analysis, where a slightly wrong cut costs a little noise in an
aggregate. Here a wrong boundary means posting the wrong text to a public
system, so neither was copied. Three concrete divergences:

* Both are ENGLISH-ONLY. ``distill_style_guide`` scans for ``"\\nFrom: "`` and
  ``"\\n-----Original Message-----"``; neither matches a Chinese client's
  ``发件人:`` or ``-----原始邮件-----``. This module leads with cues that carry
  no language at all.
* ``distill_style_guide``'s markers all begin with ``"\\n"``, so a body that
  IS a quote from character zero matches nothing and is returned whole. That is
  precisely the case where returning the whole body is most dangerous.
* ``mine_extract_marc`` cuts on a bare ``^From:\\s.+$``. One line is far too
  little evidence — "From: my perspective..." would truncate a real message.
  A lone header line is not a cue here; only a RUN of them is.

STRUCTURE FIRST, PHRASES LAST
-----------------------------
Cues are ordered by how much they depend on knowing a language:

1. ``divider``      — a rule of ``-``/``_``/``=``, optionally wrapping a label.
                      Language-free: the label between the dashes can be in any
                      script, and is never inspected.
2. ``header_block`` — a run of ``Label: value`` lines. The LABELS are
                      language-specific; the SHAPE is not, so the shape is what
                      is matched.
3. ``quoted_lines`` — ``>``-prefixed lines. Language-free.
4. ``attribution``  — ``On ... wrote:``. Genuinely English, and the only cue
                      here that is. It exists because this format ships no
                      divider and no header block, so nothing structural is left
                      to find. Listed last because it is the one cue that fails
                      silently on a non-English client (see LIMITATIONS).

A mined list of notification phrases was deliberately NOT used. Such a list
lived in ``extractor.py`` once and was removed; that removal was about its
purpose expiring rather than its accuracy, but a corpus-mined phrase list is
brittle in exactly the way a reply from an arbitrary mail client demands it not
be, so it is not resurrected here in a new costume.

LIMITATIONS — none of these are silent
--------------------------------------
* BOTTOM-POSTED replies (quote first, the person's new text below it) are NOT
  supported. Detecting them needs the quote's END, which is a strictly harder
  problem: quote blocks have no terminator. Such a body yields a boundary at or
  near 0, which a caller slicing ``body[:boundary]`` reads as "no new text".
  That is lossy but SAFE — an empty reply is obviously wrong to a human,
  whereas a reply that is secretly a quoted notification looks plausible and
  would be sent. ``0`` is therefore a meaningful return value, distinct from
  ``None``, and a caller that wants to treat bottom-posting specially can test
  for it. See :func:`find_quote_boundary`.
* ``attribution`` is English-only. A French ``a écrit :`` or German ``schrieb:``
  reply with no divider and no header block is NOT detected — the boundary
  comes back ``None`` and the caller keeps the whole body. That is the same
  failure the offline scripts have; it is not fixed here, only named.
* Signature blocks are out of scope entirely. ``-- `` is not treated as a cue
  (it marks a signature, not a quote), and no sign-off phrases are matched. A
  reply's own signature stays in the reply.
* Only the FIRST cue matters. Nesting depth is not measured and no attempt is
  made to tell a two-level thread from a ten-level one.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# --- cue patterns -----------------------------------------------------------
# A horizontal rule, with or without a label inside it. Two shapes:
#   -----Original Message-----  /  -----原始邮件-----  /  ---- Forwarded ----
#   ________________________________            (a bare rule, Outlook)
# The label is bounded and never read, so the cue is script-independent.
#
# The 4-character minimum and the requirement that a labelled rule CLOSE with
# rule characters are both false-positive guards: `-- ` (a signature delimiter,
# not a quote) and a casual `--- update ---` are rejected, while every real
# divider seen in this project's traffic is matched.
_DIVIDER_RE = re.compile(
    r"^[ \t]*(?:[-_=]{4,}[^\n]{0,60}[-_=]{4,}|[-_=]{8,})[ \t]*$",
    re.MULTILINE,
)

# One `Label: value` line. Full-width `：` is accepted beside ASCII `:` because
# CJK clients emit it. The value may be EMPTY — the real Chinese sample carries
# a bare `抄送:` (Cc with no recipients), and requiring a value would break the
# run at exactly that line.
#
# This shape ALONE is not evidence of anything: "Note: I will do that" matches
# it. Everything that makes it trustworthy lives in _find_header_block below.
_HEADER_LINE_RE = re.compile(
    r"^[ \t]*(?P<label>[^\s:\uff1a][^:\uff1a\n]{0,23})[:\uff1a](?P<value>[^\n]*)$"
)

# A quoted header block identifies PEOPLE, and that is what separates it from a
# user's own colon-formatted list. From/To/发件人/收件人 carry an address;
# "Paper number: 1030 / Title: X / Status: Y" does not.
_ADDRESS_RE = re.compile(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+")

_QUOTED_LINE_RE = re.compile(r"^[ \t]*>")

# `On <anything> wrote:` — including the wrapped form Gmail produces, where the
# trailing `wrote:` lands on its own line. DOTALL lets the match cross that
# newline; the 200-character bound stops it running away across a whole body and
# is what rejects prose like "On the other hand I wrote: some notes".
_ATTRIBUTION_RE = re.compile(
    r"^[ \t]*On\b.{0,200}?\bwrote:[ \t]*$",
    re.MULTILINE | re.DOTALL,
)

# A header block needs this many consecutive `Label: value` lines to count.
# Three is chosen against the real shapes: every quoted header seen here carries
# at least From/Sent/To (English) or 发件人/发送时间/收件人 (Chinese), so nothing
# real is lost, while incidental prose almost never stacks three colon lines in
# a row.
_MIN_HEADER_RUN = 3

# Two consecutive `>` lines are required. A real quoted message is never one
# line, and a lone `>` is far likelier to be a stray character or a markdown
# blockquote inside the person's own text.
_MIN_QUOTED_RUN = 2


class QuoteCue(NamedTuple):
    """One detected start-of-quote, and which signal found it.

    ``cue`` is carried for diagnosis, not for logic: when a boundary turns out
    wrong on a real ticket, the first question is which signal fired, and
    re-deriving that from an offset alone means re-running the scan by hand.
    """

    offset: int
    cue: str


def _iter_lines(body: str):
    """Yield ``(start_offset, line)`` for every line, newline excluded."""
    offset = 0
    for line in body.splitlines(keepends=True):
        yield offset, line.rstrip("\r\n")
        offset += len(line)


def _find_header_block(body: str) -> int | None:
    """Offset of the first trustworthy quoted header block, or None.

    Two independent guards, and BOTH are load-bearing:

    * a run of at least :data:`_MIN_HEADER_RUN` consecutive header-shaped lines,
      which incidental prose does not produce; and
    * at least one address somewhere in that run, which is what tells a real
      header block apart from a user's own list (``Paper number:`` / ``Title:``
      / ``Status:`` stacks three colon lines and names nobody).

    Dropping either one alone lets a plain metadata list truncate a real
    message, so neither is decoration.
    """
    run_start: int | None = None
    run_length = 0
    run_has_address = False

    for offset, line in _iter_lines(body):
        if _HEADER_LINE_RE.match(line):
            if run_start is None:
                run_start = offset
                run_length = 0
                run_has_address = False
            run_length += 1
            run_has_address = run_has_address or bool(_ADDRESS_RE.search(line))
            if run_length >= _MIN_HEADER_RUN and run_has_address:
                return run_start
        else:
            run_start = None
            run_length = 0
            run_has_address = False
    return None


def _find_quoted_lines(body: str) -> int | None:
    """Offset of the first run of at least two consecutive ``>`` lines."""
    run_start: int | None = None
    run_length = 0

    for offset, line in _iter_lines(body):
        if _QUOTED_LINE_RE.match(line):
            if run_start is None:
                run_start = offset
                run_length = 0
            run_length += 1
            if run_length >= _MIN_QUOTED_RUN:
                return run_start
        else:
            run_start = None
            run_length = 0
    return None


def find_quote_cues(body: str) -> list[QuoteCue]:
    """Every start-of-quote this module can find, earliest first.

    Exposed alongside :func:`find_quote_boundary` so each signal can be tested
    and diagnosed on its own. The boundary is just the earliest of these.
    """
    if not body:
        return []

    cues: list[QuoteCue] = []

    divider = _DIVIDER_RE.search(body)
    if divider is not None:
        cues.append(QuoteCue(divider.start(), "divider"))

    header = _find_header_block(body)
    if header is not None:
        cues.append(QuoteCue(header, "header_block"))

    quoted = _find_quoted_lines(body)
    if quoted is not None:
        cues.append(QuoteCue(quoted, "quoted_lines"))

    attribution = _ATTRIBUTION_RE.search(body)
    if attribution is not None:
        cues.append(QuoteCue(attribution.start(), "attribution"))

    return sorted(cues, key=lambda c: c.offset)


def find_quote_boundary(body: str) -> int | None:
    """Character offset where quoted content begins, or None if there is none.

    The offset is the START of the line that opens the quote, so
    ``body[:boundary]`` excludes the divider / header / attribution line itself,
    and ``body[boundary:]`` begins with it. Whitespace between the reply and the
    quote is left on the reply side: trimming it is the caller's decision, not a
    property of where the quote starts.

    THE EARLIEST cue wins. That is what makes a thread's depth irrelevant — in a
    reply to a reply to a reply, the first cue is where the newest author
    stopped writing, and everything past it is history regardless of how many
    levels it contains.

    Three return values, all distinct and all meaningful:

    * ``None`` — no quote found. The whole body is the person's own text.
    * ``0``    — the quote starts at character zero: the body opens with quoted
      material and carries no new text above it. A forward with no comment
      lands here, and so does a BOTTOM-POSTED reply, which this module does not
      support (see the module docstring). ``0`` is returned rather than ``None``
      on purpose: a caller slicing ``body[:0]`` gets an empty reply, which is
      visibly wrong, whereas ``None`` would hand back the entire quoted
      notification as though the person had written it.
    * a positive offset — the ordinary case.
    """
    cues = find_quote_cues(body)
    return cues[0].offset if cues else None


def extract_reply_text(body: str) -> str:
    """What the person actually wrote, with the quoted history removed.

    Thin by design: ask :func:`find_quote_boundary` where the quote starts, keep
    what is above it, trim the ends. Everything subtle lives in the boundary
    function; this one only has to not add mistakes of its own.

    TRIM SEMANTICS — ``str.strip()`` at the two ends, and NOTHING else.

    Interior whitespace is left exactly as written: blank lines between
    paragraphs, indentation, a hand-aligned list. That is a deliberate
    conservative choice rather than an oversight, because this output is posted
    essentially verbatim somewhere public. Collapsing runs of blank lines would
    silently reformat authored content, and the two errors are not
    symmetrical — one stray blank line is cosmetic, whereas dropping a line
    someone deliberately wrote changes what they said. Leading and trailing
    whitespace is the one part that carries no meaning in a posted comment, so
    it is the one part removed.

    ``strip()`` with no argument also removes Unicode whitespace, which matters
    here: a CJK client can leave an ideographic space (``\u3000``) or a
    non-breaking space around the text, and an ASCII-only trim would leave those
    behind.

    Explicitly NOT done: no signature stripping, no sign-off detection, no
    "Sent from my iPhone" removal. A signature is part of what the person wrote,
    and posting it verbatim is safer than guessing where their words end. See
    the module docstring.

    Returns, matching :func:`find_quote_boundary`'s three cases:

    * boundary ``None`` — no quote found, so the whole body is theirs.
    * boundary ``0`` — the body opens with quoted material, so there is no new
      text: returns ``""``.
    * a positive boundary — the text above it.

    An EMPTY RESULT is the reliable "this person wrote nothing new" signal, and
    it is strictly more robust than testing ``find_quote_boundary(body) == 0``.
    A body whose quote is preceded only by a blank line has a boundary of 2, not
    0, yet still contains no reply; the trim collapses that to ``""`` while a
    boundary check would call it an ordinary reply.
    """
    # Mirrors find_quote_cues' own empty guard, and keeps a NULL body column
    # from raising on ``.strip()`` once this is wired to real email rows.
    if not body:
        return ""

    boundary = find_quote_boundary(body)
    if boundary is None:
        return body.strip()
    # `0` needs no special case: ``body[:0]`` is already "". Spelled as one
    # slice rather than three branches because the two are genuinely the same
    # operation, and a separate `if boundary == 0` would only invite the two
    # paths to drift.
    return body[:boundary].strip()
