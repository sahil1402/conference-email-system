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
4. ``attribution``  — ``On ... wrote:`` and its siblings in French, German,
                      Chinese, Spanish and Japanese.
                      The only LANGUAGE-SPECIFIC tier, and it exists
                      because these formats ship no divider and no header block,
                      so nothing structural is left to find. Listed last for
                      exactly that reason: it is reached only when every
                      language-free signal has already failed.

A mined list of notification phrases was deliberately NOT used. Such a list
lived in ``extractor.py`` once and was removed; that removal was about its
purpose expiring rather than its accuracy, but a corpus-mined phrase list is
brittle in exactly the way a reply from an arbitrary mail client demands it not
be, so it is not resurrected here in a new costume.

The six attribution patterns are NOT that list, and the difference is the
source rather than the size. A mined list captures what a VENUE wrote — wording
that goes stale the moment a template is edited. These capture what a MAIL
CLIENT generates: the fixed sentence it builds around a quote, which is part of
the client's output format, and each was checked against the real output of a
major client rather than invented. Six is the whole set, matching the gap the
LIMITATIONS section named; a seventh added on spec would be the mined list
returning in a new costume.

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
* ``attribution`` covers SIX languages — English, French, German, Chinese,
  Spanish and Japanese — and no others. An Italian ``ha scritto:`` or Korean
  ``작성했습니다:`` reply carrying no divider and no header block is still NOT
  detected: the boundary comes back ``None`` and the caller keeps the whole
  body. Naming the six is what keeps this a bounded, reviewable set rather than
  the start of a phrase list; adding a seventh is a decision, not a tidy-up.
* SHORT dashed rules (``--- Original Message ---``, three per side) are not
  dividers on their own, and CANNOT SAFELY BE MADE ONE. They are structurally
  identical to a person's own ``--- update ---`` heading — same characters, same
  counts, free text between — so lowering ``_DIVIDER_RE``'s minimum to three
  makes BOTH match, and the second would cut a reply at its author's own section
  heading. Measured, not assumed.

  Such a rule is therefore absorbed ONLY when another cue has already placed the
  boundary on the very next line: the cue supplies the evidence, the rule merely
  introduces it. That holds for every cue, so the marker no longer dangles on
  the end of a reply.

  WHAT REMAINS OPEN, precisely: a quote introduced by a short rule and carrying
  NO cue of its own beneath it — no header block, no ``>`` lines, no
  attribution, no full-length rule — is not detected at all, and the whole body
  comes back as the reply. There is nothing left to corroborate the rule with,
  and promoting it unaided is exactly the change rejected above. This is a
  DETECTION gap, not a tidying one, and closing it needs evidence this module
  does not have rather than a looser pattern.
* ``Begin forwarded message:`` needs no special handling and has none. It is a
  ``Label:`` line with an empty value, so it already joins the header run it
  introduces — the same shape as the real Chinese sample's bare ``抄送:``.
* ZERO-WIDTH NON-JOINER (``\\u200c``) and ZERO-WIDTH JOINER (``\\u200d``) are
  left in place wherever they appear. They are typography, not debris —
  removing them respells Persian and Arabic words and splits emoji sequences —
  so a STRAY one in Latin text survives too. Only ZWSP (``\\u200b``) and the
  BOM (``\\ufeff``), which carry no in-text meaning, are deleted.
* A person's own field list with an address in it — ``Name:`` / ``Email:`` /
  ``Affiliation:`` — matches the header-block cue and truncates their message
  at the line above. The address guard cannot help, because such a list really
  does contain an address.
