"""The LLM sign-off placeholder [Sender name] is filled with the chair's name
when the response is finalized (prompt unchanged; only the parsed reply is)."""

from app.pipeline.drafter import _SENDER_NAME, _sanitize_reply, _split_structured


def test_sanitize_reply_fills_sender_placeholder():
    out = _sanitize_reply("Dear Author,\n\nBest regards,\n[Sender name]\nAAAI Team")
    assert "[Sender name]" not in out
    assert _SENDER_NAME in out


def test_case_and_underscore_variants_replaced():
    assert _SENDER_NAME in _sanitize_reply("Best,\n[SENDER NAME]")
    assert _SENDER_NAME in _sanitize_reply("Best,\n[sender_name]")


def test_split_structured_reply_gets_name():
    raw = (
        "=== REPLY ===\nDear X,\n\nBest regards,\n[Sender name]\nAAAI Team\n"
        "=== CITATIONS ===\npolicy_101\n"
        "=== NOTES FOR CHAIR ===\nnone\n"
        "=== CONFIDENCE ===\n0.9"
    )
    reply, _cites, _notes, _conf = _split_structured(raw)
    assert "[Sender name]" not in reply
    assert _SENDER_NAME in reply


def test_no_placeholder_left_unchanged():
    # A reply without the placeholder is not otherwise altered by the fill.
    assert _sanitize_reply("Thanks,\nProgram Chairs") == "Thanks,\nProgram Chairs"