* NORMALISATION IS COSMETIC-ONLY AND ONE-SIDED. Blank-line runs are capped at
  two and trailing whitespace is dropped, but LEADING whitespace is never
  touched: a leading ASCII space is indistinguishable from deliberate
  indentation, so the tie goes to the author.
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
#
# ⚠️ THE `\r?` BEFORE `$` IS LOAD-BEARING, not defensive punctuation. Under
# ``re.MULTILINE`` the ``$`` anchor matches immediately BEFORE a ``\n``, so on
# CRLF input the ``\r`` sits between the last matched character and the anchor —
# and ``\r`` is in neither ``[ \t]`` nor ``[-_=]``. Without it the match SILENTLY
# FAILS on every ``\r\n`` body, which is to say on ordinary email: CRLF is the
# RFC 5322 line ending, and Zendesk's ``plain_body`` is a mechanical HTML→text
# conversion that emits it. Measured before the fix: ten of fifteen real marker
# variants — every divider form this pattern exists to catch, in both English and
# Chinese — produced no cue at all on CRLF while matching perfectly on LF.
#
# It also makes a bare ``\r`` line ending (classic Mac, and what some converters
# leave behind mid-body) match, which costs nothing.
_DIVIDER_RE = re.compile(
    r"^[ \t]*(?:[-_=]{4,}[^\n]{0,60}[-_=]{4,}|[-_=]{8,})[ \t]*\r?$",
    re.MULTILINE,
)

# One `Label: value` line. Full-width `：` is accepted beside ASCII `:` because
# CJK clients emit it. The value may be EMPTY — the real Chinese sample carries
# a bare `抄送:` (Cc with no recipients), and requiring a value would break the
# run at exactly that line.
#
# This shape ALONE is not evidence of anything: "Note: I will do that" matches
# it. Everything that makes it trustworthy lives in _find_header_block below.
#
# ⚠️ THE LEADING CLASS ACCEPTS NBSP (`\u00a0`) AND THE IDEOGRAPHIC SPACE
# (`\u3000`) BESIDE ASCII SPACE AND TAB. Both are `\s` to Python but neither is
# in `[ \t]`, so before this a header line indented with either matched NOTHING
# — the label part begins `[^\s:：]`, which those characters also fail, so no
# amount of backtracking rescued it. An HTML client that indents quoted headers
# with `&nbsp;` (a real NBSP now that entity decoding happens at ingestion)
# produced a block that looked plainly quoted to a reader and was invisible to
# this cue.
#
# ⚠️ NARROW ON PURPOSE — `\s` was NOT used, and the asymmetry with `_is_blank`
# (which accepts any whitespace-only line) is principled rather than sloppy.
# `_is_blank` decides "this line is a GAP", where being generous costs nothing
# because the line carries no content. This regex decides "this line is
# EVIDENCE of a quoted header", where being generous widens what can truncate
# somebody's message. Generosity is cheap on one side of that and not the other,
# so only the two characters this project has actually observed converters using
# for indentation are accepted.
_HEADER_LINE_RE = re.compile(
    r"^[ \t\u00a0\u3000]*"
    r"(?P<label>[^\s:\uff1a][^:\uff1a\n]{0,23})[:\uff1a](?P<value>[^\n]*)$"
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
#
# ⚠️ `\r?` before `$` for exactly the reason spelled out on _DIVIDER_RE above —
# the same anchor, the same silent failure on real email. Both patterns are
# applied to the RAW body rather than going through _iter_lines, which is what
# separates them from the header_block and quoted_lines cues: those strip the
# line ending themselves (`rstrip("\r\n")`) and were never affected.
#
# FOUR LANGUAGES, ONE SHAPE. Each pattern pins an OPENING word and a CLOSING
# verb phrase on the same line, with a bounded middle holding the date and the
# person. That double anchor is the whole guard: it is what rejects "On the
# other hand I wrote: some notes", because ordinary prose keeps going after the
# colon and the `$` refuses it.
#
# ⚠️ THESE ARE NOT A MINED PHRASE LIST, and the distinction is not cosmetic.
# The list this module's docstring refuses is one of NOTIFICATION phrases —
# venue wording, scraped from a corpus, stale the moment a venue rewrites its
# templates. These are MAIL-CLIENT grammar: the fixed sentence a client
# generates around a quote. There are four of them because the docstring's
# LIMITATIONS section named exactly these as the gap, and deliberately no more —
# a fifth added speculatively would be the mined list arriving in a new costume.
#
# ⚠️ SHARED WEAKNESS, inherited knowingly: "On Monday I wrote:" matches the
# English pattern, and "Am Montag schrieb ich folgendes:" matches the German
# one. The existing English cue has always had that hole; these siblings are
# held to the same bar rather than a higher one, because attribution is the LAST
# cue tried and only reached when nothing structural was found at all.
_ATTRIBUTION_RES = (
    # English — "On Wed, 27 Aug 2026 at 15:28, X <a@b.net> wrote:"
    re.compile(r"^[ \t]*On\b.{0,200}?\bwrote:[ \t]*\r?$", re.MULTILINE | re.DOTALL),
    # French — "Le mar. 1 sept. 2026 à 10:00, X <a@b.net> a écrit :"
    # The space before the colon is French typography, not a typo, and is
    # optional here because not every client reproduces it.
    re.compile(
        r"^[ \t]*Le\b.{0,200}?\ba écrit[ \t]*:[ \t]*\r?$",
        re.MULTILINE | re.DOTALL,
    ),
    # German — "Am 01.09.2026 um 10:00 schrieb X <a@b.net>:"
    # `schrieb` sits in the MIDDLE here rather than at the end, so the closing
    # anchor is the colon after the sender's name.
    re.compile(
        r"^[ \t]*Am\b.{0,200}?\bschrieb\b.{0,200}?:[ \t]*\r?$",
        re.MULTILINE | re.DOTALL,
    ),
    # Chinese — "在 2026年9月1日, X <a@b.net> 写道:"
    # No `\b` anywhere: CJK has no word boundaries, so `\b` next to a Han
    # character behaves unpredictably. The full-width colon is accepted beside
    # the ASCII one, the same way `_HEADER_LINE_RE` accepts it.
    re.compile(
        r"^[ \t]*在.{0,200}?写道[ \t]*[:：][ \t]*\r?$",
        re.MULTILINE | re.DOTALL,
    ),
    # Spanish — "El 1 sept 2026, a las 10:00, X <a@b.net> escribió:"
    # Verified as the dominant form: Gmail's Spanish attribution is
    # `El <fecha> <hora>, "<nombre>" <correo> escribió:`, and Outlook's differs
    # only in punctuation. Same double anchor as the others — `El` opening,
    # `escribió:` closing.
    re.compile(
        r"^[ \t]*El\b.{0,200}?\bescribió[ \t]*:[ \t]*\r?$",
        re.MULTILINE | re.DOTALL,
    ),
    # Japanese — "2026年9月1日 10:00 X さんは書きました:" / "…は次のように書きました:"
    #
    # ⚠️ THE ONE PATTERN WITH NO OPENING ANCHOR, and that is a property of the
    # language rather than an oversight. Every other attribution opens with a
    # preposition or particle to pin — On / Le / Am / El / 在 — and Japanese has
    # none: the line begins with the date itself. Anchoring on `\d{4}年` was
    # considered and rejected, because Thunderbird's DEFAULT (reply_header_type
    # 1) emits the author with no date at all and would be missed.
    #
    # So the weight rests on the closing form: 書きました immediately followed by
    # a colon at end of line. It is the invariant across both attested shapes —
    # Thunderbird's `<名前>さんは書きました:` and the `…は次のように書きました:`
    # variant. Japanese prose ends a sentence with 。, not with a colon, so a
    # line ending exactly `書きました:` is an attribution rather than a sentence.
    # No `\b` and a full-width colon accepted, for the same CJK reasons as above.
    #
    # ⚠️ NO re.DOTALL HERE — THE ONLY PATTERN WITHOUT IT, and removing it fixed a
    # real bug rather than tidying one. The others carry DOTALL so a Gmail
    # attribution that WRAPS (`On … <addr>` / newline / `wrote:`) still matches,
    # and their opening anchor keeps that span honest: the match cannot begin
    # anywhere but at an `On` / `Le` / `Am` / `El` / `在`.
    #
    # This pattern has no such anchor, so with DOTALL the leading `^.{0,200}?`
    # started at the FIRST line of the body and ran across newlines to reach
    # 書きました further down — swallowing the person's actual reply, which came
    # back empty. Observed on the very first run, not reasoned about. Without
    # DOTALL the match is confined to one line, which is also the correct shape:
    # no wrapped Japanese attribution is attested.
    re.compile(
        r"^[ \t]*.{0,200}?書きました[ \t]*[:：][ \t]*\r?$",
        re.MULTILINE,
    ),
)

# A SHORT dashed rule — three or more per side, where `_DIVIDER_RE` demands four.
#
# ⚠️ NOT A DIVIDER CUE ON ITS OWN, and that is the point. `--- Original Message
# ---` and `--- update ---` are structurally IDENTICAL: same characters, same
# counts, a free-text label between. Measured, not assumed — lowering
# `_DIVIDER_RE` to three makes BOTH match, which would cut a person's message at
# their own section heading. No language-free rule separates them.
#
# So this is used only to EXTEND a header block upward: a short rule sitting
# immediately above one is part of the same quoted structure, because the header
# block is what supplies the evidence and the rule merely introduces it.
# `--- update ---` is followed by the person's own prose, no header block forms,
# and nothing changes for it. See `_find_header_block`.
_SHORT_RULE_RE = re.compile(r"^[ \t]*[-_=]{3,}[^\n]{0,60}[-_=]{3,}[ \t]*$")

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


# --- output normalisation ---------------------------------------------------
# Applied to what the person wrote, AFTER the quote has been cut away. Never to
# the body being scanned for cues — see _normalize_reply.

# At most this many consecutive blank lines survive. Three or more in a row is a
# conversion artifact, not a paragraph break; two is already more than anyone
# types on purpose, so capping THERE rather than at one keeps a deliberate
# double break intact while still removing the runs of six the converter emits.
_MAX_BLANK_LINES = 2

# A run of blank lines longer than the cap. Only meaningful AFTER each line has
# been right-trimmed, because the converter's "blank" lines usually carry spaces
# or a decoded NBSP and would not otherwise match `\n`.
# `_MAX_BLANK_LINES` blank lines is `_MAX_BLANK_LINES + 1` newlines, so a run of
# 4+ collapses to 3.
_EXCESSIVE_BLANKS_RE = re.compile(r"\n{4,}")

# Invisible characters deleted from ANYWHERE in the reply, not just its ends.
#
# ⚠️ TWO OF THE FOUR ZERO-WIDTH CHARACTERS, and the omission is the decision.
#
# REMOVED — neither carries meaning inside running text:
#   U+200B ZERO WIDTH SPACE          a line-break hint; deleting it changes no
#                                    word, and Word/Outlook HTML round-tripping
#                                    sprinkles it through text.
#   U+FEFF ZERO WIDTH NO-BREAK SPACE meaningful ONLY as a byte-order mark at the
#                                    start of a stream. Mid-text it is the
#                                    deprecated ZWNBSP — pure debris.
#
# KEPT — both are TYPOGRAPHY, not debris, and deleting them corrupts words:
#   U+200C ZERO WIDTH NON-JOINER     separates morphemes in Persian/Arabic and
#                                    Indic scripts. Measured: Persian "می‌رود"
#                                    (mi-ravad) becomes "میرود" without it — a
#                                    different, misspelled word.
#   U+200D ZERO WIDTH JOINER         forms ligatures, and joins emoji sequences.
#                                    Measured: the family emoji 👩‍👧 splits into
#                                    two separate glyphs without it.
#
# All four look identical to a reader — invisible — which is exactly why they
# cannot be treated identically. A conditional rule (delete ZWNJ/ZWJ only
# between ASCII neighbours, say) was considered and rejected: it buys the
# removal of a rare stray character in Latin text at the cost of a script-
# detection heuristic that can corrupt a real word when it guesses wrong, on the
# smallest of the seven defects this series addressed. Under the harm asymmetry
# used throughout, a stray invisible character that survives is a far better
# outcome than a mangled Persian word posted to a public venue.
#
# ⚠️ RESIDUAL, NAMED: a stray ZWNJ/ZWJ in the INTERIOR of Latin text is not
# removed. At the two ENDS they are, because ``_EDGE_TRIM_RE`` below already
# lists all four — which turns out to be the more coherent rule than the one
# aimed at: ZWNJ/ZWJ survive exactly where they can join something (between two
# characters) and are trimmed where they cannot (a boundary with nothing on one
# side). That falls out of the two patterns composing rather than being designed,
# so it is pinned by test lest one half be "tidied" without the other.
_INTERIOR_INVISIBLE_RE = re.compile("[​﻿]")

# Trim at the two ends: Unicode whitespace (`\s` covers NBSP and the ideographic
# space) PLUS the zero-width characters, which `str.strip()` does NOT remove
# because Python does not classify them as whitespace.
_EDGE_TRIM_RE = re.compile(r"^[\s​‌‍﻿]+|[\s​‌‍﻿]+$")


def _normalize_reply(text: str) -> str:
    """Tidy the CONVERSION artifacts out of a reply, and nothing else.

    Zendesk's ``plain_body`` is a mechanical HTML-to-text rendering, and it
    leaves debris a person never typed: runs of six blank lines between a
    salutation and a one-line body, trailing spaces on every line. Commit 6
    deliberately refused to touch interior whitespace, reasoning that "dropping a
    line someone deliberately wrote changes what they said". That reasoning was
    protecting AUTHORED STRUCTURE and it still holds — but it was being applied
    to debris. Nobody deliberately writes six blank lines.

    THREE STEPS, each chosen to be the conservative side of a harm asymmetry:
    this text is posted essentially verbatim to a public venue, so removing
    something a person meant is materially worse than leaving one stray blank
    line behind.

    1. RIGHT-TRIM EACH LINE. Trailing whitespace carries no meaning in any
       rendering, so removing it cannot change what was said. It is also the
       ENABLER for step 2: a converter's "blank" line usually holds spaces or a
       decoded NBSP, so without this it is not blank to a regex.
    2. CAP BLANK-LINE RUNS at :data:`_MAX_BLANK_LINES`.
    3. TRIM THE TWO ENDS, including zero-width characters.

    ⚠️ WHAT IS DELIBERATELY NOT DONE, and why — the offline miner
    ``scripts/data_mining/mine_extract_marc.py`` collapses every run of spaces
    and tabs to one (``_WS.sub(" ", text)``), and that rule was measured against
    this use and REJECTED on both counts:

    * It does not fix the artifact it was cited for. A single leading space is
      already a run of one, so the substitution is a no-op on
      ``" I think there is a mix-up..."`` — the exact reported symptom.
    * It destroys authored indentation. ``"    - first sub-point"`` becomes
      ``" - first sub-point"``, and a 4-space and an 8-space indent both flatten
      to one, erasing nesting levels outright.

    That script caps bodies at 2000 characters and deletes quoted lines
    wholesale; it is building a corpus FEATURE, where a little lossiness costs
    noise in an aggregate. Here the output is somebody's words on their way to a
    public forum. Same regex, different contract.

    LEADING whitespace is therefore left exactly as written. A leading ASCII
    space is indistinguishable from deliberate indentation, and under the harm
    asymmetry the tie goes to the author. So the reported "odd leading space"
    symptom is NOT fixed by this function, and that is a decision rather than an
    oversight.

    ⚠️ ONE CONSEQUENCE WORTH NAMING: right-trimming a line also removes the
    ``\\r`` of a CRLF ending, so the returned text always uses ``\\n``. A line
    ending is transport framing, never something a person authored, and the text
    goes on to a web API rather than back into a mail client — but it IS a
    visible change to this function's output and is pinned by test rather than
    left to be discovered.
    """
    # ⚠️ STEP ZERO, AND THE ORDER IS LOAD-BEARING. A line holding nothing but a
    # zero-width space is blank to every reader and NOT blank to any of the
    # steps below — it survives `rstrip()`, and `_is_blank` rejects it, because
    # Python does not classify these as whitespace. Deleting them first is what
    # lets such a line count toward the blank-line cap.
    #
    # Measured on four invisible-only lines between two paragraphs:
    #   remove first, then cap -> "A\n\n\nB"          (capped, correct)
    #   cap, then remove last  -> "A\n\n\n\n\nB"      (never capped)
    visible = _INTERIOR_INVISIBLE_RE.sub("", text)
    lines = [line.rstrip() for line in visible.split("\n")]
    capped = _EXCESSIVE_BLANKS_RE.sub("\n" * (_MAX_BLANK_LINES + 1), "\n".join(lines))
    return _EDGE_TRIM_RE.sub("", capped)


def _is_blank(line: str) -> bool:
    """A line carrying no visible characters — the one interruption a header
    block is allowed to survive.

    ``not line.strip()`` rather than an explicit character list, because
    ``str.strip()`` with no argument already removes ALL Unicode whitespace.
    That covers the two shapes an HTML-to-text converter actually produces —
    NBSP (``\\u00a0``) and the ideographic space (``\\u3000``) — along with the
    rest of the Unicode whitespace table, and it does so without a hand-kept
    list that would silently miss the next one.

    ⚠️ DELIBERATELY NOT BLANK, both out of scope here:

    * ``\\u200b`` ZERO WIDTH SPACE and friends. Python does not classify them as
      whitespace (``"\\u200b".isspace()`` is False), and they are invisible
      rather than blank — a different problem, belonging with the other
      zero-width handling.
    * The LITERAL six characters ``&nbsp;``. An undecoded HTML entity is not
      whitespace, it is text that should have been decoded upstream. Tolerating
      it here would paper over the missing decode step and make the real fix
      harder to see. Once entity decoding lands, such a line arrives as
      ``\\u00a0`` and this function already accepts it — the two changes compose
      rather than overlap. Pinned by test.
    """
    return not line.strip()


def _find_header_block(body: str) -> int | None:
    """Offset of the first trustworthy quoted header block, or None.

    Two independent guards, and BOTH are load-bearing:

    * a run of at least :data:`_MIN_HEADER_RUN` header-shaped lines, which
      incidental prose does not produce; and
    * at least one address somewhere in that run, which is what tells a real
      header block apart from a user's own list (``Paper number:`` / ``Title:``
      / ``Status:`` stacks three colon lines and names nobody).

    Dropping either one alone lets a plain metadata list truncate a real
    message, so neither is decoration.

    BLANK LINES BRIDGE A RUN; ANYTHING ELSE STILL BREAKS IT. Real quoted headers
    arrive with blank lines between the fields often enough that requiring them
    to be strictly adjacent lost whole blocks — a divider-less notification with
    one blank line between ``From:`` and ``Date:`` went completely undetected,
    and the entire quoted message was returned as the person's own reply. A
    content line that is neither blank nor header-shaped still resets the run to
    zero, exactly as before: this is a narrow tolerance for invisible
    interruptions, not a general loosening.

    ⚠️ A BRIDGED LINE IS SKIPPED, NOT COUNTED. It does not advance
    ``run_length``, so the threshold still means three REAL header lines. The
    alternative — counting them — would let ``From: a@b.net`` followed by two
    blank lines reach three on the strength of one actual header, which is not
    evidence of anything. Mutation-verified: with counting, two header lines
    separated by one blank incorrectly qualify.

    ⚠️ KNOWN CONSEQUENCE, accepted rather than overlooked: a person who types
    their own field list with blank lines between the entries — ``Name:`` /
    ``Email: me@uni.edu`` / ``Affiliation:`` — now matches, where the blank lines
    used to protect them. The address guard does not help there, because such a
    list contains a real address. The trade is deliberate: a missed header block
    posts a whole quoted notification to a public venue, while a false positive
    truncates a draft that a chair reads before anything is sent. If it proves a
    problem in practice, the lever is bounding how many consecutive blank lines
    may bridge (currently unbounded); the shape is pinned by test so the
    behaviour is visible rather than discovered.
    """
    run_start: int | None = None
    run_length = 0
    run_has_address = False
    #: The line above the current one, so a short rule introducing the block can
    #: be absorbed into it. Reset whenever the run is.
    rule_above: int | None = None
    prev: tuple[int, str] | None = None

    for offset, line in _iter_lines(body):
        if _HEADER_LINE_RE.match(line):
            if run_start is None:
                run_start = offset
                run_length = 0
                run_has_address = False
                # A short dashed rule directly above the first header line is
                # the marker that introduces the quote — `--- Original Message
                # ---`, which `_DIVIDER_RE` refuses because three dashes per
                # side is also what a person's own `--- update ---` looks like.
                # Absorbed HERE instead, where the header block supplies the
                # evidence the rule cannot supply for itself, so the marker stops
                # being left dangling on the end of the reply. A rule with no
                # header block under it still means nothing.
                rule_above = (
                    prev[0]
                    if prev is not None and _SHORT_RULE_RE.match(prev[1])
                    else None
                )
            run_length += 1
            run_has_address = run_has_address or bool(_ADDRESS_RE.search(line))
            if run_length >= _MIN_HEADER_RUN and run_has_address:
                return rule_above if rule_above is not None else run_start
        elif _is_blank(line) and run_start is not None:
            # Bridge: the run survives, unchanged. Note the `run_start is not
            # None` guard — a blank line cannot START a run, so `run_start`
            # keeps pointing at the first real header line and the boundary
            # never swallows blank lines that belong to the reply above.
            prev = (offset, line)
            continue
        else:
            run_start = None
            run_length = 0
            run_has_address = False
            rule_above = None
        prev = (offset, line)
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

    # EARLIEST across all four languages. A thread can carry more than one —
    # an English reply above a French one — and the earliest is where the newest
    # author stopped writing, exactly as with the other cues.
    starts = [m.start() for m in (p.search(body) for p in _ATTRIBUTION_RES) if m]
    if starts:
        cues.append(QuoteCue(min(starts), "attribution"))

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
    if not cues:
        return None
    return _absorb_rule_above(body, cues[0].offset)


def _absorb_rule_above(body: str, boundary: int) -> int:
    """Back the boundary up over a short rule introducing the quote, if any.

    GENERALISES Fix 6's one-line absorption from the header-block cue to EVERY
    cue, because measuring showed the dangling marker was never specific to
    header blocks:

        rule + quoted_lines  -> kept "Thanks.\\n\\n--- Original Message ---"
        rule + attribution   -> kept "Thanks.\\n\\n--- Original Message ---"
        rule + 4-dash rule   -> kept "Thanks.\\n\\n--- Original Message ---"
        rule + header block  -> kept "Thanks."          (already handled)

    Only the last was covered, so a client marker was left on the end of the
    reply — bound for a public venue — in the other three.

    ⚠️ THE CORROBORATION RULE IS UNCHANGED, and it is the whole safety argument.
    `--- Original Message ---` and `--- update ---` are structurally IDENTICAL,
    so a short rule is never a boundary ON ITS OWN. It is absorbed only when
    something else has already placed the boundary on the very next line — the
    cue supplies the evidence, the rule merely introduces it.

    ⚠️ BOUNDED TO EXACTLY ONE LINE, same as Fix 6. That bound is what protects
    commit 5's guard, and the protection is structural rather than lucky: a
    person writing `--- update ---` follows it with their OWN text, so their
    rule is never the line directly above a quote. Measured —

        "Dear Chairs,  /  --- update ---  /  I resubmitted it.  /  > your note"

    keeps all three of their lines, because their rule is two lines up from the
    boundary, not one.

    Idempotent where Fix 6 already backtracked: the line above the rule is then
    the reply or a blank, neither of which is a rule, so nothing more is taken.
    """
    # Cue offsets always point at the START of a line, so the newline just
    # before `boundary` ends the previous line. A boundary of 0 needs no
    # separate guard: `rfind` over an empty slice returns -1 and falls into the
    # branch below. An explicit `if boundary <= 0` was written first and removed
    # after a mutation proved it changed nothing — a redundant guard reads as
    # load-bearing and sends the next reader hunting for the case it covers.
    prev_end = body.rfind("\n", 0, boundary)
    if prev_end == -1:
        return boundary
    prev_start = body.rfind("\n", 0, prev_end) + 1
    previous = body[prev_start:prev_end].rstrip("\r")
    return prev_start if _SHORT_RULE_RE.match(previous) else boundary


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
        return _normalize_reply(body)
    # `0` needs no special case: ``body[:0]`` is already "". Spelled as one
    # slice rather than three branches because the two are genuinely the same
    # operation, and a separate `if boundary == 0` would only invite the two
    # paths to drift.
    #
    # ⚠️ NORMALISED AFTER THE SLICE, NEVER BEFORE. `find_quote_boundary` reads
    # the RAW body and returns an offset into it; normalising first would
    # change the string's length and desynchronise the offset from the text
    # being cut — the same drift the CRLF commit chose a regex anchor to avoid
    # and the entity commit chose ingestion-time decoding to avoid. Doing it
    # here means boundary detection is untouched by this change, which is
    # pinned by test.
    return _normalize_reply(body[:boundary])
